
from typing import Optional

from rich.console import Console
from rich.panel import Panel

from pipecatcloud.cli import PANEL_TITLE_ERROR
from pipecatcloud.errors import ERROR_CODES

console = Console()


def print_api_error(error_code: Optional[str], title: str):
    error_message = "Unknown error" if not error_code else ERROR_CODES.get(
        error_code, "Unknown error")

    console.print(Panel(
        f"[red]{title}[/red]\n\n"
        f"[dim]Error message:[/dim]\n{error_message}",
        title=f"[bold red]{PANEL_TITLE_ERROR} - {error_code}[/bold red]",
        subtitle=f"[dim]Docs: https://docs.pipecat.cloud/troubleshooting/#{error_code}[/dim]",
        title_align="left",
        subtitle_align="left",
        border_style="red"
    ))
