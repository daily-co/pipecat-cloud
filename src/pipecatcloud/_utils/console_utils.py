from typing import Optional, Union

from rich.console import Console
from rich.panel import Panel

from pipecatcloud.cli import PANEL_TITLE_ERROR, PANEL_TITLE_SUCCESS, PIPECAT_CLI_NAME


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
        self.print(
            Panel(
                f"Unauthorized request / invalid user token.\n\nPlease log in again using [bold cyan]{PIPECAT_CLI_NAME} auth login[/bold cyan]",
                title=f"[bold red]{PANEL_TITLE_ERROR} - Unauthorized (401)[/bold red]",
                subtitle="",
                title_align="left",
                subtitle_align="left",
                border_style="red"))

    def api_error(
            self,
            error_code: Optional[Union[str, dict]] = None,
            title: Optional[str] = "API Error",
            hide_subtitle: bool = False):
        DEFAULT_ERROR_MESSAGE = "Unknown error. Please contact support."

        if isinstance(error_code, dict):
            error_message = error_code.get("error", DEFAULT_ERROR_MESSAGE)
            code = error_code.get("code")
        else:
            error_message = str(error_code) if error_code else DEFAULT_ERROR_MESSAGE
            code = None

        if not error_message:
            hide_subtitle = True

        self.print(
            Panel(
                f"[red]{title}[/red]\n\n"
                f"[dim]Error message:[/dim]\n{error_message}",
                title=f"[bold red]{PANEL_TITLE_ERROR}{f' - {code}' if code else ''}[/bold red]",
                subtitle=f"[dim]Docs: https://docs.pipecat.daily.co/agents/error-codes#{code}[/dim]" if not hide_subtitle and code else None,
                title_align="left",
                subtitle_align="left",
                border_style="red"))


console = PipecatConsole()
