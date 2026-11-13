#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""`pipecat cloud eval suite` and `eval bundle`: suite runs and stored bundles.

A suite run runs many scenarios against an agent as one. The service plans its
members, runs them under its own limits, and gives one verdict. `eval run`
starts one from a scenario file where the organization has suite runs, and
`eval suite run` starts one from a suite file in a stored bundle.

A bundle is an organization's stored eval files. `eval bundle push` publishes
a directory as a bundle's next revision. A run pins its revision, so a later
push never changes what an accepted run runs.

Exit codes when a command waits for a suite run:

    3  an API error, or a usage error
    1  the verdict is `failed`
    2  it ended without a verdict: a member ended without one, or the suite
       run was cancelled, expired or ended in error
    0  the verdict is `passed` or `skipped`, or a measurement (a repeat above
       1, which has pass rates and no verdict) completed

Ctrl-C while waiting exits 130 and leaves the suite run going.
"""

import asyncio
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any, NoReturn
from uuid import uuid4

import typer
from rich.console import Group
from rich.live import Live
from rich.markup import escape
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.console_utils import console, format_timestamp, stdin_is_interactive
from pipecatcloud._utils.eval_bundle import BundleError, collect_directory
from pipecatcloud.api import _parse_retry_after
from pipecatcloud.cli import PIPECAT_CLI_NAME

# The API client and the clock are read through the module, `base.API` and
# `base._monotonic`, so the eval commands and these share one of each.
from pipecatcloud.cli.commands import evals as base
from pipecatcloud.cli.commands.evals import (
    _JSON_OPTION,
    _ORG_OPTION,
    _VERBOSE_OPTION,
    EXIT_ERROR,
    EXIT_INTERRUPTED,
    EXIT_NO_VERDICT,
    EXIT_PASSED,
    POLL_FACTOR,
    POLL_MAX_CONSECUTIVE_ERRORS,
    POLL_MAX_SECONDS,
    POLL_RETRY_AFTER_CAP_SECONDS,
    POLL_START_SECONDS,
    _check,
    _EvalCommand,
    _EvalGroup,
    _fail,
    _fields,
    _fmt_seconds,
    _login_check,
    _org,
    _print,
    _print_details,
    _Row,
    _run_label,
    _table,
    _use_json,
    _wait_limit,
    exit_code_for,
)

SUITE_LIVE_STATUSES = ("queued", "running")

# A suite run's creation and a bundle's publication carry a request ID, so a
# request that got no answer, or a retryable refusal, is sent again as it was.
CREATE_ATTEMPTS = 3

# Members explained in full after a suite run ends; the rest are listed by
# `eval list AGENT --suite ID`.
MAX_EXPLAINED_MEMBERS = 20

# Pages read when listing every member of a suite run (members at most 500).
RUNS_PAGE = 200
ITEMS_PAGE = 500

# What a bundle may be named, as the API has it.
BUNDLE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$")
BUNDLE_NAME_MAX_LENGTH = 128
BUNDLE_NAME_RULE = (
    "1 to 128 lowercase letters, digits, dots, dashes or underscores, starting and "
    "ending with a letter or a digit"
)

MIXED_WARNING = (
    "Mixed judging versions: these results were judged by more than one version "
    "(a deploy changed how runs are judged mid-suite)"
)

suite_cli = typer.Typer(
    name="suite",
    cls=_EvalGroup,
    help="Run many scenarios as one suite run, and read and cancel suite runs",
    no_args_is_help=True,
)

bundle_cli = typer.Typer(
    name="bundle",
    cls=_EvalGroup,
    help="Publish and read stored eval bundles",
    no_args_is_help=True,
)


# ---- Suite runs: helpers ----


def suite_exit_code(suite: dict) -> int:
    """The exit code for an ended suite run."""
    if suite.get("status") != "completed":
        return EXIT_NO_VERDICT
    if suite.get("measurement"):
        return EXIT_PASSED
    return exit_code_for(suite)


def _suite_is_live(suite: dict) -> bool:
    return suite.get("status") in SUITE_LIVE_STATUSES


def _suite_status_command(suite_run_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} eval suite status {suite_run_id} --wait"


def _suite_cancel_command(suite_run_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} eval suite cancel {suite_run_id}"


def _members_command(agent: str, suite_run_id: str) -> str:
    return f"{PIPECAT_CLI_NAME} eval list {agent} --suite {suite_run_id}"


def _count(counts: dict, key: str) -> int:
    value = counts.get(key)
    return value if isinstance(value, int) else 0


def _counts_markup(suite: dict, elapsed: float | None = None) -> str:
    """One line of a suite run's counts: done of planned, then each outcome."""
    counts = suite.get("counts") or {}
    parts = [
        f"{_count(counts, 'terminal')}/{_count(counts, 'planned')} done",
        f"[green]{_count(counts, 'passed')} passed[/green]",
        f"[red]{_count(counts, 'failed')} failed[/red]",
    ]
    if _count(counts, "skipped"):
        parts.append(f"[yellow]{_count(counts, 'skipped')} skipped[/yellow]")
    if _count(counts, "withoutVerdict"):
        parts.append(f"[yellow]{_count(counts, 'withoutVerdict')} without verdict[/yellow]")
    if _count(counts, "live"):
        parts.append(f"{_count(counts, 'live')} live")
    if _count(counts, "pending"):
        parts.append(f"{_count(counts, 'pending')} pending")
    if elapsed is not None:
        parts.append(_fmt_seconds(elapsed))
    return "  " + "  ·  ".join(parts)


def _effective(suite: dict, key: str) -> int | None:
    limit = suite.get(key)
    value = limit.get("effective") if isinstance(limit, dict) else None
    return value if isinstance(value, int) else None


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _target(suite: dict) -> str | None:
    target = suite.get("target") or {}
    if not isinstance(target, dict):
        return None
    return target.get("suitePath") or target.get("scenarioPath")


def _print_suite_settings(suite: dict) -> None:
    """How the suite run will go, in `pipecat eval suite`'s Settings block."""
    counts = suite.get("counts") or {}
    rows = [
        ("agent", suite.get("serviceName")),
        ("suite run", suite.get("id")),
        ("members", _count(counts, "planned")),
        ("repeat", _effective(suite, "repeat")),
        ("concurrency", _effective(suite, "concurrency")),
    ]
    _print("Settings:")
    for label, value in rows:
        if value is not None:
            _print(f"  [bold]{label:<11}[/bold] -> {escape(str(value))}")
    if suite.get("measurement"):
        _print("  [dim]A measurement: it reports pass rates, with no verdict.[/dim]")
    _print()


class _SuiteView:
    """A suite run while it runs: a spinner and its counts, redrawn on every refresh."""

    def __init__(self, suite: dict, started: float):
        self.suite = suite
        self.started = started
        self.final: float | None = None
        self.spinner = Spinner("dots", style="cyan")

    def __rich__(self) -> Group:
        elapsed = (self.final if self.final is not None else base._monotonic()) - self.started
        line = Text.from_markup(_counts_markup(self.suite, elapsed))
        if self.final is not None or not _suite_is_live(self.suite):
            return Group(line)
        grid = Table.grid(padding=(0, 1))
        grid.add_column(no_wrap=True)
        grid.add_column()
        grid.add_row(self.spinner, line)
        return Group(grid)


class _SuiteInterrupted(Exception):
    """Ctrl-C while waiting, with the suite run as last seen."""

    def __init__(self, suite: dict):
        super().__init__("interrupted")
        self.suite = suite


def _suite_wait_over(limit: float | None, started: float, suite: dict) -> None:
    """Stop waiting for a suite run that is still live well after it should have ended."""
    if limit is None or base._monotonic() - started <= limit:
        return
    suite_run_id = str(suite.get("id"))
    _fail(
        f"Suite run {suite_run_id} hasn't ended, though it is well past its deadline, so this "
        f"stopped waiting. Check on it: {_suite_status_command(suite_run_id)}",
        code="CLI-WAIT",
    )


async def _wait_for_suite(org: str, suite: dict, on_update) -> dict:
    """Poll a suite run until it has ended, and return it.

    A poll that fails for a reason that may pass (a dropped connection, a 5xx,
    a 429) is tried again; a permanent one (a 4xx) ends the wait.
    """
    delay = POLL_START_SECONDS
    pause = delay
    failures = 0
    limit = _wait_limit(suite)
    started = base._monotonic()
    while _suite_is_live(suite):
        _suite_wait_over(limit, started, suite)
        on_update(suite)
        try:
            await asyncio.sleep(pause)
            delay = min(delay * POLL_FACTOR, POLL_MAX_SECONDS)
            pause = delay
            data, error = await base.API.bubble_error().eval_suite_run_get(org, str(suite["id"]))
            if data:
                failures = 0
                suite = data
                continue
            status = base.API.error_status
            permanent = status is not None and 400 <= status < 500 and status != 429
            failures += 1
            if permanent or failures >= POLL_MAX_CONSECUTIVE_ERRORS:
                if not console.json_output:
                    base.API.print_error()
                _check(None, error or {"error": "Lost contact with the API.", "code": "CLI-POLL"})
            retry_after = _parse_retry_after(base.API.error_retry_after)
            if retry_after is not None:
                pause = max(pause, min(retry_after, POLL_RETRY_AFTER_CAP_SECONDS))
        except (KeyboardInterrupt, asyncio.CancelledError):
            raise _SuiteInterrupted(suite) from None
    on_update(suite)
    return suite


def _suite_interrupted(suite: dict) -> NoReturn:
    suite_run_id = str(suite.get("id"))
    if _suite_is_live(suite):
        _print(
            "\n[yellow]Stopped waiting. The suite run is still going; Ctrl-C doesn't cancel "
            "it.[/yellow]"
        )
        _print(f"Check on it: [bold]{_suite_status_command(suite_run_id)}[/bold]")
        _print(f"Cancel it:   [bold]{_suite_cancel_command(suite_run_id)}[/bold]")
    else:
        _print("\n[yellow]Stopped waiting. The suite run has ended.[/yellow]")
        _print(f"See it: [bold]{PIPECAT_CLI_NAME} eval suite status {suite_run_id}[/bold]")
    if console.json_output:
        console.output_json(suite)
    raise typer.Exit(EXIT_INTERRUPTED)


async def _member_runs(org: str, agent: str, suite_run_id: str) -> list[dict]:
    """Every admitted member's eval run, without results."""
    runs: list[dict] = []
    offset = 0
    while True:
        data, error = await base._api().eval_runs_list(
            org, agent, limit=RUNS_PAGE, offset=offset, suite_run_id=suite_run_id
        )
        page = _check(data, error)
        batch = page.get("runs") or []
        runs.extend(batch)
        offset += len(batch)
        total = page.get("total_count")
        if not batch or not isinstance(total, int) or offset >= total:
            return runs


async def _member_items(org: str, suite_run_id: str) -> list[dict]:
    """Every planned member, in order, each with its eval run's ID once admitted."""
    items: list[dict] = []
    offset = 0
    while True:
        data, error = await base._api().eval_suite_run_items(
            org, suite_run_id, limit=ITEMS_PAGE, offset=offset
        )
        page = _check(data, error)
        batch = page.get("items") or []
        items.extend(batch)
        offset += len(batch)
        total = page.get("total")
        if not batch or not isinstance(total, int) or offset >= total:
            return items


def _member_labels(items: list[dict]) -> dict[str, str]:
    """Each admitted member's label by its run's ID: entry, scenario, and attempt where several."""
    several = any(isinstance(i.get("attempt"), int) and i["attempt"] > 1 for i in items)
    labels: dict[str, str] = {}
    for item in items:
        run_id = item.get("evalRunId")
        if not run_id:
            continue
        stem = Path(str(item.get("scenarioPath") or "")).stem
        label = f"{stem}/{item.get('scenarioName')}" if stem else str(item.get("scenarioName"))
        if item.get("label"):
            label = f"{item['label']}: {label}"
        if several:
            label = f"{label} #{item.get('attempt')}"
        labels[str(run_id)] = label
    return labels


def _print_pass_rates(suite: dict) -> None:
    rates = suite.get("passRates") or []
    if not rates:
        return
    mixed = bool(suite.get("mixed"))
    columns = ["Entry", "Scenario", "Passed", "Failed", "Skipped", "No verdict", "Pass rate"]
    if mixed:
        columns.insert(2, "Judging version")
    rows = []
    for r in rates:
        stem = Path(str(r.get("scenarioPath") or "")).stem
        rate = r.get("passRate")
        row: list[Any] = [
            r.get("label"),
            f"{stem}/{r.get('scenarioName')}" if stem else r.get("scenarioName"),
            r.get("passed"),
            r.get("failed"),
            r.get("skipped"),
            r.get("withoutVerdict"),
            f"{round(rate * 100)}%" if isinstance(rate, int | float) else None,
        ]
        if mixed:
            row.insert(2, r.get("fingerprint"))
        rows.append(tuple(row))
    _print()
    _table(columns, rows, title="Pass rates")


async def _report_suite(
    org: str, suite: dict, verbose: bool, *, counts_shown: bool = False
) -> None:
    """After a suite run ends: its counts (unless just shown), what ended it, then its
    members that need a look."""
    agent = str(suite.get("serviceName"))
    suite_run_id = str(suite.get("id"))
    measurement = bool(suite.get("measurement"))
    status = str(suite.get("status"))
    _print()
    if not counts_shown:
        _print(f"[bold]{_counts_markup(suite)}[/bold]")
    verdict = suite.get("verdict")
    if status != "completed":
        _print(f"  [yellow]The suite run ended {escape(status)}, without a verdict.[/yellow]")
    elif measurement:
        _print("  A measurement: pass rates, no verdict.")
    elif verdict:
        color = {"passed": "green", "failed": "red"}.get(str(verdict), "yellow")
        _print(f"  Verdict: [{color}]{escape(str(verdict))}[/{color}]")
    else:
        _print("  [yellow]No verdict: a member ended without one.[/yellow]")
    error = suite.get("error")
    if isinstance(error, dict) and error.get("message"):
        _print(
            f"  [red]{escape(str(error['message']))}[/red] [dim]({escape(str(error.get('code')))})[/dim]"
        )
    if suite.get("mixed"):
        tail = "; pass rates are split by version" if measurement else ""
        _print(f"  [yellow]{MIXED_WARNING}{tail}.[/yellow]")
    if measurement:
        _print_pass_rates(suite)

    runs = await _member_runs(org, agent, suite_run_id)
    counts = suite.get("counts") or {}
    never = _count(counts, "planned") - len(runs)
    if verbose:
        explain = runs
    elif measurement:
        explain = []
    else:
        explain = [r for r in runs if exit_code_for(r)]
    if explain:
        labels = _member_labels(await _member_items(org, suite_run_id))
        if not verbose:
            _print()
            _print(f"  [bold red]Failures ({len(explain)} of {len(runs)}):[/bold red]")
        for listed in explain[:MAX_EXPLAINED_MEMBERS]:
            data, error = await base._api().eval_run_get(org, agent, str(listed.get("id")))
            run = _check(data, error)
            label = labels.get(str(run.get("id"))) or _run_label(run)
            _print()
            _print_details(_Row(label, run=run), agent, verbose)
        if len(explain) > MAX_EXPLAINED_MEMBERS:
            _print()
            _print(f"  [dim]And {len(explain) - MAX_EXPLAINED_MEMBERS} more.[/dim]")
    if never > 0 and status != "completed":
        _print()
        _print(f"  [dim]{never} of the members never started: the suite run ended first.[/dim]")
    _print()
    _print(f"  members: {_members_command(agent, suite_run_id)}")


async def _follow_suite(org: str, suite: dict, verbose: bool, *, settings: bool) -> NoReturn:
    """Wait for a suite run to end, showing its counts as they move, then report and exit."""
    if settings and not console.json_output:
        _print_suite_settings(suite)
    started = base._monotonic()
    view = _SuiteView(suite, started)
    last_line: list[str] = []

    def on_update(latest: dict) -> None:
        view.suite = latest
        if console.json_output or console.rich_output:
            return
        line = _counts_markup(latest)
        if last_line != [line]:
            last_line[:] = [line]
            _print(line)

    display = (
        Live(view, console=console, refresh_per_second=8)
        if console.rich_output and not console.json_output
        else nullcontext()
    )
    try:
        with display:
            if _suite_is_live(suite):
                suite = await _wait_for_suite(org, suite, on_update)
            view.suite = suite
            view.final = base._monotonic()
    except _SuiteInterrupted as stopped:
        _suite_interrupted(stopped.suite)

    if console.json_output:
        console.output_json(suite)
    else:
        await _report_suite(org, suite, verbose, counts_shown=True)
    raise typer.Exit(suite_exit_code(suite))


async def _create(request: Any, body: dict, refused: Any = None) -> tuple[dict | None, dict | None]:
    """Send a create request, again as it was on no answer or a refusal that says to retry.

    `request` is the API call, `body` its request, which carries a request ID
    so the API answers a repeat with what the first one made. Returns the
    answer, or `(None, error)` for a refusal that `refused` accepts; any other
    failure exits.
    """
    for attempt in range(1, CREATE_ATTEMPTS + 1):
        data, error = await request(body)
        if data:
            return data, None
        if refused is not None and isinstance(error, dict) and refused(error):
            return None, error
        retry_after = _parse_retry_after(base.API.error_retry_after)
        retryable = base.API.no_response() or (
            base.API.error_status in (429, 503) and retry_after is not None
        )
        if not retryable or attempt == CREATE_ATTEMPTS:
            if not console.json_output:
                base.API.print_error()
            _check(None, error or {"error": "The API didn't answer.", "code": "CLI-SUBMISSION"})
        wait = retry_after if retry_after is not None else POLL_START_SECONDS
        await asyncio.sleep(min(wait, POLL_RETRY_AFTER_CAP_SECONDS))
    raise AssertionError("unreachable")


async def run_suite(org: str, agent: str, body: dict, *, wait: bool, verbose: bool) -> NoReturn:
    """Start a suite run, then follow it to its end, or show it accepted."""
    body = {"requestId": str(uuid4()), **body}

    async def request(b: dict):
        return await base.API.bubble_error().eval_suite_run_create(org, agent, b)

    try:
        with console.status(
            f"[dim]Starting a suite run on [bold]'{escape(agent)}'[/bold][/dim]", spinner="dots"
        ):
            suite, _ = await _create(request, body)
    except (KeyboardInterrupt, asyncio.CancelledError):
        message = (
            "Stopped while starting the suite run. The request may have reached the API, so a "
            f"suite run may exist. See: {PIPECAT_CLI_NAME} eval suite list {agent}"
        )
        if console.json_output:
            console.output_json({"error": {"error": message, "code": "CLI-INTERRUPTED"}})
        else:
            _print(f"\n[yellow]{escape(message)}[/yellow]")
        raise typer.Exit(EXIT_INTERRUPTED) from None
    assert suite is not None

    if wait:
        await _follow_suite(org, suite, verbose, settings=True)
    if console.json_output:
        console.output_json(suite)
    else:
        counts = suite.get("counts") or {}
        _print(
            f"Suite run [bold]{escape(str(suite.get('id')))}[/bold] accepted: "
            f"{_count(counts, 'planned')} members on [bold]'{escape(agent)}'[/bold]."
        )
        _print(f"Follow it: [bold]{_suite_status_command(str(suite.get('id')))}[/bold]")
    raise typer.Exit(EXIT_PASSED)


async def suites_available(org: str) -> bool:
    """Whether the organization has suite runs, as `/eval-capabilities` says.

    An API without that route has none.
    """
    data, error = await base.API.bubble_error().eval_capabilities(org)
    if data:
        return data.get("suites") is True
    if base.API.error_status == 404:
        return False
    if not console.json_output:
        base.API.print_error()
    _check(None, error)
    return False


def _render_suite(suite: dict) -> None:
    """A suite run as it is: its fields and counts."""
    status = str(suite.get("status"))
    verdict = suite.get("verdict")
    if not _suite_is_live(suite) and suite.get("measurement") and status == "completed":
        verdict = "none (a measurement)"
    created = suite.get("createdAt")
    finished = suite.get("finishedAt")
    deadline = suite.get("deadlineAt")
    _fields(
        [
            ("Suite run", suite.get("id")),
            ("Agent", suite.get("serviceName")),
            ("Region", suite.get("region")),
            ("Status", status),
            ("Verdict", verdict),
            ("Target", _target(suite)),
            ("Revision", suite.get("bundleRevisionId")),
            ("Members", str(_count(suite.get("counts") or {}, "planned"))),
            ("Repeat", _text(_effective(suite, "repeat"))),
            ("Concurrency", _text(_effective(suite, "concurrency"))),
            ("Judging version", suite.get("fingerprint")),
            ("Created", format_timestamp(created) if created else None),
            (
                "Deadline",
                format_timestamp(deadline) if deadline and _suite_is_live(suite) else None,
            ),
            ("Finished", format_timestamp(finished) if finished else None),
        ]
    )
    entries = [e for e in suite.get("entries") or [] if isinstance(e, dict)]
    if any(e.get("label") for e in entries):
        _print("Entries:")
        for e in entries:
            cap = f" [dim](concurrency {e['concurrency']})[/dim]" if e.get("concurrency") else ""
            _print(f"  {escape(str(e.get('label') or '(unnamed)'))}{cap}")


# ---- Suite runs: commands ----


_REPEAT_OPTION = typer.Option(
    None,
    "--repeat",
    min=1,
    help="Run each scenario this many times. Above 1, the suite run is a measurement: "
    "pass rates, no verdict.",
)
_CONCURRENCY_OPTION = typer.Option(
    None, "--concurrency", min=1, help="Run at most this many at once. The API sets the bounds."
)
_SUITE_DEADLINE_OPTION = typer.Option(
    None,
    "--deadline",
    min=1,
    help="Seconds the whole suite run may take, from creation. The API sets the bounds.",
)


def _bundle_ref(ref: str) -> tuple[str, int | None]:
    """`NAME` or `NAME@N`: a bundle's name, and a revision number when one is given."""
    name, sep, number = ref.rpartition("@")
    if not sep:
        name, number = ref, ""
    elif not number.isdigit() or int(number) < 1:
        _fail(f"{ref!r}: a revision is NAME@N, with N a revision number from 1.")
    _check_bundle_name(name)
    return name, int(number) if number else None


def _check_bundle_name(name: str) -> None:
    if len(name) > BUNDLE_NAME_MAX_LENGTH or not BUNDLE_NAME_PATTERN.match(name):
        _fail(f"{name!r} isn't a bundle name. A name is {BUNDLE_NAME_RULE}.")


async def _find_bundle(org: str, name: str) -> dict | None:
    """The organization's live bundle with that name, if there is one."""
    offset = 0
    while True:
        data, error = await base._api().eval_bundles_list(org, limit=RUNS_PAGE, offset=offset)
        page = _check(data, error)
        bundles = page.get("bundles") or []
        for bundle in bundles:
            if bundle.get("name") == name:
                return bundle
        offset += len(bundles)
        total = page.get("total_count")
        if not bundles or not isinstance(total, int) or offset >= total:
            return None


async def _resolve_revision(org: str, ref: str) -> tuple[dict, dict]:
    """The bundle and revision `NAME[@N]` names: revision N, or the latest."""
    name, number = _bundle_ref(ref)
    bundle = await _find_bundle(org, name)
    if bundle is None:
        _fail(
            f"There's no bundle named {name!r}. See the bundles with "
            f"`{PIPECAT_CLI_NAME} eval bundle list`."
        )
    latest = bundle.get("latestRevision") or {}
    if number is None or latest.get("number") == number:
        return bundle, latest
    offset = 0
    while True:
        data, error = await base._api().eval_bundle_revisions_list(
            org, str(bundle["id"]), limit=RUNS_PAGE, offset=offset
        )
        page = _check(data, error)
        revisions = page.get("revisions") or []
        for revision in revisions:
            if revision.get("number") == number:
                return bundle, revision
        offset += len(revisions)
        total = page.get("total_count")
        if not revisions or not isinstance(total, int) or offset >= total:
            _fail(
                f"Bundle {name!r} has no revision {number}. Its latest is {latest.get('number')}."
            )


@suite_cli.command(
    name="run",
    cls=_EvalCommand,
    help="Run a suite file from a stored bundle against an agent, as one suite run.",
)
@synchronizer.create_blocking
async def suite_run(
    agent: str = typer.Argument(..., help="Name of the agent to run against", show_default=False),
    suite: str = typer.Option(
        ..., "--suite", help="The suite file's path in the bundle", show_default=False
    ),
    bundle: str = typer.Option(
        None,
        "--bundle",
        help="The bundle: NAME for its latest revision, or NAME@N for revision N",
    ),
    revision: str = typer.Option(
        None, "--revision", help="A revision by its ID, in place of --bundle"
    ),
    repeat: int = _REPEAT_OPTION,
    concurrency: int = _CONCURRENCY_OPTION,
    deadline: int = _SUITE_DEADLINE_OPTION,
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Wait for the suite run to end and exit with its verdict. With --no-wait, start "
        "it and return.",
    ),
    verbose: bool = _VERBOSE_OPTION,
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    if (bundle is None) == (revision is None):
        _fail("Name the files to run with either --bundle NAME[@N] or --revision ID.")
    await _login_check()
    org = _org(organization)

    if bundle is not None:
        found, rev = await _resolve_revision(org, bundle)
        revision_id = str(rev.get("id"))
        if wait and not console.json_output:
            _print(
                f"Bundle: [bold]{escape(found['name'])}@{rev.get('number')}[/bold], suite {escape(suite)}"
            )
            _print()
    else:
        revision_id = revision
    body: dict[str, Any] = {"bundleRevisionId": revision_id, "suitePath": suite}
    for key, value in (
        ("repeat", repeat),
        ("concurrency", concurrency),
        ("deadlineSeconds", deadline),
    ):
        if value is not None:
            body[key] = value
    await run_suite(org, agent, body, wait=wait, verbose=verbose)


@suite_cli.command(
    name="status",
    cls=_EvalCommand,
    help="Show a suite run. With --wait, wait for it to end and exit with its verdict.",
)
@synchronizer.create_blocking
async def suite_status(
    suite_run_id: str = typer.Argument(..., help="The suite run's ID", show_default=False),
    wait: bool = typer.Option(
        False, "--wait", help="Wait for the suite run to end and exit with its verdict"
    ),
    verbose: bool = _VERBOSE_OPTION,
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    data, error = await base._api().eval_suite_run_get(org, suite_run_id)
    suite = _check(data, error)
    if wait:
        if not console.json_output:
            _render_suite(suite)
            _print()
        await _follow_suite(org, suite, verbose, settings=False)
    if console.json_output:
        console.output_json(suite)
        return
    _render_suite(suite)
    if _suite_is_live(suite):
        _print()
        _print(_counts_markup(suite))
        _print(
            f"[dim]The suite run hasn't ended. Wait for it with "
            f"[bold]{_suite_status_command(suite_run_id)}[/bold].[/dim]"
        )
        return
    await _report_suite(org, suite, verbose)


@suite_cli.command(
    name="list",
    cls=_EvalCommand,
    help="List suite runs, newest first: the organization's, or one agent's.",
)
@synchronizer.create_blocking
async def suite_list(
    agent: str = typer.Argument(None, help="Only this agent's suite runs", show_default=False),
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=200, help="How many to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="How many of the newest to skip"),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    paging: dict[str, int] = {"limit": limit}
    if offset:
        paging["offset"] = offset
    data, error = await base._api().eval_suite_runs_list(org, agent, **paging)
    result = _check(data, error)
    if console.json_output:
        console.output_json(result)
        return

    suites = result.get("suiteRuns") or []
    if not suites:
        where = f" on [bold]'{escape(agent)}'[/bold]" if agent else ""
        _print(f"[dim]No suite runs{where}.[/dim]")
        return
    rows = []
    for s in suites:
        counts = s.get("counts") or {}
        rows.append(
            (
                s.get("id"),
                s.get("serviceName"),
                s.get("status"),
                s.get("verdict") or ("measurement" if s.get("measurement") else None),
                f"{_count(counts, 'terminal')}/{_count(counts, 'planned')}",
                _count(counts, "passed"),
                _count(counts, "failed"),
                format_timestamp(s["createdAt"]) if s.get("createdAt") else None,
            )
        )
    _table(["ID", "Agent", "Status", "Verdict", "Done", "Passed", "Failed", "Created"], rows)
    total = result.get("total")
    if isinstance(total, int) and total > offset + len(suites):
        _print(
            f"[dim]Showing {offset + 1} to {offset + len(suites)} of {total}. "
            f"For the next page use --offset {offset + len(suites)}.[/dim]"
        )


@suite_cli.command(
    name="cancel",
    cls=_EvalCommand,
    help="Ask for a suite run to be cancelled. Members not yet started never run; running "
    "ones are asked to stop.",
)
@synchronizer.create_blocking
async def suite_cancel(
    suite_run_id: str = typer.Argument(..., help="The suite run's ID", show_default=False),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    data, error = await base.API.bubble_error().eval_suite_run_cancel(org, suite_run_id)
    if not data and base.API.error_status == 409:
        # Already ended some other way: say how, then exit as the refusal it is.
        ended, _ = await base.API.bubble_error().eval_suite_run_get(org, suite_run_id)
        if console.json_output:
            console.output_json({"error": error, "suiteRun": ended})
        elif ended:
            verdict = ended.get("verdict")
            tail = f", verdict {verdict}" if verdict else ""
            _print(
                f"[yellow]Suite run {escape(suite_run_id)} already ended: "
                f"{escape(str(ended.get('status')))}{escape(tail)}.[/yellow]"
            )
        else:
            base.API.print_error()
        raise typer.Exit(EXIT_ERROR)
    if not data and not console.json_output:
        base.API.print_error()
    suite = _check(data, error)
    if console.json_output:
        console.output_json(suite)
        return
    if suite.get("status") == "cancelled":
        _print(f"Suite run [bold]{escape(suite_run_id)}[/bold] is cancelled.")
    else:
        _print(
            f"Cancellation requested for suite run [bold]{escape(suite_run_id)}[/bold]: members "
            "not yet started won't run, and running ones are asked to stop. "
            f"Check on it: [bold]{_suite_status_command(suite_run_id)}[/bold]"
        )


# ---- Bundles ----


def _kib(size: int) -> str:
    return f"{size / 1024:.1f} KiB" if size >= 1024 else f"{size} bytes"


def _print_catalog(catalog: Any) -> None:
    """What a revision holds, as the API catalogued it: suites, scenarios, bodies, fragments."""
    if not isinstance(catalog, dict):
        return
    suites = [s for s in catalog.get("suites") or [] if isinstance(s, dict)]
    if suites:
        _print("Suites:")
        for s in suites:
            entries = len(s.get("entries") or [])
            notes = [f"{entries} {'entry' if entries == 1 else 'entries'}"]
            for key in ("repeat", "concurrency"):
                if s.get(key) is not None:
                    notes.append(f"{key} {s[key]}")
            _print(
                f"  [cyan]{escape(str(s.get('path')))}[/cyan] [dim]{escape(', '.join(notes))}[/dim]"
            )
    files = catalog.get("scenarioFiles") or {}
    if isinstance(files, dict) and files:
        _print("Scenario files:")
        for path, scenarios in files.items():
            names = ", ".join(
                f"{s.get('name')} ({s.get('kind')}"
                + (
                    f", runs {s['runs']}"
                    if isinstance(s.get("runs"), int) and s["runs"] > 1
                    else ""
                )
                + ")"
                for s in scenarios or []
                if isinstance(s, dict)
            )
            _print(f"  [cyan]{escape(str(path))}[/cyan] [dim]{escape(names)}[/dim]")
    for heading, key in (("Bodies", "bodies"), ("Fragments", "fragments")):
        held = catalog.get(key) or []
        paths = list(held.keys()) if isinstance(held, dict) else list(held)
        if paths:
            _print(f"{heading}:")
            for path in paths:
                _print(f"  [cyan]{escape(str(path))}[/cyan]")


def _print_ignored(revision: dict) -> None:
    checker = revision.get("checker") or {}
    ignored = [i for i in checker.get("ignored") or [] if isinstance(i, dict)]
    if not ignored:
        return
    _print("[yellow]Ignored keys[/yellow] [dim](a hosted run doesn't use them):[/dim]")
    for i in ignored:
        where = " ".join(str(x) for x in (i.get("file"), i.get("scenario")) if x)
        _print(f"  [cyan]{escape(where)}[/cyan] {escape(str(i.get('key')))}")
    total = checker.get("ignoredTotal")
    if isinstance(total, int) and total > len(ignored):
        _print(f"  [dim]And {total - len(ignored)} more.[/dim]")


def _print_problems(error: dict) -> None:
    problems = [p for p in error.get("problems") or [] if isinstance(p, dict)]
    _print("[red]The bundle wasn't published: its files have problems.[/red]")
    for p in problems:
        message = str(p.get("message"))
        # The API's message starts with the file it is about; name it only when it doesn't.
        where = (
            "" if p.get("file") and message.startswith(f"{p['file']}") else str(p.get("file") or "")
        )
        lead = f"[cyan]{escape(where)}[/cyan] " if where else ""
        _print(f"  [red]✗[/red] {lead}{escape(message)} [dim]({escape(str(p.get('code')))})[/dim]")
    total = error.get("total")
    if isinstance(total, int) and total > len(problems):
        _print(f"  [dim]And {total - len(problems)} more.[/dim]")


def _refused_files(error: dict) -> bool:
    return error.get("code") == "EVAL-BUNDLE-REFUSED"


@bundle_cli.command(
    name="push",
    cls=_EvalCommand,
    help="Publish a directory's YAML and JSON files as a bundle's next revision, creating "
    "the bundle if it's new.",
)
@synchronizer.create_blocking
async def bundle_push(
    name: str = typer.Argument(..., help="The bundle's name", show_default=False),
    directory: Path = typer.Argument(
        Path("."), help="The directory to publish", file_okay=False, show_default="."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Publish without asking to confirm"),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    _check_bundle_name(name)
    try:
        found = collect_directory(directory)
    except BundleError as e:
        _fail(str(e), code="CLI-BUNDLE")
    await _login_check()
    org = _org(organization)

    existing = await _find_bundle(org, name)
    if existing is not None:
        latest = (existing.get("latestRevision") or {}).get("number")
        target = f"revision {latest + 1 if isinstance(latest, int) else 'next'} of '{name}'"
    else:
        target = f"a new bundle '{name}'"
    if not console.json_output:
        _print(
            f"Publishing {len(found.files)} {'file' if len(found.files) == 1 else 'files'} "
            f"({_kib(found.size)}) from {escape(str(directory))} as {escape(target)}:"
        )
        for key, text in found.files.items():
            _print(f"  [cyan]{escape(key)}[/cyan] [dim]{_kib(len(text.encode('utf-8')))}[/dim]")
        for key, reason in found.skipped:
            _print(f"  [dim]Left out: {escape(key)}, which {escape(reason)}.[/dim]")
        if found.other_files:
            _print(
                f"  [dim]{found.other_files} other {'file is' if found.other_files == 1 else 'files are'}"
                " not YAML or JSON, so left out.[/dim]"
            )
    if not yes:
        if console.json_output or not stdin_is_interactive():
            _fail("Publishing asks to confirm. Add --yes to publish without asking.")
        if not typer.confirm("Publish?", default=True):
            _print("Nothing was published.")
            raise typer.Exit(EXIT_PASSED)

    async def append(bundle_id: str) -> tuple[dict | None, dict | None]:
        async def request(b: dict):
            return await base.API.bubble_error().eval_bundle_revision_append(org, bundle_id, b)

        return await _create(
            request, {"requestId": str(uuid4()), "files": found.files}, _refused_files
        )

    async def create(b: dict):
        return await base.API.bubble_error().eval_bundle_create(org, b)

    with console.status("[dim]Checking and publishing the files[/dim]", spinner="dots"):
        if existing is not None:
            revision, refusal = await append(str(existing["id"]))
        else:
            created, refusal = await _create(
                create,
                {"requestId": str(uuid4()), "name": name, "files": found.files},
                lambda e: _refused_files(e) or e.get("code") == "EVAL-BUNDLE-NAME-TAKEN",
            )
            if refusal is not None and refusal.get("code") == "EVAL-BUNDLE-NAME-TAKEN":
                # Created by someone else since it was looked up: append to it.
                existing = await _find_bundle(org, name)
                if existing is None:
                    _fail(f"Bundle {name!r} was created and removed while publishing. Try again.")
                revision, refusal = await append(str(existing["id"]))
            else:
                revision = (created or {}).get("revision")
    if refusal is not None:
        if console.json_output:
            console.output_json({"error": refusal})
        else:
            _print_problems(refusal)
        raise typer.Exit(EXIT_ERROR)
    assert revision is not None

    if console.json_output:
        console.output_json(revision)
        return
    _print(
        f"[green]✓[/green] Published [bold]{escape(name)}@{revision.get('number')}[/bold] "
        f"[dim]({escape(str(revision.get('id')))}, checked: {escape(str(revision.get('coverage')))})[/dim]"
    )
    _print_catalog(revision.get("catalog"))
    _print_ignored(revision)
    suites = [s for s in (revision.get("catalog") or {}).get("suites") or [] if isinstance(s, dict)]
    if suites:
        _print()
        _print(
            f"Run a suite: [bold]{PIPECAT_CLI_NAME} eval suite run AGENT --bundle "
            f"{escape(name)}@{revision.get('number')} --suite {escape(str(suites[0].get('path')))}[/bold]"
        )


@bundle_cli.command(name="list", cls=_EvalCommand, help="List the organization's bundles, by name.")
@synchronizer.create_blocking
async def bundle_list(
    limit: int = typer.Option(10, "--limit", "-n", min=1, max=200, help="How many to show"),
    offset: int = typer.Option(0, "--offset", min=0, help="How many to skip"),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    await _login_check()
    org = _org(organization)

    paging: dict[str, int] = {"limit": limit}
    if offset:
        paging["offset"] = offset
    data, error = await base._api().eval_bundles_list(org, **paging)
    result = _check(data, error)
    if console.json_output:
        console.output_json(result)
        return
    bundles = result.get("bundles") or []
    if not bundles:
        _print("[dim]No bundles. Publish one with `eval bundle push NAME DIR`.[/dim]")
        return
    _table(
        ["Name", "Latest", "Checked", "Updated", "ID"],
        [
            (
                b.get("name"),
                (b.get("latestRevision") or {}).get("number"),
                (b.get("latestRevision") or {}).get("coverage"),
                format_timestamp(b["updatedAt"]) if b.get("updatedAt") else None,
                b.get("id"),
            )
            for b in bundles
        ],
    )
    total = result.get("total_count")
    if isinstance(total, int) and total > offset + len(bundles):
        _print(
            f"[dim]Showing {offset + 1} to {offset + len(bundles)} of {total}. "
            f"For the next page use --offset {offset + len(bundles)}.[/dim]"
        )


@bundle_cli.command(
    name="show",
    cls=_EvalCommand,
    help="Show a bundle's revision: what it holds and its files. The latest unless NAME@N.",
)
@synchronizer.create_blocking
async def bundle_show(
    ref: str = typer.Argument(..., metavar="NAME[@N]", help="The bundle, and a revision number"),
    files: bool = typer.Option(False, "--files", help="Also print the files' contents"),
    organization: str = _ORG_OPTION,
    output_json: bool = _JSON_OPTION,
):
    _use_json(output_json)
    _bundle_ref(ref)
    await _login_check()
    org = _org(organization)

    bundle, summary = await _resolve_revision(org, ref)
    data, error = await base._api().eval_bundle_revision_get(org, str(summary.get("id")))
    revision = _check(data, error)
    if console.json_output:
        console.output_json(revision)
        return
    checker = revision.get("checker") or {}
    created = revision.get("createdAt")
    _fields(
        [
            ("Bundle", bundle.get("name")),
            ("Revision", str(revision.get("number"))),
            ("ID", revision.get("id")),
            ("Digest", revision.get("digest")),
            ("Checked", revision.get("coverage")),
            ("Checked in", checker.get("region")),
            ("Pipecat", checker.get("harnessVersion")),
            ("Created", format_timestamp(created) if created else None),
        ]
    )
    _print()
    _print_catalog(revision.get("catalog"))
    _print_ignored(revision)
    held = revision.get("files") or {}
    if isinstance(held, dict):
        _print()
        _table(
            ["File", "Size"],
            [(path, _kib(len(str(text).encode("utf-8")))) for path, text in held.items()],
        )
        if files:
            for path, text in held.items():
                _print()
                _print(f"[bold cyan]==> {escape(str(path))} <==[/bold cyan]")
                console.print(str(text), markup=False, highlight=False)
