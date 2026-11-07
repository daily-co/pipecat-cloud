#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""`pipecat cloud eval`: run a scenario against an agent and read the verdict.

Exit codes of `run --wait` and `status --wait`:

    0  the verdict is `passed` or `skipped`
    1  the verdict is `failed`
    2  the run ended without a verdict (cancelled, expired, refused, ...)
    3  an API error, or a usage error (including a bundle that can't be sent)

Without `--wait`, 0 means the request was accepted. Ctrl-C while waiting exits
130 and leaves the run going.
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NoReturn

import click
import typer
from rich import box
from rich.markup import escape
from rich.table import Table
from typer.core import TyperCommand, TyperGroup

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
# The API ends every run at its deadline, so a wait that outlasts the deadline by
# this much has lost the run and stops rather than waiting for ever.
WAIT_PAST_DEADLINE_SECONDS = 120

# The synthetic run settings `--param` takes, as the API names them.
RUN_PARAMS = ("connectTimeoutSeconds", "botReadyTimeoutSeconds", "defaultTimeoutMs")


class _EvalCommand(TyperCommand):
    """A command whose usage errors, and unexpected errors, exit 3, as its API errors do.

    Click exits 2 on a usage error, and here 2 means a run ended without a
    verdict. An uncaught exception exits 1, which here means the verdict was
    `failed`. A script that tests the exit code must be able to tell them apart.
    The usage error is still shown by Typer, in its usual format.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as e:
            e.exit_code = EXIT_ERROR
            raise

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.UsageError as e:
            e.exit_code = EXIT_ERROR
            raise
        except (click.ClickException, click.exceptions.Exit, click.Abort):
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

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except click.UsageError as e:
            e.exit_code = EXIT_ERROR
            raise

    def invoke(self, ctx: click.Context) -> Any:
        try:
            return super().invoke(ctx)
        except click.UsageError as e:
            e.exit_code = EXIT_ERROR
            raise


eval_cli = typer.Typer(
    name="eval",
    cls=_EvalGroup,
    help="Run scenarios against an agent and read the results",
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


def _scenario_name(requested: str | None, names: list[str], scenario_path: str) -> str:
    if requested is not None:
        if requested not in names:
            held = ", ".join(names) if names else "none"
            _fail(f"{scenario_path!r} has no scenario named {requested!r}. It holds: {held}.")
        return requested
    if len(names) == 1:
        return names[0]
    if not names:
        _fail(f"{scenario_path!r} holds no named scenarios.")
    _fail(
        f"{scenario_path!r} holds several scenarios. Choose one with --scenario: {', '.join(names)}."
    )


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
    console.print(table)


def _verdict_line(run: dict) -> str:
    status = run.get("status")
    verdict = run.get("verdict")
    if _is_live(run):
        return f"[cyan]{escape(str(status)).upper()}[/cyan]"
    if verdict == "passed":
        return "[bold green]PASSED[/bold green]"
    if verdict == "skipped":
        return "[bold yellow]SKIPPED[/bold yellow]"
    if verdict == "failed":
        return "[bold red]FAILED[/bold red]"
    return f"[bold yellow]NO VERDICT[/bold yellow] [dim]({escape(str(status))})[/dim]"


def _render_run(run: dict, agent: str) -> None:
    """The run for a human: verdict, reason, failures with their turns, metrics."""
    run_id = str(run.get("id"))
    console.print(_verdict_line(run))

    scenario = run.get("scenarioPath") or ""
    if run.get("scenarioName"):
        scenario = f"{scenario} ({run['scenarioName']})"
    rows: list[tuple[str, str | None]] = [
        ("Run", run_id),
        ("Agent", agent),
        ("Kind", run.get("kind")),
        ("Status", run.get("status")),
        ("Scenario", scenario),
        ("Session", run.get("sessionId")),
        ("Created", format_timestamp(run["createdAt"]) if run.get("createdAt") else None),
        ("Finished", format_timestamp(run["finishedAt"]) if run.get("finishedAt") else None),
    ]
    _table(["Field", "Value"], [(k, v) for k, v in rows if v])

    if _is_live(run):
        console.print(
            f"[dim]The run hasn't ended. Wait for it with [bold]{_status_command(agent, run_id)}"
            f"[/bold].[/dim]"
        )
        return

    result = run.get("result") or {}
    reason = result.get("reason")
    if reason:
        console.print(f"[bold]Reason:[/bold] {escape(str(reason))}")

    details = result.get("details") or {}
    failures = details.get("failures") or []
    if failures:
        _table(
            ["Turn", "Kind", "Check", "Reason"],
            [(f.get("turn"), f.get("kind"), f.get("name"), f.get("reason")) for f in failures],
            title="Failures",
        )

    metrics = result.get("metrics") or []
    if metrics:
        _table(
            ["Metric", "Score", "Result", "Reason"],
            [
                (
                    m.get("name"),
                    m.get("score"),
                    "passed" if m.get("passed") else "failed",
                    m.get("reason"),
                )
                for m in metrics
            ],
            title="Metrics",
        )

    if result.get("truncated"):
        console.print(
            "[dim]The result was too large to keep whole, so the conversation and details "
            "are left out.[/dim]"
        )
    if run.get("status") == "completed" and not result:
        console.print("[dim]The run has no result to show.[/dim]")


# ---- Waiting ----


async def _wait_for_end(org: str, agent: str, run: dict, status_view) -> dict:
    """Poll a live run until it ends, and return it.

    The run is polled with a back-off. A poll that fails for a reason that may
    pass (a dropped connection, a 5xx, a 429) is tried again; a permanent one
    (a 4xx) ends the wait.
    """
    delay = POLL_START_SECONDS
    pause = delay
    failures = 0
    while _is_live(run):
        _check_deadline(run, agent)
        status_view.update(f"Run is {run.get('status')}")
        try:
            await asyncio.sleep(pause)
            data, error = await API.bubble_error().eval_run_get(org, agent, str(run["id"]))
        except (KeyboardInterrupt, asyncio.CancelledError):
            # Ctrl-C reaches a coroutine run by the synchronizer as a
            # cancellation. Here the latest state the run was seen in is known.
            status_view.stop()
            _interrupted(run, agent)
        delay = min(delay * POLL_FACTOR, POLL_MAX_SECONDS)
        pause = delay
        if data:
            failures = 0
            run = data
            continue
        status = API.error_status
        permanent = status is not None and 400 <= status < 500 and status != 429
        failures += 1
        if permanent or failures >= POLL_MAX_CONSECUTIVE_ERRORS:
            if not console.json_output:
                API.print_error()
            _check(None, error or {"error": "Lost contact with the API.", "code": "CLI-POLL"})
        retry_after = _parse_retry_after(API.error_retry_after)
        if retry_after is not None:
            pause = max(pause, min(retry_after, POLL_RETRY_AFTER_CAP_SECONDS))
    return run


def _now() -> datetime:
    return datetime.now(UTC)


def _check_deadline(run: dict, agent: str) -> None:
    """Stop waiting for a run that is still live well after its deadline."""
    raw = run.get("deadlineAt")
    if not isinstance(raw, str):
        return
    try:
        deadline = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    if _now() > deadline + timedelta(seconds=WAIT_PAST_DEADLINE_SECONDS):
        run_id = str(run.get("id"))
        _fail(
            f"Run {run_id} hasn't ended, though its deadline passed over "
            f"{WAIT_PAST_DEADLINE_SECONDS} seconds ago, so this stopped waiting. "
            f"Check on it: {_status_command(agent, run_id)}",
            code="CLI-WAIT",
        )


def _interrupted_starting(agent: str) -> NoReturn:
    """Ctrl-C while the run is being created: the request may have got through."""
    message = (
        "Stopped while starting the run. The request may have reached the API, so a run "
        f"may exist. See: {PIPECAT_CLI_NAME} eval list {agent}"
    )
    if console.json_output:
        console.output_json({"error": {"error": message, "code": "CLI-INTERRUPTED"}})
    else:
        console.print(f"\n[yellow]{escape(message)}[/yellow]")
    raise typer.Exit(EXIT_INTERRUPTED)


def _interrupted(run: dict, agent: str) -> NoReturn:
    run_id = str(run.get("id"))
    console.print(
        "\n[yellow]Stopped waiting. The run is still going; Ctrl-C doesn't cancel it.[/yellow]"
    )
    console.print(f"Check on it: [bold]{_status_command(agent, run_id)}[/bold]")
    console.print(f"Cancel it:   [bold]{_cancel_command(agent, run_id)}[/bold]")
    if console.json_output:
        console.output_json(run)
    raise typer.Exit(EXIT_INTERRUPTED)


async def _wait_and_report(org: str, agent: str, run: dict) -> None:
    """Wait for a run to end, print it, and exit with its verdict's code."""
    last = run
    with console.status("[dim]Waiting for the run[/dim]", spinner="dots") as status_view:
        if _is_live(run):
            last = await _wait_for_end(org, agent, run, status_view)
    if console.json_output:
        console.output_json(last)
    else:
        _render_run(last, agent)
    raise typer.Exit(exit_code_for(last))


# ---- Commands ----

_ORG_OPTION = typer.Option(
    None, "--organization", "-o", help="Organization to use (defaults to active org)"
)
_JSON_OPTION = typer.Option(
    False, "--json", help="Print the run as JSON on stdout (alias for the global --output json)"
)


@eval_cli.command(
    name="run",
    cls=_EvalCommand,
    help="Run a scenario against an agent. Pass --session to judge a finished session instead.",
)
@synchronizer.create_blocking
async def run(
    agent: str = typer.Argument(..., help="Name of the agent to run against", show_default=False),
    scenario_file: Path = typer.Argument(
        ..., help="Scenario YAML file", show_default=False, dir_okay=False
    ),
    scenario: str = typer.Option(
        None,
        "--scenario",
        help="Name of the scenario in the file. Optional when the file holds one.",
    ),
    session: str = typer.Option(
        None,
        "--session",
        help="Judge this finished session of the agent, by the scenario's success: and metrics:.",
    ),
    param: list[str] = typer.Option(
        [],
        "--param",
        help="A synthetic run's setting, KEY=VALUE. Repeat for several. "
        f"Settings: {', '.join(RUN_PARAMS)}.",
    ),
    deadline: int = typer.Option(
        None,
        "--deadline",
        min=1,
        help="Seconds the run may take, from creation. The API sets the bounds.",
    ),
    root: Path = typer.Option(
        None,
        "--root",
        help="Directory the scenario's included files are read from, and may not leave. "
        "Defaults to the scenario file's directory.",
    ),
    wait: bool = typer.Option(
        False, "--wait", help="Wait for the run to end and exit with its verdict"
    ),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    if session and param:
        _fail("--param is for synthetic runs. A session run takes no params.")
    params = _parse_params(param)

    try:
        bundle = build_bundle(scenario_file, root)
    except BundleError as e:
        _fail(str(e), code="CLI-BUNDLE")
    name = _scenario_name(scenario, bundle.scenario_names, bundle.scenario_path)

    body: dict[str, Any] = {
        "kind": "session" if session else "synthetic",
        "files": bundle.files,
        "scenarioPath": bundle.scenario_path,
        "scenarioName": name,
    }
    if session:
        body["sessionId"] = session
    if params:
        body["params"] = params
    if deadline is not None:
        body["deadlineSeconds"] = deadline

    # Creating a run is not idempotent, so a failed request is never repeated here.
    try:
        with console.status(f"[dim]Starting a run on [bold]'{agent}'[/bold][/dim]", spinner="dots"):
            data, error = await _api().eval_run_create(org, agent, body)
    except (KeyboardInterrupt, asyncio.CancelledError):
        _interrupted_starting(agent)
    run_data = _check(data, error)

    if wait:
        if not console.json_output:
            console.print(f"Started run [bold]{run_data.get('id')}[/bold]")
        await _wait_and_report(org, agent, run_data)
        return

    if console.json_output:
        console.output_json(run_data)
        return
    _render_run(run_data, agent)


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
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    data, error = await _api().eval_run_get(org, agent, run_id)
    run_data = _check(data, error)

    if wait:
        await _wait_and_report(org, agent, run_data)
        return

    if console.json_output:
        console.output_json(run_data)
        return
    _render_run(run_data, agent)


@eval_cli.command(name="list", cls=_EvalCommand, help="List an agent's eval runs, newest first.")
@synchronizer.create_blocking
async def list_runs(
    agent: str = typer.Argument(..., help="Name of the agent", show_default=False),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=200, help="How many runs to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="How many of the newest runs to skip"),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    paging: dict[str, int] = {"limit": limit}
    if offset:
        paging["offset"] = offset
    data, error = await _api().eval_runs_list(org, agent, **paging)
    result = _check(data, error)

    if console.json_output:
        console.output_json(result)
        return

    runs = result.get("runs") or []
    if not runs:
        console.print(f"[dim]No eval runs on [bold]'{agent}'[/bold].[/dim]")
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
        console.print(
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
        console.print(f"Run [bold]{run_id}[/bold] is cancelled.")
    else:
        console.print(
            f"Cancellation requested for run [bold]{run_id}[/bold]. It ends as soon as it can. "
            f"Check on it: [bold]{_status_command(agent, run_id)}[/bold]"
        )
