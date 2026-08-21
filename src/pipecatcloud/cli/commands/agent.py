#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
from enum import Enum

import aiohttp
import questionary
import typer
from loguru import logger
from rich import box
from rich.columns import Columns
from rich.console import Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pipecatcloud.__version__ import version as _cli_version
from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import (
    calculate_percentiles,
    console,
    format_duration,
    format_timestamp,
)
from pipecatcloud._utils.deploy_utils import (
    CONFIG_FILE_OPTION,
    DeployConfigParams,
    GitDeployWait,
    follow_git_deploy,
    format_health_lines,
    report_git_deploy_result,
    with_deploy_config,
)
from pipecatcloud._utils.github_utils import (
    DEFAULT_DOCKERFILE_PATH,
    binding_summary,
    describe_deploy,
    is_running_linked_binding,
    is_valid_branch_name,
    is_valid_repo_full_name,
    ref_to_branch,
    short_sha,
)
from pipecatcloud._utils.regions import get_region_codes, validate_region
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config
from pipecatcloud.constants import Region

agent_cli = typer.Typer(name="agent", help="Agent management", no_args_is_help=True)


def sparkline(values: list[int | float], max_width: int = 50) -> str:
    """Generate Unicode sparkline from values, downsampling if needed."""
    if not values:
        return ""

    # Downsample if too many values
    if len(values) > max_width:
        bucket_size = len(values) / max_width
        downsampled = []
        for i in range(max_width):
            start = int(i * bucket_size)
            end = int((i + 1) * bucket_size)
            bucket = values[start:end]
            downsampled.append(sum(bucket) / len(bucket) if bucket else 0)
        values = downsampled

    blocks = "▁▂▃▄▅▆▇█"
    lo, hi = min(values), max(values)
    if hi == lo:
        return blocks[4] * len(values)  # flat line
    scale = (hi - lo) / 7
    return "".join(blocks[min(7, int((v - lo) / scale))] for v in values)


def format_bytes(b: int) -> str:
    """Format bytes as human-readable string."""
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024 * 1024 * 1024):.1f}GB"
    if b >= 1024 * 1024:
        return f"{b / (1024 * 1024):.0f}MB"
    if b >= 1024:
        return f"{b / 1024:.0f}KB"
    return f"{b}B"


def format_cpu(millicores: int) -> str:
    """Format CPU millicores as human-readable string."""
    return f"{millicores / 1000:.2f} cores"


def _image_display(deployment: dict) -> tuple[str, str]:
    """Label and value for the artifact a deployment runs.

    Cloud-built deployments have their internal ECR image URI redacted by the
    API (sanitizeDeploymentForResponse), which used to render as "Image: N/A".
    The build ID is the customer-facing reference for those, so show it.
    """
    spec = deployment.get("manifest", {}).get("spec", {})
    image = spec.get("image")
    if image:
        return "Image", str(image)
    build_id = deployment.get("buildId")
    if build_id:
        return "Build", str(build_id)
    return "Image", "N/A"


def _git_status_rows(data: dict) -> list[tuple[str, str]]:
    """GitHub rows for `agent status`, empty for an agent with no binding.

    Reports the configured binding and what is actually running as separate
    facts. They disagree in three legitimate states — just linked, repo
    re-pointed, branch changed — and each means nothing from the binding is
    live yet, which is worth saying rather than hiding.
    """
    git = data.get("git")
    if not git:
        return []

    rows = [
        ("GitHub Repository", str(git.get("repoFullName", "—"))),
        ("GitHub Branch", str(git.get("branch", "—"))),
        ("Dockerfile Path", str(git.get("dockerfilePath") or DEFAULT_DOCKERFILE_PATH)),
    ]
    if git.get("subdirectory"):
        rows.append(("Build Subdirectory", str(git["subdirectory"])))
    rows.append(("Auto-deploy On Push", "yes" if git.get("autoDeploy", True) else "no"))

    deployed = data.get("deployedCommit")
    if deployed and deployed.get("sha"):
        running = short_sha(deployed["sha"])
        repo = deployed.get("repoFullName")
        if not is_running_linked_binding(git, deployed):
            # Name the repo/branch it did come from, so "not from the link" is
            # actionable rather than just a warning.
            origin = repo or "another repository"
            ref = deployed.get("ref")
            if ref:
                origin = f"{origin}@{ref_to_branch(ref)}"
            running += f" (from {origin}, not the current link)"
        rows.append(("Running Commit", running))
    else:
        rows.append(("Running Commit", "— (nothing from this link is live yet)"))

    latest = data.get("latestDeploy")
    if latest:
        rows.append(("Latest Deploy", describe_deploy(latest)))
    return rows


# ----- Agent Commands -----


@agent_cli.command(name="list", help="List agents in an organization.")
@synchronizer.create_blocking
@requires_login
async def list_agents(
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization to list agents for"
    ),
    region: Region | None = typer.Option(
        None,
        "--region",
        "-r",
        help="Filter by region",
    ),
):
    org = organization or config.get("org")

    # Validate region if provided
    if region and not await validate_region(region):
        valid_regions = await get_region_codes()
        console.print(
            f"[red]Invalid region '{region}'. Valid regions are: {', '.join(valid_regions)}[/red]"
        )
        raise typer.Exit(1)

    with console.status(
        f"[dim]Fetching agents for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        # include=git adds the binding + source per service in one query; an
        # older API just omits the fields, which renders as "—".
        data, error = await API.agents(org=org, region=region, include=["git"])

        if error:
            raise typer.Exit(1)

        if not data or len(data) == 0:
            console.error(
                f"[red]No agents found for organization '{org}'[/red]\n\n"
                f"[dim]Please deploy an agent first using[/dim] [bold cyan]{PIPECAT_CLI_NAME} deploy[/bold cyan]"
            )
            raise typer.Exit(1)

        elif console.json_output:
            console.output_json({"agents": data})
        elif not console.rich_output:
            console.print_records(
                [
                    "Name",
                    "Region",
                    "Agent ID",
                    "Active Deployment ID",
                    "Created At",
                    "Updated At",
                    "GitHub",
                ],
                [
                    (
                        service["name"],
                        service["region"],
                        service["id"],
                        service["activeDeploymentId"],
                        service["createdAt"],
                        service["updatedAt"],
                        binding_summary(service.get("git")),
                    )
                    for service in data
                ],
                title=f"Agents for organization: {org} ({len(data)} results)",
            )
        else:
            table = Table(show_header=True, show_lines=True, border_style="dim", box=box.SIMPLE)
            table.add_column("Name")
            table.add_column("Region")
            table.add_column("Agent ID")
            table.add_column("Active Deployment ID")
            table.add_column("Created At")
            table.add_column("Updated At")
            table.add_column("GitHub")

            for service in data:
                table.add_row(
                    f"[bold]{service['name']}[/bold]",
                    service["region"],
                    service["id"],
                    service["activeDeploymentId"],
                    service["createdAt"],
                    service["updatedAt"],
                    binding_summary(service.get("git")),
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

    with console.status(
        f"[dim]Looking up agent with name {agent_name}[/dim]", spinner="dots"
    ) as live:
        data, error = await API.agent(agent_name=agent_name, org=org, live=live)

        logger.debug(f"Agent status: {data}")

        live.stop()

        if error:
            raise typer.Exit(1)

        if not data:
            console.error(f"No deployment data found for agent with name '{agent_name}'")
            raise typer.Exit(1)

        if console.json_output:
            console.output_json(data)
            return

        if not console.rich_output:
            spec = data.get("deployment", {}).get("manifest", {}).get("spec", {})
            console.print(f"Agent: {agent_name}")
            console.print(f"Ready: {bool(data.get('ready'))}")
            current_rev = data.get("currentRevision")
            if current_rev:
                console.print(f"Deployment Phase: {current_rev.get('phase', 'Unknown')}")
            console.print(f"Active Session Count: {data.get('activeSessionCount', 'N/A')}")
            image_label, image_value = _image_display(data.get("deployment", {}))
            console.print(f"{image_label}: {image_value}")
            if data.get("agentProfile"):
                console.print(f"Agent Profile: {data['agentProfile']}")
            # Same rows as the rich table below (PCC-1064: plain carries the same
            # information). Architecture is the deployment's pin (PCC-1105);
            # Resources is the resolved sizing and, for a profile-less agent on
            # explicit resources, its only visible sizing (PCC-1063).
            arch = spec.get("arch")
            if arch:
                console.print(f"Architecture: {arch}")
            resources = data.get("resources")
            if resources and isinstance(resources, dict):
                console.print(
                    f"Resources: cpu={resources.get('cpu', 'N/A')}, "
                    f"memory={resources.get('memory', 'N/A')}"
                )
            for label, value in _git_status_rows(data):
                console.print(f"{label}: {value}")
            console.print(f"Active Deployment ID: {data.get('activeDeploymentId', 'N/A')}")
            console.print(f"Created At: {data.get('createdAt', 'N/A')}")
            console.print(f"Updated At: {data.get('updatedAt', 'N/A')}")
            krisp_viva = data.get("krispViva") or {}
            audio_filter = krisp_viva.get("audioFilter")
            console.print(
                f"Krisp VIVA: {f'Enabled ({audio_filter})' if audio_filter else 'Disabled'}"
            )
            max_session_duration = spec.get("maxSessionDurationSeconds")
            console.print(
                f"Max Session Duration: "
                f"{f'{max_session_duration}s' if max_session_duration is not None else 'default'}"
            )
            autoscaling = data.get("autoScaling") or {}
            if autoscaling:
                console.print(f"Min Agents: {autoscaling.get('minReplicas', 0)}")
                console.print(f"Max Agents: {autoscaling.get('maxReplicas', 0)}")
            for status_error in data.get("errors") or []:
                code = status_error.get("code", "")
                detail = status_error.get("message") or status_error.get("error", "Unknown error")
                console.print(f"Error: {code} {detail}")
            return

        # Deployment info

        deployment_table = Table(show_header=False, show_lines=False, box=box.SIMPLE)
        deployment_table.add_column("Key")
        deployment_table.add_column("Value")
        deployment_table.add_row(
            "[bold]Active Session Count:[/bold]",
            str(data.get("activeSessionCount", "N/A")),
        )
        image_label, image_value = _image_display(data.get("deployment", {}))
        deployment_table.add_row(
            f"[bold]{image_label}:[/bold]",
            image_value,
        )

        # Display agent profile if available
        agent_profile = data.get("agentProfile")
        if agent_profile:
            deployment_table.add_row(
                "[bold]Agent Profile:[/bold]",
                str(agent_profile),
            )

        # The architecture the deployment pinned (PCC-1105) — which nodes it
        # schedules on and which image platform was resolved.
        arch = data.get("deployment", {}).get("manifest", {}).get("spec", {}).get("arch")
        if arch:
            deployment_table.add_row(
                "[bold]Architecture:[/bold]",
                str(arch),
            )

        # Resolved sizing from the deployment manifest. For an agent deployed
        # with explicit resources (enterprise regions), there is no profile —
        # this row is its only visible sizing.
        resources = data.get("resources")
        if resources and isinstance(resources, dict):
            deployment_table.add_row(
                "[bold]Resources:[/bold]",
                f"cpu={resources.get('cpu', 'N/A')}, memory={resources.get('memory', 'N/A')}",
            )

        for label, value in _git_status_rows(data):
            deployment_table.add_row(f"[bold]{label}:[/bold]", value)

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

        # Check for Krisp VIVA status (reverse-mapped by API)
        krisp_viva = data.get("krispViva")
        krisp_viva_status = "[dim]Disabled[/dim]"

        if krisp_viva and isinstance(krisp_viva, dict):
            audio_filter = krisp_viva.get("audioFilter")
            if audio_filter:
                krisp_viva_status = f"[green]Enabled ({audio_filter})[/green]"

        deployment_table.add_row(
            "[bold]Krisp VIVA:[/bold]",
            krisp_viva_status,
        )

        # Max session duration (read from deployment manifest spec).
        # Absent from the manifest when the user never set an explicit value —
        # the platform applies its default via the CRD in that case.
        max_session_duration = (
            data.get("deployment", {})
            .get("manifest", {})
            .get("spec", {})
            .get("maxSessionDurationSeconds")
        )
        deployment_table.add_row(
            "[bold]Max Session Duration:[/bold]",
            f"{max_session_duration}s"
            if max_session_duration is not None
            else "[dim]Default[/dim]",
        )

        # Autoscaling info
        autoscaling_data = data.get("autoScaling", None)
        if autoscaling_data:
            scaling_renderables = [
                Panel(
                    f"[bold]Minimum Agents[/bold]\n{autoscaling_data.get('minReplicas', 0)}",
                    expand=True,
                ),
                Panel(
                    f"[bold]Maximum Agents[/bold]\n{autoscaling_data.get('maxReplicas', 0)}",
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

        # Build health/status panel with revision info when available
        current_rev = data.get("currentRevision")
        previous_rev = data.get("previousRevision")

        if current_rev:
            # Rich status with revision details
            health_lines = []

            rev_phase = current_rev.get("phase", "Unknown")
            rev_id = current_rev.get("deploymentID", "")[:8]
            rev_replicas = current_rev.get("readyReplicas")

            if data["ready"]:
                health_lines.append("[bold green]Ready[/bold green]")
            else:
                health_lines.append(f"[bold yellow]{rev_phase}[/bold yellow]")

            current_parts = [f"  Current  [bold]({rev_id})[/bold] {rev_phase}"]
            if rev_replicas is not None:
                current_parts.append(f"[dim]·[/dim] {rev_replicas} agents")
            health_lines.append(" ".join(current_parts))

            # Show health details when available (skip if healthy with no restarts)
            rev_health = current_rev.get("health")
            if rev_health and not (
                rev_health.get("ready") and rev_health.get("restartCount", 0) == 0
            ):
                health_lines.extend(format_health_lines(rev_health))

            if current_rev.get("hasInfrastructureIssue"):
                health_lines.append(
                    "    [yellow]Infrastructure issue detected — contact support[/yellow]"
                )

            if previous_rev:
                prev_phase = previous_rev.get("phase", "Unknown")
                prev_id = previous_rev.get("deploymentID", "")[:8]
                prev_replicas = previous_rev.get("readyReplicas")
                prev_parts = [f"  Previous [bold]({prev_id})[/bold] {prev_phase}"]
                if prev_replicas is not None:
                    prev_parts.append(f"[dim]·[/dim] {prev_replicas} agents")
                health_lines.append(" ".join(prev_parts))

            health_content = "\n".join(health_lines)
            health_border = "green" if data["ready"] else "yellow"
        else:
            # Fallback: no revision data from API
            health_content = f"[{'bold green' if data['ready'] else 'bold yellow'}]Health: {'Ready' if data['ready'] else 'Stopped'}[/]"
            health_border = "green" if data["ready"] else "yellow"

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
                        health_content,
                        border_style=health_border,
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
    config_file: str | None = CONFIG_FILE_OPTION,
    agent_name: str = typer.Argument(
        None, help="Name of the agent to list sessions for e.g. 'my-agent'", show_default=False
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

    # Get agent name from argument or deploy config
    if not agent_name:
        if deploy_config and deploy_config.agent_name:
            agent_name = deploy_config.agent_name
        else:
            console.error("No target agent name provided")
            raise typer.Exit(1)

    # If session_id is specified, fetch single session with detailed metrics
    if session_id:
        with console.status(
            f"[dim]Looking up session '{session_id}'[/dim]", spinner="dots"
        ) as live:
            data, error = await API.agent_session(
                agent_name=agent_name, session_id=session_id, org=org, live=live
            )
            live.stop()

            if error:
                raise typer.Exit(1)

            if data and console.json_output:
                console.output_json(data)
                return

            if not data:
                console.error(f"Session '{session_id}' not found")
                raise typer.Exit(1)

            # Display detailed session view
            session_duration = format_duration(data.get("createdAt"), data.get("endedAt")) or "N/A"
            status = data.get("completionStatus", "")
            if data.get("endedAt"):
                status_display = "[red]Error (500)[/red]" if status == "500" else "Complete"
            else:
                status_display = "[yellow]Active[/yellow]"

            # Build session info panel
            info_lines = [
                f"[bold]Session ID:[/bold] {data['sessionId']}",
                f"[bold]Status:[/bold] {status_display}"
                + (f" ({status})" if status and status != "500" else ""),
                f"[bold]Duration:[/bold] {session_duration}",
                f"[bold]Created:[/bold] {format_timestamp(data.get('createdAt'))}",
                f"[bold]Ended:[/bold] {format_timestamp(data.get('endedAt')) if data.get('endedAt') else '[dim]N/A[/dim]'}",
                f"[bold]Bot Start:[/bold] {data.get('botStartSeconds')}s"
                if data.get("botStartSeconds") is not None
                else "[bold]Bot Start:[/bold] [dim]N/A[/dim]",
                f"[bold]Cold Start:[/bold] {'[red]Yes[/red]' if data.get('coldStart') else 'No'}",
            ]

            # Add resource metrics if available
            metrics = data.get("resourceMetrics")
            if metrics:
                timeseries = metrics.get("timeseries", [])
                sample_count = metrics.get("sampleCount", 0)

                # Calculate duration from timeseries
                if len(timeseries) >= 2:
                    ts_duration = timeseries[-1].get("t", 0) - timeseries[0].get("t", 0)
                    ts_duration_str = f"{ts_duration}s"
                else:
                    ts_duration_str = "N/A"

                info_lines.append("")
                info_lines.append(
                    f"[bold]Resource Metrics[/bold] ({sample_count} samples over {ts_duration_str}):"
                )

                # CPU sparkline and percentiles
                cpu_values = [s.get("c", 0) for s in timeseries]
                cpu_spark = sparkline(cpu_values) if cpu_values else ""
                cpu_p50 = metrics.get("cpuMillicoresP50", 0)
                cpu_p99 = metrics.get("cpuMillicoresP99", 0)
                info_lines.append(
                    f"  CPU:    {cpu_spark}  p50: {format_cpu(cpu_p50)}  p99: {format_cpu(cpu_p99)}"
                )

                # Memory sparkline and percentiles
                mem_values = [s.get("m", 0) for s in timeseries]
                mem_spark = sparkline(mem_values) if mem_values else ""
                mem_p50 = int(metrics.get("memoryBytesP50", 0))
                mem_p99 = int(metrics.get("memoryBytesP99", 0))
                info_lines.append(
                    f"  Memory: {mem_spark}  p50: {format_bytes(mem_p50)}  p99: {format_bytes(mem_p99)}"
                )

            console.success(
                Panel(
                    "\n".join(info_lines),
                    title=f"Session details for agent [bold]{agent_name}[/bold] [dim]({org})[/dim]",
                    title_align="left",
                ),
            )
            return

    with console.status(
        f"[dim]Looking up agent with name '{agent_name}'[/dim]", spinner="dots"
    ) as live:
        data, error = await API.agent_sessions(agent_name=agent_name, org=org, live=live)

        live.stop()

        if error:
            raise typer.Exit(1)

        if not data:
            console.error(f"No session data found for agent with name '{agent_name}'")
            raise typer.Exit(1)

        sessions_list = data.get("sessions", [])
        total_sessions = len(sessions_list)

        if console.json_output:
            console.output_json(data)
            return

        if not console.rich_output:
            console.print_records(
                [
                    "Session ID",
                    "Created At",
                    "Ended At",
                    "Duration",
                    "Status",
                    "Bot Start Seconds",
                    "Cold Start",
                ],
                [
                    (
                        s["sessionId"],
                        format_timestamp(s["createdAt"]),
                        format_timestamp(s["endedAt"]) if s["endedAt"] else "",
                        format_duration(s["createdAt"], s["endedAt"]) or "",
                        ("Error (500)" if s.get("completionStatus") == "500" else "Complete")
                        if s["endedAt"]
                        else "Active",
                        s["botStartSeconds"] if s["botStartSeconds"] is not None else "",
                        s["coldStart"],
                    )
                    for s in sessions_list
                    if not session_id or s["sessionId"] == session_id
                ],
                title=f"Session data for agent {agent_name} ({org})",
            )
            return

        completed_sessions = [s for s in sessions_list if s.get("endedAt")]

        durations = []
        for session in completed_sessions:
            try:
                from datetime import datetime

                created_at = datetime.fromisoformat(session["createdAt"].replace("Z", "+00:00"))
                ended_at = datetime.fromisoformat(session["endedAt"].replace("Z", "+00:00"))
                duration_seconds = (ended_at - created_at).total_seconds()
                durations.append(duration_seconds)
            except BaseException:
                continue

        bot_start_times = [
            s["botStartSeconds"] for s in sessions_list if s.get("botStartSeconds") is not None
        ]
        bot_start_metrics = calculate_percentiles(bot_start_times)
        duration_metrics = calculate_percentiles(durations)
        cold_starts_count = sum(1 for s in sessions_list if s.get("coldStart") is True)
        metric_renderables = []  # Initialize to empty list for type consistency

        if duration_metrics and bot_start_metrics and total_sessions > 0:
            cold_start_percent = cold_starts_count / total_sessions * 100
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
            # Note: session["sessionId"] is accessed without defensive checks.
            # If the API returns malformed data missing sessionId, the CLI will crash with
            # a KeyError rather than silently skip sessions. This ensures smoke tests and
            # API verification catch breaking changes immediately. We could instead use
            # console.error() and skip the session but its unclear if thats better..
            if session_id and session["sessionId"] != session_id:
                continue

            session_duration = (
                format_duration(session["createdAt"], session["endedAt"]) or "[dim]N/A[/dim]"
            )
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
                format_timestamp(session["createdAt"]),
                format_timestamp(session["endedAt"]) if session["endedAt"] else "[dim]N/A[/dim]",
                session_duration,
                status_display,
                f"{session['botStartSeconds']}s"
                if session["botStartSeconds"] is not None
                else "[dim]N/A[/dim]",
                "[red]Yes[/red]"
                if session["coldStart"] is True
                else "No"
                if session["coldStart"] is False
                else "[dim]N/A[/dim]",
            ]

            if is_cold_start:
                row_data = [f"[{row_style}]{cell}[/]" for cell in row_data]

            table.add_row(*row_data)

        console.success(
            Group(
                Columns(metric_renderables, equal=True)
                if metric_renderables and not session_id
                else "",
                table,
            ),
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
        None, "--organization", "-o", help="Organization to get status of agent for"
    ),
    level: LogLevel = typer.Option(None, "--level", "-l", help="Level of logs to get"),
    format: LogFormat = typer.Option(LogFormat.TEXT, "--format", "-f", help="Logs format"),
    limit: int = typer.Option(100, "--limit", "-n", help="Number of logs to get"),
    deployment_id: str = typer.Option(
        None, "--deployment", "-d", help="Filter logs by deployment ID"
    ),
    session_id: str = typer.Option(None, "--session-id", "-s", help="Filter logs by session ID"),
):
    org = organization or config.get("org")

    status_text = "agent"
    if deployment_id:
        status_text = f"deployment ({deployment_id})"
    if session_id:
        status_text = f"session ({session_id})"

    with console.status(
        f"[dim]Fetching logs for {status_text}: [bold]'{agent_name}'[/bold] with severity: [bold cyan]{level.value if level else 'ALL'}[/bold cyan][/dim]",
        spinner="dots",
    ):
        data, error = await API.agent_logs(
            agent_name=agent_name,
            org=org,
            limit=limit,
            deployment_id=deployment_id,
            session_id=session_id,
        )

        if not data or not data.get("logs"):
            console.print("[dim]No logs found for agent[/dim]")
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(data)
        return

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
        console.require_interactive("--force")
        if not await questionary.confirm(
            "Are you sure you want to delete this agent? Note: active sessions will not be interrupted and will continue to run until completion."
        ).ask_async():
            console.print("[bold]Aborting delete request[/bold]")
            raise typer.Exit(1)

    with console.status(f"[dim]Deleting agent: [bold]'{agent_name}'[/bold][/dim]", spinner="dots"):
        data, error = await API.agent_delete(agent_name=agent_name, org=org)

        if error:
            raise typer.Exit(1)

        if not data:
            console.error(f"Agent '{agent_name}' not found in organization '{org}'")
            raise typer.Exit(1)

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
                    headers={
                        "Authorization": f"Bearer {token}",
                        "User-Agent": f"PipecatCloudCLI/{_cli_version}",
                    },
                )
            if response.status != 200:
                error_code = str(response.status)
                response.raise_for_status()

            data = await response.json()

            if console.json_output:
                console.output_json(data)
                return

            if not console.rich_output:
                console.print_records(
                    ["ID", "Node Type", "Image / Build", "Created At", "Updated At"],
                    [
                        (
                            deployment.get("id", "N/A"),
                            deployment.get("manifest", {})
                            .get("spec", {})
                            .get("dailyNodeType", "N/A"),
                            _image_display(deployment)[1],
                            deployment.get("createdAt", "N/A"),
                            deployment.get("updatedAt", "N/A"),
                        )
                        for deployment in data["deployments"]
                    ],
                    title=f"Deployments for agent: {agent_name}",
                )
                return

            table = Table(
                show_header=True,
                show_lines=True,
                border_style="dim",
                box=box.SIMPLE,
            )
            table.add_column("ID")
            table.add_column("Node Type")
            table.add_column("Image / Build")
            table.add_column("Created At")
            table.add_column("Updated At")

            for deployment in data["deployments"]:
                spec = deployment.get("manifest", {}).get("spec", {})
                table.add_row(
                    deployment.get("id", "N/A"),
                    spec.get("dailyNodeType", "N/A"),
                    _image_display(deployment)[1],
                    deployment.get("createdAt", "N/A"),
                    deployment.get("updatedAt", "N/A"),
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
        raise typer.Exit(1)


@agent_cli.command(name="start", help="Start an agent instance")
@synchronizer.create_blocking
@requires_login
@with_deploy_config
async def start(
    deploy_config=typer.Option(None, hidden=True),
    config_file: str | None = CONFIG_FILE_OPTION,
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
            raise typer.Exit(1)

        agent_name = default_agent_name

    if not default_public_api_key:
        console.print(
            Panel(
                f"No public API key provided. Please provide a public API key using the --api-key flag or set a default using [bold cyan]{PIPECAT_CLI_NAME} organizations keys use[/bold cyan].\n\n"
                f"If you have not yet created a public API key, you can do so by running [bold cyan]{PIPECAT_CLI_NAME} organizations keys create[/bold cyan].",
                title="Public API Key Required",
                title_align="left",
                border_style="yellow",
            )
        )

        raise typer.Exit(1)

    # Validate daily_properties JSON if provided
    if use_daily and daily_properties:
        try:
            json.loads(daily_properties)
        except json.JSONDecodeError as e:
            console.error(f"Invalid JSON format for Daily room properties: {daily_properties}")
            console.print(f"[dim]JSON error: {str(e)}[/dim]")
            raise typer.Exit(1)

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
            )
        )
        console.require_interactive("--force")
        if not await questionary.confirm(
            "Are you sure you want to start an active session for this agent?"
        ).ask_async():
            console.print("[bold]Aborting start request[/bold]")
            raise typer.Exit(1)

    with console.status(
        "[dim]Checking agent health...[/dim]", spinner="dots", refresh_per_second=4
    ) as live:
        health_data, error = await API.agent(agent_name=agent_name, org=org, live=live)
        if not health_data or not health_data["ready"]:
            live.stop()
            console.error(
                f"Agent '{agent_name}' does not exist or is not in a healthy state. Please check the agent status with [bold cyan]{PIPECAT_CLI_NAME} agent status {agent_name}[/bold cyan]"
            )
            raise typer.Exit(1)

        live.update(f"[dim]Agent '{agent_name}' is healthy, sending start request...[/dim]")

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
            raise typer.Exit(1)

        live.stop()

        if console.json_output:
            console.output_json(data if isinstance(data, dict) else {})

        console.success(f"Agent '{agent_name}' started successfully")

        if use_daily and isinstance(data, dict):
            daily_room = data.get("dailyRoom")
            daily_token = data.get("dailyToken")
            if daily_room:
                url = f"{daily_room}?t={daily_token}"
                console.print("\nJoin your session by visiting the link below:")
                console.print(f"[link={url}]{url}[/link]")

        if isinstance(data, dict):
            session_id = data.get("sessionId")
            if session_id:
                console.print(f"\nSession ID: {session_id}")


@agent_cli.command(name="stop", help="Stop an active agent session")
@synchronizer.create_blocking
@requires_login
@with_deploy_config
async def stop(
    deploy_config=typer.Option(None, hidden=True),
    config_file: str | None = CONFIG_FILE_OPTION,
    agent_name: str = typer.Argument(None, help="Name of the agent e.g. 'my-agent'"),
    session_id: str = typer.Option(
        ...,
        "--session-id",
        "-s",
        help="ID of the session to stop",
    ),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization which the agent belongs to"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Bypass prompt for confirmation",
    ),
):
    org = organization or config.get("org")

    # Load values from deployment config file (if one exists)
    partial_config = deploy_config or DeployConfigParams()

    # Get agent name from argument or deploy config
    if not agent_name:
        if partial_config and partial_config.agent_name:
            agent_name = partial_config.agent_name
        else:
            console.error("No target agent name provided")
            raise typer.Exit(1)

    # Confirm stop request
    if not force:
        console.print(
            Panel(
                f"Agent Name: {agent_name}\nSession ID: {session_id}",
                title="[bold]Stop Session[/bold]",
                title_align="left",
                border_style="yellow",
            )
        )
        console.require_interactive("--force")
        if not await questionary.confirm("Are you sure you want to stop this session?").ask_async():
            console.print("[bold]Aborting stop request[/bold]")
            raise typer.Exit(1)

    with console.status(
        f"[dim]Stopping session [bold]'{session_id}'[/bold] for agent [bold]'{agent_name}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.agent_session_terminate(
            agent_name=agent_name, session_id=session_id, org=org
        )

        if error:
            raise typer.Exit(1)

        console.success(f"Session '{session_id}' stopped successfully")


# ----- GitHub source (PCC-933) -----


def _print_git_binding(git: dict) -> None:
    rows = [
        ("Repository", str(git.get("repoFullName", "—"))),
        ("Branch", str(git.get("branch", "—"))),
        ("Dockerfile path", str(git.get("dockerfilePath") or DEFAULT_DOCKERFILE_PATH)),
        ("Subdirectory", str(git.get("subdirectory") or "—")),
        ("Auto-deploy on push", "yes" if git.get("autoDeploy", True) else "no"),
    ]
    if not console.rich_output:
        console.print_records(["Field", "Value"], rows)
        return
    table = Table(show_header=False, box=box.SIMPLE)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


@agent_cli.command(
    name="link",
    help="Link an agent to a GitHub repository and branch, or re-point an existing link.",
)
@synchronizer.create_blocking
@requires_login
async def link(
    agent_name: str = typer.Argument(..., help="Name of the agent to link e.g. 'my-agent'"),
    repo: str = typer.Option(..., "--repo", help="Repository as 'owner/repo'"),
    branch: str = typer.Option(..., "--branch", help="Branch to build and deploy from"),
    dockerfile_path: str = typer.Option(
        None,
        "--dockerfile-path",
        help=f"Path to the Dockerfile within the repository (default: {DEFAULT_DOCKERFILE_PATH})",
    ),
    subdirectory: str = typer.Option(
        None, "--subdirectory", help="Build context subdirectory within the repository"
    ),
    auto_deploy: bool = typer.Option(
        None,
        "--auto-deploy/--no-auto-deploy",
        help="Deploy automatically when the branch is pushed to (default: enabled)",
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    # Validate locally first: the same rules the API applies, so a typo fails
    # before a round-trip rather than after one.
    if not is_valid_repo_full_name(repo):
        console.error(f"Invalid repository '{repo}'. Expected the form 'owner/repo'.")
        raise typer.Exit(1)
    if not is_valid_branch_name(branch):
        console.error(
            f"Invalid branch '{branch}'. A branch must be a valid git ref with no empty or "
            "dot-only segments."
        )
        raise typer.Exit(1)

    # An upsert where omitted optional fields keep their stored value, so only
    # send what the caller actually asked for. Sending a default here would
    # silently reset a stored dockerfile path on a branch-only change.
    payload: dict = {"repoFullName": repo, "branch": branch}
    if dockerfile_path is not None:
        payload["dockerfilePath"] = dockerfile_path
    if subdirectory is not None:
        payload["subdirectory"] = subdirectory
    if auto_deploy is not None:
        payload["autoDeploy"] = auto_deploy

    with console.status(
        f"[dim]Linking agent [bold]'{agent_name}'[/bold] to [bold]{repo}@{branch}[/bold][/dim]",
        spinner="dots",
    ):
        git_config, error = await API.agent_git_connect(
            agent_name=agent_name, org=org, payload=payload
        )
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"gitConfig": git_config or {}})
        return

    console.success(
        f"Linked agent '{agent_name}' to {binding_summary(git_config)}.\n"
        "[dim]Linking does not change what is running. Deploy the branch now with "
        f"[bold]{PIPECAT_CLI_NAME} agent deploy {agent_name} --github[/bold].[/dim]"
    )
    _print_git_binding(git_config or {})


@agent_cli.command(name="unlink", help="Remove an agent's GitHub repository link.")
@synchronizer.create_blocking
@requires_login
async def unlink(
    agent_name: str = typer.Argument(..., help="Name of the agent to unlink"),
    organization: str = typer.Option(None, "--organization", "-o"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip the confirmation prompt"),
):
    org = organization or config.get("org")

    if not force:
        console.require_interactive("--force")
        if not await questionary.confirm(
            f"Remove the GitHub link from agent '{agent_name}'? Pushes stop deploying it. "
            "The running agent is not touched and its current deployment stays live."
        ).ask_async():
            console.print("[bold]Aborting unlink request[/bold]")
            raise typer.Exit(1)

    with console.status(f"[dim]Unlinking agent [bold]'{agent_name}'[/bold][/dim]", spinner="dots"):
        _, error = await API.agent_git_disconnect(agent_name=agent_name, org=org)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"unlinked": agent_name})
        return
    console.success(
        f"Unlinked agent '{agent_name}' from GitHub.\n"
        "[dim]Its current deployment is still running.[/dim]"
    )


@agent_cli.command(
    name="deploy",
    help="Deploy a GitHub-linked agent from its connected branch's current HEAD.",
)
@synchronizer.create_blocking
@requires_login
async def agent_deploy(
    agent_name: str = typer.Argument(..., help="Name of the agent to deploy"),
    github: bool = typer.Option(
        False,
        "--github",
        help="Build and deploy the agent's linked GitHub branch (currently required)",
    ),
    wait: bool = typer.Option(False, "--wait", help="Follow the deploy until it succeeds or fails"),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    # The flag is explicit rather than implied so that adding image-based
    # deploys under this command later cannot change what an existing script
    # does. Deploying an image today is still `pipecat cloud deploy`.
    if not github:
        console.error(
            "Pass [bold]--github[/bold] to deploy an agent's linked GitHub branch.\n"
            f"[dim]To deploy an image or a cloud build, use [bold]{PIPECAT_CLI_NAME} deploy"
            "[/bold].[/dim]"
        )
        raise typer.Exit(1)

    with console.status(
        f"[dim]Queueing GitHub deploy for [bold]'{agent_name}'[/bold][/dim]", spinner="dots"
    ):
        intent, error = await API.agent_git_deploy(agent_name=agent_name, org=org)
        if error:
            raise typer.Exit(1)

    if not intent:
        console.error("The API did not return a deploy intent")
        raise typer.Exit(1)

    commit = intent.get("commitSha", "")
    branch = ref_to_branch(intent.get("ref") or "")

    if not console.json_output:
        console.success(
            f"Queued deploy of '{agent_name}' from {branch} at commit "
            f"{short_sha(commit) if commit else 'unknown'}.\n"
            f"[dim]Deploy intent: {intent.get('id', 'unknown')}[/dim]"
        )

    if not wait:
        if console.json_output:
            console.output_json({"deployIntent": intent})
        return

    result = await follow_git_deploy(agent_name=agent_name, org=org, commit_sha=commit)

    if console.json_output:
        # `waitOutcome` is the machine-readable half of what the human
        # rendering says: a consumer has to tell "superseded" and "never
        # observed" apart from a plain in-flight timeout.
        console.output_json(
            {
                "deployIntent": intent,
                "latestDeploy": result.deploy,
                "waitOutcome": result.outcome.value,
                "supersededBy": result.superseded_by,
            }
        )
        if result.outcome is GitDeployWait.UNOBSERVED:
            raise typer.Exit(1)
        return

    report_git_deploy_result(result, agent_name)
