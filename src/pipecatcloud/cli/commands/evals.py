#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""`pipecat cloud eval`: run scenarios, judge finished calls, and read the verdicts.

`run` runs the scenarios it's given, every one in the file unless `--scenario`
names some. Where the organization has suite runs, they run as one, with one
verdict, which `eval_suites.py` follows. Otherwise, and with `--param`, which a
suite run doesn't take, it starts a run for each and the service runs them
side by side. It follows those the way `pipecat eval` does: the scenarios'
settings, a row a run, a summary, and what went wrong with each run that
didn't pass.

`judge` submits finished sessions against one criteria policy, without starting
an agent session. Each submission and result retains its source session ID.

`run` and `judge` wait by default (`--no-wait` submits and returns); `status`
shows a run as it is, and waits with `--wait`. Exit codes when they wait, for
several runs the first that applies:

    3  an API error, or a usage error (including a bundle that can't be sent)
    1  a verdict is `failed`
    2  a run ended without a verdict (cancelled, expired, refused, ...)
    0  every verdict is `passed` or `skipped`

Without waiting, 0 means every request was accepted. Ctrl-C while waiting
exits 130 and leaves the runs going.
"""

import asyncio
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NoReturn
from uuid import UUID

import typer
from rich import box
from rich.console import Group
from rich.live import Live
from rich.markup import escape
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from typer.core import TyperCommand, TyperGroup

# Typer 0.26+ bundles its own copy of click and raises that copy's exceptions,
# which the click package's classes don't match. Earlier typer is built on the
# click package. typer.Exit and typer.Abort are the right classes either way;
# UsageError, ClickException and Context have no public typer name that matches
# the base classes below, so take them from whichever click typer runs on.
try:
    from typer._click.core import Context  # pyright: ignore[reportMissingImports]
    from typer._click.exceptions import (  # pyright: ignore[reportMissingImports]
        ClickException,
        UsageError,
    )
except ImportError:
    from click.core import Context
    from click.exceptions import ClickException, UsageError

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import OutputMode, console, format_timestamp
from pipecatcloud._utils.eval_bundle import BundleError, build_bundle
from pipecatcloud.api import _parse_retry_after
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_NO_VERDICT = 2
EXIT_ERROR = 3
EXIT_INTERRUPTED = 130

LIVE_STATUSES = ("queued", "starting", "running")


def _print(*objects: Any, **kwargs: Any) -> None:
    """Print without rich's highlighting, which colors numbers, quotes and ids on its
    own. `pipecat eval` leaves values uncolored; only the markup here colors."""
    kwargs.setdefault("highlight", False)
    console.print(*objects, **kwargs)


# Polling starts quickly, since a queued run is taken up within seconds, and
# backs off to a steady pace: a run takes from tens of seconds to minutes.
POLL_START_SECONDS = 2.0
POLL_FACTOR = 1.5
POLL_MAX_SECONDS = 10.0
# Consecutive failed polls (a dropped connection, a 5xx, a 429) before giving up.
# A 4xx other than 429 is not going to change and ends the wait at once.
POLL_MAX_CONSECUTIVE_ERRORS = 5
# A failed poll's `Retry-After` is honoured up to this many seconds.
POLL_RETRY_AFTER_CAP_SECONDS = 60
# A run's deadline is not its last moment. The API lets the worker that holds a
# run 15 minutes past the deadline to store its result, and only then does its
# sweep, which runs every 60 seconds, end a stuck run as `expired` (a run with
# no verdict, exit 2). A wait gives up only after both, so a stuck run reaches
# its `expired` end here rather than as an error.
API_RESULT_GRACE_SECONDS = 15 * 60
API_SWEEP_SECONDS = 60

# The synthetic run settings `--param` takes, as the API names them.
RUN_PARAMS = ("connectTimeoutSeconds", "botReadyTimeoutSeconds", "defaultTimeoutMs")


class _EvalCommand(TyperCommand):
    """A command whose usage errors, and unexpected errors, exit 3, as its API errors do.

    Click exits 2 on a usage error, and here 2 means a run ended without a
    verdict. An uncaught exception exits 1, which here means the verdict was
    `failed`. A script that tests the exit code must be able to tell them apart.
    The usage error is still shown by Typer, in its usual format.
    """

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except UsageError as e:
            e.exit_code = EXIT_ERROR
            raise

    def invoke(self, ctx: Context) -> Any:
        try:
            return super().invoke(ctx)
        except UsageError as e:
            e.exit_code = EXIT_ERROR
            raise
        except (ClickException, typer.Exit, typer.Abort):
            raise
        except Exception as e:
            # The type only: the text of an exception can quote a file or a token.
            _fail(
                f"Unexpected error ({type(e).__name__}). The run, if one was started, "
                f"can be found with `{PIPECAT_CLI_NAME} eval list`.",
                code="CLI-INTERNAL",
            )


class _EvalGroup(TyperGroup):
    """A group whose usage errors exit 3, like its commands'."""

    def parse_args(self, ctx: Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except UsageError as e:
            e.exit_code = EXIT_ERROR
            raise

    def invoke(self, ctx: Context) -> Any:
        try:
            return super().invoke(ctx)
        except UsageError as e:
            e.exit_code = EXIT_ERROR
            raise


eval_cli = typer.Typer(
    name="eval",
    cls=_EvalGroup,
    help="Run scenarios, judge finished sessions, and read the results",
    no_args_is_help=True,
)


# ---- Helpers ----


def _use_json(output_json: bool) -> None:
    if output_json:
        # Predates the global --output mechanism in this command family; kept
        # as the same alias spend-limit offers.
        console.set_output_mode(OutputMode.json)


def _fail(message: str, code: str = "CLI-USAGE") -> NoReturn:
    """Report a client-side failure and exit with the error code."""
    if console.json_output:
        console.output_json({"error": {"error": message, "code": code}})
    else:
        console.error(escape(message))
    raise typer.Exit(EXIT_ERROR)


def _org(organization: str | None) -> str:
    # `requires_login` has settled an organization by the time a command runs.
    return str(organization or config.get("org"))


def _api():
    # In json mode the error object is the only thing on stdout, so the client
    # must not print its own rendering of it.
    return API.bubble_error() if console.json_output else API


def _check(data: Any, error: Any) -> dict:
    """The data of an API call, or the exit for its failure."""
    if error:
        if console.json_output:
            console.output_json({"error": error})
        # Otherwise the client has printed it.
        raise typer.Exit(EXIT_ERROR)
    if data is None:
        _fail("The request failed before the API answered. Check your connection.")
    return data


async def _login_check() -> None:
    """Fail as an error (3), where `requires_login` alone would exit 1: failed."""

    @requires_login
    async def nothing() -> None:
        return None

    try:
        await nothing()
    except typer.Exit:
        raise typer.Exit(EXIT_ERROR) from None


def exit_code_for(run: dict) -> int:
    """The exit code for an ended run."""
    verdict = run.get("verdict")
    # A skip is not a failure, and test runners count it as a pass.
    if verdict in ("passed", "skipped"):
        return EXIT_PASSED
    if verdict == "failed":
        return EXIT_FAILED
    return EXIT_NO_VERDICT


def _is_live(run: dict) -> bool:
    return run.get("status") in LIVE_STATUSES


def _cancel_command(agent: str, run_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} eval cancel {run_id} --agent {agent}"


def _status_command(agent: str, run_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} eval status {run_id} --agent {agent} --wait"


def _conversation_command(agent: str, run_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} eval status {run_id} --agent {agent} -v"


def _logs_command(agent: str, session_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} agent logs {agent} --session-id {session_id}"


def _parse_params(items: list[str]) -> dict[str, int | float]:
    params: dict[str, int | float] = {}
    for item in items:
        key, sep, value = item.partition("=")
        if not sep or not key or not value:
            _fail(f"--param expects KEY=VALUE, not {item!r}.")
        if key not in RUN_PARAMS:
            _fail(f"--param {key!r} isn't a setting. The settings are: {', '.join(RUN_PARAMS)}.")
        try:
            number: int | float = int(value)
        except ValueError:
            try:
                number = float(value)
            except ValueError:
                _fail(f"--param {key} expects a number, not {value!r}.")
        params[key] = number
    return params


def _selected_scenarios(requested: list[str], names: list[str], scenario_path: str) -> list[str]:
    """The scenarios to run: those named with --scenario, or every one in the file."""
    if not names:
        _fail(f"{scenario_path!r} holds no named scenarios.")
    if not requested:
        return names
    unknown = [r for r in requested if r not in names]
    if unknown:
        _fail(
            f"{scenario_path!r} has no scenario named {', '.join(map(repr, unknown))}. "
            f"It holds: {', '.join(names)}."
        )
    return list(dict.fromkeys(requested))


# ---- Rendering ----


def _table(columns: list[str], rows: list[tuple], title: str | None = None) -> None:
    rows = [tuple("-" if cell is None or cell == "" else cell for cell in row) for row in rows]
    if not console.rich_output:
        console.print_records(columns, rows, title=title)
        return
    table = Table(show_header=True, header_style="bold", box=box.SIMPLE, title=title)
    for column in columns:
        table.add_column(column, overflow="fold")
    for row in rows:
        table.add_row(*(escape(str(cell)) for cell in row))
    _print(table)


def _fields(rows: list[tuple[str, str | None]]) -> None:
    """Key/value rows: bold keys and no header in rich mode, as `agent status` shows
    them; Field/Value records otherwise."""
    rows_set = [(k, str(v)) for k, v in rows if v]
    if not console.rich_output:
        console.print_records(["Field", "Value"], rows_set)
        return
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="bold")
    grid.add_column(overflow="fold")
    for key, value in rows_set:
        grid.add_row(key, escape(value))
    _print(grid)


def _fmt_seconds(seconds: float) -> str:
    """An elapsed time the way `pipecat eval` writes one: 57.0s, 1m 29s.

    Tenths under a minute, then minutes and seconds, deciding on the rounded
    value so nothing ever reads 60.0s.
    """
    tenths = round(seconds, 1)
    if tenths < 60:
        return f"{tenths:.1f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}m {secs:02d}s"


def _duration_ms(run: dict) -> int | None:
    """How long the run took by the server's clock, from leaving the queue to its end."""
    begun = _parse_time(run.get("startedAt")) or _parse_time(run.get("createdAt"))
    ended = _parse_time(run.get("finishedAt"))
    if not begun or not ended or ended < begun:
        return None
    return round((ended - begun).total_seconds() * 1000)


def _run_label(run: dict) -> str:
    """The scenario, with the source session for a finished-call judgment."""
    path = str(run.get("scenarioPath") or "")
    stem = Path(path).stem if path else ""
    name = run.get("scenarioName")
    label = f"{stem}/{name}" if stem and name else stem or str(name or "")
    if run.get("kind") == "session" and run.get("sessionId"):
        return f"{label} ({run['sessionId']})"
    return label


# The marks and colors `pipecat eval` gives an outcome.
_MARK = {"passed": ("✓", "green"), "failed": ("✗", "red"), "skipped": ("⊘", "yellow")}


def _mark(run: dict) -> str:
    mark, color = _MARK.get(run.get("verdict") or "", ("⊘", "yellow"))
    return f"[{color}]{mark}[/{color}]"


# ---- The scenarios, before they run ----


def _scenario_view(data: Any, name: str) -> dict:
    """A scenario with its file's defaults: a key the scenario sets replaces the file's."""
    if not isinstance(data, dict):
        return {}
    top = {k: v for k, v in data.items() if k not in ("name", "scenarios")}
    for entry in data.get("scenarios") or []:
        if isinstance(entry, dict) and entry.get("name") == name:
            return {**top, **{k: v for k, v in entry.items() if k != "name"}}
    return top


def _service(block: Any, default: str | None = None, *, second: str) -> str | None:
    """A service block as `pipecat eval` names it: service/voice, or factory:path."""
    if not isinstance(block, dict):
        return default
    if block.get("factory"):
        return f"factory:{block['factory']}"
    service = block.get("service") or default
    other = block.get(second)
    return f"{service}/{other}" if service and other else service


# `pipecat eval`'s palette for a scenario's config lines: the labels bold, the
# separators dim, and each keyword one hue per category; values uncolored.
_CFG_MODALITY = "yellow"
_CFG_SERVICE = "green"  # speech (TTS) and transcription (STT)
_CFG_EVAL = "magenta"  # the LLMs: the judge's eval and a simulation's persona
_CFG_LIMIT = "cyan"  # a simulation's run caps

# A config line's segment: keyword, value, and the style its keyword is painted with.
_Segment = tuple[str, str, str]


def _config_lines(view: dict) -> list[tuple[str, list[_Segment] | str]]:
    """The user, judge and goal lines `pipecat eval` prints for a scenario.

    The caller and the judge are hosted on Pipecat Cloud, so they're shown as
    such: a scenario's own simulator: and judge.eval: aren't what runs there.
    """
    simulated = "persona" in view and "turns" not in view
    user_block, judge_block = view.get("user"), view.get("judge")
    user: dict = user_block if isinstance(user_block, dict) else {}
    judge: dict = judge_block if isinstance(judge_block, dict) else {}

    user_mode = str(user.get("modality") or "text")
    user_segs: list[_Segment] = [("modality", user_mode, _CFG_MODALITY)]
    if user_mode == "audio":
        speech = _service(user.get("speech"), second="voice")
        if speech:
            user_segs.append(("speech", speech, _CFG_SERVICE))
    if simulated:
        user_segs.append(("persona", "hosted", _CFG_EVAL))
        for key in ("max_turns", "max_duration_s", "max_silence_s"):
            if view.get(key) is not None:
                user_segs.append((key, str(view[key]), _CFG_LIMIT))

    judge_mode = str(judge.get("modality") or "text")
    judge_segs: list[_Segment] = [("modality", judge_mode, _CFG_MODALITY)]
    if judge_mode == "audio":
        transcription = _service(judge.get("transcription"), "moonshine", second="model")
        judge_segs.append(("transcription", str(transcription), _CFG_SERVICE))
    judge_segs.append(("eval", "hosted", _CFG_EVAL))

    lines: list[tuple[str, list[_Segment] | str]] = [("user", user_segs), ("judge", judge_segs)]
    goal = view.get("goal")
    if simulated and isinstance(goal, str) and goal.strip():
        lines.append(("goal", " ".join(goal.split())))
    return lines


def _config_markup(label: str, body: list[_Segment] | str) -> str:
    """One config line, `label -> key: value | key: value`, painted as `pipecat eval` paints it."""
    if isinstance(body, str):
        text = escape(body)
    else:
        text = "[dim] | [/dim]".join(
            f"[{style}]{escape(key)}:[/{style}] {escape(value)}" for key, value, style in body
        )
    return f"[bold]{label:<5}[/bold][dim] -> [/dim]{text}"


def _print_scenario_header(scenarios: list[tuple[str, dict]]) -> None:
    """The scenarios about to run, grouped by kind, as `pipecat eval` lists them."""
    groups: dict[str, list[tuple[str, dict]]] = {"Scripted": [], "Simulated": []}
    for label, view in scenarios:
        kind = "Simulated" if "persona" in view and "turns" not in view else "Scripted"
        groups[kind].append((label, view))
    for kind, entries in groups.items():
        if not entries:
            continue
        _print(f"{kind} scenarios:")
        for label, view in entries:
            _print(f"  [bold cyan]{escape(label)}:[/bold cyan]")
            for key, body in _config_lines(view):
                _print(f"    {_config_markup(key, body)}")
        _print()


def _print_judge_header(label: str, view: dict, session_ids: list[str]) -> None:
    """The criteria and finished sessions to judge, without a simulated caller."""
    _print("Criteria:")
    _print(f"  [bold cyan]{escape(label)}:[/bold cyan]")
    for key, body in _config_lines(view):
        if key == "judge":
            _print(f"    {_config_markup(key, body)}")
    if isinstance(success := view.get("success"), str) and success.strip():
        _print(f"    {_config_markup('success', ' '.join(success.split()))}")
    _print()
    _print("Sessions:")
    for session_id in session_ids:
        _print(f"  {escape(session_id)}")
    _print()


def _print_settings(agent: str, runs: int) -> None:
    """How the runs will go, in `pipecat eval suite`'s Settings block."""
    _print("Settings:")
    for label, value in (("agent", agent), ("runs", str(runs))):
        _print(f"  [bold]{label:<11}[/bold] -> {escape(value)}")
    _print()


# ---- The runs, while and after they run ----


@dataclass
class _Row:
    """One run on the table: its scenario, and the run, or why it couldn't start."""

    label: str
    run: dict | None = None
    error: dict[str, Any] | None = None
    scenario_path: str | None = None
    scenario_name: str | None = None
    session_id: str | None = None
    submission_status: Literal["error", "unknown", "not_submitted"] = "not_submitted"
    first_running: float | None = None
    spinner: Spinner = field(default_factory=lambda: Spinner("dots", style="cyan"))
    printed: bool = False

    @property
    def ended(self) -> bool:
        return self.error is not None or (self.run is not None and not _is_live(self.run))

    @property
    def passed(self) -> bool:
        return self.run is not None and self.run.get("verdict") == "passed"

    @property
    def error_message(self) -> str:
        return str((self.error or {}).get("error") or "The API didn't answer.")

    def as_json(self) -> dict[str, Any]:
        """Keep server run objects intact; unacknowledged submissions have no run ID."""
        if self.run is not None:
            return self.run
        entry: dict[str, Any] = {
            "scenarioPath": self.scenario_path,
            "scenarioName": self.scenario_name,
            "submissionStatus": self.submission_status,
        }
        if self.session_id is not None:
            entry["sessionId"] = self.session_id
        if self.error is not None:
            entry["error"] = self.error
        return entry


def _row_cells(row: _Row, agent: str) -> tuple[Any, str, str, str]:
    """The mark, agent, scenario and time of a row, in rich markup or a renderable."""
    label = f"[cyan]{escape(row.label)}[/cyan]"
    if row.error is not None:
        return "[red]✗[/red]", escape(agent), label, f"[red]{escape(row.error_message)}[/red]"
    run = row.run or {}
    status = str(run.get("status"))
    if not _is_live(run):
        if run.get("verdict"):
            ms = _duration_ms(run)
            when = f"[dim]{ms}ms[/dim]" if ms is not None else ""
        else:
            when = f"[yellow]no verdict ({escape(status)})[/yellow]"
        return _mark(run), escape(agent), label, when
    if status == "running":
        # Padded to the width of `2m 04s`, as `pipecat eval` pads it, so nothing beside it moves.
        since = row.first_running if row.first_running is not None else _monotonic()
        return (
            row.spinner,
            escape(agent),
            label,
            f"[dim]{_fmt_seconds(_monotonic() - since):>6}[/dim]",
        )
    return "[dim]·[/dim]", escape(agent), label, f"[dim]{escape(status)}[/dim]"


def _summary_text(rows: list[_Row], elapsed: float) -> str:
    total = len(rows)
    passed = sum(r.passed for r in rows)
    done = sum(r.ended for r in rows)
    return f"  {passed}/{total} passed  ·  {done}/{total} done  ·  {_fmt_seconds(elapsed)}"


class _RunsView:
    """The runs as `pipecat eval suite` shows them, redrawn on every refresh.

    Each running row's clock and spinner are read at render time, so they move
    between polls, which back off to seconds apart.
    """

    def __init__(self, rows: list[_Row], agent: str, started: float):
        self.rows = rows
        self.agent = agent
        self.started = started
        self.final: float | None = None

    def __rich__(self) -> Group:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(no_wrap=True)
        grid.add_column(no_wrap=True)
        grid.add_column(overflow="fold")
        for row in self.rows:
            grid.add_row(*_row_cells(row, self.agent))
        elapsed = (self.final if self.final is not None else _monotonic()) - self.started
        return Group(grid, Text(""), Text(_summary_text(self.rows, elapsed), style="bold"))


def _print_row_line(row: _Row, agent: str) -> None:
    """A finished row as one line, for output that isn't a terminal."""
    mark, agent_cell, label, when = _row_cells(row, agent)
    ms = _duration_ms(row.run) if row.run is not None and row.run.get("verdict") else None
    if ms is not None:
        when = f"[dim]({ms}ms)[/dim]"
    _print(f"  {mark} {agent_cell} {label} {when}".rstrip())
    row.printed = True


def _score(metric: dict) -> str:
    score = metric.get("score")
    return "unscored" if score is None else f"{float(score):.2f}"


def _bot_replies(result: dict) -> list[str]:
    return [
        str(m.get("content") or "")
        for m in result.get("conversation") or []
        if m.get("role") == "assistant"
    ]


def _shorten(text: str, width: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _metric_turns(name: str, details: dict) -> tuple[int, list[dict]]:
    """How many turns a judged metric scored, and the checks on it that failed."""
    checks = [
        {**check, "turn": turn.get("turn")}
        for turn in details.get("turns") or []
        for check in turn.get("checks") or []
        if check.get("name") == name
    ]
    return len(checks), [c for c in checks if not c.get("passed")]


def _print_conversation(conversation: list[dict], indent: str = "      ") -> None:
    """The conversation, the way `pipecat eval -v` prints a simulation's."""
    if not conversation:
        _print(f"{indent}[dim]No conversation to show.[/dim]")
        return
    for message in conversation:
        role = message.get("role")
        if role == "tool_call":
            args = ", ".join(f"{k}={v!r}" for k, v in (message.get("arguments") or {}).items())
            cancelled = " [dim](cancelled)[/dim]" if message.get("cancelled") else ""
            _print(
                f"{indent}[dim]→ {escape(str(message.get('name')))}({escape(args)})[/dim]{cancelled}"
            )
        else:
            speaker, color = ("bot", "green") if role == "assistant" else (str(role), "cyan")
            content = escape(str(message.get("content") or ""))
            _print(f"{indent}[{color}]{escape(speaker)}:[/{color}] {content}")


def _print_details(row: _Row, agent: str, verbose: bool) -> None:
    """A run and what decided it, as `pipecat eval` lists a failure: its line, then a
    bullet for each thing that went wrong, or that the judge weighed."""
    run = row.run or {}
    result = run.get("result") or {}
    details = result.get("details") or {}
    if row.error is not None:
        _print(
            f"  [red]✗[/red] {escape(agent)} [cyan]{escape(row.label)}[/cyan] "
            f"[dim]— {escape(row.error_message)}[/dim]"
        )
        return
    _print(f"  {_mark(run)} {escape(agent)} [cyan]{escape(row.label)}[/cyan]")
    bullet = "[green]•[/green]" if row.passed else "[red]•[/red]"
    if verbose:
        _print_conversation(result.get("conversation") or [])
        _print()
    reason = result.get("reason")
    if reason and not row.passed:
        _print(f"      {bullet} {escape(str(reason))}")
    goal = details.get("goal") or {}
    if goal.get("criterion"):
        answer = str(goal.get("verdict") or ("yes" if goal.get("passed") else "no"))
        color = "green" if goal.get("passed") else "red"
        _print(
            f"      {bullet} judge: [{color}]{escape(answer)}[/{color}] [dim]—[/dim] "
            f"{escape(str(goal['criterion']))}"
        )
    for f in details.get("failures") or []:
        turn = f"turn {escape(str(f['turn']))} " if f.get("turn") is not None else ""
        name = f"{escape(str(f['name']))} " if f.get("name") else ""
        _print(
            f"      [red]•[/red] {turn}{name}"
            f"[red]{escape(str(f.get('kind') or ''))}[/red] — {escape(str(f.get('reason') or ''))}"
        )
    replies = _bot_replies(result)
    for m in result.get("metrics") or []:
        color = "green" if m.get("passed") else "red"
        scored, failed = _metric_turns(str(m.get("name")), details)
        # Turn counts when the metric was judged turn by turn; otherwise its reason,
        # such as a measure's `longest reply 39 words, at most 60`.
        detail = f"{scored - len(failed)}/{scored} turns" if scored else m.get("reason")
        tail = f" [dim]{escape(str(detail))}[/dim]" if detail else ""
        _print(
            f"      [{color}]•[/{color}] [cyan]{escape(str(m.get('name')))}:[/cyan] "
            f"[{color}]{_score(m)}[/{color}]{tail}"
        )
        for check in failed:
            turn = check.get("turn")
            said = replies[turn - 1] if isinstance(turn, int) and 0 < turn <= len(replies) else ""
            _print(
                f"          [red]✗[/red] [dim]turn {turn}:[/dim] {escape(_shorten(said))} "
                f"[dim]— {escape(str(check.get('reason') or ''))}[/dim]"
            )
    if result.get("truncated"):
        _print(
            "      [dim]The result was too large to keep whole, so the conversation and "
            "details are left out.[/dim]"
        )
    run_id = str(run.get("id"))
    _print(f"      [dim]run:  {_conversation_command(agent, run_id)}[/dim]")
    if run.get("sessionId"):
        _print(f"      [dim]logs: {_logs_command(agent, str(run['sessionId']))}[/dim]")


def _report(rows: list[_Row], agent: str, verbose: bool, *, every: bool = False) -> None:
    """After the table, as `pipecat eval` ends a run: each run that failed or ended
    without a verdict under a `Failures` heading (every run with -v or `every`),
    then where to look next."""
    if every or verbose:
        for row in rows:
            _print()
            _print_details(row, agent, verbose)
    else:
        failed = [r for r in rows if r.error is not None or (r.run and exit_code_for(r.run))]
        if failed:
            _print()
            _print(f"  [bold red]Failures ({len(failed)} of {len(rows)}):[/bold red]")
            for row in failed:
                _print()
                _print_details(row, agent, verbose)
    _print()
    if len(rows) == 1 and rows[0].run is not None:
        if not (every or verbose):
            _print(f"  run: {_conversation_command(agent, str(rows[0].run.get('id')))}")
    else:
        _print(f"  runs: {PIPECAT_CLI_NAME} eval list {agent}")


def _exit_code(rows: list[_Row]) -> int:
    """For several runs, the first that applies: an error, a failure, a run without a verdict."""
    if any(r.run is None or r.error is not None for r in rows):
        return EXIT_ERROR
    codes = {exit_code_for(r.run) for r in rows if r.run is not None}
    for code in (EXIT_FAILED, EXIT_NO_VERDICT):
        if code in codes:
            return code
    return EXIT_PASSED


def _render_run(run: dict, agent: str, verbose: bool = False) -> None:
    """One run, as `status` shows it: its fields while it's live, its outcome once it isn't."""
    run_id = str(run.get("id"))
    if _is_live(run):
        _print(f"[cyan]{escape(str(run.get('status'))).upper()}[/cyan]")
        scenario = run.get("scenarioPath") or ""
        if run.get("scenarioName"):
            scenario = f"{scenario} ({run['scenarioName']})"
        _fields(
            [
                ("Run", run_id),
                ("Agent", agent),
                ("Kind", run.get("kind")),
                ("Session", run.get("sessionId") if run.get("kind") == "session" else None),
                ("Status", run.get("status")),
                ("Scenario", scenario),
                ("Created", format_timestamp(run["createdAt"]) if run.get("createdAt") else None),
            ]
        )
        _print(
            f"[dim]The run hasn't ended. Wait for it with [bold]{_status_command(agent, run_id)}"
            f"[/bold].[/dim]"
        )
        return
    row = _Row(_run_label(run), run=run)
    _print_row_line(row, agent)
    _report([row], agent, verbose, every=True)


# ---- Waiting ----


class _Interrupted(Exception):
    """Ctrl-C while waiting, with the runs as last seen."""

    def __init__(self, runs: list[dict]):
        super().__init__("interrupted")
        self.runs = runs


async def _wait_for_end(org: str, agent: str, runs: list[dict], on_update) -> list[dict]:
    """Poll the live runs until every one has ended, and return them.

    One loop polls each live run in turn, with one back-off. A poll that fails
    for a reason that may pass (a dropped connection, a 5xx, a 429) is tried
    again; a permanent one (a 4xx) ends the wait. The runs are polled one after
    another, not at once, since the API client keeps a failed call's status and
    Retry-After on itself.
    """
    runs = list(runs)
    delay = POLL_START_SECONDS
    pause = delay
    failures = [0] * len(runs)
    limits = [_wait_limit(r) for r in runs]
    started = _monotonic()
    while any(_is_live(r) for r in runs):
        for i, r in enumerate(runs):
            if _is_live(r):
                _wait_over(limits[i], started, r, agent)
        on_update(runs)
        try:
            await asyncio.sleep(pause)
            delay = min(delay * POLL_FACTOR, POLL_MAX_SECONDS)
            pause = delay
            for i, r in enumerate(runs):
                if not _is_live(r):
                    continue
                data, error = await API.bubble_error().eval_run_get(org, agent, str(r["id"]))
                if data:
                    failures[i] = 0
                    runs[i] = data
                    continue
                status = API.error_status
                permanent = status is not None and 400 <= status < 500 and status != 429
                failures[i] += 1
                if permanent or failures[i] >= POLL_MAX_CONSECUTIVE_ERRORS:
                    if not console.json_output:
                        API.print_error()
                    _check(
                        None, error or {"error": "Lost contact with the API.", "code": "CLI-POLL"}
                    )
                retry_after = _parse_retry_after(API.error_retry_after)
                if retry_after is not None:
                    pause = max(pause, min(retry_after, POLL_RETRY_AFTER_CAP_SECONDS))
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl-C reaches a coroutine run by the synchronizer as a
            # cancellation. Here the latest state each run was seen in is known.
            raise _Interrupted(runs) from None
    on_update(runs)
    return runs


def _monotonic() -> float:
    return time.monotonic()


def _parse_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _wait_limit(run: dict) -> float | None:
    """How long to wait for a run, timed on this machine's monotonic clock.

    The run's own length (its deadline less its creation, both the server's
    times, so this machine's clock doesn't matter), plus what the API allows
    after the deadline. None when the run doesn't say.
    """
    created = _parse_time(run.get("createdAt"))
    deadline = _parse_time(run.get("deadlineAt"))
    if created is None or deadline is None:
        return None
    try:
        length = (deadline - created).total_seconds()
    except TypeError:
        return None
    if length <= 0:
        return None
    return length + API_RESULT_GRACE_SECONDS + API_SWEEP_SECONDS


def _wait_over(limit: float | None, started: float, run: dict, agent: str) -> None:
    """Stop waiting for a run that is still live well after it should have ended."""
    if limit is None or _monotonic() - started <= limit:
        return
    run_id = str(run.get("id"))
    _fail(
        f"Run {run_id} hasn't ended, though it is well past its deadline, so this stopped "
        f"waiting. Check on it: {_status_command(agent, run_id)}",
        code="CLI-WAIT",
    )


def _interrupted_starting(agent: str, rows: list[_Row], as_list: bool) -> NoReturn:
    """Ctrl-C while a run is being created: the request may have got through."""
    if as_list or rows[0].session_id is not None:
        _print("\n[yellow]Stopped submitting runs. Accepted runs were not cancelled.[/yellow]")
        for row in rows:
            if row.run is not None:
                run_id = str(row.run["id"])
                _print(f"Accepted {escape(row.label)}: {escape(run_id)}")
                _print(f"Check on it: [bold]{_status_command(agent, run_id)}[/bold]")
                if _is_live(row.run):
                    _print(f"Cancel it:   [bold]{_cancel_command(agent, run_id)}[/bold]")
            elif row.submission_status == "unknown":
                message = (
                    f"Submission of {row.label} was interrupted. The request may have reached "
                    f"the API; check `{PIPECAT_CLI_NAME} eval list {agent}` before starting it again."
                )
                row.error = {"error": message, "code": "CLI-INTERRUPTED"}
                _print(f"[yellow]{escape(message)}[/yellow]")
            elif row.error is not None:
                _print(f"[red]{escape(row.label)}: {escape(row.error_message)}[/red]")
            else:
                _print(f"Not submitted: {escape(row.label)}")
        if console.json_output:
            console.output_json([r.as_json() for r in rows] if as_list else rows[0].as_json())
        raise typer.Exit(EXIT_INTERRUPTED)
    message = (
        "Stopped while starting the run. The request may have reached the API, so a run "
        f"may exist. See: {PIPECAT_CLI_NAME} eval list {agent}"
    )
    if console.json_output:
        console.output_json({"error": {"error": message, "code": "CLI-INTERRUPTED"}})
    else:
        _print(f"\n[yellow]{escape(message)}[/yellow]")
    raise typer.Exit(EXIT_INTERRUPTED)


def _interrupted(rows: list[_Row], agent: str, as_list: bool) -> NoReturn:
    live = [r.run for r in rows if r.run is not None and _is_live(r.run)]
    if len(live) == 1:
        _print(
            "\n[yellow]Stopped waiting. The run is still going; Ctrl-C doesn't cancel it.[/yellow]"
        )
        run_id = str(live[0].get("id"))
        _print(f"Check on it: [bold]{_status_command(agent, run_id)}[/bold]")
        _print(f"Cancel it:   [bold]{_cancel_command(agent, run_id)}[/bold]")
    elif live:
        _print(
            f"\n[yellow]Stopped waiting. {len(live)} runs are still going; Ctrl-C doesn't "
            "cancel them.[/yellow]"
        )
        _print(f"Check on them: [bold]{PIPECAT_CLI_NAME} eval list {agent}[/bold]")
        for r in live:
            _print(f"Cancel one:    [bold]{_cancel_command(agent, str(r.get('id')))}[/bold]")
    else:
        _print("\n[yellow]Stopped waiting. No runs were cancelled.[/yellow]")
    if console.json_output:
        console.output_json([r.as_json() for r in rows] if as_list else rows[0].as_json())
    raise typer.Exit(EXIT_INTERRUPTED)


async def _follow(org: str, agent: str, rows: list[_Row], verbose: bool, as_list: bool) -> NoReturn:
    """Wait for the rows' runs to end, showing them as they go, then report and exit."""
    tracked = [r for r in rows if r.run is not None]
    started = _monotonic()
    view = _RunsView(rows, agent, started)

    def on_update(runs: list[dict]) -> None:
        now = _monotonic()
        for row, run in zip(tracked, runs):
            row.run = run
            if row.first_running is None and run.get("status") == "running":
                row.first_running = now
        if console.json_output or console.rich_output:
            return
        for row in rows:
            if row.ended and not row.printed:
                _print_row_line(row, agent)

    display = (
        Live(view, console=console, refresh_per_second=8)
        if console.rich_output and not console.json_output
        else nullcontext()
    )
    try:
        with display:
            on_update([r.run for r in tracked if r.run is not None])
            if any(_is_live(r.run) for r in tracked if r.run is not None):
                await _wait_for_end(
                    org, agent, [r.run for r in tracked if r.run is not None], on_update
                )
            view.final = _monotonic()
    except _Interrupted as stopped:
        on_update(stopped.runs)
        _interrupted(rows, agent, as_list)

    if console.json_output:
        console.output_json([r.as_json() for r in rows] if as_list else rows[0].as_json())
    else:
        if not console.rich_output:
            _print()
            _print(_summary_text(rows, (view.final or _monotonic()) - started))
        _report(rows, agent, verbose)
    raise typer.Exit(_exit_code(rows))


async def _submit_runs(
    org: str,
    agent: str,
    rows: list[_Row],
    body_for: Callable[[_Row], dict[str, Any]],
    *,
    wait: bool,
    verbose: bool,
) -> None:
    """Submit each requested run once, then follow it or show its acknowledgment."""
    as_list = len(rows) > 1
    try:
        if len(rows) == 1 and rows[0].session_id is None:
            rows[0].submission_status = "unknown"
            with console.status(
                f"[dim]Starting a run on [bold]'{agent}'[/bold][/dim]", spinner="dots"
            ):
                created, error = await _api().eval_run_create(org, agent, body_for(rows[0]))
            rows[0].run = _check(created, error)
        else:
            with console.status(
                f"[dim]Submitting {len(rows)} eval {'runs' if as_list else 'run'} on "
                f"[bold]'{agent}'[/bold][/dim]",
                spinner="dots",
            ):
                for row in rows:
                    row.submission_status = "unknown"
                    created, error = await API.bubble_error().eval_run_create(
                        org, agent, body_for(row)
                    )
                    if created:
                        row.run = created
                    else:
                        row.submission_status = "error"
                        row.error = (
                            dict(error)
                            if isinstance(error, dict)
                            else {"error": "The API didn't answer.", "code": "CLI-SUBMISSION"}
                        )
    except (KeyboardInterrupt, asyncio.CancelledError):
        _interrupted_starting(agent, rows, as_list)

    if wait:
        await _follow(org, agent, rows, verbose, as_list)

    if console.json_output:
        if as_list:
            console.output_json([r.as_json() for r in rows])
        else:
            console.output_json(rows[0].as_json())
    elif not as_list:
        if rows[0].run is not None:
            _render_run(rows[0].run, agent, verbose)
        else:
            _print_row_line(rows[0], agent)
    else:
        for row in rows:
            mark, agent_cell, label, _ = _row_cells(row, agent)
            run_id = row.run.get("id") if row.run else ""
            detail = (
                f"[dim]{escape(str(run_id))}[/dim]"
                if row.run
                else f"[red]{escape(row.error_message)}[/red]"
            )
            _print(f"  {mark} {agent_cell} {label} {detail}")
        _print(f"[dim]Follow them with [bold]{PIPECAT_CLI_NAME} eval list {agent}[/bold].[/dim]")
    if any(r.error is not None for r in rows):
        raise typer.Exit(EXIT_ERROR)


# ---- Commands ----

_ORG_OPTION = typer.Option(
    None, "--organization", "-o", help="Organization to use (defaults to active org)"
)
_JSON_OPTION = typer.Option(
    False, "--json", help="Print the run as JSON on stdout (alias for the global --output json)"
)
_VERBOSE_OPTION = typer.Option(
    False,
    "--verbose",
    "-v",
    help="Also print each run's conversation and what decided it, once the runs have ended",
)


@eval_cli.command(
    name="run",
    cls=_EvalCommand,
    help="Run a file's scenarios against an agent: every one, or those named with "
    "--scenario. Where the organization has suite runs, they run as one suite run, "
    "with one verdict; otherwise side by side.",
)
@synchronizer.create_blocking
async def run(
    agent: str = typer.Argument(..., help="Name of the agent to run against", show_default=False),
    scenario_file: Path = typer.Argument(
        ..., help="Scenario YAML file", show_default=False, dir_okay=False
    ),
    scenario: list[str] = typer.Option(
        [],
        "--scenario",
        help="A scenario in the file to run. Repeat for several. Every one by default.",
    ),
    param: list[str] = typer.Option(
        [],
        "--param",
        help="A synthetic run's setting, KEY=VALUE. Repeat for several. "
        f"Settings: {', '.join(RUN_PARAMS)}. A suite run takes none, so with --param the "
        "scenarios run side by side.",
    ),
    repeat: int = typer.Option(
        None,
        "--repeat",
        min=1,
        help="Suite runs only: run each scenario this many times. Above 1, the suite run "
        "is a measurement: pass rates, no verdict.",
    ),
    concurrency: int = typer.Option(
        None,
        "--concurrency",
        min=1,
        help="Suite runs only: run at most this many at once. The API sets the bounds.",
    ),
    deadline: int = typer.Option(
        None,
        "--deadline",
        min=1,
        help="Seconds the runs may take, from creation: the whole suite run, or each run "
        "side by side. The API sets the bounds.",
    ),
    root: Path = typer.Option(
        None,
        "--root",
        help="Directory the scenario's included files are read from, and may not leave. "
        "Defaults to the scenario file's directory.",
    ),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Wait for the runs to end and exit with their verdicts, as `pipecat eval` does. "
        "With --no-wait, start them and return.",
    ),
    verbose: bool = _VERBOSE_OPTION,
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    params = _parse_params(param)

    try:
        bundle = build_bundle(scenario_file, root)
    except BundleError as e:
        _fail(str(e), code="CLI-BUNDLE")
    names = _selected_scenarios(scenario, bundle.scenario_names, bundle.scenario_path)

    data = bundle.data if isinstance(bundle.data, dict) else {}
    file_name = data.get("name") if isinstance(data.get("name"), str) else None
    file_label = file_name or Path(bundle.scenario_path).stem
    labels = {name: f"{file_label}/{name}" for name in names}

    # Where the organization has suite runs, the file's scenarios run as one,
    # which takes no --param settings.
    from pipecatcloud.cli.commands import eval_suites

    as_suite = not params and await eval_suites.suites_available(org)
    if not as_suite and (repeat is not None or concurrency is not None):
        _fail(
            "--repeat and --concurrency are for suite runs, which take no --param."
            if params
            else "--repeat and --concurrency are for suite runs, which this organization "
            "doesn't have yet."
        )

    if wait and not console.json_output:
        _print_scenario_header([(labels[n], _scenario_view(data, n)) for n in names])
        if not as_suite:
            _print_settings(agent, len(names))

    if as_suite:
        suite_body: dict[str, Any] = {"files": bundle.files, "scenarioPath": bundle.scenario_path}
        if scenario:
            suite_body["scenarioNames"] = names
        for key, value in (
            ("repeat", repeat),
            ("concurrency", concurrency),
            ("deadlineSeconds", deadline),
        ):
            if value is not None:
                suite_body[key] = value
        await eval_suites.run_suite(org, agent, suite_body, wait=wait, verbose=verbose)

    def body_for(row: _Row) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": "synthetic",
            "files": bundle.files,
            "scenarioPath": bundle.scenario_path,
            "scenarioName": row.scenario_name,
        }
        if params:
            body["params"] = params
        if deadline is not None:
            body["deadlineSeconds"] = deadline
        return body

    # Creating a run is not idempotent, so a failed request is never repeated here.
    rows = [
        _Row(labels[name], scenario_path=bundle.scenario_path, scenario_name=name) for name in names
    ]
    await _submit_runs(org, agent, rows, body_for, wait=wait, verbose=verbose)


@eval_cli.command(
    name="judge",
    cls=_EvalCommand,
    help="Judge finished sessions against one criteria policy. No agent session is started.",
)
@synchronizer.create_blocking
async def judge(
    agent: str = typer.Argument(..., help="Name of the sessions' agent", show_default=False),
    session_ids: list[UUID] = typer.Argument(
        ..., help="Finished session IDs to judge", show_default=False
    ),
    criteria: Path = typer.Option(
        ...,
        "--criteria",
        help="YAML file containing exactly one named criteria entry under scenarios:",
        show_default=False,
        dir_okay=False,
    ),
    deadline: int = typer.Option(
        None,
        "--deadline",
        min=1,
        help="Seconds each judgment may take, from creation. The API sets the bounds.",
    ),
    root: Path = typer.Option(
        None,
        "--root",
        help="Directory the criteria's included files are read from, and may not leave. "
        "Defaults to the criteria file's directory.",
    ),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Wait for the judgments and exit with their verdicts. With --no-wait, submit and return.",
    ),
    verbose: bool = _VERBOSE_OPTION,
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)
    sessions = [str(session_id) for session_id in session_ids]
    seen: set[str] = set()
    for session_id in sessions:
        if session_id in seen:
            _fail(f"Session ID {session_id!r} was provided more than once.")
        seen.add(session_id)

    try:
        bundle = build_bundle(criteria, root)
    except BundleError as e:
        _fail(str(e), code="CLI-BUNDLE")
    data = bundle.data if isinstance(bundle.data, dict) else {}
    entries = data.get("scenarios")
    names = bundle.scenario_names
    if (
        not isinstance(entries, list)
        or len(entries) != 1
        or len(names) != 1
        or not names[0].strip()
    ):
        _fail("--criteria requires exactly one named entry under scenarios:.")
    name = names[0]
    file_name = data.get("name") if isinstance(data.get("name"), str) else None
    label = f"{file_name or Path(bundle.scenario_path).stem}/{name}"
    if wait and not console.json_output:
        _print_judge_header(label, _scenario_view(data, name), sessions)
        _print_settings(agent, len(sessions))
    rows = [
        _Row(
            f"{label} ({session_id})",
            scenario_path=bundle.scenario_path,
            scenario_name=name,
            session_id=session_id,
        )
        for session_id in sessions
    ]

    def body_for(row: _Row) -> dict[str, Any]:
        body: dict[str, Any] = {
            "kind": "session",
            "sessionId": row.session_id,
            "files": bundle.files,
            "scenarioPath": bundle.scenario_path,
            "scenarioName": name,
        }
        if deadline is not None:
            body["deadlineSeconds"] = deadline
        return body

    await _submit_runs(org, agent, rows, body_for, wait=wait, verbose=verbose)


@eval_cli.command(
    name="status",
    cls=_EvalCommand,
    help="Show an eval run. With --wait, wait for it to end and exit with its verdict.",
)
@synchronizer.create_blocking
async def status(
    run_id: str = typer.Argument(..., help="The run's id", show_default=False),
    agent: str = typer.Option(..., "--agent", "-a", help="Name of the agent the run is on"),
    wait: bool = typer.Option(
        False, "--wait", help="Wait for the run to end and exit with its verdict"
    ),
    verbose: bool = _VERBOSE_OPTION,
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    data, error = await _api().eval_run_get(org, agent, run_id)
    run_data = _check(data, error)

    if wait:
        await _follow(org, agent, [_Row(_run_label(run_data), run=run_data)], verbose, False)

    if console.json_output:
        console.output_json(run_data)
        return
    _render_run(run_data, agent, verbose)


@eval_cli.command(name="list", cls=_EvalCommand, help="List an agent's eval runs, newest first.")
@synchronizer.create_blocking
async def list_runs(
    agent: str = typer.Argument(..., help="Name of the agent", show_default=False),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=200, help="How many runs to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="How many of the newest runs to skip"),
    suite: str = typer.Option(
        None, "--suite", help="Only the runs of this suite run's members", metavar="SUITE_RUN_ID"
    ),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    paging: dict[str, Any] = {"limit": limit}
    if offset:
        paging["offset"] = offset
    if suite:
        paging["suite_run_id"] = suite
    data, error = await _api().eval_runs_list(org, agent, **paging)
    result = _check(data, error)

    if console.json_output:
        console.output_json(result)
        return

    runs = result.get("runs") or []
    if not runs:
        where = f" in suite run [bold]{escape(suite)}[/bold]" if suite else ""
        _print(f"[dim]No eval runs on [bold]'{agent}'[/bold]{where}.[/dim]")
        return
    _table(
        ["ID", "Status", "Verdict", "Kind", "Scenario", "Created"],
        [
            (
                r.get("id"),
                r.get("status"),
                r.get("verdict"),
                r.get("kind"),
                r.get("scenarioName") or r.get("scenarioPath"),
                format_timestamp(r["createdAt"]) if r.get("createdAt") else None,
            )
            for r in runs
        ],
    )
    total = result.get("total_count")
    if isinstance(total, int) and total > offset + len(runs):
        _print(
            f"[dim]Showing {offset + 1} to {offset + len(runs)} of {total}. "
            f"For the next page use --offset {offset + len(runs)}.[/dim]"
        )


@eval_cli.command(
    name="cancel",
    cls=_EvalCommand,
    help="Ask for an eval run to be cancelled. A running run ends as soon as it can.",
)
@synchronizer.create_blocking
async def cancel(
    run_id: str = typer.Argument(..., help="The run's id", show_default=False),
    agent: str = typer.Option(..., "--agent", "-a", help="Name of the agent the run is on"),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    data, error = await _api().eval_run_cancel(org, agent, run_id)
    run_data = _check(data, error)

    if console.json_output:
        console.output_json(run_data)
        return
    if run_data.get("status") == "cancelled":
        _print(f"Run [bold]{run_id}[/bold] is cancelled.")
    else:
        _print(
            f"Cancellation requested for run [bold]{run_id}[/bold]. It ends as soon as it can. "
            f"Check on it: [bold]{_status_command(agent, run_id)}[/bold]"
        )
