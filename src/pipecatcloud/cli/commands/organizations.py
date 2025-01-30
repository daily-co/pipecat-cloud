#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import questionary
import typer
from loguru import logger
from rich import box
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import (
    config,
    update_user_config,
    user_config_path,
)

organization_cli = typer.Typer(
    name="organizations", help="User organizations", no_args_is_help=True
)
keys_cli = typer.Typer(name="keys", help="API key management commands", no_args_is_help=True)
organization_cli.add_typer(keys_cli)


# ---- Commands
@organization_cli.command(name="select", help="Select an organization to use.")
@synchronizer.create_blocking
@requires_login
async def select(organization: str = typer.Option(None, "--organization", "-o")):
    current_org = config.get("org")

    with console.status(
        "[dim]Retrieve user namespace / organization data...[/dim]", spinner="dots"
    ):
        org_list, error = await API.organizations()

        if error:
            typer.Exit()

    try:
        selected_org = None, None
        if not organization:
            # Prompt user to select organization
            value = await questionary.select(
                "Select default namespace / organization",
                choices=[
                    {
                        "name": f"{org['verboseName']} ({org['name']})",
                        "value": (org["name"], org["verboseName"]),
                        "checked": org["name"] == current_org,
                    }
                    for org in org_list
                ],
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

        update_user_config(None, selected_org[0])
        # _store_user_config(ctx.obj["token"], selected_org[0])

        console.success(
            f"Current organization set to [bold green]{selected_org[1]} [dim]({selected_org[0]})[/dim][/bold green]\n"
            f"[dim]Default namespace updated in {user_config_path}[/dim]"
        )
    except Exception:
        console.error("Unable to update user credentials. Please contact support.")


@organization_cli.command(name="list", help="List organizations user is a member of.")
@synchronizer.create_blocking
@requires_login
async def list():
    current_org = config.get("org")

    with console.status(
        "[dim]Retrieve user namespace / organization data...[/dim]", spinner="dots"
    ):
        org_list, error = await API.organizations()

        if error:
            return typer.Exit()

    if not org_list or not len(org_list):
        console.error(
            "No namespaces associated with user account. Please complete onboarding via the dashboard.",
            subtitle=config.get("dashboard_host"),
        )
        return typer.Exit(1)

    table = Table(border_style="dim", box=box.SIMPLE, show_edge=True, show_lines=False)
    table.add_column("Organization", style="white")
    table.add_column("Name", style="white")
    for org in org_list:
        if current_org and org["name"] == current_org:
            table.add_row(
                f"[cyan bold]{org['verboseName']}[/cyan bold]",
                f"[cyan bold]{org['name']} (active)[/cyan bold]",
            )
        else:
            table.add_row(org["verboseName"], org["name"])

    console.success(table, title_extra=f"{len(org_list)} results")


# ---- API Token Commands ----


@keys_cli.command(name="list", help="List API keys for an organization.")
@synchronizer.create_blocking
@requires_login
async def keys(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to list API keys for",
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching API keys for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.api_keys(org)

        if error:
            return typer.Exit()

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


@keys_cli.command(name="create", help="Create an API key for an organization.")
@synchronizer.create_blocking
@requires_login
async def create_key(
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
        help="Set the newly created key as the active / default key in local config",
    ),
):
    org = organization or config.get("org")

    if not api_key_name:
        api_key_name = await questionary.text(
            "Enter human readable name for API key e.g. 'Pipecat Key'"
        ).ask_async()

    if not api_key_name or api_key_name == "":
        console.error("You must enter a name for the API key")
        return typer.Exit(1)

    data = None

    with console.status(
        f"[dim]Creating API key with name: [bold]'{api_key_name}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.api_key_create(api_key_name, org)
        if error:
            return typer.Exit(1)

    if not data or "key" not in data:
        console.error("Invalid response from server. Please contact support.")
        return typer.Exit(1)

    # Determine as to whether we should make this key the active default
    make_active = default
    if not default:
        make_active = await questionary.confirm(
            "Would you like to make this key the default key in your local configuration?",
            default=False,
        ).ask_async()

    if make_active:
        update_user_config(
            active_org=org,
            additional_data={
                "default_public_key": data["key"],
                "default_public_key_name": api_key_name,
            },
        )
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
        data["key"],
        org,
    )

    console.success(table, subtitle="Using as default in local config")


@keys_cli.command(name="delete", help="Delete an API key for an organization.")
@synchronizer.create_blocking
@requires_login
async def delete_key(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to get tokens for",
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching API keys for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.api_keys(org)

        if error:
            return typer.Exit()

        if len(data["public"]) == 0:
            console.error(
                f"[bold]No API keys found.[/bold]\n"
                f"[dim]Create a new API key with the "
                f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
            )
            typer.Exit(1)
            return

    # Prompt user to delete a key
    key = await questionary.select(
        "Select API key to delete",
        choices=[
            {"name": key["metadata"]["name"], "value": (key["id"], key["key"])}
            for key in data["public"]
        ],
    ).ask_async()

    if not key:
        typer.Exit(1)

    key_is_default = config.get("default_public_key") == key[1]

    if key_is_default:
        await questionary.confirm(
            "This key is currently set as the default in your local config. Are you sure you want to proceed?"
        ).ask_async()

        # Update config to remove default key

        try:
            update_user_config(
                active_org=org,
                additional_data={"default_public_key_name": None, "default_public_key": None},
            )
        except Exception:
            console.error("Unable to remove default key from local user config")
            return typer.Exit(1)

    with console.status(f"[dim]Deleting API key with ID {key[0]}...[/dim]", spinner="dots"):
        data, error = await API.api_key_delete(key[0], org)

        if error:
            return typer.Exit(1)

    console.success(f"API key with ID: [bold]'{key[0]}'[/bold] deleted successfully.")


@keys_cli.command(name="use", help="Set default API key for an organization in local config.")
@synchronizer.create_blocking
@requires_login
async def use_key(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to get tokens for",
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching API keys for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.api_keys(org)

        if error:
            return typer.Exit()

    if len(data["public"]) == 0:
        console.print(
            f"[bold]No API keys found.[/bold]\n"
            f"[dim]Create a new API key with the "
            f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
        )
        typer.Exit(1)
        return

    # Prompt user to use a key
    key = await questionary.select(
        "Select API key to use",
        choices=[
            {"name": key["metadata"]["name"], "value": (key["key"], key["metadata"]["name"])}
            for key in data["public"]
        ],
    ).ask_async()

    if not key:
        typer.Exit(1)
        return

    try:
        update_user_config(
            active_org=org,
            additional_data={"default_public_key": key[0], "default_public_key_name": key[1]},
        )
        console.success(f"API key with name: [bold]'{key[1]}'[/bold] set as default.")
    except Exception as e:
        logger.debug(e)
        console.error("Unable to set default key in local config. Please contact support.")
