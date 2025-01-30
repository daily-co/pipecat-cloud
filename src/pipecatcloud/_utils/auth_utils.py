import functools

from rich.console import Console
from rich.panel import Panel

from pipecatcloud import PIPECAT_CLI_NAME
from pipecatcloud.cli import PANEL_TITLE_ERROR
from pipecatcloud.config import config


def requires_login(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        console = Console()
        org = config.get("org")
        token = config.get("token")
        if org is None or token is None:
            console.print(Panel(
                f"You are not logged in. Please run `{PIPECAT_CLI_NAME} auth login` first.",
                title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                title_align="left",
                border_style="red",
            ))
            return
        return func(*args, **kwargs)
    return wrapper
