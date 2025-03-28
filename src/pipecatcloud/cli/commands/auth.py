#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import itertools
import webbrowser
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional, Tuple

import aiohttp
import typer
from loguru import logger
from rich.columns import Columns
from rich.live import Live
from rich.panel import Panel

from pipecatcloud._utils.async_utils import synchronize_api, synchronizer
from pipecatcloud._utils.auth_utils import requires_login
from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli.api import API
from pipecatcloud.cli.config import (
    config,
    remove_user_config,
    update_user_config,
    user_config_path,
)

auth_cli = typer.Typer(name="auth", help="Manage Pipecat Cloud credentials", no_args_is_help=True)


class _AuthFlow:
    def __init__(self):
        pass

    @asynccontextmanager
    async def start(self) -> AsyncGenerator[Tuple[Optional[str], Optional[str]], None]:
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{API.construct_api_url('login_path')}"
                logger.debug(url)
                async with session.post(url) as resp:
                    if resp.status != 200:
                        raise Exception(f"Failed to start auth flow: {resp.status}")
                    data = await resp.json()
                    self.token_flow_id = data["token_flow_id"]
                    self.wait_secret = data["wait_secret"]
                    web_url = data["web_url"]
                    yield (self.token_flow_id, web_url)
        except Exception:
            yield (None, None)

    async def finish(self, timeout: float = 40.0, network_timeout: float = 5.0) -> Optional[str]:
        start_time = asyncio.get_event_loop().time()
        async with aiohttp.ClientSession() as session:
            while (asyncio.get_event_loop().time() - start_time) < timeout:
                try:
                    async with session.get(
                        f"{config.get('api_host')}{config.get('login_status_path')}",
                        params={
                            "token_flow_id": self.token_flow_id,
                            "wait_secret": self.wait_secret,
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
        is_wsl = 'WSL_DISTRO_NAME' in os.environ or 'WSL_INTEROP' in os.environ
        has_display = bool(os.environ.get('DISPLAY') or os.environ.get('WAYLAND_DISPLAY'))

        if is_wsl and not has_display:
            return False

        browser = webbrowser.get()
        if (isinstance(browser, webbrowser.GenericBrowser) and
                browser.name not in ['open', 'x-www-browser', 'xdg-open']):
            return False

        return browser.open_new_tab(url)
    except (webbrowser.Error, ImportError, AttributeError):
        return False


async def _get_account_org(
    token: str, active_org: Optional[str] = None
) -> Tuple[Optional[str], Optional[str]]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API.construct_api_url('organization_path')}",
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

                return None, None
            else:
                raise Exception(f"Failed to retrieve account organization: {resp.status}")


# ---- Login ----


@auth_cli.command(name="login", help="Login to Pipecat Cloud and get a new token")
@synchronizer.create_blocking
async def login(
    headless: bool = typer.Option(
        False,
        "--headless",
        "-h",
        help="Skip opening a browser window for authentication and print the URL instead",
    ),
):
    active_org = config.get("org")
    auth_flow = _AuthFlow()

    if active_org:
        logger.debug(f"Current active org: {active_org}")

    try:
        async with auth_flow.start() as (token_flow_id, web_url):
            if web_url is None:
                console.error(
                    "Unable to connect to Pipecat Cloud API. Please check your network connection and try again."
                )
                return

            with Live(
                console.status(
                    "[dim]Waiting for authentication to complete...[/dim]", spinner="dots"
                ),
                transient=True,
            ) as live:
                # Open the web url in the browser
                if not headless and _open_url(web_url):
                    live.update(
                        console.status(
                            Panel(
                                "The web browser should have opened for you to authenticate with Pipecat Cloud.\n"
                                "If it didn't, please copy this URL into your web browser manually:\n\n"
                                f"[blue][link={web_url}]{web_url}[/link][/blue]\n", )))
                else:
                    # For headless use-cases, just print the URL
                    live.stop()
                    console.print("[dim]Unable to launch web browser[/dim]")
                    console.print("[bold]Please visit the following URL to confirm login[/bold]\n")
                    console.print(web_url)

                live.start()

                for attempt in itertools.count():
                    result = await auth_flow.finish()
                    if result is not None:
                        break
                    live.update(
                        console.status(
                            f"[dim]Waiting for authentication to complete... (attempt {attempt + 1})[/dim]"
                        )
                    )
                if result is None:
                    live.stop()
                    console.error("Authentication failed")
                    return typer.Exit()

                live.update(console.status("[dim]Obtaining account data[/dim]", spinner="dots"))
                try:
                    account_name, account_name_verbose = await _get_account_org(result, active_org)
                    live.stop()
                    logger.debug(f"Setting namespace to {account_name_verbose} ({account_name})")
                    if account_name is None:
                        raise
                except Exception:
                    live.stop()
                    console.error(
                        "Account has no associated namespace. Have you completed the onboarding process? Please first sign in via the web dashboard."
                    )
                    return typer.Exit()

            console.success(
                "Web authentication finished successfully!\n"
                f"[dim]Account details stored to [magenta]{user_config_path}[/magenta][/dim]"
            )

    except Exception as e:
        logger.debug(e)
        console.error("Unexpected login error occured. Please contact support.")

    console.status("[dim]Storing credentials...[/dim]")

    update_user_config(result, account_name)


# ----- Logut


@auth_cli.command(name="logout", help="Logout from Pipecat Cloud")
@synchronizer.create_blocking
@requires_login
async def logout():
    with console.status("Removing user ID", spinner="dots"):
        remove_user_config()

    console.success(
        "User credentials for Pipecat Cloud removed. Please sign out via dashboard to fully revoke session.",
        subtitle=f"[dim]Please visit:[/dim] {config.get('dashboard_host')}/sign-out",
    )


@auth_cli.command(name="whoami",
                  help="Display data about the current user. Also show Daily API key.")
@synchronizer.create_blocking
@requires_login
async def whomai():
    org = config.get("org")

    try:
        with Live(
            console.status("[dim]Requesting current user data...[/dim]", spinner="dots"),
            transient=True,
        ) as live:
            user_data, error = await API.whoami(live=live)

            if error:
                return typer.Exit()

            live.update(
                console.status("[dim]Requesting user namespace / organization data...[/dim]")
            )

            # Retrieve default user organization
            account, error = await API.organizations_current(org=org, live=live)
            if error:
                API.print_error()
                return typer.Exit()

            if not account["name"] or not account["verbose_name"]:
                raise

            # Retrieve user Daily API key
            # Note: we don't raise an error if this fails, as it's not required for
            # the CLI to function
            live.update(console.status("[dim]Fetching Daily API key...[/dim]", spinner="dots"))

            daily_api_key = None
            try:
                daily_api_key, error = await API.organizations_daily_key(org=org, live=live)
            except Exception:
                pass

            live.stop()
            message = Columns([
                "[bold]User ID[/bold]\n"
                "[bold]Active Organization[/bold]\n"
                "[bold]Daily API Key[/bold]",
                f"{user_data['user']['userId']}\n"
                f"{account['verbose_name']} [dim]({account['name']})[/dim]\n"
                f"{daily_api_key.get('apiKey', '[dim]N/A[/dim]') if daily_api_key else '[dim]N/A[/dim]'}",
            ])
            console.success(message)
    except Exception:
        console.error("Unable to obtain user data. Please contact support")
