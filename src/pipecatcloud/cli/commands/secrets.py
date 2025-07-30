#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import base64
import os
import re
from xmlrpc.client import boolean

import questionary
import typer
from loguru import logger
from rich import box
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

secrets_cli = typer.Typer(
    name="secrets", help="Secret and image pull secret management", no_args_is_help=True
)


# ---- Methods ----


def validate_secrets(secrets: dict):
    valid_name_pattern = re.compile(r"^[a-zA-Z0-9_-]+$")

    for key, value in secrets.items():
        if not key or not value:
            console.print(
                "[red]Error: Secrets must be provided as key-value pairs. Please reference --help for more information.[/red]"
            )
            return typer.Exit(1)

        if len(key) > 64:
            console.print("[red]Error: Secret names must not exceed 64 characters in length.[/red]")
            return typer.Exit(1)

        if not valid_name_pattern.match(key):
            console.print(
                "[red]Error: Secret names must contain only alphanumeric characters, underscores, and hyphens.[/red]"
            )
            return typer.Exit(1)


def validate_secret_name(name: str):
    valid_name_pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$")
    return bool(valid_name_pattern.match(name))


# ---- Commands ----


@secrets_cli.command(name="set", help="Create a new secret set for active organization")
@synchronizer.create_blocking
@requires_login
async def set(
    name: str = typer.Argument(help="Name of the secret set to create e.g. 'my-secret-set'"),
    secrets: list[str] = typer.Argument(
        None,
        help="List of secret key-value pairs e.g. 'KEY1=value1 KEY2=\"value with spaces\"'",
    ),
    from_file: str = typer.Option(
        None,
        "--file",
        "-f",
        help="Load secrets from a relative file path",
    ),
    skip_confirm: boolean = typer.Option(
        False,
        "--skip",
        "-s",
        help="Skip confirmations / force creation or update",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to create secret set in",
    ),
):
    if not validate_secret_name(name):
        console.print(
            "[red]Secret set name must only contain characters, numbers and hyphens.[/red]"
        )
        return typer.Exit(1)

    if not secrets and not from_file:
        console.print(
            "[red]Command requires either passed key-values or relative file path. See --help for more information.[/red]"
        )
        return typer.Exit(1)

    if secrets and from_file:
        console.print("[red]Cannot pass key-value pairs with --file option")
        return typer.Exit(1)

    secrets_dict = {}
    org = organization or config.get("org")

    # Load file if provided
    if from_file:
        if not os.path.exists(from_file):
            console.print(f"[red]Error: File '{from_file}' does not exist.[/red]")
            return typer.Exit(1)

        try:
            with open(from_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue

                    if "=" not in line:
                        console.print(
                            f"[red]Error: Invalid line format in {from_file}. Each line must be a key-value pair using '=' separator.[/red]"
                        )
                        return typer.Exit(1)

                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip()

                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]

                    if not key or not value:
                        console.error(f"Error: Empty key or value found in {from_file}")
                        return typer.Exit(1)

                    secrets_dict[key] = value

            if not secrets_dict:
                console.error(f"Error: No valid secrets found in {from_file}")
                return typer.Exit(1)
        except Exception as e:
            console.error(f"Error reading file '{from_file}': {str(e)}")
            return typer.Exit(1)

    else:
        for secret in secrets:
            if "=" not in secret:
                console.error(
                    "Error: Secrets must be provided as key-value pairs using '=' separator. Example: KEY=value"
                )
                return typer.Exit(1)

            key, value = secret.split("=", 1)
            key = key.strip()

            # Handle quoted values while preserving quotes within the value
            value = value.strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]

            if not key or not value:
                console.print(
                    "[red]Error: Both key and value must be provided for each secret.[/red]"
                )
                return typer.Exit(1)

            secrets_dict[key] = value

    logger.debug(secrets_dict)

    validate_secrets(secrets_dict)

    if not skip_confirm:
        table = Table(
            border_style="dim", box=box.SIMPLE, show_header=True, show_edge=True, show_lines=False
        )
        table.add_column("Key", style="white")
        table.add_column("Value Preview", style="white")
        for key, value in secrets_dict.items():
            preview = value[:5] + "..." if len(value) > 5 else value
            table.add_row(key, preview)
        console.print(
            Panel(
                table,
                title="[bold]Secrets to create / modify in set[/bold]",
                title_align="left",
            )
        )
        # Confirm our secrets
        looks_good = await questionary.confirm(
            "Would you like to proceed with these secrets?"
        ).ask_async()
        if not looks_good:
            return typer.Exit(1)

    # Confirm if we are sure we want to create a new secret set (if one doesn't already exist)
    existing_set = None
    with console.status(
        f"[dim]Checking for existing secret set with name [bold]'{name}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.secrets_list(org=org, secret_set=name)

        if error:
            return typer.Exit()

        if data and len(data):
            existing_set = data

    # Check for overlapping secret names
    if existing_set:
        existing_secret_names = {secret["fieldName"] for secret in existing_set}
        overlapping_secrets = existing_secret_names.intersection(secrets_dict.keys())

        if overlapping_secrets and not skip_confirm:
            create = await questionary.confirm(
                f"The following secret(s) already exist in {name} will be overwritten: {', '.join(overlapping_secrets)}. Would you like to continue?"
            ).ask_async()
            if not create:
                console.print("[bold red]Secret set creation cancelled[/bold red]")
                return typer.Exit(1)

    with console.status(
        f"[dim]{'Modifying' if existing_set else 'Creating'} secret set [bold]'{name}'[/bold][/dim]",
        spinner="dots",
    ):
        for key, value in secrets_dict.items():
            data, error = await API.secrets_upsert(
                data={
                    "name": name,
                    "isImagePullSecret": False,
                    "secretKey": key,
                    "secretValue": value,
                },
                set_name=name,
                org=org,
            )

            if error:
                return typer.Exit()

    action = "created" if not existing_set else "modified"
    message = f"Secret set [bold green]'{name}'[/bold green] {action} successfully"
    if action == "modified":
        message += "\n[bold white]You must re-deploy any agents using this secret set for changes to take effect[/bold white]"
    else:
        message += f"\n[dim]Deploy your agent with {PIPECAT_CLI_NAME} deploy agent-name --secrets {name}[/dim]"
    console.success(message)


@secrets_cli.command(name="unset", help="Delete a secret within specified secret set")
@synchronizer.create_blocking
@requires_login
async def unset(
    name: str = typer.Argument(
        None, help="Name of the secret set to delete a secret from e.g. 'my-secret-set'"
    ),
    secret_key: str = typer.Argument(
        None,
        help="Name of the secret to delete e.g. 'my-secret'",
    ),
    skip_confirm: boolean = typer.Option(
        False,
        "--skip",
        "-s",
        help="Skip confirmations / force creation or update",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to create secret set in",
    ),
):
    org = organization or config.get("org")

    if not name or not secret_key:
        console.error(
            "Error: Secret set name and secret name must be provided. Please reference --help for more information."
        )
        return typer.Exit(1)

    # Confirm to proceed
    if not skip_confirm:
        confirm = await questionary.confirm(
            f"Are you sure you want to unset secret with key '{secret_key}' from set '{name}'?"
        ).ask_async()
        if not confirm:
            console.error("Secret key unset cancelled")
            return typer.Exit(1)

    with console.status(
        f"[dim]Deleting secret [bold]'{secret_key}'[/bold] from secret set [bold]'{name}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.secrets_delete(set_name=name, secret_name=secret_key, org=org)

        if not data:
            console.error(f"Key not found in set '{name}'")
            return typer.Exit()

        if error:
            return typer.Exit()

    console.success(
        f"Secret [bold green]'{secret_key}'[/bold green] deleted successfully from secret set [bold green]'{name}'[/bold green]"
    )


@secrets_cli.command(name="list", help="List secret sets and set keys")
@synchronizer.create_blocking
@requires_login
async def list(
    name: str = typer.Argument(
        None, help="Name of the secret set to list secrets from e.g. 'my-secret-set'"
    ),
    show_all: boolean = typer.Option(
        True,
        "--sets",
        "-s",
        help="Filter results to show secret sets only (no image pull secrets)",
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")
    status_title = "Retrieving secret sets"

    logger.debug(f"Secret set name to lookup: {name}")

    with console.status(f"[dim]{status_title}[/dim]", spinner="dots"):
        data, error = await API.bubble_error().secrets_list(org=org, secret_set=name)

        if error:
            if error == 400:
                console.error("Unable to lookup image pull secrets")
            else:
                API.print_error()
            return typer.Exit()

        if not data or not len(data):
            if name:
                console.error(
                    f"No secrets sets with name [bold]'{name}'[/bold] found in [bold]'{org}'[/bold]"
                )
            else:
                console.error(f"No secrets sets for namespace / organization [bold]'{org}'[/bold]")
            return typer.Exit()

        if name:
            # Match

            table = Table(border_style="dim", show_header=False, show_edge=True, show_lines=True)
            table.add_column(name, style="white")
            for s in data:
                table.add_row(s["fieldName"])
            console.print(
                Panel(
                    table,
                    title=f"[bold]Secret keys for set [green]{name}[/green][/bold]",
                    title_align="left",
                )
            )
        else:
            # Filter out image pull secrets if show all is False
            filtered_sets = [s for s in data if show_all or s["type"] != "imagePullSecret"]

            if not filtered_sets or not len(filtered_sets):
                console.error(f"No secret sets in namespace / organization [bold]'{org}'[/bold]")
                return typer.Exit()

            table = Table(
                show_header=True,
                box=box.SIMPLE,
                border_style="dim",
                show_edge=True,
                show_lines=False,
            )
            table.add_column("Secret Set Name", style="white")
            if show_all:
                table.add_column("Type", style="white")
                for secret_set in filtered_sets:
                    set_type = (
                        "Image Pull Secret"
                        if secret_set["type"] == "imagePullSecret"
                        else "Secret Set"
                    )
                    table.add_row(secret_set["name"], set_type)
            else:
                for secret_set in filtered_sets:
                    table.add_row(secret_set["name"])

            console.success(table, title_extra=f"Secret sets for {org}")


@secrets_cli.command(name="delete", help="Delete a secret set from active organization")
@synchronizer.create_blocking
@requires_login
async def delete(
    name: str = typer.Argument(help="Name of the secret set to delete e.g. 'my-secret-set'"),
    skip_confirm: boolean = typer.Option(
        False,
        "--skip",
        "-s",
        help="Skip confirmations / force creation or update",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
    ),
):
    org = organization or config.get("org")

    # Confirm to proceed
    if not skip_confirm:
        confirm = await questionary.confirm(
            f"Are you sure you want to delete secret set '{name}'?"
        ).ask_async()
        if not confirm:
            console.print("[bold red]Secret deletion cancelled[/bold red]")
            return typer.Exit(1)

    with console.status(f"Deleting secret set [bold]'{name}'[/bold]", spinner="dots"):
        data, error = await API.secrets_delete_set(set_name=name, org=org)

        if not data:
            console.error(f"No set found with name '{name}")
            return typer.Exit(1)

        if error:
            return typer.Exit(1)

    console.success(f"Secret set [bold green]'{name}'[/bold green] deleted successfully")


@secrets_cli.command(
    name="image-pull-secret", help="Create an image pull secret for active organization."
)
@synchronizer.create_blocking
@requires_login
async def image_pull_secret(
    name: str = typer.Argument(
        help="Name of the image pull secret to reference in deployment e.g. 'my-image-pull-secret'"
    ),
    host: str = typer.Argument(
        help="Host address of the image repository e.g. https://index.docker.io/v1/"
    ),
    credentials: str = typer.Argument(
        None, help="Credentials of the image repository e.g. 'username:password'"
    ),
    base64encode: bool = typer.Option(
        True, "--encode", "-e", help="base64 encode credentials for added security"
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
    ),
):
    org = organization or config.get("org")

    if not name or not host:
        console.error(
            "Name and host must be provided. Please reference --help for more information."
        )
        return typer.Exit(1)

    if not credentials:
        console.print(
            "[cyan]For more information about image pull secrets, see: "
            "https://docs.pipecat.daily.co/agents/secrets#image-pull-secrets[/cyan]\n"
        )
        username = await questionary.text(f"Username for image repository '{host}'").ask_async()
        password = await questionary.password(
            f"Access token for image repository '{host}'"
        ).ask_async()
        if not username or not password:
            console.print("[bold red]Image pull secret creation cancelled[/bold red]")
            return typer.Exit(1)
        credentials = f"{username}:{password}"

    if base64encode:
        credentials = base64.b64encode(credentials.encode()).decode()

    # Check if secret already exists
    with Live(
        console.status(
            f"[dim]Checking if image pull secret '{name}' already exists[/dim]", spinner="dots"
        ),
        refresh_per_second=4,
    ) as live:
        data, error = await API.secrets_list(org=org)

        if error:
            return typer.Exit()

        if data:
            existing_secret = next(
                (s for s in data if s["name"] == name and s["type"] == "imagePullSecret"), None
            )
            if existing_secret:
                live.stop()
                console.error(
                    f"Image pull secret '[bold]{name}'[/bold] already exists. Please choose a different name or delete the existing one first."
                )
                return typer.Exit(1)

        live.update(
            console.status(
                f"[dim]Creating image pull secret [bold]'{name}'[/bold][/dim]", spinner="dots"
            )
        )

        data, error = await API.secrets_upsert(
            data={"isImagePullSecret": True, "secretValue": credentials, "host": host},
            set_name=name,
            org=org,
        )

        if error:
            return typer.Exit()

    console.success(
        f"Image pull secret [bold green]'{name}'[/bold green] for [bold green]{host}[/bold green] created successfully.",
    )
