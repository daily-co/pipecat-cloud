import base64
import re

import aiohttp
import questionary
import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.http_utils import construct_api_url
from pipecatcloud.cli import PANEL_TITLE_ERROR, PANEL_TITLE_SUCCESS

secrets_cli = typer.Typer(
    name="secrets", help="Secret management.", no_args_is_help=True
)

console = Console()


def validate_secrets(secrets: dict):
    valid_name_pattern = re.compile(r'^[a-zA-Z0-9_-]+$')

    for key, value in secrets.items():
        if not key or not value:
            console.print(
                "[red]Error: Secrets must be provided as key-value pairs. Please reference --help for more information.[/red]")
            return typer.Exit(1)

        if len(key) > 64:
            console.print(
                "[red]Error: Secret names must not exceed 64 characters in length.[/red]")
            return typer.Exit(1)

        if not valid_name_pattern.match(key):
            console.print(
                "[red]Error: Secret names must contain only alphanumeric characters, underscores, and hyphens.[/red]")
            return typer.Exit(1)


async def _get_secret_set(name: str, org: str, token: str):
    request_url = f"{construct_api_url('secrets_path').format(org=org)}/{name}"
    logger.debug(f"Requesting secret set information from {request_url}")

    async with aiohttp.ClientSession() as session:
        response = await session.get(request_url, headers={"Authorization": f"Bearer {token}"})
        if response.status != 200:
            return None
        return await response.json()


async def _create_secret(name: str, secret_key: str, secret_value: str, org: str, token: str):
    request_url = f"{construct_api_url('secrets_path').format(org=org)}/{name}"
    async with aiohttp.ClientSession() as session:
        response = await session.put(request_url, headers={"Authorization": f"Bearer {token}"}, json={
            "name": name,
            "isImagePullSecret": False,
            "secretKey": secret_key,
            "secretValue": secret_value
        })
        response.raise_for_status()


async def _delete_secret_set(name: str, org: str, token: str):
    request_url = f"{construct_api_url('secrets_path').format(org=org)}/{name}"
    logger.debug(f"Deleting secret set {name} from {request_url}")

    async with aiohttp.ClientSession() as session:
        response = await session.delete(request_url, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()


async def _delete_secret(name: str, secret_name: str, org: str, token: str):
    request_url = f"{construct_api_url('secrets_path').format(org=org)}/{name}/{secret_name}"
    logger.debug(f"Deleting secret {secret_name} from {request_url}")

    async with aiohttp.ClientSession() as session:
        response = await session.delete(request_url, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()


async def _create_image_pull_secret(name: str, host: str, credentials: str, org: str, token: str):
    request_url = f"{construct_api_url('secrets_path').format(org=org)}/{name}"
    async with aiohttp.ClientSession() as session:
        response = await session.put(request_url, headers={"Authorization": f"Bearer {token}"}, json={
            "isImagePullSecret": True,
            "secretValue": credentials,
            "host": host
        })
        response.raise_for_status()


@secrets_cli.command(name="set", help="Create a new secret set for active organization")
@synchronizer.create_blocking
@requires_login
async def set(
    ctx: typer.Context,
    name: str = typer.Argument(
        None,
        help="Name of the secret set to create e.g. 'my-secret-set'"
    ),
    secrets: list[str] = typer.Argument(
        None,
        help="List of secret key-value pairs e.g. 'KEY1=value1 KEY2=\"value with spaces\"'",
    )
):
    # Parse secrets
    if not secrets:
        console.print(
            "[red]Error: Secrets must be provided as key-value pairs. Please reference --help for more information.[/red]")
        return typer.Exit(1)

    secrets_dict = {}
    for secret in secrets:
        if '=' not in secret:
            console.print(
                "[red]Error: Secrets must be provided as key-value pairs using '=' separator. Example: KEY=value[/red]")
            return typer.Exit(1)

        key, value = secret.split('=', 1)  # Split on first = only
        key = key.strip()

        # Handle quoted values while preserving quotes within the value
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            # Remove only the enclosing quotes
            value = value[1:-1]

        if not key or not value:
            console.print(
                "[red]Error: Both key and value must be provided for each secret.[/red]")
            return typer.Exit(1)

        secrets_dict[key] = value

    validate_secrets(secrets_dict)

    # Prompt for set name if not provided
    if not name:
        name = typer.prompt("Enter the name for your secret set")

    # Confirm if we are sure we want to create a new secret set (if one doesn't already exist)
    existing_set = None
    with console.status(f"Retrieving secret set [bold]'{name}'[/bold]", spinner="dots"):
        try:
            existing_set = await _get_secret_set(name, ctx.obj["org"], ctx.obj["token"])
        except Exception as e:
            console.print(Panel(
                f"[red]Unable to retrieve secret set. Operation failed with error: {e}[/red]",
                title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                title_align="left",
                border_style="red",
            ))
            return typer.Exit(1)

    # Check for overlapping secret names
    if existing_set:
        existing_secret_names = {secret['fieldName'] for secret in existing_set['secrets']}
        overlapping_secrets = existing_secret_names.intersection(secrets_dict.keys())

        if overlapping_secrets:
            create = await questionary.confirm(
                f"The following secrets already exist and will be overwritten: {', '.join(overlapping_secrets)}. Would you like to continue?").ask_async()
            if not create:
                console.print("[bold red]Secret set creation cancelled[/bold red]")
                return typer.Exit(1)
    else:
        create = await questionary.confirm(
            f"Secret set with name '{name}' does not exist. Would you like to create it?").ask_async()
        if not create:
            console.print("[bold red]Secret set creation cancelled[/bold red]")
            return typer.Exit(1)

    try:
        with console.status(f"{'Modifying' if existing_set else 'Creating'} secret set [bold]'{name}'[/bold]", spinner="dots"):
            for key, value in secrets_dict.items():
                await _create_secret(name, key, value, ctx.obj["org"], ctx.obj["token"])
    except Exception as e:
        console.print(Panel(
            f"[red]Unable to create secret set. Operation failed with error: {e}[/red]",
            title=f"[red]{PANEL_TITLE_ERROR}[/red]",
            title_align="left",
            border_style="red",
        ))
        return typer.Exit(1)

    action = "created" if not existing_set else "modified"
    console.print(
        Panel(
            f"[bold green]Secret set '{name}' {action} successfully[/bold green]",
            title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
            title_align="left",
            border_style="green",
        ))


@secrets_cli.command(name="unset", help="Delete a secret within specified secret set")
@synchronizer.create_blocking
@requires_login
async def unset(
    ctx: typer.Context,
    name: str = typer.Argument(
        None,
        help="Name of the secret set to delete a secret from e.g. 'my-secret-set'"
    ),
    secret_key: str = typer.Argument(
        None,
        help="Name of the secret to delete e.g. 'my-secret'",
    )
):
    if not name or not secret_key:
        console.print(
            "[red]Error: Secret set name and secret name must be provided. Please reference --help for more information.[/red]")
        return typer.Exit(1)

    try:
        with console.status(f"Deleting secret [bold]'{secret_key}'[/bold] from secret set [bold]'{name}'[/bold]", spinner="dots"):
            await _delete_secret(name, secret_key, ctx.obj["org"], ctx.obj["token"])
    except Exception:
        console.print(
            Panel(
                f"[red]Unable to delete secret '{secret_key}' from secret set '{name}'. Are you sure it exists?[/red]",
                title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                title_align="left",
                border_style="red",
            ))
        return typer.Exit(1)

    console.print(
        Panel(
            f"[bold green]Secret '{secret_key}' deleted successfully from secret set '{name}'[/bold green]",
            title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
            title_align="left",
            border_style="green",
        ))


@secrets_cli.command(name="list", help="List secrets for active organization")
@synchronizer.create_blocking
@requires_login
async def list(ctx: typer.Context, name: str = typer.Argument(
    None,
    help="Name of the secret set to list secrets from e.g. 'my-secret-set'"
)):
    token = ctx.obj["token"]
    org = ctx.obj["org"]

    if not name:
        request_url = f"{construct_api_url('secrets_path').format(org=org)}"
    else:
        request_url = f"{construct_api_url('secrets_path').format(org=org)}/{name}"

    logger.debug(f"Requesting secrets from {request_url}")

    try:
        with console.status("Retrieving secret sets for organization", spinner="dots"):
            async with aiohttp.ClientSession() as session:
                response = await session.get(request_url, headers={"Authorization": f"Bearer {token}"})
                response.raise_for_status()
                data = await response.json()

                if name:
                    for s in data["secrets"]:
                        table = Table(
                            border_style="dim",
                            show_edge=True,
                            show_lines=False)
                        table.add_column(name, style="white")
                        table.add_row(s["fieldName"])

                    console.print(table)
                else:
                    console.print(f"[bold green]Secret sets for organization {org}:[/bold green]")
                    table = Table(
                        border_style="dim",
                        show_edge=True,
                        show_lines=False)
                    table.add_column("Secret Set Name", style="white")
                    table.add_column("Type", style="white")
                    for s in data["sets"]:
                        if s["type"] == "imagePullSecret":
                            table.add_row(s["name"], "Image Pull Secret")
                        else:
                            table.add_row(s["name"], "Secret")

                    console.print(table)

    except Exception:
        console.print(Panel(
            "[red]Unable to retrieve secret sets. Please contact support.[/red]",
            title=f"[red]{PANEL_TITLE_ERROR}[/red]",
            title_align="left",
            border_style="red",
        ))


@secrets_cli.command(name="delete", help="Delete a secret set from active organization")
@synchronizer.create_blocking
@requires_login
async def delete(
    ctx: typer.Context,
    name: str = typer.Argument(
        None,
        help="Name of the secret set to delete e.g. 'my-secret-set'"
    ),
):
    if not name:
        console.print(
            "[red]Error: Secret set name must be provided. Please reference --help for more information.[/red]")
        return typer.Exit(1)

    # Confirm to proceed
    confirm = await questionary.confirm(
        f"Are you sure you want to delete secret set '{name}'?").ask_async()
    if not confirm:
        console.print("[bold red]Secret deletion cancelled[/bold red]")
        return typer.Exit(1)

    try:
        with console.status(f"Deleting secret set [bold]'{name}'[/bold]", spinner="dots"):
            await _delete_secret_set(name, ctx.obj["org"], ctx.obj["token"])
    except Exception:
        console.print(Panel(
            f"[red]Unable to delete secret set '{name}'. Are you sure it exists?[/red]",
            title=f"[red]{PANEL_TITLE_ERROR}[/red]",
            title_align="left",
            border_style="red",
        ))
        return typer.Exit(1)

    console.print(
        Panel(
            f"[bold green]Secret set '{name}' deleted successfully[/bold green]",
            title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
            title_align="left",
            border_style="green",
        ))


@secrets_cli.command(name="image-pull-secret",
                     help="Create an image pull secret for active organization. See https://docs.pipecat.cloud/deployment/agent-images for more information.")
@synchronizer.create_blocking
@requires_login
async def image_pull_secret(
    ctx: typer.Context,
    name: str = typer.Argument(
        None,
        help="Name of the image pull secret to reference in deployment e.g. 'my-image-pull-secret'"
    ),
    host: str = typer.Argument(
        None,
        help="Host address of the image repository e.g. https://index.docker.io/v1/"
    ),
    credentials: str = typer.Argument(
        None,
        help="Credentials of the image repository e.g. 'username:password'"
    ),
    base64encode: bool = typer.Option(
        True,
        "--base64encode",
        help="Encode credentials in base64 format"
    )
):
    org = ctx.obj["org"]
    token = ctx.obj["token"]

    if not name or not host:
        console.print(
            "[red]Error: Name and host must be provided. Please reference --help for more information.[/red]")
        return typer.Exit(1)

    if not credentials:
        username = await questionary.text(
            f"Enter username for image repository '{host}'").ask_async()
        password = await questionary.password(
            f"Enter password for image repository '{host}'").ask_async()
        if not username or not password:
            console.print("[bold red]Image pull secret creation cancelled[/bold red]")
            return typer.Exit(1)
        credentials = f"{username}:{password}"

    if base64encode:
        credentials = base64.b64encode(credentials.encode()).decode()

    # Check if secret already exists
    with console.status(f"Checking if image pull secret '{name}' already exists", spinner="dots"):
        request_url = f"{construct_api_url('secrets_path').format(org=org)}"
        async with aiohttp.ClientSession() as session:
            response = await session.get(request_url, headers={"Authorization": f"Bearer {token}"})
            response.raise_for_status()
            data = await response.json()
            existing_secret = next(
                (s for s in data["sets"] if s["name"] == name and s["type"] == "imagePullSecret"), None)
            if existing_secret:
                console.print(
                    Panel(
                        f"[red]Image pull secret '{name}' already exists. Please choose a different name.[/red]",
                        title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                        title_align="left",
                        border_style="red",
                    ))
                return typer.Exit(1)

    try:
        with console.status(f"Creating image pull secret [bold]'{name}'[/bold]", spinner="dots"):
            await _create_image_pull_secret(name, host, credentials, org, token)
    except Exception as e:
        console.print(
            Panel(
                f"[red]Unable to create image pull secret '{name}'. Operation failed with error: {e}[/red]",
                title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                title_align="left",
                border_style="red",
            ))
        return typer.Exit(1)

    console.print(
        Panel(
            f"[bold green]Image pull secret '{name}' for {host} created successfully[/bold green]",
            title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
            title_align="left",
            border_style="green",
        ))
