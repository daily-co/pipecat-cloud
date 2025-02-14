import typer

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.console_utils import console

# ----- Run


def create_init_command(app: typer.Typer):
    @app.command(name="init", help="Initialize project directory with template files")
    @synchronizer.create_blocking
    async def init(

    ):
        console.error("Not yet implemented")
    return init
