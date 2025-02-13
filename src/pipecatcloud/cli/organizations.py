import aiohttp
import questionary
import typer
from rich import box
from rich.table import Table

from pipecatcloud import PIPECAT_CLI_NAME
from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console, print_api_error
from pipecatcloud._utils.http_utils import construct_api_url
from pipecatcloud.api import API
from pipecatcloud.config import (
    _store_user_config,
    dashboard_host,
    update_user_config,
    user_config_path,
)

organization_cli = typer.Typer(
    name="organizations", help="User organizations.", no_args_is_help=True
)
keys_cli = typer.Typer(name="keys", help="API key management commands.", no_args_is_help=True)
organization_cli.add_typer(keys_cli)


# ---- Methods

def _set_key_as_default(org: str, key_name: str, key_value: str):
    update_user_config({"default_public_key": key_value, "default_public_key_name": key_name}, org)


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


# ---- Commands
@organization_cli.command(name="select", help="Select an organization to use.")
@synchronizer.create_blocking
@requires_login
async def select(
    ctx: typer.Context,
    organization: str = typer.Option(
        None,
        "--organization",
        "-o"
    )
):
    current_org = ctx.obj["org"]

    with console.status("[dim]Retrieve user namespace / organization data...[/dim]", spinner="dots"):
        org_list, error = await API.organizations()

        if error:
            typer.Exit()

    try:
        selected_org = None, None
        if not organization:
            # Prompt user to select organization
            value = await questionary.select(
                "Select default namespace / organization",
                choices=[{"name": f"{org['verboseName']} ({org['name']})", "value": (org["name"], org["verboseName"]), "checked": org["name"] == current_org} for org in org_list],
            ).ask_async()

            if not value:
                return typer.Exit(1)

            selected_org = value[0], value[1]

        else:
            # Attempt to match passed org with results
            match = None
            for o in org_list:
                if o["name"] == organization:
                    match = o
            if not match:
                console.error(
                    f"Unable to find namespace [bold]'{organization}'[/bold] in user's available organizations"
                )
                return typer.Exit(1)
            selected_org = match["name"], match["verboseName"]

        _store_user_config(ctx.obj["token"], selected_org[0])

        console.success(
            f"Current organization set to [bold green]{selected_org[1]} [dim]({selected_org[0]})[/dim][/bold green]\n"
            f"[dim]Default namespace updated in {user_config_path}[/dim]")
    except Exception:
        console.error("Unable to update user credentials. Please contact support.")


@organization_cli.command(name="list", help="List organizations user is a member of.")
@synchronizer.create_blocking
@requires_login
async def list(ctx: typer.Context):
    current_org = ctx.obj["org"]
    with console.status("[dim]Retrieve user namespace / organization data...[/dim]", spinner="dots"):
        org_list, error = await API.organizations()

        if error:
            return typer.Exit()

    if not org_list or not len(org_list):
        console.error(
            "No namespaces associated with user account. Please complete onboarding via the dashboard.",
            subtitle=dashboard_host)
        return typer.Exit(1)

    table = Table(
        border_style="dim",
        box=box.SIMPLE,
        show_edge=True,
        show_lines=False)
    table.add_column("Organization", style="white")
    table.add_column("Name", style="white")
    for org in org_list:
        if org["name"] == current_org:
            table.add_row(f"[cyan bold]{org['verboseName']}[/cyan bold]",
                          f"[cyan bold]{org['name']} (active)[/cyan bold]")
        else:
            table.add_row(org["verboseName"], org["name"])

    console.success(table, title_extra=f"{len(org_list)} results")


# ---- API Token Commands ----

@keys_cli.command(name="list", help="List API keys for an organization.")
@synchronizer.create_blocking
@requires_login
async def keys(
    ctx: typer.Context,
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to list API keys for",
    ),
):
    org = organization or ctx.obj["org"]

    try:
        with console.status(f"[dim]Fetching API keys for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"):
            data = await API.api_keys(org)

            if len(data["public"]) == 0:
                console.error(
                    f"[bold]No API keys found.[/bold]\n"
                    f"[dim]Create a new API key with the "
                    f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
                )
                return typer.Exit(1)

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

            console.success(table, title_extra=f"API keys for organization: {org}")
    except Exception:
        console.error("Failed to fetch API keys. Please contact support")


@keys_cli.command(name="create", help="Create an API key for an organization.")
@synchronizer.create_blocking
@requires_login
async def create_key(
    ctx: typer.Context,
    api_key_name: str = typer.Option(
        None,
        "--name",
        "-n",
        help="Human readable name for new API key",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to create API key for",
    ),
    default: bool = typer.Option(
        False,
        "--default",
        "-d",
        help="Set the newly created key as the active / default key in local config"
    )
):
    org = organization or ctx.obj["org"]

    if not api_key_name:
        api_key_name = await questionary.text(
            "Enter human readable name for API key e.g. 'Pipecat Key'"
        ).ask_async()

    if not api_key_name or api_key_name == "":
        console.error("You must enter a name for the API key")
        return typer.Exit(1)

    error_code = None
    data = None
    try:
        with console.status(f"[dim]Creating API key with name: [bold]'{api_key_name}'[/bold][/dim]", spinner="dots"):
            data = await API.api_key_create(api_key_name, org)
    except Exception:
        console.api_error(error_code, title="Error creating API key")
        return typer.Exit(1)

    if not data or 'key' not in data:
        console.error("Invalid response from server. Please contact support.")
        return typer.Exit(1)

    # Determine as to whether we should make this key the active default
    make_active = default
    if not default:
        make_active = await questionary.confirm("Would you like to make this key the default key in your local configuration?", default=False).ask_async()

    if make_active:
        _set_key_as_default(org, api_key_name, data['key'])
    else:
        console.print("[dim]Bypassing using key as default")

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

    console.success(table)


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
