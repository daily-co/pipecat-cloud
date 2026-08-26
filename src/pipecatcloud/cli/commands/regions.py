#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import questionary
import typer
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.regions import get_regions
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

regions_cli = typer.Typer(name="regions", help="Region management", no_args_is_help=True)


@regions_cli.command(name="list", help="List available regions")
@synchronizer.create_blocking
@requires_login
async def list_regions():
    """List all available regions with their display names."""

    with console.status("[dim]Fetching available regions...[/dim]", spinner="dots"):
        regions = await get_regions()

    # Before the empty-result return: an empty set must still emit a
    # well-formed JSON payload rather than zero bytes on stdout.
    if console.json_output:
        console.output_json({"regions": regions or []})
        return

    if not regions:
        console.print("[yellow]No regions available[/yellow]")
        return

    # Architecture capability (PCC-1105): what --architecture may name per
    # region — e.g. Daily-hosted regions are arm64-only today, which is
    # data here, never an assumption baked into the CLI. Older APIs omit
    # the fields; render a dash rather than guessing.
    def arch_cell(region: dict) -> str:
        supported = region.get("supported_architectures")
        return ", ".join(supported) if supported else "—"

    def default_cell(region: dict) -> str:
        return region.get("default_architecture") or "—"

    rows = [
        (
            region["code"],
            region["display_name"],
            arch_cell(region),
            default_cell(region),
        )
        for region in regions
    ]

    if not console.rich_output:
        console.print_records(["Code", "Name", "Architectures", "Default"], rows)
        return

    # Create table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Code")
    table.add_column("Name")
    table.add_column("Architectures")
    table.add_column("Default")

    # Add rows
    for row in rows:
        table.add_row(*row)

    console.print(table)


# --- Self-hosted region lifecycle (PCC-1103) --------------------------------
# Registration is an omit-preserves upsert: every option below is optional
# except the region key, and omitted fields keep their stored values — so
# re-running with one flag updates just that field.


def _region_rows(region: dict) -> list[tuple[str, str]]:
    """The full region record as label/value rows, stable order."""
    archs = region.get("supported_architectures") or []
    return [
        ("Region key", str(region.get("region_key", ""))),
        ("Display name", str(region.get("display_name") or "—")),
        ("Enrollment status", str(region.get("enrollment_status", ""))),
        ("Intermediate expires", str(region.get("intermediate_expires_at") or "—")),
        ("WS public endpoint", str(region.get("ws_public_endpoint") or "—")),
        ("Architectures", ", ".join(archs) if archs else "—"),
        ("Default architecture", str(region.get("default_architecture") or "—")),
        ("Workloads namespace", str(region.get("workloads_namespace") or "—")),
    ]


def _print_region(region: dict) -> None:
    if console.json_output:
        console.output_json({"region": region})
        return
    rows = _region_rows(region)
    if not console.rich_output:
        console.print_records(["Field", "Value"], rows)
        return
    table = Table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    for label, value in rows:
        table.add_row(label, value)
    console.print(table)


@regions_cli.command(
    name="register",
    help="Register or update a self-hosted region (omitted options are preserved)",
)
@synchronizer.create_blocking
@requires_login
async def register_region(
    region_key: str = typer.Argument(..., help="The region's key, e.g. acme-us-east"),
    workloads_namespace: str = typer.Option(
        None,
        "--workloads-namespace",
        help="Customer-chosen namespace bot workloads run in (must match the install's global.workloadsNamespace)",
    ),
    architectures: str = typer.Option(
        None,
        "--architectures",
        help="Comma-separated CPU architectures the cluster schedules, e.g. amd64,arm64. Only declare what it can actually run.",
    ),
    default_architecture: str = typer.Option(
        None,
        "--default-architecture",
        help="Architecture deploys get when they don't declare one",
    ),
    ws_public_endpoint: str = typer.Option(
        None,
        "--ws-public-endpoint",
        help="Public wss:// endpoint for WebSocket transports (omit for regions without a WS front door)",
    ),
    display_name: str = typer.Option(
        None,
        "--display-name",
        help="Human-readable name shown in region pickers (without one, pickers show the uppercased key)",
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    # A region without a display name renders as its uppercased key in every
    # picker — worth one interactive question, skippable with Enter (the
    # upsert preserves whatever is stored).
    if display_name is None and console.is_terminal and not console.json_output:
        answer = await questionary.text(
            "Display name for region pickers (Enter to skip):"
        ).ask_async()
        if answer:
            display_name = answer

    payload: dict = {"regionKey": region_key}
    if workloads_namespace is not None:
        payload["workloadsNamespace"] = workloads_namespace
    if architectures is not None:
        payload["supportedArchitectures"] = [
            a.strip() for a in architectures.split(",") if a.strip()
        ]
    if default_architecture is not None:
        payload["defaultArchitecture"] = default_architecture
    if ws_public_endpoint is not None:
        payload["wsPublicEndpoint"] = ws_public_endpoint
    if display_name is not None:
        payload["displayName"] = display_name

    with console.status(
        f"[dim]Registering region [bold]'{region_key}'[/bold][/dim]", spinner="dots"
    ):
        region, error = await API.region_register(org=org, payload=payload)
        if error:
            raise typer.Exit(1)

    if not console.json_output:
        console.success(f"Region '{region_key}' registered")
    _print_region(region or {})


@regions_cli.command(name="show", help="Show a self-hosted region's full record")
@synchronizer.create_blocking
@requires_login
async def show_region(
    region_key: str = typer.Argument(..., help="The region's key"),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    with console.status(f"[dim]Fetching region [bold]'{region_key}'[/bold][/dim]", spinner="dots"):
        region, error = await API.region_get(org=org, region_key=region_key)
        if error:
            raise typer.Exit(1)

    if not region:
        console.error(f"Region '{region_key}' not found in organization '{org}'")
        raise typer.Exit(1)

    _print_region(region)


@regions_cli.command(
    name="delete",
    help="Revoke a self-hosted region (refused while it has live sessions or services)",
)
@synchronizer.create_blocking
@requires_login
async def delete_region(
    region_key: str = typer.Argument(..., help="The region's key"),
    # --yes skips the prompt and nothing else (PCC-1141). The guard on live
    # sessions and deployed services is the server's and has no bypass, so a
    # non-interactive caller reaching for this flag cannot destroy anything
    # the interactive path would have refused.
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip the confirmation prompt",
    ),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    if not yes:
        console.require_interactive("--yes")
        if not await questionary.confirm(
            f"Revoke region '{region_key}'? Its agent loses the control-plane "
            "channel; the cluster itself is not touched. The server refuses "
            "while the region has live sessions or deployed services; remove "
            "its agents first."
        ).ask_async():
            console.print("[bold]Aborting delete request[/bold]")
            raise typer.Exit(1)

    with console.status(f"[dim]Revoking region [bold]'{region_key}'[/bold][/dim]", spinner="dots"):
        # The 409 guard message (live session/service counts) is curated,
        # customer-safe API copy — the error panel passes it through.
        data, error = await API.region_delete(org=org, region_key=region_key)
        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json({"deleted": region_key, "result": data or {}})
        return
    console.success(f"Region '{region_key}' revoked")
    # A 202 carries propagation: pending — the revocation is recorded, but the
    # cutoff had not finished landing when the call returned. Reporting a flat
    # "revoked" would claim more than the API did.
    if (data or {}).get("propagation") == "pending":
        console.print(
            "[yellow]The region's connection may persist briefly until the "
            "change finishes propagating.[/yellow]"
        )


@regions_cli.command(
    name="enroll-token",
    help="Mint a one-time enrollment token for a self-hosted region",
)
@synchronizer.create_blocking
@requires_login
async def enroll_token(
    region_key: str = typer.Argument(..., help="The region's key"),
    organization: str = typer.Option(None, "--organization", "-o"),
):
    org = organization or config.get("org")

    with console.status(
        f"[dim]Minting enrollment token for [bold]'{region_key}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.region_enroll_token(org=org, region_key=region_key)
        if error:
            raise typer.Exit(1)

    token = (data or {}).get("token")
    if not token:
        console.error("The API did not return a token")
        raise typer.Exit(1)

    kubectl_cmd = (
        "kubectl -n pipecat-system create secret generic pipecat-region-enroll-token "
        f"--from-literal=token={token}"
    )
    if console.json_output:
        # Shown exactly once — stdout carries it, chrome goes to stderr.
        console.output_json({"region": region_key, "token": token, "kubectlCommand": kubectl_cmd})
        return
    console.success(
        f"One-time enrollment token minted for '{region_key}' — it cannot be retrieved again."
    )
    console.print("\nStage it in the cluster before the install:\n")
    console.print(f"  {kubectl_cmd}\n", soft_wrap=True)
