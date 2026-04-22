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
from typing import Any

import aiohttp
import typer
from loguru import logger
from rich.columns import Columns
from rich.live import Live

from pipecatcloud.__version__ import version as _cli_version
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
USER_AGENT = f"PipecatCloudCLI/{_cli_version}"

# Ports to try for the localhost OAuth callback server.
# Our audience is developers who may have services running on common ports.
# We try a range and use the first available.
CALLBACK_HOST = "127.0.0.1"
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
    """Fetch and validate OIDC discovery document from the OAuth issuer (RFC 8414).

    This gives us the authorization and token endpoints without
    hardcoding any provider-specific URLs.

    Validates the discovery metadata per RFC 8414 §3.1 and RFC 9207 §2.4:
    - issuer must exactly match the expected value
    - authorization_endpoint and token_endpoint must use HTTPS
    - code_challenge_methods_supported must include S256 (when present)
    - response_types_supported must include "code" (when present)
    """
    url = f"{issuer}/.well-known/openid-configuration"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                raise RuntimeError(f"OIDC discovery failed ({resp.status}) at {url}")
            doc = await resp.json()

    # RFC 8414 §3.1: issuer in metadata MUST exactly match the expected issuer.
    if doc.get("issuer") != issuer:
        raise RuntimeError(f"OIDC issuer mismatch: expected {issuer!r}, got {doc.get('issuer')!r}")

    # Endpoints must use HTTPS to prevent credential interception.
    for key in ("authorization_endpoint", "token_endpoint"):
        endpoint = doc.get(key, "")
        if not endpoint.startswith("https://"):
            raise RuntimeError(f"OIDC {key} must use HTTPS, got {endpoint!r}")

    # If the server advertises supported challenge methods, verify S256 is included.
    challenge_methods = doc.get("code_challenge_methods_supported")
    if challenge_methods is not None and "S256" not in challenge_methods:
        raise RuntimeError(
            f"OIDC server does not support S256 PKCE (supported: {challenge_methods})"
        )

    # If the server advertises supported response types, verify "code" is included.
    response_types = doc.get("response_types_supported")
    if response_types is not None and "code" not in response_types:
        raise RuntimeError(
            f"OIDC server does not support 'code' response type (supported: {response_types})"
        )

    return doc


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
    token: str, active_org: str | None = None
) -> tuple[str | None, str | None]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{API.construct_api_url('organization_path')}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
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


def _callback_page(title: str, message: str, success: bool = True) -> str:
    """Generate a styled HTML callback page matching the Pipecat Cloud dashboard."""
    icon_color = "#4ade80" if success else "#f87171"
    icon_svg = (
        '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>'
        if success
        else '<circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/>'
    )
    # Pipecat logo SVG paths (from pipecat.daily.co/logo-light.svg), recolored for dark bg
    logo_svg = (
        '<svg width="48" height="29" viewBox="0 0 158 96" fill="none" xmlns="http://www.w3.org/2000/svg">'
        '<path d="M15.97 28.56c1.63-.62 3.47-.15 4.62 1.16l14.97 17.12h44.7l14.97-17.12c1.15-1.31 2.99-1.78 4.62-1.16'
        " 1.63.62 2.71 2.18 2.71 3.93v37.8h13.26v8.4H94.2V43.65l-8.89 10.17c-.8.91-1.94 1.43-3.15 1.43H33.67"
        'c-1.2 0-2.35-.52-3.15-1.43l-8.89-10.17v35.05H0v-8.4h13.26V32.49c0-1.75 1.08-3.31 2.71-3.93Z" fill="#e5e5e5"/>'
        '<path d="M94.2 87.1h21.63v8.4H94.2v-8.4Z" fill="#e5e5e5"/>'
        '<path d="M0 87.1h21.63v8.4H0v-8.4Z" fill="#e5e5e5"/>'
        '<path d="M44.66 73.1a5.58 5.58 0 1 1-11.16 0 5.58 5.58 0 0 1 11.16 0Z" fill="#e5e5e5"/>'
        '<path d="M82.34 73.1a5.58 5.58 0 1 1-11.17 0 5.58 5.58 0 0 1 11.17 0Z" fill="#e5e5e5"/>'
        '<path fill-rule="evenodd" clip-rule="evenodd" d="M81.03 10.18c-10.3 3.39-17.9 11.85-20.04 22.13l-.93 4.49'
        "-4.37-1.42c-6.03-1.96-12.45-.88-17.48 3.41l-5.35-6.23c6.13-5.24 13.77-7.17 21.17-5.99 3.74-11.35 12.8-20.36"
        " 24.47-24.19l.04-.01.05-.01c27.31-8.29 51.81 12.42 50.56 38.36h1.58c14.25 0 27.26 13.11 27.26 28.15"
        " 0 15.38-13.37 26.64-27.27 26.64h-7.66v-8.2h7.66c9.87 0 19.05-8.07 19.05-18.44 0-10.7-9.53-19.95-19.05-19.95"
        'h-11l.81-4.78c3.73-22.15-16.46-40.92-39.51-33.96Z" fill="#e5e5e5"/>'
        "</svg>"
    )
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} - Pipecat Cloud</title>
<style>
  body {{ margin:0; min-height:100vh; display:flex; align-items:center; justify-content:center;
         background:#0a0a0a; color:#e5e5e5;
         font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif; }}
  .card {{ text-align:center; padding:3rem; max-width:420px; }}
  .logo {{ margin:0 auto 2rem; opacity:0.5; }}
  .status {{ width:48px; height:48px; margin:0 auto 1.5rem; }}
  .status svg {{ width:48px; height:48px; stroke:{icon_color}; stroke-width:1.5; fill:none;
                 stroke-linecap:round; stroke-linejoin:round; }}
  h1 {{ font-size:1.25rem; font-weight:600; margin:0 0 0.5rem; color:#fafafa; }}
  p {{ font-size:0.875rem; color:#a3a3a3; margin:0; line-height:1.6; }}
</style></head>
<body><div class="card">
  <div class="logo">{logo_svg}</div>
  <div class="status"><svg viewBox="0 0 24 24">{icon_svg}</svg></div>
  <h1>{title}</h1>
  <p>{message}</p>
</div></body></html>"""


# ---- Localhost callback server ----


async def _start_callback_server() -> tuple[Any, int, "asyncio.Future"]:
    """Start a localhost HTTP server for the OAuth callback.

    Tries ports from CALLBACK_PORTS in order, using the first available.
    Returns (runner, port, result_future).
    """
    from aiohttp import web

    result_future: asyncio.Future[tuple[str | None, str | None]] = (
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
                text=_callback_page(
                    "Authentication failed",
                    "Something went wrong. Please try again.",
                    success=False,
                ),
                content_type="text/html",
            )

        if not result_future.done():
            result_future.set_result((code, state))
        return web.Response(
            text=_callback_page(
                "Authentication successful", "You can close this tab and return to your terminal."
            ),
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
            site = web.TCPSite(runner, CALLBACK_HOST, port)
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


async def refresh_access_token(refresh_token: str) -> dict | None:
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
        except (TimeoutError, aiohttp.ClientError) as e:
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

    redirect_uri = f"http://{CALLBACK_HOST}:{port}{CALLBACK_PATH}"

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
        except TimeoutError:
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
    revocation_succeeded = False

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
                            revocation_succeeded = True
                        else:
                            logger.debug(f"Logout revocation returned {resp.status}")
            except (TimeoutError, aiohttp.ClientError) as e:
                logger.debug(f"Logout revocation failed: {e}")
                console.print(
                    "[yellow]Warning: Could not reach logout endpoint. "
                    "Token will expire within 24 hours.[/yellow]"
                )

    with console.status("[dim]Removing credentials...[/dim]", spinner="dots"):
        remove_user_config()

    if revocation_succeeded:
        console.success("Logged out and session revoked.")
    elif refresh_token:
        console.success(
            "Local credentials removed. Server-side revocation could not be confirmed.\n"
            "[dim]The token will expire automatically within 24 hours.[/dim]"
        )
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
    token: str | None = typer.Argument(
        None, help="[deprecated] PAT as argument (leaks to shell history)"
    ),
):
    if token is not None:
        console.print(
            "[yellow]Warning: Passing tokens as arguments is deprecated — they are visible in "
            "shell history and process listings. Next time, omit the argument to use the "
            "secure prompt.[/yellow]"
        )
    else:
        import getpass

        token = getpass.getpass("Personal Access Token: ")
        if not token:
            console.error("No token provided.")
            return
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
