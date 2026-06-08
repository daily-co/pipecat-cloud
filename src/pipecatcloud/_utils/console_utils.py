#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import statistics
from datetime import datetime

from rich.console import Console
from rich.panel import Panel

from pipecatcloud.cli import PANEL_TITLE_ERROR, PANEL_TITLE_SUCCESS, PIPECAT_CLI_NAME


def format_cents(cents: int | None) -> str:
    """Render a cents value as a USD dollar string (e.g. 1234 -> "$12.34").

    Uses integer arithmetic so the rendered value is exact for any in-range
    integer, even values float-division would round (e.g. very large cent
    counts where `cents / 100` loses precision).
    """
    if cents is None:
        return "no limit"
    sign = "-" if cents < 0 else ""
    abs_cents = abs(cents)
    return f"{sign}${abs_cents // 100}.{abs_cents % 100:02d}"


class PipecatConsole(Console):
    def success(
        self,
        message,
        title: str | None = None,
        title_extra: str | None = None,
        subtitle: str | None = None,
    ):
        if not title:
            title = f"{PANEL_TITLE_SUCCESS}{f' - {title_extra}' if title_extra is not None else ''}"

        self.print(
            Panel(
                message,
                title=f"[bold green]{title}[/bold green]",
                subtitle=subtitle,
                title_align="left",
                subtitle_align="left",
                border_style="green",
            )
        )

    def error(
        self,
        message,
        title: str | None = None,
        title_extra: str | None = None,
        subtitle: str | None = None,
    ):
        if not title:
            title = f"{PANEL_TITLE_ERROR}{f' - {title_extra}' if title_extra is not None else ''}"

        self.print(
            Panel(
                message,
                title=f"[bold red]{title}[/bold red]",
                subtitle=subtitle,
                title_align="left",
                subtitle_align="left",
                border_style="red",
            )
        )

    def cancel(self):
        self.print("[yellow]Cancelled by user[/yellow]")

    def unauthorized(self):
        self.print(
            Panel(
                f"Unauthorized request / invalid user token.\n\nPlease log in again using [bold cyan]{PIPECAT_CLI_NAME} auth login[/bold cyan]",
                title=f"[bold red]{PANEL_TITLE_ERROR} - Unauthorized (401)[/bold red]",
                subtitle="",
                title_align="left",
                subtitle_align="left",
                border_style="red",
            )
        )

    def api_error(
        self,
        error_code: str | dict | None = None,
        title: str | None = "API Error",
        hide_subtitle: bool = False,
    ):
        DEFAULT_ERROR_MESSAGE = "Unknown error. Please contact support."

        if isinstance(error_code, dict):
            error_message = (
                error_code.get("error", None)
                or error_code.get("message", None)
                or DEFAULT_ERROR_MESSAGE
            )
            code = error_code.get("code")
        else:
            error_message = str(error_code) if error_code else DEFAULT_ERROR_MESSAGE
            code = None

        if code == "SPEND_LIMIT_REACHED":
            # Safe to pass through: code is only set when error_code is a dict.
            self._spend_limit_reached(error_code)  # type: ignore[arg-type]
            return

        if not error_message:
            hide_subtitle = True

        self.print(
            Panel(
                f"[red]{title}[/red]\n\n[dim]Error message:[/dim]\n{error_message}",
                title=f"[bold red]{PANEL_TITLE_ERROR}{f' - {code}' if code else ''}[/bold red]",
                subtitle=f"[dim]Docs: https://docs.pipecat.daily.co/agents/error-codes#{code}[/dim]"
                if not hide_subtitle and code
                else None,
                title_align="left",
                subtitle_align="left",
                border_style="red",
            )
        )

    def _spend_limit_reached(self, error: dict):
        """Render the wrapped remediation message for HTTP 402 SPEND_LIMIT_REACHED.

        Falls back gracefully if the error body omits the spend fields: in that
        case we render the remediation message without the usage line.
        """
        from pipecatcloud.config import config as base_config

        current = error.get("currentSpendCents")
        limit = error.get("limitCents")

        if current is not None and limit is not None:
            usage_line = (
                f"Your organization has reached its spend limit "
                f"({format_cents(current)} of {format_cents(limit)} used in this billing period)."
            )
        else:
            usage_line = (
                "Your organization has reached its spend limit for the current billing period."
            )

        # Fall back to the documented default if the host is unset or empty,
        # so the rendered link is never `[link=][/link]` or `[link=None]None[/link]`.
        dashboard = base_config.get("dashboard_host") or "https://pipecat.daily.co"
        body = (
            f"{usage_line}\n"
            "New sessions are being rejected. In-flight sessions continue to run.\n\n"
            "To unblock new sessions:\n"
            f"  [bold cyan]{PIPECAT_CLI_NAME} spend-limit set <new amount>[/bold cyan]\n"
            f"  [bold cyan]{PIPECAT_CLI_NAME} spend-limit clear[/bold cyan]\n\n"
            f"Or visit [link={dashboard}]{dashboard}[/link]"
        )

        self.print(
            Panel(
                body,
                title=f"[bold red]{PANEL_TITLE_ERROR} - Spend limit reached (402)[/bold red]",
                title_align="left",
                border_style="red",
            )
        )


console = PipecatConsole()


def format_timestamp(timestamp: str) -> str:
    """
    Format a timestamp string to a more readable format.
    Handles timestamps with variable microsecond precision.

    Args:
        timestamp (str): The timestamp string in ISO format with microseconds (e.g. "2024-01-01T12:34:56.789Z")

    Returns:
        str: The formatted timestamp string
    """
    from datetime import datetime

    # First try parsing the timestamp directly
    try:
        return datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S.%fZ").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        pass

    # Handle case where microseconds have higher precision
    try:
        parts = timestamp.split(".")
        if len(parts) == 2 and parts[1].endswith("Z"):
            # Truncate microseconds to 6 digits
            microseconds = parts[1][:-1][:6].ljust(6, "0")
            normalized = f"{parts[0]}.{microseconds}Z"
            return datetime.strptime(normalized, "%Y-%m-%dT%H:%M:%S.%fZ").strftime(
                "%Y-%m-%d %H:%M:%S"
            )
    except (ValueError, IndexError):
        pass

    # Return original if parsing fails
    return timestamp


def format_duration(created_at_str: str, ended_at_str: str) -> str | None:
    """Calculate and format session duration as HH:MM:SS"""
    if not ended_at_str:
        return None

    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        ended_at = datetime.fromisoformat(ended_at_str.replace("Z", "+00:00"))
        duration = ended_at - created_at

        # Convert to total seconds
        total_seconds = int(duration.total_seconds())

        # Calculate hours, minutes, seconds
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        seconds = total_seconds % 60

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    except Exception:
        return None


def calculate_percentiles(data: list[float]) -> tuple[float, float, float] | None:
    """
    Calculate average, 5th percentile, and 95th percentile for a list of numeric data.

    Args:
        data: List of float values to analyze

    Returns:
        Tuple of (average, p5, p95) if data is provided, None if empty list.
        For single values, p5 and p95 will equal the single value.
    """
    if not data:
        return None

    avg = statistics.mean(data)

    if len(data) == 1:
        return avg, data[0], data[0]

    sorted_data = sorted(data)

    def percentile(data_list, p):
        if not data_list:
            return 0
        k = (len(data_list) - 1) * p / 100
        f = int(k)
        c = k - f
        if f + 1 < len(data_list):
            return data_list[f] * (1 - c) + data_list[f + 1] * c
        else:
            return data_list[f]

    p5 = percentile(sorted_data, 5)
    p95 = percentile(sorted_data, 95)

    return avg, p5, p95


async def cli_updates_available() -> str | None:
    """
    Check if there are updates available for the CLI from PyPI.
    """
    import aiohttp

    try:
        from importlib.metadata import version as get_version

        current_version = get_version("pipecatcloud")
    except ImportError:
        return None

    pypi_url = "https://pypi.org/pypi/pipecatcloud/json"

    try:
        async with aiohttp.ClientSession() as session:
            response = await session.get(pypi_url)
            if not response.ok:
                return None

            data = await response.json()
            latest_version = data["info"]["version"]

            if latest_version > current_version:
                return latest_version
            return None

    except Exception:
        return None
