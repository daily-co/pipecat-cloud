
import aiohttp
import questionary
import typer
from loguru import logger
from rich import box
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from pipecatcloud._utils.agent_utils import handle_agent_start_error
from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.deploy_utils import DEPLOY_STATUS_MAP
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

agent_cli = typer.Typer(
    name="agent", help="Agent management", no_args_is_help=True
)


# ----- Agent Commands -----


@agent_cli.command(name="status", help="Get status of agent deployment")
@synchronizer.create_blocking
@requires_login
async def status(
    agent_name: str = typer.Argument(
        help="Name of the agent to get status of e.g. 'my-agent'"
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to get status of agent for"
    ),
):
    org = organization or config.get("org")

    with Live(console.status(f"[dim]Looking up agent with name {agent_name}[/dim]", spinner="dots")) as live:
        data, error = await API.agent(agent_name=agent_name, org=org, live=live)

        live.stop()

        if error:
            return typer.Exit()

        if not data:
            console.error(f"No deployment data found for agent with name '{agent_name}'")
            return typer.Exit()

        conditions = data["conditions"]
        table = Table(
            show_header=True,
            show_lines=True,
            border_style="dim",
            box=box.SIMPLE
        )
        table.add_column("Status")
        table.add_column("Type")
        table.add_column("Message")
        table.add_column("Reason")
        table.add_column("Date")

        for condition in conditions:
            table.add_row(
                DEPLOY_STATUS_MAP.get(condition["status"], "[dim]Unknown[/dim]"),
                condition['type'],
                condition.get('message', 'No message'),
                condition.get('reason', 'No reason'),
                condition['lastTransitionTime']
            )

        color = "bold green" if data['ready'] else "bold yellow"
        subtitle = f"[dim]Start a new active session with[/dim] [bold cyan]{PIPECAT_CLI_NAME} agent start {agent_name}[/bold cyan]" if data[
            'ready'] else f"[dim]For more information check logs with[/dim] [bold cyan]{PIPECAT_CLI_NAME} agent logs {agent_name}[/bold cyan]"
        console.print(
            Panel(
                Group(
                    Panel(
                        f"[{color}]Health: {'Ready' if data['ready'] else 'Stopped'}[/]",
                        border_style="green" if data['ready'] else "yellow",
                        expand=False,
                    ),
                    table),
                title=f"Status for agent [bold]{agent_name}[/bold]",
                title_align="left",
                subtitle_align="left",
                subtitle=subtitle,
            ))


@agent_cli.command(name="scale", help="Modify agent runtime configuration")
@synchronizer.create_blocking
@requires_login
async def scale():
    console.error("Not implemented")


@agent_cli.command(name="list", help="List agents in an organization.")
@synchronizer.create_blocking
@requires_login
async def list(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to list agents for"
    ),
):
    token = config.get("token")
    org = organization or config.get("org")

    with console.status(f"Fetching agents for organization: [bold]'{org}'[/bold]", spinner="dots"):
        async with aiohttp.ClientSession() as session:
            response = await session.get(
                f"{API.construct_api_url('services_path').format(org=org)}",
                headers={"Authorization": f"Bearer {token}"},
            )

        data = await response.json()

        if "error" in data:
            console.error(
                f"[red]Unable to get agents for '{org}'[/red]\n\n"
                f"[dim]API response:[/dim] {data['error']}",
            )
        else:
            table = Table(show_header=True, show_lines=True, border_style="dim", box=box.SIMPLE)
            table.add_column("Name")
            table.add_column("ID")
            table.add_column("Active Deployment")
            table.add_column("Created At")
            table.add_column("Updated At")

            for service in data['services']:
                table.add_row(
                    f"[bold]{service['name']}[/bold]",
                    service['id'],
                    service['activeDeploymentId'],
                    service['createdAt'],
                    service['updatedAt']
                )

            console.print(Panel(
                table,
                padding=1,
                title=f"[bold]Agents for organization: {org}[/bold]",
                title_align="left",
                border_style="green",
            ))


@agent_cli.command(name="logs", help="Get logs for the given agent.")
@synchronizer.create_blocking
@requires_login
async def logs(ctx: typer.Context, agent_name: str, organization: str = typer.Option(
    None,
    "--organization",
    "-o",
    help="Organization to get status of agent for"
),):
    org = organization or config.get("org")
    token = config.get("token")

    try:
        with console.status(f"Fetching logs for agent: [bold]'{agent_name}'[/bold]", spinner="dots"):
            async with aiohttp.ClientSession() as session:
                response = await session.get(
                    f"{API.construct_api_url('services_logs_path').format(org=org, service=agent_name)}?limit=100&order=desc",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status != 200:
                    response.raise_for_status()
                data = await response.json()
                console.print(data)
    except Exception:
        console.error(f"Unable to get logs for {agent_name}")


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
):
    token = config.get("token")
    org = organization or config.get("org")

    with console.status(f"Deleting agent: [bold]'{agent_name}'[/bold]", spinner="dots"):
        async with aiohttp.ClientSession() as session:
            response = await session.delete(
                f"{API.construct_api_url('services_path').format(org=org)}/{agent_name}",
                headers={"Authorization": f"Bearer {token}"},
            )
            data = await response.json()
            console.print(data)


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
        with console.status(f"Fetching deployments for agent: [bold]'{agent_name}'[/bold]", spinner="dots"):
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

            for deployment in data['deployments']:
                table.add_row(
                    deployment["id"],
                    deployment["manifest"]["spec"]["dailyNodeType"],
                    deployment["manifest"]["spec"]["image"],
                    deployment["createdAt"],
                    deployment["updatedAt"],
                )

            console.print(Panel(
                table,
                title=f"[bold]Deployments for agent: {agent_name}[/bold]",
                title_align="left",
            ))
    except Exception as e:
        logger.debug(e)
        console.api_error(error_code, f"Unable to get deployments for {agent_name}")


@agent_cli.command(name="start", help="Start an agent instance")
@synchronizer.create_blocking
@requires_login
async def start(
    ctx: typer.Context,
    agent_name: str,
    force: bool = typer.Option(
        False,
        "--force",
        "--f",
        help="Bypass prompt for confirmation",
        rich_help_panel="Start Configuration",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "--org",
        help="Organization to start agent for",
    ),
    api_key: str = typer.Option(
        None,
        "--api-key",
        "--key",
        help="Public API key to use for starting agent",
        rich_help_panel="Start Configuration",
    ),
    data: str = typer.Option(
        None,
        "--data",
        "--d",
        help="Data to pass to the agent (stringified JSON)",
        rich_help_panel="Start Configuration",
    ),
    use_daily: bool = typer.Option(
        False,
        "--use-daily",
        "--daily",
        help="Create a Daily WebRTC session for the agent",
        rich_help_panel="Start Configuration",
    ),
):
    console = Console()

    token = ctx.obj["token"]
    org = organization or ctx.obj["org"]
    default_public_key = api_key or ctx.obj["default_public_key"]
    default_public_key_name = "CLI provided" if api_key else ctx.obj["default_public_key_name"]

    if not default_public_key:
        print_api_error("PCC-1002", f"Unable to start agent '{agent_name}' without public api key")
        return typer.Exit(1)

    # Confirm start request
    if not force:
        console.print(Panel(
            f"Agent Name: {agent_name}\n"
            f"Organization: {org}\n"
            f"Public API Key: {default_public_key_name} [dim]{default_public_key}[/dim]\n"
            f"Use Daily: {use_daily}\n"
            f"Data: {data}",
            title=f"[bold]Start Request for agent: {agent_name}[/bold]",
            title_align="left",
            border_style="yellow",
        ))
        if not await questionary.confirm("Are you sure you want to start this agent?").ask_async():
            console.print("[bold]Aborting start request[/bold]")
            return typer.Exit(1)

    # Check if agent exists and is healthy
    with Live(console.status(f"Sending start request with key: {default_public_key_name}", spinner="dots"), refresh_per_second=4) as live:
        error_code = None
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{construct_api_url('start_path').format(service=agent_name)}",
                    headers={"Authorization": f"Bearer {default_public_key}"},
                    json={
                        "createDailyRoom": bool(use_daily),
                        "body": {}
                    }
                )
                if response.status != 200:
                    error_code = handle_agent_start_error(response.status)
                    response.raise_for_status()

        except Exception as e:
            live.update(console.status(f"Agent '{agent_name}' failed to start", spinner="dots"))
            live.stop()
            logger.debug(e)
            print_api_error(
                error_code,
                f"Unable to start agent '{agent_name}'. Please check logs for more information.")
            return typer.Exit(1)

        live.update(console.status(f"Agent '{agent_name}' started successfully", spinner="dots"))
        live.stop()

        console.print(Panel(
            f"Agent '{agent_name}' started successfully",
            title=f"{PANEL_TITLE_SUCCESS}",
            title_align="left",
            border_style="green",
        ))
