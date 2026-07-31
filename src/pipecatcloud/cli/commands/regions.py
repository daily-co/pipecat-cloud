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

    rows = [(region["code"], region["display_name"]) for region in regions]

    if not console.rich_output:
        console.print_records(["Code", "Name"], rows)
        return

    # Create table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Code")
    table.add_column("Name")

    # Add rows
    for code, name in rows:
        table.add_row(code, name)

    console.print(table)
