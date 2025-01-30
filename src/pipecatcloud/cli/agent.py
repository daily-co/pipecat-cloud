import aiohttp
import questionary
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from pipecatcloud._utils.async_utils import synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud.config import config, _store_user_config, user_config_path
from pipecatcloud.exception import AuthError

agent_cli = typer.Typer(
    name="agent", help="Agent management.", no_args_is_help=True
)


@agent_cli.command(name="list", help="List organizations user is a member of.")
@synchronizer.create_blocking
@requires_login
async def list(ctx: typer.Context):
    console = Console()
    console.print("Agent list")
