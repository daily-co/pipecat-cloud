#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio

import typer
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.deploy_utils import (
    DeployConfigParams,
    ScalingParams,
    load_deploy_config_file,
)
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

MAX_ALIVE_CHECKS = 30
ALIVE_CHECK_SLEEP = 5

# ----- Command


async def _deploy(params: DeployConfigParams, org, force: bool = False):
    existing_agent = False

    # Check for an existing deployment with this agent name
    with Live(
        console.status("[dim]Checking for existing agent deployment...[/dim]", spinner="dots"), transient=True
    ) as live:
        data, error = await API.agent(agent_name=params.agent_name, org=org, live=live)

        if error:
            live.stop()
            return typer.Exit(1)

        if data:
            existing_agent = True

            if not force:
                live.stop()
                if not typer.confirm(
                    f"Deployment for agent '{params.agent_name}' exists. Do you want to update it? Note: this will not interrupt any active sessions",
                    default=True,
                ):
                    console.cancel()
                    return typer.Exit()

    # Start the deployment process
    with Live(console.status("[dim]Preparing deployment...", spinner="dots")) as live:
        """
        # 1. Check that provided secret set exists
        """
        if params.secret_set:
            live.update(
                console.status(f"[dim]Verifying secret set {params.secret_set} exists...[/dim]")
            )
            secrets_exist, error = await API.secrets_list(
                secret_set=params.secret_set, org=org, live=live
            )

            if error:
                return typer.Exit()

            if not secrets_exist:
                live.stop()
                console.error(
                    f"Secret set [bold]'{params.secret_set}'[/bold] not found in namespace [bold]'{org}'[/bold]"
                )
                return typer.Exit()

        """
        # 2. Check that provided image pull secret exists
        """
        if params.image_credentials:
            live.update(
                console.status(
                    f"[dim]Verifying image pull secret {params.image_credentials} exists...[/dim]"
                )
            )
            creds_exist, error = await API.bubble_error().secrets_list(
                secret_set=params.image_credentials, org=org, live=live
            )

            if error:
                if error == 400:
                    creds_exist = True
                else:
                    API.print_error()
                    return typer.Exit()

            if not creds_exist:
                live.stop()
                console.error(
                    f"Image pull secret with name [bold]'{params.image_credentials}'[/bold] not found in namespace [bold]'{org}'[/bold]"
                )

        live.update(
            console.status(
                f"[dim]{'Updating' if existing_agent else 'Pushing'} agent manifest for[/dim] [cyan]'{params.agent_name}'[/cyan]"
            )
        )

        result, error = await API.deploy(
            deploy_config=params, update=existing_agent, org=org, live=live
        )

        if error:
            return typer.Exit()

        if not existing_agent and not result:
            live.stop()
            console.error("A problem occured during deployment. Please contact support.")
            return typer.Exit()

        # Close the live display before starting the new polling phase
        live.stop()

    """
    # 3. Poll status until healthy
    """
    active_deployment_id = None
    has_failed = False
    is_ready = False
    checks_performed = 0

    # Create a simple spinner for the polling phase
    with console.status(
        "[dim]Waiting for deployment to become ready...[/dim]", spinner="bouncingBar"
    ) as status:
        try:
            while checks_performed < MAX_ALIVE_CHECKS:
                # Get deployment status
                agent_status, error = await API.agent(
                    agent_name=params.agent_name, org=org, live=None
                )

                if error:
                    status.stop()
                    console.error("Error checking deployment status")
                    return typer.Exit()

                # Update deployment ID if received (silently)
                if not active_deployment_id and agent_status.get("activeDeploymentId"):
                    active_deployment_id = agent_status["activeDeploymentId"]

                # Check if deployment is ready
                if agent_status.get("ready"):
                    is_ready = True
                    break

                # Wait before checking again
                await asyncio.sleep(ALIVE_CHECK_SLEEP)
                checks_performed += 1

        except KeyboardInterrupt:
            status.stop()
            console.print(
                "\n[yellow]Deployment monitoring interrupted. The deployment may still be in progress.[/yellow]"
            )
            return typer.Exit()

    # Final result handling
    if has_failed:
        console.print(has_failed["message"])
        console.error("Deployment failed with the above error")
        return typer.Exit()

    if is_ready:
        console.success(
            f"Agent deployment [bold]'{params.agent_name}'[/bold] is ready\n\n"
            f"[dim]Start a session with your new agent by running:\n[/dim]"
            f"[bold]`{PIPECAT_CLI_NAME} agent start {params.agent_name}`[/bold]",
            title_extra=f"{'Update' if existing_agent else 'Deployment'} complete",
        )
    else:
        console.error(
            f"Deployment did not enter ready state within {MAX_ALIVE_CHECKS * ALIVE_CHECK_SLEEP} seconds. "
            f"Please check logs with `{PIPECAT_CLI_NAME} agent logs {params.agent_name}`")

    return typer.Exit()


def create_deploy_command(app: typer.Typer):
    @app.command(name="deploy", help="Deploy agent to Pipecat Cloud")
    @synchronizer.create_blocking
    @requires_login
    async def deploy(
        agent_name: str = typer.Argument(
            None, help="Name of the agent to deploy e.g. 'my-agent'", show_default=False
        ),
        image: str = typer.Argument(
            None, help="Docker image location e.g. 'my-image:latest'", show_default=False
        ),
        min_instances: int = typer.Option(
            None,
            "--min-instances",
            "-min",
            help="Minimum number of instances to keep warm",
            rich_help_panel="Deployment Configuration",
            min=0,
        ),
        max_instances: int = typer.Option(
            None,
            "--max-instances",
            "-max",
            help="Maximum number of allowed instances",
            rich_help_panel="Deployment Configuration",
            min=1,
            max=50,
        ),
        secret_set: str = typer.Option(
            None,
            "--secrets",
            "-s",
            help="Secret set to use for deployment",
            rich_help_panel="Deployment Configuration",
        ),
        organization: str = typer.Option(
            None,
            "--organization",
            "-o",
            help="Organization to deploy to",
            rich_help_panel="Deployment Configuration",
        ),
        credentials: str = typer.Option(
            None,
            "--credentials",
            "-c",
            help="Image pull secret to use for deployment",
            rich_help_panel="Deployment Configuration",
        ),
        skip_confirm: bool = typer.Option(
            False,
            "--force",
            "-f",
            help="Force deployment / skip confirmation",
        ),
    ):
        org = organization or config.get("org")

        # Compose deployment config from CLI options and config file (if provided)
        # Order of precedence:
        #   1. Arguments provided to the CLI deploy command
        #   2. Values from the config toml file
        #   3. CLI command defaults

        partial_config = DeployConfigParams()

        # Load values from deployment config file (if one exists)
        try:
            if deploy_config := load_deploy_config_file():
                partial_config = deploy_config
        except Exception as e:
            console.error(str(e))
            return typer.Exit()

        # Override any local config values from passed CLI arguments
        partial_config.agent_name = agent_name or partial_config.agent_name
        partial_config.image = image or partial_config.image
        partial_config.image_credentials = credentials or partial_config.image_credentials
        partial_config.secret_set = secret_set or partial_config.secret_set
        partial_config.scaling = ScalingParams(
            min_instances=min_instances
            if min_instances is not None
            else partial_config.scaling.min_instances,
            max_instances=max_instances
            if max_instances is not None
            else partial_config.scaling.max_instances,
        )

        # Assert agent name and image are provided
        if not partial_config.agent_name:
            console.error("Agent name is required")
            return typer.Exit()

        if not partial_config.image:
            console.error("Image / repository URL is required")
            return typer.Exit()

        # Create and display table
        table = Table(show_header=False, border_style="dim", show_edge=True, show_lines=True)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Min instances", str(partial_config.scaling.min_instances))
        table.add_row("Max instances", str(partial_config.scaling.max_instances))

        content = Group(
            (f"[bold white]Agent name:[/bold white] [green]{partial_config.agent_name}[/green]"),
            (f"[bold white]Image:[/bold white] [green]{partial_config.image}[/green]"),
            (f"[bold white]Organization:[/bold white] [green]{org}[/green]"),
            (f"[bold white]Secret set:[/bold white] {'[dim]None[/dim]' if not partial_config.secret_set else '[green] '+ partial_config.secret_set + '[/green]'}"),
            (f"[bold white]Image pull secret:[/bold white] {'[dim]None[/dim]' if not partial_config.image_credentials else '[green]' + partial_config.image_credentials + '[/green]'}"),
            "\n[dim]Scaling configuration:[/dim]",
            table,
            *
            (
                [] if partial_config.scaling.min_instances else [
                    Text(
                        "Note: Deploying with 0 minimum instances may result in cold starts",
                        style="red",
                    )]),
        )

        console.print(
            Panel(content, title="Review deployment", title_align="left", border_style="yellow")
        )

        if not skip_confirm and not typer.confirm(
            "\nDo you want to proceed with deployment?", default=True
        ):
            console.cancel()
            return typer.Abort()

        # Deploy method posts the deployment config to the API
        # and polls the deployment status until it's ready
        await _deploy(partial_config, org, skip_confirm)

    return deploy
