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
from pipecatcloud._utils.console_utils import console, stdin_is_interactive
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.api import API
from pipecatcloud.cli.commands.registry_keys import registry_keys_cli
from pipecatcloud.cli.config import (
    config,
    update_user_config,
    user_config_path,
)

organization_cli = typer.Typer(
    name="organizations", help="User organizations", no_args_is_help=True
)
keys_cli = typer.Typer(name="keys", help="API key management commands", no_args_is_help=True)
properties_cli = typer.Typer(
    name="properties", help="Organization property management", no_args_is_help=True
)
organization_cli.add_typer(keys_cli)
organization_cli.add_typer(registry_keys_cli)
organization_cli.add_typer(properties_cli)


# ---- Commands
@organization_cli.command(name="select", help="Select an organization to use.")
@synchronizer.create_blocking
@requires_login
async def select(organization: str = typer.Option(None, "--organization", "-o")):
    current_org = config.get("org")

    with console.status("[dim]Retrieve user organization data...[/dim]", spinner="dots"):
        org_list, error = await API.organizations()

        if error:
            raise typer.Exit(1)

    try:
        selected_org = None, None
        if not organization:
            console.require_interactive("--organization")
            # Prompt user to select organization
            value = await questionary.select(
                "Select default organization",
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
                raise typer.Exit(1)

            selected_org = value[0], value[1]

        else:
            # Attempt to match passed org with results
            match = None
            for o in org_list:
                if o["name"] == organization:
                    match = o
            if not match:
                console.error(
                    f"No organization with ID [bold]'{organization}'[/bold].\n"
                    f"[dim]Run [bold]{PIPECAT_CLI_NAME} organizations list[/bold] "
                    f"to see available IDs.[/dim]"
                )
                raise typer.Exit(1)
            selected_org = match["name"], match["verboseName"]

        update_user_config(None, selected_org[0])
        # _store_user_config(ctx.obj["token"], selected_org[0])

        console.success(
            f"Current organization set to [bold green]{selected_org[1]} [dim]({selected_org[0]})[/dim][/bold green]\n"
            f"[dim]Default organization updated in {user_config_path}[/dim]"
        )
    except typer.Exit:
        raise
    except Exception:
        console.error("Unable to update user credentials. Please contact support.")
        raise typer.Exit(1)


@organization_cli.command(name="list", help="List organizations user is a member of.")
@synchronizer.create_blocking
@requires_login
async def list_organizations():
    current_org = config.get("org")

    with console.status("[dim]Retrieve user organization data...[/dim]", spinner="dots"):
        org_list, error = await API.organizations()

        if error:
            raise typer.Exit(1)

    if not org_list or not len(org_list):
        console.error(
            "No organizations associated with user account. Please complete onboarding via the dashboard.",
            subtitle=config.get("dashboard_host"),
        )
        raise typer.Exit(1)

    if console.json_output:
        console.output_json({"organizations": org_list})
        return

    if not console.rich_output:
        console.print_records(
            ["Organization Name", "Organization ID", "Active"],
            [
                (
                    org["verboseName"],
                    org["name"],
                    "active" if current_org and org["name"] == current_org else "",
                )
                for org in org_list
            ],
        )
        return

    table = Table(border_style="dim", box=box.SIMPLE, show_edge=True, show_lines=False)
    # `verboseName` is the display string; `name` is the unique slug used in
    # API paths, the k8s namespace, and `--organization`.
    table.add_column("Organization Name", style="white")
    table.add_column("Organization ID", style="white")
    # The active marker gets its own column so the ID cell holds nothing but
    # the value `--organization` accepts.
    table.add_column("Active", style="white")
    for org in org_list:
        active = bool(current_org and org["name"] == current_org)
        table.add_row(
            org["verboseName"],
            org["name"],
            "active" if active else "",
            style="cyan bold" if active else None,
        )

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
            raise typer.Exit(1)

        if len(data["public"]) == 0:
            console.error(
                f"[bold]No API keys found.[/bold]\n"
                f"[dim]Create a new API key with the "
                f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
            )
            raise typer.Exit(1)

        if console.json_output:
            console.output_json(data)
            return

        if not console.rich_output:
            console.print_records(
                ["Name", "Key", "Created At", "Status"],
                [
                    (
                        key["metadata"]["name"],
                        key["key"],
                        key["createdAt"],
                        "Revoked" if key["revoked"] else "Active",
                    )
                    for key in data["public"]
                ],
                title=f"API keys for organization: {org}",
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
        console.require_interactive("--name")
        api_key_name = await questionary.text(
            "Enter human readable name for API key e.g. 'Pipecat Key'"
        ).ask_async()

    if not api_key_name or api_key_name == "":
        console.error("You must enter a name for the API key")
        raise typer.Exit(1)

    data = None

    with console.status(
        f"[dim]Creating API key with name: [bold]'{api_key_name}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.api_key_create(api_key_name, org)
        if error:
            raise typer.Exit(1)

    if not data or "key" not in data:
        console.error("Invalid response from server. Please contact support.")
        raise typer.Exit(1)

    # Determine as to whether we should make this key the active default.
    # Non-interactively (key already created at this point, so failing here
    # would be worse than proceeding) fall back to the prompt's default: no.
    make_active = default
    if not default and stdin_is_interactive():
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

    if console.json_output:
        console.output_json(data)
        return

    if not console.rich_output:
        console.print_records(
            ["Name", "Key", "Organization ID"],
            [(api_key_name, data["key"], org)],
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
    table.add_column("Organization ID")

    table.add_row(
        api_key_name,
        data["key"],
        org,
    )

    console.success(
        table,
        subtitle="Using as default in local config" if make_active else None,
    )


async def _revoke_key_flow(organization: str | None) -> None:
    """Shared implementation for the ``revoke`` command and its ``delete`` alias.

    Prompts the user to pick an active API key, clears it from local config if
    it was the default, and asks the server to revoke it.
    """
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching API keys for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.api_keys(org)

        if error:
            raise typer.Exit(1)

        if len(data["public"]) == 0:
            console.error(
                f"[bold]No API keys found.[/bold]\n"
                f"[dim]Create a new API key with the "
                f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
            )
            raise typer.Exit(1)

    # Only offer keys that are not already revoked — revoking a revoked key
    # is a no-op on the server and confuses the interactive flow.
    active_keys = [k for k in data["public"] if not k.get("revoked")]

    if not active_keys:
        console.error(
            "[bold]No active API keys to revoke.[/bold]\n"
            "[dim]All keys in this organization are already revoked.[/dim]"
        )
        raise typer.Exit(1)

    # Prompt user to revoke a key
    console.require_interactive(None)
    key = await questionary.select(
        "Select API key to revoke",
        choices=[
            {"name": key["metadata"]["name"], "value": (key["id"], key["key"])}
            for key in active_keys
        ],
    ).ask_async()

    if not key:
        raise typer.Exit(1)

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
            raise typer.Exit(1)

    with console.status(f"[dim]Revoking API key with ID {key[0]}...[/dim]", spinner="dots"):
        data, error = await API.api_key_revoke(key[0], org)

        if error:
            raise typer.Exit(1)

    console.success(f"API key with ID: [bold]'{key[0]}'[/bold] revoked successfully.")


@keys_cli.command(name="revoke", help="Revoke an API key for an organization.")
@synchronizer.create_blocking
@requires_login
async def revoke_key(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization the API key belongs to",
    ),
):
    await _revoke_key_flow(organization)


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
            raise typer.Exit(1)

    if len(data["public"]) == 0:
        console.print(
            f"[bold]No API keys found.[/bold]\n"
            f"[dim]Create a new API key with the "
            f"[bold]{PIPECAT_CLI_NAME} organizations keys create[/bold] command.[/dim]"
        )
        raise typer.Exit(1)

    # Prompt user to use a key
    console.require_interactive(None)
    key = await questionary.select(
        "Select API key to use",
        choices=[
            {"name": key["metadata"]["name"], "value": (key["key"], key["metadata"]["name"])}
            for key in data["public"]
        ],
    ).ask_async()

    if not key:
        raise typer.Exit(1)

    try:
        update_user_config(
            active_org=org,
            additional_data={"default_public_key": key[0], "default_public_key_name": key[1]},
        )
        console.success(f"API key with name: [bold]'{key[1]}'[/bold] set as default.")
    except Exception as e:
        logger.debug(e)
        console.error("Unable to set default key in local config. Please contact support.")
        raise typer.Exit(1)


# ---- Properties Commands ----


@properties_cli.command(name="list", help="List current organization property values.")
@synchronizer.create_blocking
@requires_login
async def properties_list(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to list properties for",
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching properties for organization: [bold]'{org}'[/bold][/dim]", spinner="dots"
    ):
        data, error = await API.properties(org)

        if error:
            raise typer.Exit(1)

    # Before the empty-result return: an empty set must still emit a
    # well-formed JSON payload rather than zero bytes on stdout.
    if console.json_output:
        console.output_json(data or {})
        return

    if not data:
        console.print("[dim]No properties configured.[/dim]")
        return

    if not console.rich_output:
        console.print_records(
            ["Property", "Value"],
            [(prop_name, prop_value) for prop_name, prop_value in data.items()],
            title=f"Properties for organization: {org}",
        )
        return

    table = Table(
        show_header=True,
        show_lines=False,
        border_style="dim",
        box=box.SIMPLE,
    )
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")

    for prop_name, prop_value in data.items():
        table.add_row(prop_name, str(prop_value))

    console.success(table, title_extra=f"Properties for organization: {org}")


@properties_cli.command(name="schema", help="Show available properties with metadata.")
@synchronizer.create_blocking
@requires_login
async def properties_schema(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to show properties schema for",
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Fetching properties schema for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.properties_schema(org)

        if error:
            raise typer.Exit(1)

    # Before the empty-result return: an empty set must still emit a
    # well-formed JSON payload rather than zero bytes on stdout.
    if console.json_output:
        console.output_json(data or {})
        return

    if not data:
        console.print("[dim]No properties available.[/dim]")
        return

    if not console.rich_output:
        console.print_records(
            ["Property", "Type", "Current Value", "Default", "Description"],
            [
                (
                    prop_name,
                    prop_info.get("type", ""),
                    prop_info.get("currentValue", ""),
                    prop_info.get("default", ""),
                    prop_info.get("description", ""),
                )
                for prop_name, prop_info in data.items()
            ],
            title=f"Properties schema for organization: {org}",
        )
        return

    table = Table(
        show_header=True,
        show_lines=True,
        border_style="dim",
        box=box.SIMPLE,
    )
    table.add_column("Property", style="cyan")
    table.add_column("Type")
    table.add_column("Current Value", style="green")
    table.add_column("Default")
    table.add_column("Description")

    for prop_name, prop_info in data.items():
        current = prop_info.get("currentValue", "")
        default = prop_info.get("default", "")
        available = prop_info.get("availableValues")

        # Show available values in description if present
        description = prop_info.get("description", "")
        if available:
            description += f"\n[dim]Available: {', '.join(str(v) for v in available)}[/dim]"

        table.add_row(
            prop_name,
            prop_info.get("type", ""),
            str(current) if current is not None else "[dim]not set[/dim]",
            str(default) if default is not None else "",
            description,
        )

    console.success(table, title_extra=f"Properties schema for organization: {org}")


@properties_cli.command(name="set", help="Update an organization property.")
@synchronizer.create_blocking
@requires_login
async def properties_set(
    property_name: str = typer.Argument(..., help="Name of the property to set"),
    value: str = typer.Argument(..., help="Value to set"),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to update property for",
    ),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Updating property [bold]'{property_name}'[/bold] for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.properties_update(org, {property_name: value})

        if error:
            raise typer.Exit(1)

    if not data:
        console.error("Failed to update property.")
        raise typer.Exit(1)

    new_value = data.get(property_name, value)
    console.success(
        f"Property [bold cyan]{property_name}[/bold cyan] set to [bold green]{new_value}[/bold green]"
    )


# ---- Convenience Commands ----


@organization_cli.command(
    name="default-region", help="Get or set the default region for an organization."
)
@synchronizer.create_blocking
@requires_login
async def default_region(
    region: str = typer.Argument(None, help="Region to set as default (omit to show current)"),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to configure",
    ),
):
    org = organization or config.get("org")

    if region:
        # Set the default region
        with console.status(
            f"[dim]Setting default region to [bold]'{region}'[/bold] for organization: [bold]'{org}'[/bold][/dim]",
            spinner="dots",
        ):
            data, error = await API.properties_update(org, {"defaultRegion": region})

            if error:
                raise typer.Exit(1)

        if not data:
            console.error("Failed to update default region.")
            raise typer.Exit(1)

        console.success(
            f"Default region set to [bold green]{data.get('defaultRegion', region)}[/bold green]"
        )
    else:
        # Show the current default region
        with console.status(
            f"[dim]Fetching default region for organization: [bold]'{org}'[/bold][/dim]",
            spinner="dots",
        ):
            data, error = await API.properties_schema(org)

            if error:
                raise typer.Exit(1)

        if not data or "defaultRegion" not in data:
            console.print("[dim]No default region configured.[/dim]")
            return

        prop = data["defaultRegion"]
        current = prop.get("currentValue", prop.get("default", "not set"))
        available = prop.get("availableValues", [])

        console.print(f"Default region: [bold green]{current}[/bold green]")
        if available:
            console.print(f"[dim]Available regions: {', '.join(available)}[/dim]")
