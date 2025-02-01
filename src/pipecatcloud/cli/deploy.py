import asyncio
from typing import Optional

import aiohttp
import typer
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.deploy_utils import load_deploy_config_file
from pipecatcloud._utils.http_utils import construct_api_url
from pipecatcloud.cli import PANEL_TITLE_ERROR, PANEL_TITLE_SUCCESS

console = Console()

TEST_CONFIG_FILE = {}


async def _poll_deployment_status(
        token: str,
        organization: str,
        agent_id: str,
        max_attempts: int = 10):

    attempts = 0
    while attempts < max_attempts:
        with console.status(f"Monitoring status for deployment id: [bold]'{agent_id}'[/bold] (attempt {attempts + 1} of {max_attempts})", spinner="dots"):
            async with aiohttp.ClientSession() as session:
                response = await session.get(
                    f"{construct_api_url('services_path').format(org=organization)}/{agent_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = await response.json()
                if data["body"]["ready"]:
                    console.print(Panel(
                        f"[green]Agent '{agent_id}' is ready[/green]",
                        title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
                        title_align="left",
                        border_style="green",
                    ))
                    break
                """
                if not data["body"]["ready"]:
                    console.print(Panel(
                        f"[red]Unable to deploy '{agent_id}'[/red]\n\n"
                        f"[dim]Message from API:[/dim]\n{data['body']['conditions'][-1]['message']}",
                        title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                        title_align="left",
                        border_style="red",
                    ))
                    break
                """
                await asyncio.sleep(2)
                attempts += 1


async def _deploy(
        token: str,
        agent_name: str,
        image: str,
        deployment_config: dict,
        credentials: Optional[str]):
    organization = deployment_config.get("organization")
    if not organization or not token:
        console.print("[red]Not logged in[/red]")
        return

    try:
        with console.status(f"Deploying [bold]'{agent_name}'[/bold]", spinner="dots"):
            async with aiohttp.ClientSession() as session:
                request_url = f"{construct_api_url('services_path').format(org=organization)}"
                # Build request payload
                payload = {
                    "serviceName": agent_name,
                    "image": image,
                }
                if deployment_config.get("secrets"):
                    payload["secretSet"] = deployment_config["secrets"]

                if credentials:
                    payload["imagePullSecretSet"] = credentials

                async with session.put(
                    request_url,
                    headers={"Authorization": f"Bearer {token}"},
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
    except Exception as e:
        console.print(Panel(
            f"[red]Unable to deploy '{agent_name}'. {e}[/red]",
            title=f"[red]{PANEL_TITLE_ERROR}[/red]",
            title_align="left",
            border_style="red",
        ))
        return typer.Exit(1)

    # Poll the deployment status until it's ready
    await _poll_deployment_status(token, organization, agent_name)

# ----- Deploy


def create_deploy_command(app: typer.Typer):
    # Note we wrap the deploy command to avoid circular imports
    @app.command(name="deploy", help="Deploy to Pipecat Cloud")
    @synchronizer.create_blocking
    @requires_login
    async def deploy(
        ctx: typer.Context,
        agent_name: str = typer.Argument(
            None,
            help="Name of the agent to deploy e.g. 'my-agent'",
            show_default=False),
        image: str = typer.Argument(
            None,
            help="Docker image location e.g. 'my-image:latest'",
            show_default=False),
        min_instances: int = typer.Option(
            1,
            "--min-instances",
            help="Minimum number of instances to keep warm",
            rich_help_panel="Deployment Configuration",
            min=1),
        max_instances: int = typer.Option(
            20,
            "--max-instances",
            help="Maximum number of allowed instances",
            rich_help_panel="Deployment Configuration",
            min=0,
            max=50),
        secrets: str = typer.Option(
            None,
            "--secrets",
            "--s",
            help="Secret set to use for deployment",
            rich_help_panel="Deployment Configuration",
        ),
        organization: str = typer.Option(
            None,
            "--organization",
            "--org",
            help="Organization to deploy to",
            rich_help_panel="Deployment Configuration",
        ),
        credentials: str = typer.Option(
            None,
            "--credentials",
            "--c",
            help="Image pull secret to use for deployment",
            rich_help_panel="Deployment Configuration",
        ),
    ):
        token = ctx.obj["token"]

        # Compose deployment config from CLI options and config file (if provided)
        # Order of precedence:
        #   1. Arguments provided to the CLI deploy command
        #   2. Values from the config toml file
        #   3. CLI command defaults
        deployment_config = {
            "min_instances": min_instances or 1,
            "max_instances": max_instances or 20,
            "organization": organization or ctx.obj.get("org"),
            "secrets": secrets,
        }

        # Collect passed values from CLI arguments (ignoring defaults)
        passed_values = {}
        for param in ctx.command.params:
            if param.name == "agent_name" or param.name == "image":
                continue
            value = ctx.params.get(str(param.name))
            # Only include if the value is different from the parameter's default
            if value != param.default:
                passed_values[param.name] = value
        deployment_config.update(passed_values)

        # Merge with values from deployment config file
        if deploy_config := load_deploy_config_file():
            for key, value in deploy_config.items():
                deployment_config.setdefault(key, value)

        final_agent_name = agent_name or deployment_config.get("agent_name")
        final_image = image or deployment_config.get("image")

        # Assert agent name and image are provided
        if not final_agent_name:
            raise typer.BadParameter("Agent name is required")
        if not final_image:
            console.print("[red]Error:[/red] Image location is required", style="bold red")
            raise typer.BadParameter("Image location is required")

        # Create and display table
        table = Table(
            show_header=False,
            border_style="dim",
            show_edge=True,
            show_lines=True)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Min instances", str(deployment_config['min_instances']))
        table.add_row("Max instances", str(deployment_config['max_instances']))

        content = Group(
            (f"[bold white]Agent name:[/bold white] [green]{final_agent_name}[/green]"),
            (f"[bold white]Image:[/bold white] [green]{final_image}[/green]"),
            (f"[bold white]Organization:[/bold white] [green]{deployment_config['organization']}[/green]"),
            (f"[bold white]Secret set:[/bold white] [green]{secrets}[/green]"),
            (f"[bold white]Image pull secret:[/bold white] [green]{credentials}[/green]"),
            "\n[dim]Deployment configuration:[/dim]",
            table)

        console.print(
            Panel(
                content,
                title="Review deployment",
                title_align="left",
                padding=1,
                style="yellow",
                border_style="yellow"))

        if not typer.confirm("\nDo you want to proceed with deployment?", default=True):
            raise typer.Abort()

        # Deploy method posts the deployment config to the API
        # and polls the deployment status until it's ready
        await _deploy(token, final_agent_name, final_image, deployment_config, credentials)
