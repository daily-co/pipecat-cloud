#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import typer
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud._utils.regions import get_regions

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
