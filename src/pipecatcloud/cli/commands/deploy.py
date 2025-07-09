#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio

import typer
from loguru import logger
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
    with_deploy_config,
)
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

MAX_ALIVE_CHECKS = 18
ALIVE_CHECK_SLEEP = 5

# ----- Command


async def _deploy(params: DeployConfigParams, org, force: bool = False):
    existing_agent = False

    # Check for an existing deployment with this agent name
    with Live(
        console.status("[dim]Checking for existing agent deployment...[/dim]", spinner="dots"),
        transient=True,
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
    with Live(console.status("[dim]Preparing deployment...", spinner="dots"), transient=True) as live:
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
                if error.get("code") == "400":
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
    is_ready = False
    checks_performed = 0

    console.print(
        f"[bold cyan]{'Updating' if existing_agent else 'Pushing'}[/bold cyan] deployment for agent '{params.agent_name}'")

    # Create a simple spinner for the polling phase
    deployment_status_message = "[dim]Waiting for deployment to become ready...[/dim]"
    with console.status(
        deployment_status_message, spinner="bouncingBar"
    ) as status:
        try:
            while checks_performed < MAX_ALIVE_CHECKS:
                logger.debug("Polling for deployment status")

                # Get deployment status
                agent_status, error = await API.agent(
                    agent_name=params.agent_name, org=org, live=None
                )

                logger.debug(f"Deployment status: {agent_status}")

                # Look for any error messages in the agent status
                # Exit out of the polling loop if we find an error
                status_errors = agent_status.get("errors", [])
                if status_errors and len(status_errors) > 0:
                    status.stop()
                    # Pluck the first error message
                    error_message = status_errors[0]
                    if "code" in error_message and "message" in error_message:
                        console.api_error(error_message, "Agent deployment failed")
                    else:
                        console.error(f"Deployment failed with an unknown error: {status_errors}")
                    return typer.Exit()

                if error:
                    status.stop()
                    console.error("Error checking deployment status")
                    return typer.Exit()

                # Update deployment ID if received
                if not active_deployment_id and agent_status.get("activeDeploymentId"):
                    active_deployment_id = agent_status["activeDeploymentId"]
                    deployment_status_message = f"[dim]Waiting for deployment to become ready (deployment ID: {active_deployment_id})...[/dim]"
                    status.update(deployment_status_message)

                # If we have an active deployment ID, start tailing the log output
                # @TODO - Implement this

                # Check if deployment is ready
                if agent_status.get("activeDeploymentReady", False):
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

    if is_ready:
        public_api_key = config.get("default_public_key")
        extra_message = ""
        if not public_api_key:
            extra_message = "\n\n[yellow]Note: if you have not already created a public API key (required to start a session), you can do so by running:\n[/yellow]"
            extra_message += f"[bold yellow]`{PIPECAT_CLI_NAME} organizations keys create`[/bold yellow]"

        console.success(
            f"Agent deployment [bold]'{params.agent_name}'[/bold] is ready\n\n"
            f"[white]Start a session with your new agent by running:\n[/white]"
            f"[bold]`{PIPECAT_CLI_NAME} agent start {params.agent_name}`[/bold]"
            f"{extra_message}",
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
    @with_deploy_config
    async def deploy(
        deploy_config=typer.Option(None, hidden=True),
        agent_name: str = typer.Argument(
            None, help="Name of the agent to deploy e.g. 'my-agent'", show_default=False
        ),
        image: str = typer.Argument(
            None, help="Docker image location e.g. 'my-image:latest'", show_default=False
        ),
        credentials: str = typer.Option(
            None,
            "--credentials",
            "-c",
            help="Image pull secret to use for deployment",
            rich_help_panel="Deployment Configuration",
            show_default=False,
        ),
        min_agents: int = typer.Option(
            None,
            "--min-agents",
            "-min",
            help="Minimum number of agents to keep warm",
            rich_help_panel="Deployment Configuration",
            min=0,
        ),
        max_agents: int = typer.Option(
            None,
            "--max-agents",
            "-max",
            help="Maximum number of allowed agents",
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

        krisp: bool = typer.Option(
            False,
            "--enable-krisp",
            "-krisp",
            help="Enable Krisp integration for this deployment",
            rich_help_panel="Deployment Configuration",
        ),
        skip_confirm: bool = typer.Option(
            False,
            "--force",
            "-f",
            help="Force deployment / skip confirmation",
            rich_help_panel="Additional Options",
        ),
        no_credentials: bool = typer.Option(
            False,
            "--no-credentials",
            "-nc",
            help="Deployment will not require an image pull secret",
            rich_help_panel="Additional Options",
        ),
        # @deprecated
        min_instances: int = typer.Option(
            None,
            "--min-instances",
            help="[Deprecated] Use --min-agents instead",
            hidden=True,
            min=0,
        ),
        # @deprecated
        max_instances: int = typer.Option(
            None,
            "--max-instances",
            help="[Deprecated] Use --max-agents instead",
            hidden=True,
            min=1,
            max=50,
        ),
    ):

        # Handle @deprecated options
        if min_instances is not None:
            logger.warning("min_instances is deprecated, use min_agents instead")
            min_agents = min_instances

        if max_instances is not None:
            logger.warning("max_instances is deprecated, use max_agents instead")
            max_agents = max_instances

        org = organization or config.get("org")

        # Compose deployment config from CLI options and config file (if provided)
        # Order of precedence:
        #   1. Arguments provided to the CLI deploy command
        #   2. Values from the config toml file
        #   3. CLI command defaults

        partial_config = deploy_config or DeployConfigParams()

        # Override any local config values from passed CLI arguments
        partial_config.agent_name = agent_name or partial_config.agent_name
        partial_config.image = image or partial_config.image
        partial_config.image_credentials = credentials or partial_config.image_credentials
        partial_config.secret_set = secret_set or partial_config.secret_set
        partial_config.scaling = ScalingParams(
            min_agents=min_agents
            if min_agents is not None
            else partial_config.scaling.min_agents,
            max_agents=max_agents
            if max_agents is not None
            else partial_config.scaling.max_agents,
        )
        partial_config.enable_krisp = krisp or partial_config.enable_krisp

        # Assert agent name and image are provided
        if not partial_config.agent_name:
            console.error("Agent name is required")
            return typer.Exit()

        if not partial_config.image:
            console.error("Image / repository URL is required")
            return typer.Exit()

        # Assert credentials are provided if not using --no-credentials / force flag
        if not no_credentials and not partial_config.image_credentials and not skip_confirm:
            console.error(
                "Deployments require an image pull secret [bold](--credentials)[/bold] to securely pull images from private repositories."
                "\nPlease provide an image pull secret name or use [bold][--no-credentials][/bold] to deploy without one.",
                subtitle="Learn more:https://docs.pipecat.daily.co/agents/secrets#image-pull-secrets",
                title_extra="Attempt to deploy without repository credentials",
            )
            return typer.Exit()

        # Create and display table
        table = Table(show_header=False, border_style="dim", show_edge=True, show_lines=True)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Min agents", str(partial_config.scaling.min_agents))
        if partial_config.scaling.max_agents:
            table.add_row("Max agents", str(partial_config.scaling.max_agents))
        else:
            table.add_row("Max agents", "[dim]Use existing or default[/dim]")

        content = Group(
            (f"[bold white]Agent name:[/bold white] [green]{partial_config.agent_name}[/green]"),
            (f"[bold white]Image:[/bold white] [green]{partial_config.image}[/green]"),
            (f"[bold white]Organization:[/bold white] [green]{org}[/green]"),
            (f"[bold white]Secret set:[/bold white] {'[dim]None[/dim]' if not partial_config.secret_set else '[green] '+ partial_config.secret_set + '[/green]'}"),
            (f"[bold white]Image pull secret:[/bold white] {'[dim]None[/dim]' if not partial_config.image_credentials else '[green]' + partial_config.image_credentials + '[/green]'}"),
            (f"[bold white]Krisp:[/bold white] {'[dim]Disabled[/dim]' if not partial_config.enable_krisp else '[green]Enabled[/green]'}"),
            "\n[dim]Scaling configuration:[/dim]",
            table,
            *
            (
                [
                    Text(
                        f"Note: Usage costs will apply for {partial_config.scaling.min_agents} reserved agent(s). Please see: https://www.daily.co/pricing/pipecat-cloud/",
                        style="red",
                    )] if partial_config.scaling.min_agents else [
                    Text(
                        "Note: Deploying with 0 minimum agents may result in cold starts",
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
