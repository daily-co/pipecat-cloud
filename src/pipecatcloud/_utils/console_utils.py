from typing import Optional

from rich.console import Console
from rich.panel import Panel

from pipecatcloud.cli import PANEL_TITLE_ERROR, PANEL_TITLE_SUCCESS
from pipecatcloud.errors import ERROR_CODES


class PipecatConsole(Console):
    def success(
            self,
            message,
            title: Optional[str] = None,
            title_extra: Optional[str] = None,
            subtitle: Optional[str] = None):

        if not title:
            title = f"{PANEL_TITLE_SUCCESS}{f' - {title_extra}' if title_extra is not None else ''}"

        self.print(
            Panel(
                message,
                title=f"[bold green]{title}[/bold green]",
                subtitle=subtitle,
                title_align="left",
                subtitle_align="left",
                border_style="green"))

    def error(
            self,
            message,
            title: Optional[str] = None,
            title_extra: Optional[str] = None,
            subtitle: Optional[str] = None):

        if not title:
            title = f"{PANEL_TITLE_ERROR}{f' - {title_extra}' if title_extra is not None else ''}"

        self.print(
            Panel(
                message,
                title=f"[bold red]{title}[/bold red]",
                subtitle=subtitle,
                title_align="left",
                subtitle_align="left",
                border_style="red"))

    def cancel(self):
        self.print("[yellow]Cancelled by user[/yellow]")

    def unauthorized(self):
        self.api_error("401", "Unauthorized", hide_subtitle=True)

    def api_error(
            self,
            error_code: Optional[str],
            title: Optional[str] = "API Error",
            hide_subtitle: bool = False):
        DEFAULT_ERROR_MESSAGE = "Unknown error. Please contact support."
        ERROR_MESSAGE = ERROR_CODES.get(error_code, None) if error_code else None

        if not ERROR_MESSAGE:
            hide_subtitle = True

        error_message = ERROR_MESSAGE if ERROR_MESSAGE else DEFAULT_ERROR_MESSAGE
        self.print(
            Panel(
                f"[red]{title}[/red]\n\n"
                f"[dim]Error message:[/dim]\n{error_message}",
                title=f"[bold red]{PANEL_TITLE_ERROR} - {error_code}[/bold red]",
                subtitle=f"[dim]Docs: https://docs.pipecat.daily.co/agents/error-codes#{error_code}[/dim]" if not hide_subtitle else None,
                title_align="left",
                subtitle_align="left",
                border_style="red"))


console = PipecatConsole()
