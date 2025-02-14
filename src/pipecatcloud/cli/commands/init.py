import typer

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.console_utils import console

# ----- Run


def create_init_command(app: typer.Typer):
    @app.command(name="init", help="Run an agent locally")
    @synchronizer.create_blocking
    async def init(

    ):
        console.error("Not yet implemented")
    return init
