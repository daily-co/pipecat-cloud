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
from pipecatcloud._utils.console_utils import OutputMode, console, format_cents, format_timestamp
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


def _with_org(data: dict | None, org: str | None) -> dict:
    """Return the payload with the organization it describes.

    The API response does not name the organization, so JSON consumers had no
    way to tell which org a limit belonged to. A server-supplied
    `organization` key wins if one ever appears.

    An absent payload stays empty. `spend_limit_get` returns None on a 404,
    and `{}` is the sentinel JSON consumers test for; adding a key would make
    the no-data result truthy.
    """
    if not data:
        return {}
    payload = dict(data)
    if org:
        payload.setdefault("organization", org)
    return payload


def _render_show(data: dict, org: str | None) -> None:
    """Pretty-print the spend-limit payload for a human reader.

    The organization is rendered as the first row so the numbers are never
    ambiguous when several orgs are in play.
    """
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

    if org:
        table.add_row("Organization", org)

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
        help="Deprecated alias for the global --output json mode",
    ),
):
    org = organization or config.get("org")

    if output_json:
        # Predates the global --output mechanism; kept as an alias.
        console.set_output_mode(OutputMode.json)

    if console.json_output:
        # Bubble errors so the error object is the only thing on stdout.
        data, error = await API.bubble_error().spend_limit_get(org)
        if error:
            console.output_json({"error": error})
            raise typer.Exit(1)
        console.output_json(_with_org(data, org))
        return

    with console.status(
        f"[dim]Fetching spend limit for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.spend_limit_get(org)

        if error:
            raise typer.Exit(1)

    if data is None:
        console.print(
            f"[dim]No spend-limit data available for organization [bold]'{org}'[/bold].[/dim]"
        )
        return

    _render_show(data, org)


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
            raise typer.Exit(1)

    current_spend = (current or {}).get("currentSpendCents") or 0

    # Guard inside the prompt branches, not above them: most `set` invocations
    # (raising or setting a fresh limit) show no prompt at all, and those must
    # keep working non-interactively without --yes.
    if not yes:
        if new_cents == 0:
            console.require_interactive("--yes")
            confirm = await questionary.confirm(
                f"Setting the limit for '{org}' to $0.00 blocks all new sessions "
                "until you raise it. Continue?",
                default=False,
            ).ask_async()
            if not confirm:
                console.cancel()
                raise typer.Exit(1)
        elif current_spend > new_cents:
            console.require_interactive("--yes")
            confirm = await questionary.confirm(
                f"Current spend for '{org}' ({format_cents(current_spend)}) exceeds the new limit "
                f"({format_cents(new_cents)}). New sessions will be blocked. Continue?",
                default=False,
            ).ask_async()
            if not confirm:
                console.cancel()
                raise typer.Exit(1)

    with console.status(
        f"[dim]Setting spend limit to {format_cents(new_cents)}[/dim]",
        spinner="dots",
    ):
        data, error = await API.spend_limit_update(org, new_cents)

        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(_with_org(data, org))
        return

    console.success(
        f"Spend limit for [bold]'{org}'[/bold] set to "
        f"[bold green]{format_cents(new_cents)}[/bold green]."
    )
    if data:
        _render_show(data, org)


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
        console.require_interactive("--yes")
        confirm = await questionary.confirm(
            f"Remove the spend limit for '{org}'? New sessions will not be capped.",
            default=False,
        ).ask_async()
        if not confirm:
            console.cancel()
            raise typer.Exit(1)

    with console.status(
        f"[dim]Clearing spend limit for organization: [bold]'{org}'[/bold][/dim]",
        spinner="dots",
    ):
        data, error = await API.spend_limit_update(org, None)

        if error:
            raise typer.Exit(1)

    if console.json_output:
        console.output_json(_with_org(data, org))
        return

    console.success(f"Spend limit for [bold]'{org}'[/bold] cleared.")
    if data:
        _render_show(data, org)
