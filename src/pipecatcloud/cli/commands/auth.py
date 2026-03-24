#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import base64
import hashlib
import os
import secrets
import time
import urllib.parse
import webbrowser
from typing import Optional, Tuple

import aiohttp
import typer
from loguru import logger
from rich.columns import Columns
from rich.live import Live

from pipecatcloud._utils.async_utils import synchronizer
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

# Clerk OAuth configuration (PCC-675).
# Uses Authorization Code + PKCE — the industry-standard flow for CLI tools.
# The client_id is public per OAuth2 spec — safe to embed in client code.
CLERK_DOMAIN = "https://tender-lamb-14.clerk.accounts.dev"
CLERK_CLIENT_ID = "adc5vUqrz4E9pYg1"
REDIRECT_URI = "http://localhost:8080/oauth_callback"
SCOPES = "openid profile email offline_access"
# Cloudflare blocks requests with default Python user-agent strings (error 1010).
USER_AGENT = "PipecatCloudCLI/1.0"

AUTHORIZE_URL = f"{CLERK_DOMAIN}/oauth/authorize"
TOKEN_URL = f"{CLERK_DOMAIN}/oauth/token"


# ---- Helpers ----


def _open_url(url: str) -> bool:
    try:
        is_wsl = "WSL_DISTRO_NAME" in os.environ or "WSL_INTEROP" in os.environ
        has_display = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

        if is_wsl and not has_display:
            return False

        browser = webbrowser.get()
        if isinstance(browser, webbrowser.GenericBrowser) and browser.name not in [
            "open",
            "x-www-browser",
            "xdg-open",
        ]:
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


# ---- PKCE helpers (RFC 7636) ----


def _generate_code_verifier() -> str:
    """Generate a random code verifier (43-128 chars, RFC 7636 §4.1)."""
    return secrets.token_urlsafe(64)


def _generate_code_challenge(verifier: str) -> str:
    """S256 challenge = BASE64URL(SHA256(verifier)) per RFC 7636 §4.2."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ---- Localhost callback server ----


async def _wait_for_auth_code(timeout: float = 120.0) -> Tuple[Optional[str], Optional[str]]:
    """Start a localhost HTTP server and wait for the OAuth callback.

    Returns (auth_code, state) or (None, None) on timeout/error.
    """
    result_future: asyncio.Future[Tuple[Optional[str], Optional[str]]] = (
        asyncio.get_event_loop().create_future()
    )

    from aiohttp import web

    async def handle_callback(request: web.Request) -> web.Response:
        code = request.query.get("code")
        error = request.query.get("error")
        state = request.query.get("state")

        if error:
            logger.debug(f"OAuth callback received error: {error}")
            if not result_future.done():
                result_future.set_result((None, None))
            return web.Response(
                text="<h1>Authentication failed</h1><p>You can close this tab.</p>",
                content_type="text/html",
            )

        if not result_future.done():
            result_future.set_result((code, state))
        return web.Response(
            text="<h1>Authentication successful!</h1><p>You can close this tab.</p>",
            content_type="text/html",
        )

    app = web.Application()
    app.router.add_get("/oauth_callback", handle_callback)

    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, "localhost", 8080)
        await site.start()
    except OSError as e:
        await runner.cleanup()
        raise RuntimeError(
            f"Could not start local auth server on port 8080: {e}. "
            "Is another process using that port?"
        ) from e

    try:
        return await asyncio.wait_for(result_future, timeout=timeout)
    except asyncio.TimeoutError:
        return None, None
    finally:
        await runner.cleanup()


# ---- Token exchange & refresh ----


async def _exchange_code(code: str, code_verifier: str) -> dict:
    """Exchange an authorization code for tokens at Clerk's token endpoint."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLERK_CLIENT_ID,
                "code_verifier": code_verifier,
            },
            headers={"User-Agent": USER_AGENT},
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"Token exchange failed ({resp.status}): {body}")
            return await resp.json()


async def refresh_access_token(refresh_token: str) -> Optional[dict]:
    """Refresh an OAuth access token. Returns new token dict or None on failure.

    Called by the API client when it detects an expired token.
    """
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": CLERK_CLIENT_ID,
                },
                headers={"User-Agent": USER_AGENT},
            ) as resp:
                if resp.status != 200:
                    logger.debug(f"Token refresh failed: {resp.status}")
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug(f"Token refresh error: {e}")
            return None


# ---- Login ----


@auth_cli.command(name="login", help="Login to Pipecat Cloud")
@synchronizer.create_blocking
async def login():
    active_org = config.get("org")

    # Generate PKCE verifier + challenge
    code_verifier = _generate_code_verifier()
    code_challenge = _generate_code_challenge(code_verifier)
    state = secrets.token_urlsafe(32)

    # Build authorization URL
    params = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": CLERK_CLIENT_ID,
            "redirect_uri": REDIRECT_URI,
            "scope": SCOPES,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
    )
    authorize_url = f"{AUTHORIZE_URL}?{params}"

    # Start callback server + open browser
    console.print("[dim]Opening browser for authentication...[/dim]")
    callback_task = asyncio.ensure_future(_wait_for_auth_code())

    if not _open_url(authorize_url):
        console.print(
            f"\nOpen this URL in your browser to authenticate:\n[blue]{authorize_url}[/blue]\n"
        )

    # Wait for callback
    try:
        auth_code, returned_state = await callback_task
    except RuntimeError as e:
        console.error(str(e))
        return
    except Exception as e:
        logger.debug(e)
        console.error("Authentication failed. Please try again.")
        return

    if not auth_code:
        console.error("Authentication timed out or was cancelled.")
        return

    # Verify state to prevent CSRF
    if returned_state != state:
        console.error("Authentication failed: state mismatch (possible CSRF attack).")
        return

    # Exchange code for tokens
    try:
        tokens = await _exchange_code(auth_code, code_verifier)
    except RuntimeError as e:
        console.error(f"Token exchange failed: {e}")
        return

    access_token = tokens["access_token"]
    refresh_token = tokens.get("refresh_token")
    expires_in = tokens.get("expires_in", 86400)
    token_expires_at = time.time() + expires_in

    # Fetch user's organization
    try:
        account_name, account_name_verbose = await _get_account_org(access_token, active_org)
        if account_name is None:
            console.error(
                "Account has no associated namespace. "
                "Have you completed the onboarding process? "
                "Please first sign in via the web dashboard."
            )
            return
    except Exception:
        console.error("Failed to retrieve account information.")
        return

    # Store credentials
    update_user_config(
        token=access_token,
        active_org=account_name,
        refresh_token=refresh_token,
        token_expires_at=token_expires_at,
    )

    console.success(
        "Authentication successful!\n"
        f"[dim]Account details stored to [magenta]{user_config_path}[/magenta][/dim]"
    )


# ----- Logout -----


@auth_cli.command(name="logout", help="Logout from Pipecat Cloud")
@synchronizer.create_blocking
@requires_login
async def logout():
    refresh_token = config.get("refresh_token")

    # If this is an OAuth session, attempt server-side token revocation (PCC-678).
    # The /auth/logout endpoint proxies revocation to Clerk with the client_secret.
    if refresh_token:
        with console.status("[dim]Revoking session...[/dim]", spinner="dots"):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"{API.construct_api_url('logout_path')}",
                        json={"refresh_token": refresh_token},
                        headers={
                            "Authorization": f"Bearer {config.get('token')}",
                            "User-Agent": USER_AGENT,
                        },
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 503:
                            console.print(
                                "[yellow]Warning: Session revocation service is temporarily unavailable. "
                                "Token will expire within 24 hours.[/yellow]"
                            )
                        elif resp.ok:
                            logger.debug("Server-side token revocation succeeded")
                        else:
                            logger.debug(f"Logout revocation returned {resp.status}")
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.debug(f"Logout revocation failed: {e}")
                console.print(
                    "[yellow]Warning: Could not reach logout endpoint. "
                    "Token will expire within 24 hours.[/yellow]"
                )

    with console.status("[dim]Removing credentials...[/dim]", spinner="dots"):
        remove_user_config()

    if refresh_token:
        console.success("Logged out and session revoked.")
    else:
        console.success(
            "User credentials for Pipecat Cloud removed. Please sign out via dashboard to fully revoke session.",
            subtitle=f"[dim]Please visit:[/dim] {config.get('dashboard_host')}/sign-out",
        )


# ----- Whoami -----


@auth_cli.command(
    name="whoami", help="Display data about the current user. Also show Daily API key."
)
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
            emails = user_data.get("user", {}).get("emails", [])
            email = emails[0]["emailAddress"] if emails else user_data["user"]["userId"]

            message = Columns(
                [
                    "[bold]User[/bold]\n"
                    "[bold]Active Organization[/bold]\n"
                    "[bold]Daily API Key[/bold]",
                    f"{email}\n"
                    f"{account['verbose_name']} [dim]({account['name']})[/dim]\n"
                    f"{daily_api_key.get('apiKey', '[dim]N/A[/dim]') if daily_api_key else '[dim]N/A[/dim]'}",
                ]
            )
            console.success(message)
    except Exception:
        console.error("Unable to obtain user data. Please contact support")
