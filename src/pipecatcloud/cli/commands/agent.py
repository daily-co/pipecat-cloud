#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
import statistics
from enum import Enum

import aiohttp
import questionary
import typer
from loguru import logger
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console, format_duration, format_timestamp, calculate_percentiles
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config
from pipecatcloud._utils.deploy_utils import with_deploy_config, DeployConfigParams

agent_cli = typer.Typer(name="agent", help="Agent management", no_args_is_help=True)


# ----- Agent Commands -----


@agent_cli.command(name="list", help="List agents in an organization.")
@synchronizer.create_blocking
@requires_login
async def list(
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization to list agents for"
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching agents for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.agents(org=org)

        if error:
            return typer.Exit()

        if not data or len(data) == 0:
            console.error(
                f"[red]No agents found for namespace / organization '{org}'[/red]\n\n"
                f"[dim]Please deploy an agent first using[/dim] [bold cyan]{PIPECAT_CLI_NAME} deploy[/bold cyan]")
            return typer.Exit(1)

        else:
            table = Table(show_header=True, show_lines=True, border_style="dim", box=box.SIMPLE)
            table.add_column("Name")
            table.add_column("Agent ID")
            table.add_column("Active Deployment ID")
            table.add_column("Created At")
            table.add_column("Updated At")

            for service in data:
                table.add_row(
                    f"[bold]{service['name']}[/bold]",
                    service["id"],
                    service["activeDeploymentId"],
                    service["createdAt"],
                    service["updatedAt"],
                )

            console.success(
                table, title=f"Agents for organization: {org}", title_extra=f"{len(data)} results"
            )


@agent_cli.command(name="status", help="Get status of agent deployment")
@synchronizer.create_blocking
@requires_login
async def status(
    agent_name: str = typer.Argument(help="Name of the agent to get status of e.g. 'my-agent'"),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization to get status of agent for"
    ),
):
    org = organization or config.get("org")

    with Live(
        console.status(f"[dim]Looking up agent with name {agent_name}[/dim]", spinner="dots")
    ) as live:
        data, error = await API.agent(agent_name=agent_name, org=org, live=live)

        logger.debug(f"Agent status: {data}")

        live.stop()

        if error:
            return typer.Exit()

        if not data:
            console.error(f"No deployment data found for agent with name '{agent_name}'")
            return typer.Exit()

        # Deployment info

        deployment_table = Table(show_header=False, show_lines=False, box=box.SIMPLE)
        deployment_table.add_column("Key")
        deployment_table.add_column("Value")
        deployment_table.add_row(
            "[bold]Active Session Count:[/bold]",
            str(data.get("activeSessionCount", "N/A")),
        )
        deployment_table.add_row(
            "[bold]Image:[/bold]",
            str(data.get("deployment", {}).get("manifest", {}).get("spec", {}).get("image", "N/A")),
        )
        deployment_table.add_row(
            "[bold]Active Deployment ID:[/bold]",
            str(data.get("activeDeploymentId", "N/A")),
        )
        deployment_table.add_row(
            "[bold]Created At:[/bold]",
            str(data.get("createdAt", "N/A")),
        )
        deployment_table.add_row(
            "[bold]Updated At:[/bold]",
            str(data.get("updatedAt", "N/A")),
        )

        # Autoscaling info
        autoscaling_data = data.get("autoScaling", None)
        if autoscaling_data:
            scaling_renderables = [
                Panel(
                    f"[bold]Minimum Agents[/bold]\n{autoscaling_data.get('minAgents', 0)}",
                    expand=True,
                ),
                Panel(
                    f"[bold]Maximum Agents[/bold]\n{autoscaling_data.get('maxAgents', 0)}",
                    expand=True,
                ),
            ]
            scaling_panel = Panel(
                Columns(scaling_renderables),
                title="[bold]Scaling configuration:[/bold]",
                title_align="left",
                border_style="dim",
            )

        # Error status
        error_panel = None
        errors = data.get("errors", [])
        if errors and len(errors) > 0:
            error_table = Table(show_header=False, show_lines=False, box=box.SIMPLE)
            error_table.add_column("Code")
            error_table.add_column("Message")
            for error in errors:
                error_table.add_row(
                    f"[bold red]{error['code']}[/bold red]",
                    f"[red]{error.get('message', None) or error.get('error', 'Unknown error')}[/red]",
                )
            error_panel = Panel(
                error_table,
                title="[bold red]Agent errors:[/bold red]",
                title_align="left",
                border_style="red",
            )

        color = "bold green" if data["ready"] else "bold yellow"
        subtitle = (
            f"[dim]Start a new active session with[/dim] [bold cyan]{PIPECAT_CLI_NAME} agent start {agent_name}[/bold cyan]"
            if data["ready"]
            else f"[dim]For more information check logs with[/dim] [bold cyan]{PIPECAT_CLI_NAME} agent logs {agent_name}[/bold cyan]"
        )
        console.print(
            Panel(
                Group(
                    deployment_table,
                    scaling_panel if scaling_panel else "",
                    Panel(
                        f"[{color}]Health: {'Ready' if data['ready'] else 'Stopped'}[/]",
                        border_style="green" if data["ready"] else "yellow",
                        expand=False,
                    ),
                    error_panel if error_panel else "",
                ),
                title=f"Status for agent [bold]{agent_name}[/bold]",
                title_align="left",
                subtitle_align="left",
                subtitle=subtitle,
            )
        )


@agent_cli.command(name="sessions", help="List active sessions for an agent")
@synchronizer.create_blocking
@requires_login
@with_deploy_config
async def sessions(
    deploy_config=typer.Option(None, hidden=True),
    agent_name: str = typer.Argument(
        None,
        help="Name of the agent to list sessions for e.g. 'my-agent'",
        show_default=False
    ),
    session_id: str = typer.Option(
        None,
        "--id",
        "-i",
        help="Session ID to filter by",
    ),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization to list sessions for"
    ),
):
    org = organization or config.get("org")
    agent_name = agent_name or deploy_config.agent_name

    with Live(
        console.status(f"[dim]Looking up agent with name '{agent_name}'[/dim]", spinner="dots")
    ) as live:
        data, error = await API.agent_sessions(agent_name=agent_name, org=org, live=live)

        live.stop()

        if error:
            return typer.Exit()

        if not data:
            console.error(f"No session data found for agent with name '{agent_name}'")
            return typer.Exit()

        sessions_list = data.get("sessions", [])
        total_sessions = len(sessions_list)

        completed_sessions = [s for s in sessions_list if s.get("endedAt")]

        durations = []
        for session in completed_sessions:
            try:
                from datetime import datetime
                created_at = datetime.fromisoformat(session["createdAt"].replace('Z', '+00:00'))
                ended_at = datetime.fromisoformat(session["endedAt"].replace('Z', '+00:00'))
                duration_seconds = (ended_at - created_at).total_seconds()
                durations.append(duration_seconds)
            except BaseException:
                continue

        bot_start_times = [s["botStartSeconds"]
                           for s in sessions_list if s.get("botStartSeconds") is not None]
        bot_start_metrics = calculate_percentiles(bot_start_times)
        duration_metrics = calculate_percentiles(durations)
        cold_starts_count = sum(1 for s in sessions_list if s.get("coldStart") is True)
        cold_start_percent = cold_starts_count / total_sessions * 100

        if duration_metrics and bot_start_metrics and total_sessions > 0:
            metric_renderables = [
                Panel(
                    f"[bold]Total Sessions:[/bold]\n{total_sessions}\n ",
                    expand=True,
                ),
                Panel(
                    f"[bold]Average Duration:[/bold]\n{duration_metrics[0]:.1f}s\n[dim](p5: {duration_metrics[1]:.1f}s, p95: {duration_metrics[2]:.1f}s)[/dim]",
                    expand=True,
                ),
                Panel(
                    f"[bold]Bot Start Time:[/bold]\n{bot_start_metrics[0]:.1f}s\n[dim](p5: {bot_start_metrics[1]:.1f}s, p95: {bot_start_metrics[2]:.1f}s)[/dim]",
                    expand=True,
                ),
                Panel(
                    f"[bold]Cold Starts:[/bold]\n{cold_starts_count}/{total_sessions}\n[dim]({cold_start_percent:.1f}%)[/dim]",
                    expand=True,
                ),
            ]

        table = Table(show_header=True, show_lines=True, border_style="dim", box=box.SIMPLE)
        table.add_column("Session ID")
        table.add_column("Created At")
        table.add_column("Ended At")
        table.add_column("Duration")
        table.add_column("Status")
        table.add_column("Bot Start Time")
        table.add_column("Cold Start")

        for session in data.get("sessions", []):
            if session_id and session["sessionId"] != session_id:
                continue

            session_duration = format_duration(
                session["createdAt"], session["endedAt"]) or "[dim]N/A[/dim]"
            status = session.get("completionStatus", "")
            if session["endedAt"]:
                if status == "500":
                    status_display = "[red]Error (500)[/red]"
                else:
                    status_display = "Complete"
            else:
                status_display = "[yellow]Active[/yellow]"

            is_cold_start = session["coldStart"] is True
            row_style = "on red" if is_cold_start else ""

            row_data = [
                session["sessionId"],
                format_timestamp(
                    session["createdAt"]),
                format_timestamp(
                    session["endedAt"]) if session["endedAt"] else "[dim]N/A[/dim]",
                session_duration,
                status_display,
                f"{session['botStartSeconds']}s" if session["botStartSeconds"] is not None else "[dim]N/A[/dim]",
                "[red]Yes[/red]" if session["coldStart"] is True else "No" if session["coldStart"] is False else "[dim]N/A[/dim]",
            ]

            if is_cold_start:
                row_data = [f"[{row_style}]{cell}[/]" for cell in row_data]

            table.add_row(*row_data)

        console.success(
            Group(
                Columns(
                    metric_renderables,
                    equal=True) if metric_renderables and not session_id else "",
                table),
            title=f"Session data for agent {agent_name} [dim]({org})[/dim]",
        )


@agent_cli.command(name="scale", help="Modify agent runtime configuration")
@synchronizer.create_blocking
@requires_login
async def scale():
    console.error("Not implemented")


class LogFormat(str, Enum):
    TEXT = "TEXT"
    JSON = "JSON"


class LogLevel(str, Enum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class LogLevelColors(str, Enum):
    DEBUG = "blue"
    INFO = "green"
    WARNING = "yellow"
    ERROR = "red"
    CRITICAL = "bold red"


@agent_cli.command(name="logs", help="Get logs for the given agent.")
@synchronizer.create_blocking
@requires_login
async def logs(
        agent_name: str,
        organization: str = typer.Option(
            None,
            "--organization",
            "-o",
            help="Organization to get status of agent for"),
        level: LogLevel = typer.Option(
            None,
            "--level",
            "-l",
            help="Level of logs to get"),
        format: LogFormat = typer.Option(
            LogFormat.TEXT,
            "--format",
            "-f",
            help="Logs format"),
        limit: int = typer.Option(
            100,
            "--limit",
            "-n",
            help="Number of logs to get"),
        deployment_id: str = typer.Option(
            None,
            "--deployment",
            "-d",
            help="Filter logs by deployment ID"),
):
    org = organization or config.get("org")

    status_text = "agent" if not deployment_id else f"deployment ({deployment_id})"

    with console.status(
        f"[dim]Fetching logs for {status_text}: [bold]'{agent_name}'[/bold] with severity: [bold cyan]{level.value if level else 'ALL'}[/bold cyan][/dim]",
        spinner="dots",
    ):
        data, error = await API.agent_logs(agent_name=agent_name, org=org, limit=limit, deployment_id=deployment_id)

        if not data or not data.get("logs"):
            console.print("[dim]No logs found for agent[/dim]")
            return typer.Exit(1)

    for log in data["logs"]:
        log_data = log.get("log", "")
        if log_data:
            timestamp = format_timestamp(log.get("timestamp", ""))
            severity = LogLevel.INFO
            for log_severity in LogLevel:
                if log_severity.value in log_data.upper():
                    severity = log_severity
                    break
            # filter out any messages that do not match our log level
            if level and severity.value != level.value:
                continue

            if format == LogFormat.TEXT:
                color = getattr(LogLevelColors, severity, LogLevelColors.DEBUG).value
                console.print(Text(timestamp, style="bold dim"), end=" ")
                console.print(Text(log_data, style=color))
            elif format == LogFormat.JSON:
                line = {"timestamp": timestamp, "log": log_data}
                console.print(Text(json.dumps(line, ensure_ascii=False), style="gray"))


@agent_cli.command(name="delete", help="Delete an agent.")
@synchronizer.create_blocking
@requires_login
async def delete(
    agent_name: str,
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to delete agent from",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Bypass prompt for confirmation",
    ),
):
    org = organization or config.get("org")

    if not force:
        if not await questionary.confirm(
            "Are you sure you want to delete this agent? Note: active sessions will not be interrupted and will continue to run until completion."
        ).ask_async():
            console.print("[bold]Aborting delete request[/bold]")
            return typer.Exit(1)

    with console.status(f"[dim]Deleting agent: [bold]'{agent_name}'[/bold][/dim]", spinner="dots"):
        data, error = await API.agent_delete(agent_name=agent_name, org=org)

        if error:
            return typer.Exit(1)

        if not data:
            console.error(f"Agent '{agent_name}' not found in namespace / organization '{org}'")
            return typer.Exit(1)

        console.success(f"Agent '{agent_name}' deleted successfully")


@agent_cli.command(name="deployments", help="Get deployments for an agent.")
@synchronizer.create_blocking
@requires_login
async def deployments(
    agent_name: str,
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to get deployments for",
    ),
):
    token = config.get("token")
    org = organization or config.get("org")

    error_code = None

    try:
        with console.status(
            f"[dim]Fetching deployments for agent: [bold]'{agent_name}'[/bold][/dim]",
            spinner="dots",
        ):
            async with aiohttp.ClientSession() as session:
                response = await session.get(
                    f"{API.construct_api_url('services_deployments_path').format(org=org, service=agent_name)}",
                    headers={"Authorization": f"Bearer {token}"},
                )
            if response.status != 200:
                error_code = str(response.status)
                response.raise_for_status()

            data = await response.json()

            table = Table(
                show_header=True,
                show_lines=True,
                border_style="dim",
                box=box.SIMPLE,
            )
            table.add_column("ID")
            table.add_column("Node Type")
            table.add_column("Image")
            table.add_column("Created At")
            table.add_column("Updated At")

            for deployment in data["deployments"]:
                table.add_row(
                    deployment["id"],
                    deployment["manifest"]["spec"]["dailyNodeType"],
                    deployment["manifest"]["spec"]["image"],
                    deployment["createdAt"],
                    deployment["updatedAt"],
                )

            console.print(
                Panel(
                    table,
                    title=f"[bold]Deployments for agent: {agent_name}[/bold]",
                    title_align="left",
                )
            )
    except Exception as e:
        logger.debug(e)
        console.api_error(error_code, f"Unable to get deployments for {agent_name}")


@agent_cli.command(name="start", help="Start an agent instance")
@synchronizer.create_blocking
@requires_login
@with_deploy_config
async def start(
    deploy_config=typer.Option(None, hidden=True),
    agent_name: str = typer.Argument(None, help="Name of the agent to start e.g. 'my-agent'"),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Bypass prompt for confirmation",
        rich_help_panel="Start Configuration",
    ),
    api_key: str = typer.Option(
        None,
        "--api-key",
        "-k",
        help="Public API key to use for starting agent",
        rich_help_panel="Start Configuration",
    ),
    data: str = typer.Option(
        None,
        "--data",
        "-d",
        help="Data to pass to the agent (stringified JSON)",
        rich_help_panel="Start Configuration",
    ),
    use_daily: bool = typer.Option(
        False,
        "--use-daily",
        "-D",
        help="Create a Daily WebRTC session for the agent",
        rich_help_panel="Start Configuration",
    ),
    daily_properties: str = typer.Option(
        None,
        "--daily-properties",
        "-p",
        help="Daily room properties (stringified JSON)",
        rich_help_panel="Start Configuration",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization which the agent belongs to",
    ),
):
    org = organization or config.get("org")

    default_public_api_key = api_key or config.get("default_public_key")
    default_public_api_key_name = (
        "CLI provided" if api_key else config.get("default_public_key_name")
    )

    # Load values from deployment config file (if one exists)
    partial_config = deploy_config or DeployConfigParams()

    # Get agent name from pcc-deploy.toml if not provided
    if not agent_name:
        default_agent_name = partial_config.agent_name

        if not default_agent_name:
            console.error("No target agent name provided")
            return typer.Exit(1)

        agent_name = default_agent_name

    if not default_public_api_key:
        console.print(
            Panel(
                f"No public API key provided. Please provide a public API key using the --api-key flag or set a default using [bold cyan]{PIPECAT_CLI_NAME} organizations keys use[/bold cyan].\n\n"
                f"If you have not yet created a public API key, you can do so by running [bold cyan]{PIPECAT_CLI_NAME} organizations keys create[/bold cyan].",
                title="Public API Key Required",
                title_align="left",
                border_style="yellow",
            ))

        return typer.Exit(1)

    # Validate daily_properties JSON if provided
    if use_daily and daily_properties:
        try:
            json.loads(daily_properties)
        except json.JSONDecodeError as e:
            console.error(f"Invalid JSON format for Daily room properties: {daily_properties}")
            console.print(f"[dim]JSON error: {str(e)}[/dim]")
            return typer.Exit(1)

    # Confirm start request
    if not force:
        daily_props_display = daily_properties or "None"
        # Truncate display of daily properties if too long
        if daily_properties and len(daily_properties) > 80:
            daily_props_display = daily_properties[:77] + "..."

        console.print(
            Panel(
                f"Agent Name: {agent_name}\n"
                f"Public API Key: {default_public_api_key_name} [dim]{default_public_api_key}[/dim]\n"
                f"Use Daily: {use_daily}\n"
                f"Daily Properties: {daily_props_display}\n"
                f"Data: {data}",
                title=f"[bold]Start Request for agent: {agent_name}[/bold]",
                title_align="left",
                border_style="yellow",
            ))
        if not await questionary.confirm(
            "Are you sure you want to start an active session for this agent?"
        ).ask_async():
            console.print("[bold]Aborting start request[/bold]")
            return typer.Exit(1)

    with Live(
        console.status("[dim]Checking agent health...[/dim]", spinner="dots"), refresh_per_second=4
    ) as live:
        health_data, error = await API.agent(agent_name=agent_name, org=org, live=live)
        if not health_data or not health_data["ready"]:
            live.stop()
            console.error(
                f"Agent '{agent_name}' does not exist or is not in a healthy state. Please check the agent status with [bold cyan]{PIPECAT_CLI_NAME} agent status {agent_name}[/bold cyan]"
            )
            return typer.Exit(1)

        live.update(
            console.status(
                f"[dim]Agent '{agent_name}' is healthy, sending start request...[/dim]",
                spinner="dots",
            )
        )

        data, error = await API.start_agent(
            agent_name=agent_name,
            api_key=default_public_api_key,
            use_daily=use_daily,
            data=data,
            daily_properties=daily_properties,
            live=live,
        )

        if error:
            live.stop()
            # Error is displayed from start_agent create_api_method wrapper
            return typer.Exit(1)

        live.stop()

        console.success(f"Agent '{agent_name}' started successfully")
        if use_daily and isinstance(data, dict):
            daily_room = data.get("dailyRoom")
            daily_token = data.get("dailyToken")
            if daily_room:
                url = f"{daily_room}?t={daily_token}"
                console.print("\nJoin your session by visiting the link below:")
                console.print(f"[link={url}]{url}[/link]")
