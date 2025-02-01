import aiohttp
import questionary
import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipecatcloud import PIPECAT_CLI_NAME
from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import print_api_error
from pipecatcloud._utils.http_utils import construct_api_url
from pipecatcloud.cli import PANEL_TITLE_SUCCESS
from pipecatcloud.config import _store_user_config, config, user_config_path
from pipecatcloud.exception import AuthError

organization_cli = typer.Typer(
    name="organizations", help="User organizations.", no_args_is_help=True
)
keys_cli = typer.Typer(name="keys", help="API key management commands.", no_args_is_help=True)
organization_cli.add_typer(keys_cli)


# ---- Organization Methods ----

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


async def _get_api_tokens(org_id: str, token: str):
    if not org_id:
        raise ValueError("Organization ID is required")
    if not token:
        raise ValueError("Token is required")

    async with aiohttp.ClientSession() as session:
        response = await session.get(
            construct_api_url('api_keys_path').format(org=org_id),
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        data = await response.json()
        return data


# ---- Organization Commands ----
@organization_cli.command(name="select", help="Select an organization to use.")
@synchronizer.create_blocking
@requires_login
async def select(ctx: typer.Context):
    console = Console()
    current_org = ctx.obj["org"]
    org_list = await _retrieve_organizations(ctx)

    value = await questionary.select(
        "Select active organization",
        choices=[{"name": f"{org['verboseName']} ({org['name']})", "value": (org["name"], org["verboseName"]), "checked": org["name"] == current_org} for org in org_list],
    ).ask_async()

    if not value:
        return

    _store_user_config(ctx.obj["token"], value[0])

    console.print(Panel(
        f"Current organization set to [bold green]{value[1]} [dim]({value[0]})[/dim][/bold green]\n"
        f"[dim]Account updated in {user_config_path}[/dim]",
        title="[green]Organization updated[/green]",
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
            table.add_row(org["verboseName"], f"[cyan bold]{org['name']} (active)[/cyan bold]")
        else:
            table.add_row(org["verboseName"], org["name"])

    console.print(table)


# ---- API Token Commands ----

@keys_cli.command(name="list", help="List API keys for an organization.")
@synchronizer.create_blocking
@requires_login
async def keys(
    ctx: typer.Context,
    organization: str = typer.Option(
        None,
        "--organization",
        "--org",
        help="Organization to get tokens for",
    ),
):
    console = Console()
    token = ctx.obj["token"]
    org = organization or ctx.obj["org"]

    with console.status(f"Fetching API keys for organization: [bold]'{org}'[/bold]", spinner="dots"):
        data = await _get_api_tokens(org, token)

    if len(data["public"]) == 0:
        console.print(
            f"[bold]No API keys found.[/bold]\n"
            f"[dim]Create a new API key with the "
            f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
        )
        return

    table = Table(
        show_header=True,
        show_lines=True,
        border_style="dim",
        box=box.SIMPLE,
    )
    table.add_column("Name")
    table.add_column("Key")
    table.add_column("Created At")
    table.add_column("Status")

    for key in data["public"]:
        table.add_row(
            key["metadata"]["name"],
            key["key"],
            key["createdAt"],
            "Revoked" if key["revoked"] else "Active",
            style="red" if key["revoked"] else None,
        )

    console.print(Panel(
        table,
        title=f"[bold]API keys for organization: {org}[/bold]",
        title_align="left",
    ))


@keys_cli.command(name="delete", help="Delete an API key for an organization.")
@synchronizer.create_blocking
@requires_login
async def delete_key(
    ctx: typer.Context,
    organization: str = typer.Option(
        None,
        "--organization",
        "--org",
        help="Organization to get tokens for",
    ),
):
    console = Console()
    token = ctx.obj["token"]
    org = organization or ctx.obj["org"]

    with console.status(f"Fetching API keys for organization: [bold]'{org}'[/bold]", spinner="dots"):
        data = await _get_api_tokens(org, token)

    if len(data["public"]) == 0:
        console.print(
            f"[bold]No API keys found.[/bold]\n"
            f"[dim]Create a new API key with the "
            f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
        )
        typer.Exit(1)
        return

    # Prompt user to delete a key
    key_id = await questionary.select(
        "Select API key to delete",
        choices=[{"name": key["metadata"]["name"], "value": key["id"]} for key in data["public"]],
    ).ask_async()

    if not key_id:
        typer.Exit(1)

    try:
        error_code = None
        with console.status(f"Deleting API key with ID: [bold]'{key_id}'[/bold]", spinner="dots"):
            async with aiohttp.ClientSession() as session:
                response = await session.delete(
                    f"{construct_api_url('api_keys_path').format(org=org)}/{key_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status != 204:
                    error_code = str(response.status)
                    response.raise_for_status()
    except Exception:
        print_api_error(error_code, title="Error deleting API key")
        typer.Exit(1)

    console.print(f"[green]API key with ID: [bold]'{key_id}'[/bold] deleted successfully.[/green]")


@keys_cli.command(name="create", help="Create an API key for an organization.")
@synchronizer.create_blocking
@requires_login
async def create_key(
    ctx: typer.Context,
    organization: str = typer.Option(
        None,
        "--organization",
        "--org",
        help="Organization to get tokens for",
    ),
):
    console = Console()
    token = ctx.obj["token"]
    org = organization or ctx.obj["org"]

    api_key_name = await questionary.text(
        "Enter human readable name for API key e.g. 'Pipecat Key'"
    ).ask_async()

    if not api_key_name:
        typer.Exit(1)

    error_code = None
    data = None
    try:
        with console.status(f"Creating API key with name: [bold]'{api_key_name}'[/bold]", spinner="dots"):
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{construct_api_url('api_keys_path').format(org=org)}",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"name": api_key_name, "type": "public"},
                )
                if response.status != 200:
                    error_code = str(response.status)
                    response.raise_for_status()
                data = await response.json()
    except Exception:
        print_api_error(error_code, title="Error creating API key")
        typer.Exit(1)
        return

    if not data or 'key' not in data:
        console.print("[red]Error: Invalid response from server[/red]")
        typer.Exit(1)
        return

    table = Table(
        show_header=True,
        show_lines=True,
        border_style="dim",
        box=box.SIMPLE,
    )
    table.add_column("Name")
    table.add_column("Key")
    table.add_column("Organization")

    table.add_row(
        api_key_name,
        data['key'],
        org,
    )

    console.print(
        Panel(
            table,
            title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
            title_align="left",
            border_style="green",
            subtitle=f"Use the key by default by running [bold]{PIPECAT_CLI_NAME} organizations keys use[/bold]",
            subtitle_align="left",
        ))


@keys_cli.command(name="use", help="Set default API key for an organization in local config.")
@synchronizer.create_blocking
@requires_login
async def use_key(
    ctx: typer.Context,
    organization: str = typer.Option(
        None,
        "--organization",
        "--org",
        help="Organization to get tokens for",
    ),
):
    console = Console()
    token = ctx.obj["token"]
    org = organization or ctx.obj["org"]

    with console.status(f"Fetching API keys for organization: [bold]'{org}'[/bold]", spinner="dots"):
        data = await _get_api_tokens(org, token)

    if len(data["public"]) == 0:
        console.print(
            f"[bold]No API keys found.[/bold]\n"
            f"[dim]Create a new API key with the "
            f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
        )
        typer.Exit(1)
        return

    # Prompt user to use a key
    key_id = await questionary.select(
        "Select API key to delete",
        choices=[{"name": key["metadata"]["name"], "value": (key["key"], key["metadata"]["name"])} for key in data["public"]],
    ).ask_async()

    if not key_id:
        typer.Exit(1)
        return

    _store_user_config(
        token, org, {
            "default_public_key": key_id[0], "default_public_key_name": key_id[1]})

    console.print(f"[green]API key with ID: [bold]'{key_id}'[/bold] set as default.[/green]")
