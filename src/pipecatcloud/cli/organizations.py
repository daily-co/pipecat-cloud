import aiohttp
import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud.config import config, _store_user_config, user_config_path
from pipecatcloud.exception import AuthError

organization_cli = typer.Typer(
    name="organizations", help="User organizations.", no_args_is_help=True
)


async def _retrieve_organizations(ctx: typer.Context):
    console = Console()
    token = ctx.obj["token"]
    org_list = []
    with console.status("Fetching user organizations", spinner="dots"):
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(
                    f"{config.get('server_url')}{config.get('organization_path')}",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status == 401:
                        raise AuthError()
                    if resp.status == 200:
                        data = await resp.json()
                        org_list = data["organizations"]
                    else:
                        raise Exception(f"Failed to retrieve account organization: {resp.status}")
            except Exception as e:
                console.print(f"[red]Error: {e}[/red]")

    return org_list


@organization_cli.command(name="select", help="Select an organization to use.")
@synchronizer.create_blocking
@requires_login
async def select(ctx: typer.Context):
    console = Console()
    current_org = ctx.obj["org"]
    org_list = await _retrieve_organizations(ctx)

    value = await questionary.select(
        "Select active organization",
        choices=[{"name": f"{org['name']} (current)", "value": org["name"], "checked": org["name"] == current_org} for org in org_list],
    ).ask_async()

    if not value:
        return

    _store_user_config(ctx.obj["token"], value)

    console.print(Panel(
        f"Current organization set to [bold green]{value}[/bold green]\n"
        f"[dim]Account updated in {user_config_path}[/dim]",
        title=f"[green]Organization updated[/green]",
        title_align="left",
        border_style="green",
    ))


@organization_cli.command(name="list", help="List organizations user is a member of.")
@synchronizer.create_blocking
@requires_login
async def list(ctx: typer.Context):
    console = Console()
    current_org = ctx.obj["org"]
    org_list = await _retrieve_organizations(ctx)

    if len(org_list) == 0:
        console.print("[red]No organizations found[/red]")
        return
    else:
        console.print(f"[green]Found {len(org_list)} organizations[/green]")

    table = Table(
        border_style="dim",
        show_edge=True,
        show_lines=False)
    table.add_column("Organization", style="white")
    table.add_column("Name", style="white")
    for org in org_list:
        if org["name"] == current_org:
            table.add_row("[cyan bold]Verbose name placeholder[/cyan bold]",
                          f"[cyan bold]{org['name']} (active)[/cyan bold]")
        else:
            table.add_row("Verbose name placeholder", org["name"])

    console.print(table)
