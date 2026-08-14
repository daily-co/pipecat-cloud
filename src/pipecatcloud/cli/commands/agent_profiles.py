#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import re

import typer
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.deploy_utils import ResourcesConfig
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

# Registered under the agent command group, so these surface as
# `pipecat cloud agent profiles <list|create|update|enable|disable>`.
agent_profiles_cli = typer.Typer(
    name="profiles",
    help=(
        "Agent profile management. The platform catalog is read-only; "
        "organizations with enterprise (self-hosted) regions can define their "
        "own profiles for use in those regions."
    ),
    no_args_is_help=True,
)

# Mirrors the server-side name rule so a typo fails before the network call:
# lowercase alphanumerics and hyphens, must start/end alphanumeric.
API_NAME_PATTERN = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _validate_name(name: str) -> bool:
    if not API_NAME_PATTERN.match(name):
        console.error(
            f"Invalid profile name '{name}'. Use lowercase letters, digits, and "
            "hyphens; must start and end with a letter or digit (max 63 chars)."
        )
        return False
    return True


def _validate_sizing(cpu: str, memory: str) -> bool:
    """Client-side quantity check so a typo fails before the network call."""
    try:
        ResourcesConfig(cpu=cpu, memory=memory)
        return True
    except ValueError as e:
        console.error(str(e))
        return False


@agent_profiles_cli.command(name="list", help="List agent profiles available to your organization")
@synchronizer.create_blocking
@requires_login
async def list_profiles(
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization to list profiles for"
    ),
):
    org = organization or config.get("org")

    with console.status("[dim]Fetching agent profiles...[/dim]", spinner="dots"):
        data, error = await API.agent_profiles_list(org=org)

    if error:
        raise typer.Exit(1)

    profiles = (data or {}).get("agentProfiles", [])
    if not profiles:
        console.print("[yellow]No agent profiles available[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name")
    table.add_column("Display name")
    table.add_column("CPU")
    table.add_column("Memory")
    table.add_column("Source")
    table.add_column("Enabled")

    for profile in profiles:
        resources = profile.get("resources") or {}
        table.add_row(
            profile.get("api_name", ""),
            profile.get("display_name", ""),
            str(resources.get("cpu", "")),
            str(resources.get("memory", "")),
            "custom" if profile.get("custom") else "platform",
            "yes" if profile.get("enabled", True) else "[dim]no[/dim]",
        )

    console.print(table)


@agent_profiles_cli.command(
    name="create",
    help="Create a custom agent profile (enterprise / self-hosted regions only)",
)
@synchronizer.create_blocking
@requires_login
async def create_profile(
    name: str = typer.Argument(help="Profile name used at deploy time e.g. 'telephony-large'"),
    cpu: str = typer.Option(..., "--cpu", help="CPU quantity e.g. '2' or '500m'"),
    memory: str = typer.Option(..., "--memory", help="Memory quantity e.g. '4Gi'"),
    display_name: str = typer.Option(
        None, "--display-name", help="Human-readable name (defaults to the profile name)"
    ),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization to create the profile in"
    ),
):
    org = organization or config.get("org")

    if not _validate_name(name):
        raise typer.Exit(1)
    if not _validate_sizing(cpu, memory):
        raise typer.Exit(1)

    with console.status(f"[dim]Creating agent profile '{name}'...[/dim]", spinner="dots"):
        data, error = await API.agent_profiles_create(
            api_name=name,
            display_name=display_name or name,
            cpu=cpu,
            memory=memory,
            org=org,
        )

    if error:
        raise typer.Exit(1)

    console.success(
        f"Created agent profile '{name}' (cpu={cpu}, memory={memory}). "
        f"Use it with: deploy --profile {name}"
    )


@agent_profiles_cli.command(
    name="update",
    help="Update a custom agent profile. Changes apply to future deploys only.",
)
@synchronizer.create_blocking
@requires_login
async def update_profile(
    name: str = typer.Argument(help="Profile name to update"),
    cpu: str = typer.Option(None, "--cpu", help="New CPU quantity (requires --memory)"),
    memory: str = typer.Option(None, "--memory", help="New memory quantity (requires --cpu)"),
    display_name: str = typer.Option(None, "--display-name", help="New human-readable name"),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization the profile belongs to"
    ),
):
    org = organization or config.get("org")

    if (cpu is None) != (memory is None):
        console.error("--cpu and --memory must be provided together")
        raise typer.Exit(1)
    if cpu is None and display_name is None:
        console.error("Nothing to update. Provide --cpu/--memory and/or --display-name.")
        raise typer.Exit(1)
    if cpu is not None and not _validate_sizing(cpu, memory):
        raise typer.Exit(1)

    with console.status(f"[dim]Updating agent profile '{name}'...[/dim]", spinner="dots"):
        data, error = await API.agent_profiles_update(
            api_name=name,
            org=org,
            display_name=display_name,
            cpu=cpu,
            memory=memory,
        )

    if error:
        raise typer.Exit(1)

    console.success(
        f"Updated agent profile '{name}'. Running agents keep their current "
        "resources; changes apply on the next deploy."
    )


@agent_profiles_cli.command(
    name="disable",
    help="Disable a custom agent profile so it can no longer be selected at deploy time",
)
@synchronizer.create_blocking
@requires_login
async def disable_profile(
    name: str = typer.Argument(help="Profile name to disable"),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization the profile belongs to"
    ),
):
    org = organization or config.get("org")

    with console.status(f"[dim]Disabling agent profile '{name}'...[/dim]", spinner="dots"):
        data, error = await API.agent_profiles_update(api_name=name, org=org, enabled=False)

    if error:
        raise typer.Exit(1)

    console.success(
        f"Disabled agent profile '{name}'. Agents already deployed with it keep "
        "running; it can no longer be selected for new deploys. Re-enable it "
        f"with: agent profiles enable {name}"
    )


@agent_profiles_cli.command(
    name="enable",
    help="Re-enable a previously disabled custom agent profile",
)
@synchronizer.create_blocking
@requires_login
async def enable_profile(
    name: str = typer.Argument(help="Profile name to re-enable"),
    organization: str = typer.Option(
        None, "--organization", "-o", help="Organization the profile belongs to"
    ),
):
    org = organization or config.get("org")

    with console.status(f"[dim]Enabling agent profile '{name}'...[/dim]", spinner="dots"):
        data, error = await API.agent_profiles_update(api_name=name, org=org, enabled=True)

    if error:
        raise typer.Exit(1)

    console.success(f"Enabled agent profile '{name}'. It can be selected for deploys again.")
