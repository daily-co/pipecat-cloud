import typer
from rich.console import Console

from pipecatcloud._utils.async_utils import synchronizer

console = Console()


# ----- Run


def create_run_command(app: typer.Typer):
    @app.command(name="run", help="Run an agent locally")
    @synchronizer.create_blocking
    async def run(
        ctx: typer.Context,
        entrypoint: str,
        host: str = typer.Option(
            "0.0.0.0",
            "--host",
            help="Host to run the agent on",
            rich_help_panel="Run Configuration",
        ),
        port: int = typer.Option(
            8000,
            "--port",
            help="Port to run the agent on",
            rich_help_panel="Run Configuration",
        ),
    ):
        from pipecatcloud._utils.local_runner import start_server
        await start_server(entrypoint, host, port)
    return run
