import typer
from rich.console import Console
from rich.panel import Panel

from pipecatcloud.cli import PANEL_TITLE_NOT_IMPLEMENTED

console = Console()


# ----- Run


def create_run_command(app: typer.Typer):
    # Note we wrap the deploy command to avoid circular imports
    @app.command(name="run", help="Run an agent locally")
    def run(
        ctx: typer.Context,

    ):
        console.print(
            Panel(
                "Local bot runner is not yet implemented.",
                title=PANEL_TITLE_NOT_IMPLEMENTED,
                title_align="left",
                style="yellow",
                border_style="yellow"))

    return run
