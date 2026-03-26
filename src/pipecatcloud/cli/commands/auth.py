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
from typing import Any, Optional, Tuple

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

# Cloudflare blocks requests with default Python user-agent strings (error 1010).
USER_AGENT = "PipecatCloudCLI/1.0"

# Ports to try for the localhost OAuth callback server.
# Our audience is developers who may have services running on common ports.
# We try a range and use the first available.
CALLBACK_PORTS = [8400, 8401, 8402, 8403, 8404]
CALLBACK_PATH = "/oauth_callback"


async def _fetch_oauth_config() -> dict:
    """Fetch OAuth configuration from the API server's discovery endpoint.

    The CLI doesn't hardcode any Clerk-specific values — it discovers them
    from the API server, which may differ between staging and production.
    The API server is the single source of truth (PCC-699).
    """
    config_url = f"{API.construct_api_url('auth_config_path')}"
    async with aiohttp.ClientSession() as session:
        async with session.get(config_url, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                raise RuntimeError(
                    f"Failed to fetch auth configuration ({resp.status}). "
                    "Is the API server reachable?"
                )
            return await resp.json()


async def _fetch_oidc_discovery(issuer: str) -> dict:
    """Fetch OIDC discovery document from the OAuth issuer (RFC 8414).

    This gives us the authorization and token endpoints without
    hardcoding any provider-specific URLs.
    """
    url = f"{issuer}/.well-known/openid-configuration"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"OIDC discovery failed ({resp.status}) at {url}")
            return await resp.json()


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


async def _start_callback_server() -> Tuple[Any, int, "asyncio.Future"]:
    """Start a localhost HTTP server for the OAuth callback.

    Tries ports from CALLBACK_PORTS in order, using the first available.
    Returns (runner, port, result_future).
    """
    from aiohttp import web

    result_future: asyncio.Future[Tuple[Optional[str], Optional[str]]] = (
        asyncio.get_event_loop().create_future()
    )

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
    app.router.add_get(CALLBACK_PATH, handle_callback)

    runner = web.AppRunner(app)
    await runner.setup()

    # Try each port in order. Developers often have services on common ports,
    # so we use a less-common range and try multiple.
    for port in CALLBACK_PORTS:
        try:
            site = web.TCPSite(runner, "localhost", port)
            await site.start()
            return runner, port, result_future
        except OSError:
            continue

    await runner.cleanup()
    raise RuntimeError(
        f"Could not start local auth server on any port ({CALLBACK_PORTS[0]}-{CALLBACK_PORTS[-1]}). "
        "Are these ports all in use?"
    )


# ---- Token exchange & refresh ----


async def _exchange_code(
    token_url: str, client_id: str, code: str, code_verifier: str, redirect_uri: str
) -> dict:
    """Exchange an authorization code for tokens at the OAuth token endpoint."""
    async with aiohttp.ClientSession() as session:
        async with session.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
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
    Fetches OAuth config from the discovery endpoint to get the token URL
    and client ID — no hardcoded values.
    """
    try:
        oauth_config = await _fetch_oauth_config()
        oidc = await _fetch_oidc_discovery(oauth_config["issuer"])
        token_url = oidc["token_endpoint"]
        client_id = oauth_config["client_id"]
    except Exception as e:
        logger.debug(f"Token refresh config fetch failed: {e}")
        return None

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(
                token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
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

    # Discover OAuth config from the API server — no hardcoded provider values.
    try:
        oauth_config = await _fetch_oauth_config()
        oidc = await _fetch_oidc_discovery(oauth_config["issuer"])
    except Exception as e:
        console.error(f"Failed to fetch authentication configuration: {e}")
        return

    authorize_url_base = oidc["authorization_endpoint"]
    token_url = oidc["token_endpoint"]
    client_id = oauth_config["client_id"]
    scopes = oauth_config["scopes"]

    # Start callback server on the first available port.
    try:
        runner, port, result_future = await _start_callback_server()
    except RuntimeError as e:
        console.error(str(e))
        return

    redirect_uri = f"http://localhost:{port}{CALLBACK_PATH}"

    try:
        # Generate PKCE verifier + challenge
        code_verifier = _generate_code_verifier()
        code_challenge = _generate_code_challenge(code_verifier)
        state = secrets.token_urlsafe(32)

        # Build authorization URL
        params = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": scopes,
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        authorize_url = f"{authorize_url_base}?{params}"

        # Open browser
        console.print("[dim]Opening browser for authentication...[/dim]")

        if not _open_url(authorize_url):
            console.print(
                f"\nOpen this URL in your browser to authenticate:\n[blue]{authorize_url}[/blue]\n"
            )

        # Wait for callback
        try:
            auth_code, returned_state = await asyncio.wait_for(result_future, timeout=120.0)
        except asyncio.TimeoutError:
            console.error("Authentication timed out.")
            return

        if not auth_code:
            console.error("Authentication was cancelled or failed.")
            return

        # Verify state to prevent CSRF
        if returned_state != state:
            console.error("Authentication failed: state mismatch (possible CSRF attack).")
            return

        # Exchange code for tokens
        try:
            tokens = await _exchange_code(
                token_url, client_id, auth_code, code_verifier, redirect_uri
            )
        except RuntimeError as e:
            console.error(f"Token exchange failed: {e}")
            return

    finally:
        await runner.cleanup()

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


# ----- Use PAT -----


async def _use_pat_impl(token: str):
    """Core logic for use-pat command, extracted for testability."""
    if not token.startswith("pcc_pat_"):
        console.error("Invalid token format. PATs must start with [bold]pcc_pat_[/bold]")
        return

    # Verify the PAT works by fetching the user's organizations
    # Preserve the currently active org if set
    active_org = config.get("org")
    try:
        with console.status("[dim]Verifying token...[/dim]", spinner="dots"):
            account_name, account_name_verbose = await _get_account_org(token, active_org)
            if account_name is None:
                console.error(
                    "Token is valid but account has no associated namespace. "
                    "Have you completed the onboarding process?"
                )
                return
    except Exception:
        console.error("Invalid or expired token.")
        return

    # Store PAT — clear OAuth-specific fields since they don't apply
    update_user_config(
        token=token,
        active_org=account_name,
        refresh_token="",
        token_expires_at=0,
    )

    console.success(
        f"Authenticated via PAT. Active organization: [bold]{account_name}[/bold]\n"
        f"[dim]Credentials stored to [magenta]{user_config_path}[/magenta][/dim]"
    )


@auth_cli.command(name="use-pat", help="Authenticate with a Personal Access Token")
@synchronizer.create_blocking
async def use_pat(
    token: str = typer.Argument(help="Personal Access Token (pcc_pat_...)"),
):
    await _use_pat_impl(token)


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

            token = config.get("token", "")
            auth_method = "PAT" if token.startswith("pcc_pat_") else "OAuth"

            message = Columns(
                [
                    "[bold]User[/bold]\n"
                    "[bold]Active Organization[/bold]\n"
                    "[bold]Auth Method[/bold]\n"
                    "[bold]Daily API Key[/bold]",
                    f"{email}\n"
                    f"{account['verbose_name']} [dim]({account['name']})[/dim]\n"
                    f"{auth_method}\n"
                    f"{daily_api_key.get('apiKey', '[dim]N/A[/dim]') if daily_api_key else '[dim]N/A[/dim]'}",
                ]
            )
            console.success(message)
    except Exception:
        console.error("Unable to obtain user data. Please contact support")
