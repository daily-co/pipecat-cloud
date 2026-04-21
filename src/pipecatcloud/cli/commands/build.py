#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Build commands for Pipecat Cloud CLI.
"""

import typer
from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.build_utils import BuildStatus, format_size
from pipecatcloud._utils.console_utils import console, format_timestamp
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

build_cli = typer.Typer(name="build", help="Cloud build management", no_args_is_help=True)


def _format_build_status(status: str) -> str:
    """Format build status with color."""
    status_colors = {
        BuildStatus.PENDING: "yellow",
        BuildStatus.BUILDING: "cyan",
        BuildStatus.SUCCESS: "green",
        BuildStatus.FAILED: "red",
        BuildStatus.TIMEOUT: "red",
    }
    color = status_colors.get(status, "white")
    return f"[{color}]{status}[/{color}]"


def _format_duration(seconds: int | None) -> str:
    """Format duration in seconds as human-readable string."""
    if seconds is None:
        return "[dim]N/A[/dim]"
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        return f"{hours}h {minutes}m"


@build_cli.command(name="logs", help="View logs for a cloud build")
@synchronizer.create_blocking
@requires_login
async def logs(
    build_id: str = typer.Argument(help="Build ID to get logs for"),
    limit: int = typer.Option(
        500,
        "--limit",
        "-n",
        help="Number of log lines to retrieve (max 10000)",
        min=1,
        max=10000,
    ),
    organization: str | None = typer.Option(None, "--organization", "-o", help="Organization"),
):
    """View logs for a cloud build."""
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching logs for build: [bold]{build_id}[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.build_logs(org=org, build_id=build_id, limit=limit)

        if error:
            return typer.Exit(1)

        if not data:
            console.error(f"Build '{build_id}' not found")
            return typer.Exit(1)

    logs_list = data.get("logs", [])

    if not logs_list:
        console.print("[dim]No logs available for this build yet[/dim]")
        console.print(
            f"\n[dim]The build may still be starting. "
            f"Check status with:[/dim] [bold]{PIPECAT_CLI_NAME} build status {build_id}[/bold]"
        )
        return

    # Display build ID header
    console.print(
        Panel(
            f"[bold]Build ID:[/bold] {build_id}\n[bold]Lines:[/bold] {len(logs_list)}",
            title="Build Logs",
            title_align="left",
            border_style="dim",
        )
    )

    # Display logs
    for log_entry in logs_list:
        # Handle both string logs and dict logs
        if isinstance(log_entry, str):
            message = log_entry
            timestamp = ""
        else:
            message = log_entry.get("message", log_entry.get("log", str(log_entry)))
            timestamp = log_entry.get("timestamp", "")

        # Color based on content
        style = ""
        if "error" in message.lower() or "failed" in message.lower():
            style = "red"
        elif "warning" in message.lower():
            style = "yellow"
        elif "success" in message.lower() or "complete" in message.lower():
            style = "green"

        if timestamp:
            formatted_ts = format_timestamp(timestamp)
            console.print(Text(f"{formatted_ts} ", style="dim"), end="")

        if style:
            console.print(Text(message, style=style))
        else:
            console.print(message)


@build_cli.command(name="status", help="Get status of a cloud build")
@synchronizer.create_blocking
@requires_login
async def status(
    build_id: str = typer.Argument(help="Build ID to check"),
    organization: str | None = typer.Option(None, "--organization", "-o", help="Organization"),
):
    """Get status of a cloud build."""
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching build status: [bold]{build_id}[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.build_get(org=org, build_id=build_id)

        if error:
            return typer.Exit(1)

        if not data:
            console.error(f"Build '{build_id}' not found")
            return typer.Exit(1)

    build = data.get("build", data)

    # Build info table
    info_lines = [
        f"[bold]Build ID:[/bold] {build.get('id', 'N/A')}",
        f"[bold]Status:[/bold] {_format_build_status(build.get('status', 'unknown'))}",
        f"[bold]Region:[/bold] {build.get('region', 'N/A')}",
        f"[bold]Context Hash:[/bold] {build.get('contextHash', 'N/A')}",
        f"[bold]Dockerfile:[/bold] {build.get('dockerfilePath', 'Dockerfile')}",
        f"[bold]Created:[/bold] {format_timestamp(build.get('createdAt', ''))}",
    ]

    if build.get("startedAt"):
        info_lines.append(f"[bold]Started:[/bold] {format_timestamp(build.get('startedAt'))}")

    if build.get("completedAt"):
        info_lines.append(f"[bold]Completed:[/bold] {format_timestamp(build.get('completedAt'))}")

    if build.get("buildDurationSeconds"):
        info_lines.append(
            f"[bold]Duration:[/bold] {_format_duration(build.get('buildDurationSeconds'))}"
        )

    if build.get("contextSizeBytes"):
        info_lines.append(
            f"[bold]Context Size:[/bold] {format_size(int(build['contextSizeBytes']))}"
        )

    if build.get("imageSizeBytes"):
        info_lines.append(f"[bold]Image Size:[/bold] {format_size(int(build['imageSizeBytes']))}")

    if build.get("errorMessage"):
        info_lines.append(f"\n[bold red]Error:[/bold red] {build.get('errorMessage')}")

    status_val = build.get("status", "unknown")
    if status_val == BuildStatus.SUCCESS:
        border_style = "green"
    elif status_val in (BuildStatus.FAILED, BuildStatus.TIMEOUT):
        border_style = "red"
    elif status_val == BuildStatus.BUILDING:
        border_style = "cyan"
    else:
        border_style = "yellow"

    console.print(
        Panel(
            "\n".join(info_lines),
            title="Build Status",
            title_align="left",
            border_style=border_style,
        )
    )

    # Show helpful commands based on status
    if status_val in (BuildStatus.FAILED, BuildStatus.TIMEOUT):
        console.print(
            f"\n[dim]View full logs:[/dim] [bold]{PIPECAT_CLI_NAME} build logs {build_id}[/bold]"
        )
    elif status_val in (BuildStatus.PENDING, BuildStatus.BUILDING):
        console.print("\n[dim]Build in progress. Check again or view logs:[/dim]")
        console.print(f"  [bold]{PIPECAT_CLI_NAME} build status {build_id}[/bold]")
        console.print(f"  [bold]{PIPECAT_CLI_NAME} build logs {build_id}[/bold]")


@build_cli.command(name="list", help="List recent cloud builds")
@synchronizer.create_blocking
@requires_login
async def list_builds(
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        help="Number of builds to list",
        min=1,
        max=100,
    ),
    status_filter: str | None = typer.Option(
        None,
        "--status",
        "-s",
        help="Filter by status (pending, building, success, failed, timeout)",
    ),
    region_filter: str | None = typer.Option(
        None,
        "--region",
        "-r",
        help="Filter by region",
    ),
    organization: str | None = typer.Option(None, "--organization", "-o", help="Organization"),
):
    """List recent cloud builds."""
    org = organization or config.get("org")

    with console.status("[dim]Fetching builds...[/dim]", spinner="dots"):
        data, error = await API.build_list(
            org=org,
            status=status_filter,
            region=region_filter,
            limit=limit,
        )

        if error:
            return typer.Exit(1)

    builds = data.get("builds", [])
    total = data.get("total", len(builds))

    if not builds:
        console.print("[dim]No builds found[/dim]")
        if status_filter:
            console.print(f"[dim]Filter: status={status_filter}[/dim]")
        return

    # Create table
    table = Table(
        show_header=True,
        show_lines=False,
        border_style="dim",
        box=box.SIMPLE,
        pad_edge=False,
    )
    table.add_column("Build ID", style="cyan", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Region", no_wrap=True)
    table.add_column("Duration", no_wrap=True)
    table.add_column("Created", no_wrap=True)

    for build in builds:
        table.add_row(
            build.get("id", "N/A"),
            _format_build_status(build.get("status", "unknown")),
            build.get("region", "N/A"),
            _format_duration(build.get("buildDurationSeconds")),
            format_timestamp(build.get("createdAt", "")),
        )

    # Display with count info
    showing_text = f"Showing {len(builds)}"
    if total > len(builds):
        showing_text += f" of {total}"
    showing_text += " builds"

    console.success(
        table,
        title="Cloud Builds",
        title_extra=showing_text,
    )

    console.print(
        f"\n[dim]View build details:[/dim] [bold]{PIPECAT_CLI_NAME} build status <build-id>[/bold]"
    )
