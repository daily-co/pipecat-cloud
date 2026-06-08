#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from decimal import Decimal, InvalidOperation

import questionary
import typer
from rich import box
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console, format_cents, format_timestamp
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import config

spend_limit_cli = typer.Typer(
    name="spend-limit",
    help="Manage the organization-level spend limit",
    no_args_is_help=True,
)


def _parse_amount_to_cents(amount: str) -> int:
    """Parse a user-supplied dollar amount into integer cents.

    Accepts a non-negative number with at most two decimal places.
    """
    try:
        dollars = Decimal(amount)
    except InvalidOperation as e:
        raise typer.BadParameter(
            f"Invalid dollar amount: {amount!r}. Use a number like 50 or 12.34.",
        ) from e

    if dollars < 0:
        raise typer.BadParameter("Amount must be zero or positive.")

    cents = dollars * 100
    if cents != cents.to_integral_value():
        raise typer.BadParameter(
            f"Dollar amount has more than two decimal places: {amount!r}.",
        )
    return int(cents)


def _render_show(data: dict) -> None:
    """Pretty-print the spend-limit payload for a human reader."""
    limit_cents = data.get("limitCents")
    spend_cents = data.get("currentSpendCents", 0) or 0
    period_start = data.get("periodStart")
    period_end = data.get("periodEnd")
    blocked = bool(data.get("blocked"))
    blocked_at = data.get("blockedAt")

    table = Table(
        show_header=False,
        box=box.SIMPLE,
        border_style="dim",
        show_edge=True,
        show_lines=False,
    )
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="white")

    if limit_cents is None:
        table.add_row("Limit", "[dim]no limit set[/dim]")
    else:
        table.add_row("Limit", format_cents(limit_cents))

    spend_display = format_cents(spend_cents)
    # Skip the percentage when no limit is set, and when the limit is exactly
    # $0 (a valid "block everything" state). Guards against div-by-zero.
    if limit_cents is not None and limit_cents > 0:
        pct = (spend_cents / limit_cents) * 100
        spend_display += f" [dim]({pct:.1f}%)[/dim]"
    table.add_row("Current spend", spend_display)

    if period_start:
        table.add_row("Period start", format_timestamp(period_start))
    if period_end:
        table.add_row("Period end", format_timestamp(period_end))

    if blocked:
        blocked_value = "[bold red]yes[/bold red]"
        if blocked_at:
            blocked_value += f" [dim](since {format_timestamp(blocked_at)})[/dim]"
        table.add_row("Blocked", blocked_value)
    else:
        table.add_row("Blocked", "no")

    console.success(table, title_extra="Spend limit")

    if blocked:
        console.print(
            "[yellow]New sessions are being rejected. In-flight sessions continue to run.[/yellow]"
        )


@spend_limit_cli.command(name="show", help="Show the current spend limit and usage.")
@synchronizer.create_blocking
@requires_login
async def show(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to query (defaults to active org)",
    ),
    output_json: bool = typer.Option(
        False,
        "--json",
        help="Print raw JSON instead of a formatted table",
    ),
):
    org = organization or config.get("org")

    if output_json:
        # Bubble errors so we don't print a rich panel before/instead of the JSON.
        data, error = await API.bubble_error().spend_limit_get(org)
        if error:
            console.print_json(data={"error": error})
            return typer.Exit(1)
        if data is None:
            console.print_json(data={})
            return
        console.print_json(data=data)
        return

    with console.status(
        f"[dim]Fetching spend limit for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.spend_limit_get(org)

        if error:
            return typer.Exit(1)

    if data is None:
        console.print("[dim]No spend-limit data available for this organization.[/dim]")
        return

    _render_show(data)


@spend_limit_cli.command(name="set", help="Set or update the spend limit.")
@synchronizer.create_blocking
@requires_login
async def set_limit(
    amount: str = typer.Argument(
        ...,
        help="Limit in dollars (e.g. 50 or 12.34). At most two decimal places.",
    ),
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to update (defaults to active org)",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompts.",
    ),
):
    org = organization or config.get("org")
    new_cents = _parse_amount_to_cents(amount)

    # Fetch current state so we can warn on $0 limits and downgrades that
    # would immediately block the org.
    with console.status(
        f"[dim]Checking current spend for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        current, error = await API.spend_limit_get(org)
        if error:
            return typer.Exit(1)

    current_spend = (current or {}).get("currentSpendCents") or 0

    if not yes:
        if new_cents == 0:
            confirm = await questionary.confirm(
                "Setting the limit to $0.00 blocks all new sessions until you raise it. Continue?",
                default=False,
            ).ask_async()
            if not confirm:
                console.cancel()
                return typer.Exit(1)
        elif current_spend > new_cents:
            confirm = await questionary.confirm(
                f"Current spend ({format_cents(current_spend)}) exceeds the new limit "
                f"({format_cents(new_cents)}). New sessions will be blocked. Continue?",
                default=False,
            ).ask_async()
            if not confirm:
                console.cancel()
                return typer.Exit(1)

    with console.status(
        f"[dim]Setting spend limit to {format_cents(new_cents)}[/dim]",
        spinner="dots",
    ):
        data, error = await API.spend_limit_update(org, new_cents)

        if error:
            return typer.Exit(1)

    console.success(
        f"Spend limit for [bold]'{org}'[/bold] set to "
        f"[bold green]{format_cents(new_cents)}[/bold green]."
    )
    if data:
        _render_show(data)


@spend_limit_cli.command(name="clear", help="Remove the spend limit.")
@synchronizer.create_blocking
@requires_login
async def clear(
    organization: str = typer.Option(
        None,
        "--organization",
        "-o",
        help="Organization to update (defaults to active org)",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt.",
    ),
):
    org = organization or config.get("org")

    if not yes:
        confirm = await questionary.confirm(
            f"Remove the spend limit for '{org}'? New sessions will not be capped.",
            default=False,
        ).ask_async()
        if not confirm:
            console.cancel()
            return typer.Exit(1)

    with console.status(
        f"[dim]Clearing spend limit for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.spend_limit_update(org, None)

        if error:
            return typer.Exit(1)

    console.success(f"Spend limit for [bold]'{org}'[/bold] cleared.")
    if data:
        _render_show(data)
