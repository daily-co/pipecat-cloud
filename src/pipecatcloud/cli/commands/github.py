#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Org-scoped GitHub App commands (PCC-933).

The connect flow deliberately has no local callback server: GitHub redirects
the browser to the API's own setup callback, which links the installation
server-side, so the terminal only has to open a URL and poll until the link
shows up. That is the same flow the dashboard runs.
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

# Poll cadence while waiting for the install to land. The timeout matches the
# server's signed install-state TTL: giving up sooner would abandon a connect
# that can still complete, and later would wait on a state that has expired.
_POLL_INTERVAL_SECONDS = 2.5
_POLL_TIMEOUT_SECONDS = 15 * 60


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


@github_cli.command(
    name="connect",
    help="Connect this organization to GitHub by installing the Pipecat Cloud App",
)
@synchronizer.create_blocking
@requires_login
async def connect(
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    existing, error = await API.github_installation(org=org)
    if error:
        raise typer.Exit(1)
    if existing:
        if console.json_output:
            console.output_json({"installation": existing, "connected": True})
            return
        console.print(
            f"[yellow]Organization '{org}' is already connected to GitHub "
            f"({existing.get('githubAccountLogin')}).[/yellow]\n"
            "[dim]Change which repositories the App can see from GitHub, or run "
            f"[bold]{PIPECAT_CLI_NAME} github disconnect[/bold] first to connect a "
            "different account.[/dim]"
        )
        _print_installation(existing)
        return

    # Fetched per attempt, never cached: the URL carries a single-use state
    # that the setup callback redeems.
    data, error = await API.github_install_url(org=org)
    if error:
        raise typer.Exit(1)
    install_url = (data or {}).get("url")
    if not install_url:
        console.error("The API did not return a GitHub install URL")
        raise typer.Exit(1)

    # Imported at call time: this is the only place github.py needs auth.py,
    # and a module-level import would pull the whole OAuth module into every
    # GitHub command.
    from pipecatcloud.cli.commands.auth import _open_url

    if not console.json_output:
        console.print("[dim]Opening browser to install the Pipecat Cloud GitHub App...[/dim]")
    opened = _open_url(install_url)
    if not opened and not console.json_output:
        console.print(
            f"\nOpen this URL in your browser to install the App:\n[blue]{install_url}[/blue]\n"
        )
    elif not opened:
        # In json mode stdout is reserved for the final payload, so the URL a
        # headless caller needs goes to the console's stream (stderr).
        console.print(f"Open this URL to install the App: {install_url}")

    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    installation = None
    with console.status(
        "[dim]Waiting for GitHub. Approve the installation in your browser...[/dim]",
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

    if not installation:
        console.error(
            "Timed out waiting for the GitHub App installation.\n"
            f"[dim]If you completed the install, run [bold]{PIPECAT_CLI_NAME} github status[/bold] "
            "to check. Otherwise run connect again.[/dim]"
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
            f"[dim]Run [bold]{PIPECAT_CLI_NAME} github connect[/bold] to connect it.[/dim]"
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
