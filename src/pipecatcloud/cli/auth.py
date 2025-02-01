import asyncio
import itertools
import webbrowser
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Tuple

import aiohttp
import typer
from loguru import logger
from rich.console import Console
from rich.panel import Panel

from pipecatcloud._utils.async_utils import synchronize_api, synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.http_utils import construct_api_url
from pipecatcloud.cli import PANEL_TITLE_ERROR, PANEL_TITLE_SUCCESS
from pipecatcloud.config import _remove_user_config, _store_user_config, config, user_config_path

auth_cli = typer.Typer(name="auth", help="Manage Pipecat Cloud credentials.", no_args_is_help=True)


class _AuthFlow:
    def __init__(self):
        pass

    @asynccontextmanager
    async def start(self) -> AsyncGenerator[Tuple[Optional[str], Optional[str]], None]:
        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(f"{construct_api_url('login_path')}") as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to start auth flow: {resp.status}")
                    data = await resp.json()
                    self.token_flow_id = data["token_flow_id"]
                    self.wait_secret = data["wait_secret"]
                    web_url = data["web_url"]

                    yield (self.token_flow_id, web_url)
            except Exception:
                pass
            yield (None, None)

    async def finish(self, timeout: float = 40.0, network_timeout: float = 5.0) -> Optional[str]:
        start_time = asyncio.get_event_loop().time()
        async with aiohttp.ClientSession() as session:
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                try:
                    async with session.get(
                        f"{config.get('server_url')}{config.get('login_status_path')}",
                        params={
                            "token_flow_id": self.token_flow_id,
                            "wait_secret": self.wait_secret
                        },
                        timeout=aiohttp.ClientTimeout(total=timeout + network_timeout),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            if data["status"] == "complete":
                                return data["token"]
                            if data["status"] == "failure":
                                return "failure"
                        await asyncio.sleep(2)
                        continue
                except (asyncio.TimeoutError, aiohttp.ClientError):
                    continue
            return None


AuthFlow = synchronize_api(_AuthFlow)


def _open_url(url: str) -> bool:
    try:
        browser = webbrowser.get()
        if isinstance(browser, webbrowser.GenericBrowser) and browser.name != "open":
            return False
        else:
            return browser.open_new_tab(url)
    except webbrowser.Error:
        return False


async def _set_credentials(
    token: str,
    account_org: str,
):
    console = Console()
    with console.status("Storing user credentials", spinner="dots"):
        _store_user_config(token, account_org)


async def _get_account_org(
        token: str, active_org: Optional[str] = None) -> Optional[Tuple[str, str]]:
    console = Console()
    # Retrieve account organization
    with console.status("[dim]Obtaining account organization data[/dim]", spinner="dots"):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{construct_api_url('organization_path')}",
                headers={"Authorization": f"Bearer {token}"},
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    organizations = data["organizations"]

                    # If active_org is specified, try to find it in the list
                    if active_org:
                        for org in organizations:
                            if org["name"] == active_org:
                                return org["name"], org["verboseName"]

                    # Default to first organization if active_org not found or not specified
                    if organizations:
                        return organizations[0]["name"], organizations[0]["verboseName"]

                    return None
                else:
                    raise Exception(f"Failed to retrieve account organization: {resp.status}")


# ----- Login

async def _login(active_org: Optional[str] = None):
    console = Console()
    auth_flow = _AuthFlow()

    try:
        async with auth_flow.start() as (token_flow_id, web_url):
            if web_url is None:
                console.print(
                    Panel(
                        "Unable to connect to Pipecat Cloud API. Please check your network connection and try again.",
                        title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                        title_align="left",
                        border_style="red",
                    ))
                return

            with console.status("Waiting for authentication in the web browser", spinner="dots"):
                # Open the web url in the browser
                if _open_url(web_url):
                    console.print(
                        Panel(
                            "The web browser should have opened for you to authenticate with Pipecat Cloud.\n"
                            "If it didn't, please copy this URL into your web browser manually:\n\n"
                            f"[blue][link={web_url}]{web_url}[/link][/blue]\n", ))
                else:
                    console.print(Panel(
                        "[red]Was not able to launch web browser[/red]",
                        title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                        title_align="left",
                        border_style="red",
                    ))

            with console.status("Waiting for token flow to complete...", spinner="dots") as status:
                for attempt in itertools.count():
                    result = await auth_flow.finish()
                    if result is not None:
                        break
                    status.update(f"Waiting for token flow to complete... (attempt {attempt + 1})")
                if result is None:
                    console.print(Panel(
                        "[red]Authentication failed[/red]",
                        title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                        title_align="left",
                        border_style="red",
                    ))
                    return

            # Retrieve user namespace
            if not active_org:
                try:
                    account_name, account_name_verbose = await _get_account_org(result)
                    logger.debug(f"Setting namespace to {account_name_verbose}")
                    if account_name is None:
                        raise
                except Exception:
                    console.print(
                        Panel(
                            "[red]Account has no associated namespace. Have you completed the onboarding process? Please first sign in via the web dashboard (https://dashboard.pipecat.cloud).[/red]",
                            title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                            title_align="left",
                            border_style="red",
                        ))
                    return
            else:
                account_name = active_org

            console.print(Panel(
                "[green]Web authentication finished successfully![/green]\n"
                f"Account details stored to [magenta]{user_config_path}[/magenta]",
                title=f"[green]{PANEL_TITLE_SUCCESS}[/green]",
                title_align="left",
                border_style="green",
            ))
            await _set_credentials(result, account_name)
    except Exception:
        pass


@auth_cli.command(name="login", help="Login to Pipecat Cloud and get a new token")
@synchronizer.create_blocking
async def login(ctx: typer.Context):
    active_org = ctx.obj["org"]
    await _login(active_org)


# ----- Logut

@auth_cli.command(name="logout", help="Logout from Pipecat Cloud")
@synchronizer.create_blocking
@requires_login
async def logout():
    """
    # This command should delete the session token
    # due to networkless token verification, the token will not be invalidated
    # it will just be removed from the local config file
    # if we want token invalidation, we'll need to either use sign-in tokens with clerk
    # or use a networked token verification system of our own design
    """
    console = Console()

    with console.status("Removing user ID", spinner="dots"):
        _remove_user_config()

    console.print(Panel(
        "You are now logged out of Pipecat Cloud.",
        title=f"[yellow]{PANEL_TITLE_SUCCESS}[/yellow]",
        title_align="left",
        border_style="yellow",
    ))


@auth_cli.command(name="whoami", help="Display data about the current user.")
@synchronizer.create_blocking
@requires_login
async def whomai():
    console = Console()
    token = config.get("token")
    active_org = config.get("org")

    async with aiohttp.ClientSession() as session:
        try:
            # Retrieve user data from whoami endpoint
            with console.status("[dim]Obtaining user data[/dim]", spinner="dots"):
                async with session.get(
                    f"{construct_api_url('whoami_path')}",
                    headers={"Authorization": f"Bearer {token}"},
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                    else:
                        raise

            # Retrieve default user organization
            account_name, account_name_verbose = await _get_account_org(token, active_org)
            if account_name is None:
                raise

            console.print(Panel(
                f"[bold]User ID:[/bold] {data['user']['userId']}\n"
                f"[bold]Active Organization:[/bold] {account_name_verbose} [dim]({account_name})[/dim]",
                title="whoami",
                title_align="left",
                border_style="dim",
            ))
        except Exception:
            console.print(Panel(
                "[red]Failed to get user data. Please contact support.[/red]",
                title=f"[red]{PANEL_TITLE_ERROR}[/red]",
                title_align="left",
                border_style="red",
            ))
            return
