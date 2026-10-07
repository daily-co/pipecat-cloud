#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Org-scoped GitHub App commands (PCC-933).

The connect flow deliberately has no local callback server. GitHub sends the
browser on to the Pipecat Cloud dashboard, which links the installation once
GitHub confirms that the signed-in user may: they installed the App or asked
for it, while a member of the GitHub organization it is on, or they own the
GitHub account it is on. The terminal only has to open a URL and poll until
the link shows up. That is the same flow the dashboard's own Connect button
runs.
"""

import asyncio
import time

import questionary
import typer
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.github_utils import (
    installation_settings_url,
    is_valid_repo_full_name,
)
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

github_cli = typer.Typer(
    name="github", help="GitHub App connection management", no_args_is_help=True
)

# Poll cadence while waiting for the link to land. Each timeout is how long the
# server keeps that flow alive, plus a minute for the link itself to commit:
# giving up sooner would abandon a connect that can still complete, and later
# would wait on a flow that has expired. An install's signed state lasts 15
# minutes, and redeeming it starts GitHub's authorization step, which lasts 10
# more. Linking an existing installation is the authorization step (10
# minutes), then a confirmation on the dashboard (3).
_POLL_INTERVAL_SECONDS = 2.5
# Answers no later poll can change, by status, and how many in a row it takes
# to believe one: this CLI's sign-in is no longer valid (401), or its user may
# no longer see the organization (403). A 401 can also follow a token refresh
# that failed on a network blip, which the next poll tries again, so it takes
# two. Anything else, a dropped connection or a 5xx, is polled through. "Not
# linked yet" is no error at all: the API's 404 comes back as no installation.
_POLL_REFUSALS_TO_GIVE_UP = {401: 2, 403: 1}
_COMMIT_GRACE_SECONDS = 60
_INSTALL_TIMEOUT_SECONDS = (15 + 10) * 60 + _COMMIT_GRACE_SECONDS
_LINK_TIMEOUT_SECONDS = (10 + 3) * 60 + _COMMIT_GRACE_SECONDS


def _installation_rows(installation: dict) -> list[tuple[str, str]]:
    suspended_at = installation.get("suspendedAt")
    return [
        ("Account", str(installation.get("githubAccountLogin", "—"))),
        ("Account type", str(installation.get("githubAccountType", "—"))),
        ("Installation ID", str(installation.get("githubInstallationId", "—"))),
        ("Status", f"Suspended ({suspended_at})" if suspended_at else "Active"),
        ("Manage on GitHub", installation_settings_url(installation)),
    ]


def _print_installation(installation: dict) -> None:
    rows = _installation_rows(installation)
    if not console.rich_output:
        console.print_records(["Field", "Value"], rows)
        return
    table = Table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


def _report_already_connected(org: str | None, installation: dict) -> None:
    if console.json_output:
        console.output_json({"installation": installation, "connected": True})
        return
    console.print(
        f"[yellow]Organization '{org}' is already connected to GitHub "
        f"({installation.get('githubAccountLogin')}).[/yellow]\n"
        "[dim]Change which repositories the App can see from GitHub, or run "
        f"[bold]{PIPECAT_CLI_NAME} github disconnect[/bold] first to connect a "
        "different account.[/dim]"
    )
    _print_installation(installation)


@github_cli.command(
    name="connect",
    short_help="Connect this organization to GitHub",
    help=(
        "Connect this organization to GitHub by installing the Pipecat Cloud App, "
        "or with --existing, by linking an installation already on GitHub"
    ),
)
@synchronizer.create_blocking
@requires_login
async def connect(
    organization: str = typer.Option(None, "--organization", "-o"),
    existing: bool = typer.Option(
        False,
        "--existing",
        help=(
            "Link an installation already on GitHub: one you installed or had "
            "an owner approve, while still a member of the GitHub organization "
            "it is on, or one on your personal account or a GitHub "
            "organization you own"
        ),
    ),
):
    org = organization or config.get("org")

    linked, error = await API.github_installation(org=org)
    if error:
        raise typer.Exit(1)
    if linked:
        _report_already_connected(org, linked)
        return

    # Fetched per attempt, never cached: the URL carries a single-use flow that
    # the dashboard redeems.
    if existing:
        # bubble_error: another member can link the org between the check above
        # and this call, and that is the same answer the check gives.
        data, error = await API.bubble_error().github_link_url(org=org)
        if isinstance(error, dict) and error.get("reason") == "org_has_one":
            linked, error = await API.github_installation(org=org)
            if linked:
                _report_already_connected(org, linked)
                return
            if not error:
                console.error(
                    f"The GitHub connection for organization '{org}' changed while this "
                    f"ran. Run [bold]{PIPECAT_CLI_NAME} github connect --existing[/bold] again."
                )
            raise typer.Exit(1)
        if error:
            API.print_error()
            raise typer.Exit(1)
        verb, timeout = "authorize", _LINK_TIMEOUT_SECONDS
    else:
        data, error = await API.github_install_url(org=org)
        if error:
            raise typer.Exit(1)
        verb, timeout = "install", _INSTALL_TIMEOUT_SECONDS
    if data is None:
        # No error and no body: no usable response came back (a dropped
        # connection, a DNS or TLS failure, or a non-JSON page from a proxy),
        # which the API client reports as nothing.
        console.error(
            f"Could not reach Pipecat Cloud to {verb} the GitHub App. Check your "
            "connection and try again."
        )
        raise typer.Exit(1)
    flow_url = data.get("url")
    if not flow_url:
        console.error(f"The API did not return a GitHub {verb} URL")
        raise typer.Exit(1)

    # Imported at call time: this is the only place github.py needs auth.py,
    # and a module-level import would pull the whole OAuth module into every
    # GitHub command.
    from pipecatcloud.cli.commands.auth import _open_url

    if not console.json_output:
        console.print(f"[dim]Opening browser to {verb} the Pipecat Cloud GitHub App...[/dim]")
    opened = _open_url(flow_url)
    if not opened and not console.json_output:
        console.print(
            f"\nOpen this URL in your browser to {verb} the App:\n[blue]{flow_url}[/blue]\n"
        )
    elif not opened:
        # In json mode stdout is reserved for the final payload, so the URL a
        # headless caller needs goes to the console's stream (stderr).
        console.print(f"Open this URL to {verb} the App: {flow_url}")
    # The browser half runs on the dashboard, under its own sign-in, and only
    # for the user this terminal is logged in as. This terminal can't see the
    # dashboard refuse, so it says where to look. In json mode too, where it
    # goes to stderr with the URL above: a headless caller needs it more, not
    # less.
    finish = ", where you confirm the installation to link" if existing else " to finish"
    console.print(
        f"[dim]GitHub returns you to the Pipecat Cloud dashboard{finish}. "
        "Sign in there as the same Pipecat Cloud user as this CLI if it asks. If the "
        "dashboard shows an error, press Ctrl+C here and follow what it says.[/dim]"
    )

    deadline = time.monotonic() + timeout
    installation = None
    refused = False
    refusals_in_a_row = 0
    with console.status(
        "[dim]Waiting for GitHub. Finish in your browser...[/dim]",
        spinner="dots",
    ):
        while time.monotonic() < deadline:
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            # bubble_error: a transient blip mid-poll should retry, not print a
            # panel per tick and leave the user staring at a wall of errors.
            found, error = await API.bubble_error().github_installation(org=org)
            if found:
                installation = found
                break
            status = API.error_status if error else None
            needed = None if status is None else _POLL_REFUSALS_TO_GIVE_UP.get(status)
            if needed is None:
                refusals_in_a_row = 0
                continue
            refusals_in_a_row += 1
            if refusals_in_a_row >= needed:
                refused = True
                break

    if refused:
        # Printed once the spinner is gone.
        API.print_error()
        raise typer.Exit(1)
    if not installation:
        link_existing = f"[bold]{PIPECAT_CLI_NAME} github connect --existing[/bold]"
        if existing:
            retry = f"Otherwise run {link_existing} again."
        else:
            # A member who cannot install on the GitHub account sends an owner a
            # request instead, and nothing links until they come back to it.
            retry = (
                f"If GitHub sent an owner a request to approve, run {link_existing} once "
                f"they have. Otherwise run [bold]{PIPECAT_CLI_NAME} github connect[/bold] "
                "again."
            )
        console.error(
            "Timed out waiting for the GitHub connection.\n"
            f"[dim]If you finished in the browser, run [bold]{PIPECAT_CLI_NAME} github status"
            f"[/bold] to check. {retry}[/dim]"
        )
        raise typer.Exit(1)

    if console.json_output:
        console.output_json({"installation": installation, "connected": True})
        return
    console.success(f"Connected '{org}' to GitHub ({installation.get('githubAccountLogin')})")
    _print_installation(installation)


@github_cli.command(name="status", help="Show the organization's GitHub connection")
@synchronizer.create_blocking
@requires_login
async def status(
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    with console.status("[dim]Fetching GitHub connection...[/dim]", spinner="dots"):
        installation, error = await API.github_installation(org=org)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"installation": installation})
        return

    if not installation:
        console.print(
            f"[yellow]Organization '{org}' is not connected to GitHub.[/yellow]\n"
            f"[dim]Run [bold]{PIPECAT_CLI_NAME} github connect[/bold] to connect it. If you "
            "installed the App on GitHub yourself, or an owner approved your request, run "
            f"[bold]{PIPECAT_CLI_NAME} github connect --existing[/bold] instead.[/dim]"
        )
        return

    _print_installation(installation)
    if installation.get("suspendedAt"):
        console.print(
            "\n[yellow]The GitHub App is suspended for this account. Reinstate it from "
            "GitHub to resume deploys.[/yellow]"
        )


@github_cli.command(name="disconnect", help="Disconnect this organization from GitHub")
@synchronizer.create_blocking
@requires_login
async def disconnect(
    organization: str = typer.Option(None, "--organization", "-o"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
):
    org = organization or config.get("org")

    if not yes:
        console.require_interactive("--yes")
        if not await questionary.confirm(
            f"Disconnect '{org}' from GitHub? This removes the installation and every "
            "repository link in the organization. Linked agents stop auto-deploying; "
            "what is already running is not touched."
        ).ask_async():
            console.cancel()
            raise typer.Exit(1)

    with console.status("[dim]Disconnecting from GitHub...[/dim]", spinner="dots"):
        _, error = await API.github_disconnect(org=org)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"disconnected": True, "organization": org})
        return
    console.success(
        f"Disconnected '{org}' from GitHub.\n"
        "[dim]The App is still installed on GitHub; uninstall it there to revoke access "
        "entirely.[/dim]"
    )


@github_cli.command(name="repos", help="List repositories the GitHub App can access")
@synchronizer.create_blocking
@requires_login
async def repos(
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    with console.status("[dim]Fetching repositories...[/dim]", spinner="dots"):
        repositories, error = await API.github_repositories(org=org)
        if error:
            raise typer.Exit(1)

    # Before the empty-result return: an empty set must still emit a
    # well-formed JSON payload rather than zero bytes on stdout.
    if console.json_output:
        console.output_json({"repositories": repositories or []})
        return

    if not repositories:
        console.print(
            "[yellow]The GitHub App cannot see any repositories.[/yellow]\n"
            "[dim]Grant it access to repositories from GitHub, then try again.[/dim]"
        )
        return

    rows = [
        (
            repo.get("fullName", ""),
            repo.get("defaultBranch", ""),
            "private" if repo.get("private") else "public",
        )
        for repo in repositories
    ]

    if not console.rich_output:
        console.print_records(["Repository", "Default branch", "Visibility"], rows)
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Repository")
    table.add_column("Default branch")
    table.add_column("Visibility")
    for row in rows:
        table.add_row(*row)
    console.print(table)


@github_cli.command(name="branches", help="List branches for a repository")
@synchronizer.create_blocking
@requires_login
async def branches(
    repo: str = typer.Argument(..., help="Repository as 'owner/repo'"),
    query: str = typer.Option(
        None,
        "--query",
        "-q",
        help="Prefix to search for. Reaches branches past the plain list's cap.",
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    if not is_valid_repo_full_name(repo):
        console.error(f"Invalid repository '{repo}'. Expected the form 'owner/repo'.")
        raise typer.Exit(1)

    with console.status(f"[dim]Fetching branches for [bold]{repo}[/bold]...[/dim]", spinner="dots"):
        branch_names, error = await API.github_branches(org=org, repo_full_name=repo, query=query)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"repository": repo, "branches": branch_names or []})
        return

    if not branch_names:
        console.print(f"[yellow]No branches found for '{repo}'[/yellow]")
        return

    if not console.rich_output:
        console.print_records(["Branch"], [(name,) for name in branch_names])
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Branch")
    for name in branch_names:
        table.add_row(name)
    console.print(table)
