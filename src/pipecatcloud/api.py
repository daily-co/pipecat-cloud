#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import json
import time
from collections.abc import Callable
from functools import wraps
from urllib.parse import quote

import aiohttp
from loguru import logger

from pipecatcloud.__version__ import version
from pipecatcloud._utils.deploy_utils import DeployConfigParams
from pipecatcloud._utils.log_utils import describe_body, describe_exception, describe_url
from pipecatcloud.config import config
from pipecatcloud.exception import AgentStartError


def api_method(func):
    @wraps(func)
    async def wrapper(self, *args, live=None, **kwargs):
        try:
            result = await func(self, *args, **kwargs)
            return result, self.error
        except Exception as e:
            if live:
                live.stop()
            raise e

    return wrapper


# A secrets write can be refused with a 503 and `Retry-After` while the region
# is not ready to store secrets yet (a self-hosted region's first write), while
# the server is busy with other writes, or when the region's answer was lost and
# the write may have landed. Repeating the same write is right for all of them.
# The CLI waits and repeats the request, bounded so a region that stays unready
# fails rather than hanging. Three retries cover a short warm-up (the server
# asks for about 5 seconds) with room to spare. The waits total at most
# 3 x 30 seconds; each of the 4 attempts can also take up to the client's own
# request timeout, so the worst case is those waits plus 4 timeouts. A larger
# `Retry-After` than the cap means the server wants longer than we are willing
# to wait. The request is not retried, and not clamped either: repeating it
# before the time the server named goes against the header. The error says so.
SECRETS_WRITE_MAX_RETRIES = 3
SECRETS_WRITE_MAX_RETRY_AFTER = 30

# How the API says what it takes. A server that writes several fields of a secret
# set in one request names this capability on its secrets responses, so the CLI
# knows without trying. One that does not is written one field at a time.
CAPABILITIES_HEADER = "Pipecat-Cloud-Capabilities"
SECRETS_BULK_WRITE_CAPABILITY = "secrets-bulk-write"

# Every eval route answers 503 with this code while evals are switched off.
EVALS_NOT_AVAILABLE_CODE = "EVALS-NOT-AVAILABLE"
EVALS_NOT_AVAILABLE_MESSAGE = "Evals aren't available yet."
# The suite and bundle routes answer 503 with these while their part is off.
EVAL_NOT_AVAILABLE_MESSAGES = {
    EVALS_NOT_AVAILABLE_CODE: EVALS_NOT_AVAILABLE_MESSAGE,
    "EVAL-SUITES-NOT-AVAILABLE": "Suite runs aren't available for this organization yet.",
    "EVAL-BUNDLES-NOT-AVAILABLE": "Eval bundles aren't available for this organization yet.",
}


def _parse_retry_after(value: str | None) -> int | None:
    """Seconds from a `Retry-After` header, or None when absent, negative, or not
    a whole number of seconds (the HTTP-date form is not honoured). A value above
    the cap is returned as sent; the caller decides not to wait it out."""
    if value is None:
        return None
    try:
        seconds = int(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


class _API:
    def __init__(self, token: str | None = None, is_cli: bool = False):
        self.token = token
        self.error = None
        # The HTTP status of the last failed response, beside `error`, which
        # is its body. A body's `code` is the API's own error code, which is
        # not always the status (a 403 for someone outside the organization
        # carries a code of its own), so a caller that decides on the status
        # reads it here. None when the request got no response at all.
        self.error_status: int | None = None
        # The `Retry-After` header of the last failed response, as sent, or
        # None when it had none.
        self.error_retry_after: str | None = None
        # The capabilities header of the last response, as sent, or None when it
        # had none.
        self.capabilities_header: str | None = None
        # Whether the server said, on the last `secrets_get`, that it takes
        # several fields of a set in one write. False until it does.
        self.secrets_bulk_write: bool = False
        self.bubble_next = False
        self.is_cli = is_cli

    @staticmethod
    def construct_api_url(path: str) -> str:
        if not config.get("api_host", ""):
            raise ValueError("API host config variable is not set")

        if not config.get(path, ""):
            raise ValueError(f"Endpoint {path} is not set")

        return f"{config.get('api_host', '')}{config.get(path, '')}"

    def _configure_headers(self, override_token: str | None = None) -> dict:
        headers = {"User-Agent": f"PipecatCloudCLI/{version}"}
        if self.token or override_token:
            headers["Authorization"] = f"Bearer {override_token or self.token}"
        return headers

    def _is_pat(self) -> bool:
        """Check if the current token is a Personal Access Token."""
        return bool(self.token and self.token.startswith("pcc_pat_"))

    def _is_token_expired(self) -> bool:
        """Check if the current OAuth token is near expiry.

        Returns False for PATs (they don't expire via OAuth) and for
        non-OAuth tokens (no token_expires_at stored).
        """
        if self._is_pat():
            return False

        from pipecatcloud.cli.config import config as cli_config

        expires_at = cli_config.get("token_expires_at")
        if expires_at is None:
            return False
        # Refresh 60 seconds before actual expiry to avoid race conditions
        return time.time() > (float(expires_at) - 60)

    async def _refresh_oauth_token(self) -> str | None:
        """Refresh the OAuth access token using the stored refresh token.

        Updates both the on-disk config and the in-memory token on this API
        instance. Returns the new access token, or None if refresh failed
        (in which case the request will proceed with the expired token and
        the server will return 401, prompting re-login).
        """
        # Inline import: auth.py → cli/api.py → api.py creates a circular
        # dependency at module load time. Importing here (at call time)
        # breaks the cycle.
        from pipecatcloud.cli.commands.auth import refresh_access_token
        from pipecatcloud.cli.config import config as cli_config
        from pipecatcloud.cli.config import update_user_config

        refresh_token = cli_config.get("refresh_token")
        if not refresh_token:
            return None

        tokens = await refresh_access_token(refresh_token)
        if not tokens:
            return None

        new_token = tokens["access_token"]
        new_refresh = tokens.get("refresh_token", refresh_token)
        expires_at = time.time() + tokens.get("expires_in", 86400)

        update_user_config(
            token=new_token,
            refresh_token=new_refresh,
            token_expires_at=expires_at,
        )
        self.token = new_token
        return new_token

    def _record_unread_answer(self, e: BaseException, reading_the_answer: bool = False) -> None:
        """Record a request that produced no answer this client could read, in
        the shape of an error body from the API so that it renders as the
        failure it is. The exception's type is named, never its text, which can
        carry the URL and the body the request was sent with.

        `error_status` and `error_retry_after` stay None whether the answer never
        began or stopped part way through: there is no answer to take the
        outcome from. That is what `no_response()` reads, and what tells a
        caller the request may still have been carried out.
        """
        reach = (
            "Could not read the answer from the Pipecat Cloud API"
            if reading_the_answer
            else "Could not reach the Pipecat Cloud API"
        )
        self.error = {
            "error": (
                f"{reach} ({describe_exception(e)}). Check your network connection and try again."
            )
        }
        self.error_status = None
        self.error_retry_after = None

    # TODO: several auth.py and agent.py calls bypass _base_request() with their
    # own aiohttp.ClientSession(). A longer-term cleanup could route all PCC API
    # calls through _base_request() so headers (User-Agent, Auth) are set in one place.
    async def _base_request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        json: dict | None = None,
        not_found_is_empty: bool = False,
        override_token: str | None = None,
    ) -> dict | None:
        # Auto-refresh expired OAuth tokens before making the request.
        # Only runs for CLI usage (self.is_cli) and only when an OAuth
        # refresh_token is stored (token_expires_at is set). Old device-code
        # tokens are unaffected since _is_token_expired() returns False.
        if self.is_cli and not override_token and self._is_token_expired():
            new_token = await self._refresh_oauth_token()
            if new_token is None:
                logger.debug("Token refresh failed, proceeding with expired token")

        async with aiohttp.ClientSession() as session:
            # Every route comes through here, and a body or a query can hold secret
            # values, so neither is logged, whichever route this is.
            logger.debug(
                f"Request: {method} {describe_url(url)} "
                f"params: {describe_body(params)} body: {describe_body(json)}"
            )

            try:
                response = await session.request(
                    method=method,
                    url=url,
                    headers=self._configure_headers(override_token),
                    params=params,
                    json=json,
                )
            except (aiohttp.ClientError, TimeoutError) as e:
                # The request got no answer at all: the host could not be
                # resolved or reached, the connection dropped, the request timed
                # out. Record it before raising, so it renders like any other
                # failure rather than turning a dropped network into a traceback
                # from the wrapper in `create_api_method`.
                self._record_unread_answer(e)
                raise
            self.capabilities_header = response.headers.get(CAPABILITIES_HEADER)

            if not response.ok:
                logger.debug(f"Response not ok: {response.status} {response.reason}")
                if self.is_cli and not_found_is_empty and response.status == 404:
                    return None

                self.error_status = response.status
                self.error_retry_after = response.headers.get("Retry-After")
                # Extract PCC error code, where applicable
                try:
                    # Try to parse the error as JSON
                    error_data = await response.json()
                    self.error = error_data
                except Exception:
                    # Fallback structure matching API format
                    self.error = {
                        "error": response.reason or "Bad Request",
                        "code": str(response.status),
                    }
                response.raise_for_status()

            # 204 has no body, and Express strips Content-Type with it, so
            # .json() raises ContentTypeError rather than returning anything.
            # That exception used to be swallowed by create_api_method (self.error
            # is unset on an ok response) and surface as a bare None, which is the
            # right answer reached by accident. Return it deliberately: callers
            # now branch on the shape of this value, and an accident is a poor
            # thing to branch on.
            if response.status == 204:
                return None

            try:
                return await response.json()
            except aiohttp.ContentTypeError:
                # An ok answer that is not JSON. The operation itself succeeded,
                # so this stays None, the same answer a 204 gives, rather than
                # becoming a failure: a caller tolerates a missing body already
                # (`... or {}`), and reporting a completed write as failed would
                # send the user to repeat it. Spelled out because the swallow in
                # `create_api_method` that used to produce this is gone.
                logger.debug(f"Response body is not JSON: {response.status}")
                return None
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                # The answer's headers arrived but its body did not: aiohttp
                # returns from `request()` as soon as the headers are in, so a
                # connection lost mid-body, a read that timed out, or a body
                # that ends part way through its JSON all land here rather than
                # in the catch above. It is the same transport failure, just
                # later, and the status says only that the server had begun to
                # answer — not what became of the request. So it is recorded the
                # same way, with no status: a poll reads it as a blip worth
                # repeating and a write as one that may or may not have landed,
                # which is what they did before this answer could be read.
                self._record_unread_answer(e, reading_the_answer=True)
                raise

    def create_api_method(self, method_func: Callable) -> Callable:
        """Factory method that wraps API methods with error handling and live context"""

        @wraps(method_func)
        async def wrapper(*args, live=None, **kwargs):
            self.error = None
            self.error_status = None
            self.error_retry_after = None
            try:
                result = await method_func(*args, **kwargs)
                self.bubble_next = False
                return result, self.error
            except Exception as e:
                if live and not self.bubble_next:
                    live.stop()

                if not self.error:
                    # Nothing recorded an error, so the API did not refuse the
                    # request: this is a failure on our side of it — a TypeError
                    # from a keyword that does not match the method's signature,
                    # an AttributeError, a ValueError for a config value that is
                    # not set, a KeyError on a response that is not shaped the
                    # way the method reads it. There is no error to hand back,
                    # and every caller reads a falsy error as success, so
                    # returning `(None, None)` here reports a call that never
                    # happened as a completed operation (cli#223 review: a build
                    # promotion printed "Promoted build ..." having sent no HTTP
                    # request at all). Let the exception out instead. In the CLI
                    # it ends the process non-zero, like any other mistake in a
                    # command body, and an SDK caller sees the original
                    # exception rather than a silent None. A request that got no
                    # response does not land here: `_base_request` records that
                    # as an error before re-raising, so it still renders.
                    self.bubble_next = False
                    raise

                if not self.is_cli and self.error and not self.bubble_next:
                    if self.no_response():
                        # Nothing the API said, so there is nothing to put in an
                        # AgentStartError: the exception itself is what went
                        # wrong, and it is what a caller of a client library
                        # expects to catch. The recorded error stays readable on
                        # the client for anyone who wants the rendered text.
                        raise e
                    if isinstance(self.error, dict) and self.error.get("status", "429"):
                        raise AgentStartError(self.error)
                    else:
                        raise e

                if self.error and not self.bubble_next:
                    logger.debug(describe_exception(e))
                    self.print_error()

                self.bubble_next = False
                return None, self.error

        return wrapper

    def print_error(self):
        from pipecatcloud._utils.console_utils import console

        if not self.error:
            return
        # In json mode the machine-readable error goes to stdout; the human
        # rendering below lands on stderr (the console is redirected there).
        if self.is_cli and getattr(console, "json_output", False):
            console.output_json({"error": self.error})
        if isinstance(self.error, dict) and self.error.get("code", "400") == "401":
            console.unauthorized()
        else:
            console.api_error(self.error)

    def bubble_error(self):
        self.bubble_next = True
        return self

    def no_response(self) -> bool:
        """Whether the last call failed without an answer this client could
        read: the API could not be reached, the connection dropped, the request
        timed out, or the answer stopped part way through its body. A read that
        got no answer simply has none; a write that got no answer may or may not
        have landed, which is what the user has to be told. A failure the server
        did answer with carries its status, so this is False for it."""
        return self.error is not None and self.error_status is None

    # Auth

    async def _whoami(self) -> dict:
        url = self.construct_api_url("whoami_path")
        return await self._base_request("GET", url) or {}

    @property
    def whoami(self):
        return self.create_api_method(self._whoami)

    # Organizations

    async def _organizations_current(self, org: str | None = None) -> dict | None:
        url = self.construct_api_url("organization_path")

        results = await self._base_request("GET", url)

        if not results or not len(results["organizations"]):
            return None

        # If active_org is specified, try to find it in the list
        if org:
            for o in results["organizations"]:
                if o["name"] == org:
                    return {"name": o["name"], "verbose_name": o["verboseName"]}

        # Default to first organization if active_org not found or not specified
        return {
            "name": results["organizations"][0]["name"],
            "verbose_name": results["organizations"][0]["verboseName"],
        }

    @property
    def organizations_current(self):
        return self.create_api_method(self._organizations_current)

    async def _organizations(self) -> list:
        url = self.construct_api_url("organization_path")
        results = await self._base_request("GET", url)

        # An account that belongs to no organization is not a failed request:
        # the empty list is the answer, and `organization list` says so in
        # words. This used to be a bare `raise` — a RuntimeError, since no
        # exception was being handled — which the wrapper swallowed into
        # (None, None); now that it no longer swallows, say it plainly.
        if not results:
            return []
        return results.get("organizations") or []

    @property
    def organizations(self):
        return self.create_api_method(self._organizations)

    # Daily API Key

    async def _organizations_daily_key(self, org) -> dict:
        url = self.construct_api_url("daily_key_path").format(org=org)
        return await self._base_request("GET", url) or {}

    @property
    def organizations_daily_key(self):
        return self.create_api_method(self._organizations_daily_key)

    # API Keys

    async def _api_keys(self, org) -> dict:
        url = self.construct_api_url("api_keys_path").format(org=org)
        return await self._base_request("GET", url) or {}

    @property
    def api_keys(self):
        """Get API keys for an organization.
        Args:
            org: Organization ID
        """
        return self.create_api_method(self._api_keys)

    async def _api_key_create(self, api_key_name: str, org: str) -> dict:
        url = self.construct_api_url("api_keys_path").format(org=org)
        return (
            await self._base_request("POST", url, json={"name": api_key_name, "type": "public"})
            or {}
        )

    @property
    def api_key_create(self):
        """Create API keys for an organization.
        Args:
            api_key_name: Human readable name for API key
            org: Organization ID
        """
        return self.create_api_method(self._api_key_create)

    async def _api_key_revoke(self, api_key_id: str, org: str) -> dict:
        url = f"{self.construct_api_url('api_keys_path').format(org=org)}/{api_key_id}"
        return await self._base_request("DELETE", url) or {}

    @property
    def api_key_revoke(self):
        """Revoke an API key for an organization.

        The server soft-deletes the key by marking it revoked; the record is
        preserved so audit fields (e.g. lastUsedAt) remain available.

        Args:
            api_key_id: ID of the API key to revoke
            org: Organization ID
        """
        return self.create_api_method(self._api_key_revoke)

    @property
    def api_key_delete(self):
        """Deprecated: use :attr:`api_key_revoke` instead.

        Kept as an alias so older CLI/library callers keep working.
        """
        return self.create_api_method(self._api_key_revoke)

    # Secret

    async def _secrets_list(
        self, org: str, secret_set: str | None = None, region: str | None = None
    ) -> dict | None:
        if secret_set:
            url = f"{self.construct_api_url('secrets_path').format(org=org)}/{secret_set}"
        else:
            url = f"{self.construct_api_url('secrets_path').format(org=org)}"

        # Build query params if region filter is specified
        params = {"region": region} if region else None

        result = await self._base_request("GET", url, params=params, not_found_is_empty=True) or {}

        if "sets" in result:
            return result["sets"]

        if "secrets" in result:
            return result["secrets"]

        return None

    @property
    def secrets_list(self):
        """List secrets
        Args:
            org: Organization ID,
            secret_set: (optional) name of secret set to lookup
            region: (optional) filter by region

        """
        return self.create_api_method(self._secrets_list)

    async def _secrets_get(self, org: str, secret_set: str) -> dict | None:
        """Fetch a single secret set, including its readiness status.

        Returns the full response object: {secrets, region, status, errorMessage?}.
        Use this when you need readiness; use _secrets_list when you only need
        the existence check or the flat keys array.

        Also records whether the server takes several fields in one write, which
        it says whether or not the set exists.
        """
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/{secret_set}"
        self.capabilities_header = None
        self.secrets_bulk_write = False
        result = await self._base_request("GET", url, not_found_is_empty=True)
        self.secrets_bulk_write = self.last_response_offers_bulk_write()
        return result or None

    def last_response_offers_bulk_write(self) -> bool:
        """Whether the last response came from a server that takes several fields in
        one write. In a rolling update of the API, responses can differ on this."""
        return SECRETS_BULK_WRITE_CAPABILITY in [
            c.strip() for c in (self.capabilities_header or "").split(",")
        ]

    @property
    def secrets_get(self):
        """Get a single secret set with readiness status.

        Args:
            org: Organization ID
            secret_set: Name of the secret set
        """
        return self.create_api_method(self._secrets_get)

    async def _secrets_upsert(
        self, data: dict, set_name: str, org: str, region: str | None = None
    ) -> dict:
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/{set_name}"

        # Add region to data payload only if explicitly provided
        # If not provided, API will use org's default region
        if region:
            data = {**data, "region": region}

        # A PUT replaces the value of each key it names, so repeating it is safe.
        result, _ = await self._secrets_write_with_retry("PUT", url, json=data)
        return result or {}

    async def _secrets_write_with_retry(
        self, method: str, url: str, **kwargs
    ) -> tuple[dict | None, int]:
        """A secrets write, and how many times it was repeated. A 503 with
        `Retry-After` is the server saying the write could not be done yet; wait and
        repeat, a bounded number of times. Only for a write that is safe to repeat."""
        attempt = 0
        while True:
            try:
                return await self._base_request(method, url, **kwargs), attempt
            except aiohttp.ClientResponseError as e:
                wait = _parse_retry_after(self.error_retry_after)
                too_long = wait is not None and wait > SECRETS_WRITE_MAX_RETRY_AFTER
                if (
                    e.status != 503
                    or wait is None
                    or too_long
                    or attempt >= SECRETS_WRITE_MAX_RETRIES
                ):
                    if e.status == 503 and isinstance(self.error, dict):
                        notes = []
                        if attempt:
                            # Say the retries happened, so the user does not
                            # think the first refusal was final.
                            noun = "retry" if attempt == 1 else "retries"
                            notes.append(f"Still unavailable after {attempt} {noun}.")
                        if too_long:
                            # Waiting less than the server named would go
                            # against the header, so say why nothing more was retried.
                            notes.append(
                                f"The region asked to wait {wait} seconds, longer than the "
                                f"CLI will wait ({SECRETS_WRITE_MAX_RETRY_AFTER}). "
                                "Run the command again after that."
                            )
                        if notes:
                            # Same fallback the console uses to show an error.
                            text = (
                                self.error.get("error")
                                or self.error.get("message")
                                or "Service unavailable."
                            )
                            self.error = {**self.error, "error": f"{text} {' '.join(notes)}"}
                    raise
                attempt += 1
                self._notify_retry(wait, attempt)
                await asyncio.sleep(wait)
                self.error = None
                self.error_status = None
                self.error_retry_after = None

    def _notify_retry(self, wait: int, attempt: int) -> None:
        """Tell the CLI user the request is waiting to be repeated, so a pause is not silent."""
        if not self.is_cli:
            return
        from pipecatcloud._utils.console_utils import console

        console.print(
            f"[yellow]The write could not be completed yet. Retrying in {wait}s "
            f"(attempt {attempt} of {SECRETS_WRITE_MAX_RETRIES})...[/yellow]"
        )

    @property
    def secrets_upsert(self):
        """Create / modify secret set.
        Args:
            data: key and value of secret to add (or credentials for image pull secrets)
            set_name: name of set to create or update
            org: Organization ID
            region: region for secret set
        """
        return self.create_api_method(self._secrets_upsert)

    async def _secrets_upsert_fields(
        self, fields: dict[str, str], set_name: str, org: str, region: str | None = None
    ) -> dict:
        return await self._secrets_upsert(
            data={
                "secrets": [{"secretKey": key, "secretValue": val} for key, val in fields.items()]
            },
            set_name=set_name,
            org=org,
            region=region,
        )

    @property
    def secrets_upsert_fields(self):
        """Create / modify several fields of a secret set in one write, which the
        server applies whole or not at all. Only for a server that says it takes
        this (`secrets_bulk_write`, after `secrets_get`).

        Args:
            fields: the keys and values to write
            set_name: name of set to create or update
            org: Organization ID
            region: region for secret set
        """
        return self.create_api_method(self._secrets_upsert_fields)

    async def _secrets_keys(self, org: str, region: str) -> dict | None:
        url = f"{self.construct_api_url('regions_path').format(org=org)}/{region}/secret-keys"
        return await self._base_request("GET", url, not_found_is_empty=True)

    @property
    def secrets_keys(self):
        """The organization's secret keys in a region: each key's fingerprint, state
        and times. None when the server does not offer this.

        Args:
            org: Organization ID
            region: region to read
        """
        return self.create_api_method(self._secrets_keys)

    async def _secrets_reference(self, name: str, region: str, org: str) -> dict:
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/references"
        return await self._base_request("POST", url, json={"name": name, "region": region}) or {}

    @property
    def secrets_reference(self):
        """Reference an existing Kubernetes Secret in a self-hosted region.

        The secret stays in the customer's cluster; Pipecat Cloud stores only its
        name, region, and readiness (never values or keys). Enterprise /
        self-hosted regions only — the server rejects cloud regions.

        Args:
            name: name of the Kubernetes Secret (becomes the secret set name)
            region: self-hosted region containing the secret
            org: Organization ID
        """
        return self.create_api_method(self._secrets_reference)

    async def _secrets_delete(self, set_name: str, secret_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/{set_name}/{secret_name}"
        # Deleting a key twice leaves the same set, so it is repeated like a write.
        result, retried = await self._secrets_write_with_retry(
            "DELETE", url, not_found_is_empty=True
        )
        if result is None and retried:
            # Not found once repeated: the attempt the server could not confirm
            # removed it, or it was never there. Either way the key is gone.
            return {"secretKey": secret_name}
        return result

    @property
    def secrets_delete(self):
        """Delete secret from set
        Args:
            set_name: name of set to target
            secret_name: name of secret to delete
            org: Organization ID
        """
        return self.create_api_method(self._secrets_delete)

    async def _secrets_delete_set(self, set_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/{set_name}"
        return await self._base_request("DELETE", url, not_found_is_empty=True)

    @property
    def secrets_delete_set(self):
        """Delete secret from set
        Args:
            set_name: name of set to target
            org: Organization ID
        """
        return self.create_api_method(self._secrets_delete_set)

    # Agent profiles

    async def _agent_profiles_list(self, org: str) -> dict:
        url = self.construct_api_url("agent_profiles_path").format(org=org)
        return await self._base_request("GET", url) or {}

    @property
    def agent_profiles_list(self):
        """List agent profiles visible to the organization.

        Returns the platform catalog plus any profiles the organization has
        defined (enterprise / self-hosted regions). Custom profiles are marked
        with ``custom: true``.

        Args:
            org: Organization ID
        """
        return self.create_api_method(self._agent_profiles_list)

    async def _agent_profiles_create(
        self, api_name: str, display_name: str, cpu: str, memory: str, org: str
    ) -> dict:
        url = self.construct_api_url("agent_profiles_path").format(org=org)
        return (
            await self._base_request(
                "POST",
                url,
                json={
                    "apiName": api_name,
                    "displayName": display_name,
                    "resources": {"cpu": cpu, "memory": memory},
                },
            )
            or {}
        )

    @property
    def agent_profiles_create(self):
        """Create an organization-defined agent profile (enterprise regions only).

        Args:
            api_name: Profile name used at deploy time (lowercase, hyphens)
            display_name: Human-readable name
            cpu: k8s cpu quantity, e.g. "2" or "500m"
            memory: k8s memory quantity, e.g. "4Gi"
            org: Organization ID
        """
        return self.create_api_method(self._agent_profiles_create)

    async def _agent_profiles_update(
        self,
        api_name: str,
        org: str,
        display_name: str | None = None,
        cpu: str | None = None,
        memory: str | None = None,
        enabled: bool | None = None,
    ) -> dict:
        url = f"{self.construct_api_url('agent_profiles_path').format(org=org)}/{api_name}"
        payload: dict = {}
        if display_name is not None:
            payload["displayName"] = display_name
        if cpu is not None and memory is not None:
            payload["resources"] = {"cpu": cpu, "memory": memory}
        if enabled is not None:
            payload["enabled"] = enabled
        return await self._base_request("PATCH", url, json=payload) or {}

    @property
    def agent_profiles_update(self):
        """Update or disable an organization-defined agent profile.

        Edits apply to future deploys only; running agents keep the resources
        their deployment snapshotted. Disabling (enabled=False) is how a
        profile is removed: history stays intact and the name can be recreated.

        Args:
            api_name: Profile name to update
            org: Organization ID
            display_name: New display name (optional)
            cpu / memory: New sizing, both required together (optional)
            enabled: Set False to disable (optional)
        """
        return self.create_api_method(self._agent_profiles_update)

    # Deploy

    async def _deploy(
        self, deploy_config: DeployConfigParams, org: str, update: bool = False
    ) -> dict | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}"

        # Create base payload and filter out None values
        payload = {
            "serviceName": deploy_config.agent_name,
            "imagePullSecretSet": deploy_config.image_credentials,
            "secretSet": deploy_config.secret_set,
            "region": deploy_config.region,
            "autoScaling": {
                "minAgents": deploy_config.scaling.min_agents,
                "maxAgents": deploy_config.scaling.max_agents,
            },
            "agentProfile": deploy_config.agent_profile,
            "resources": (
                deploy_config.resources.to_dict() if deploy_config.resources.is_set() else None
            ),
            "krispViva": {
                "audioFilter": deploy_config.krisp_viva.audio_filter,
            },
            "forceRedeploy": deploy_config.force_redeploy or None,
            "websocketAuth": deploy_config.websocket_auth,
            "maxSessionDuration": deploy_config.max_session_duration,
            # The field name is `architecture` — the API silently drops
            # unknown keys, so a wrong name here schedules on the region
            # default and exec-format-errors (the PCC-1105 incident).
            "architecture": deploy_config.architecture,
        }

        # Exactly one image source. A git-sourced agent has none of the three:
        # its image is produced by the build that its first deploy triggers.
        if deploy_config.git.is_set():
            payload["git"] = deploy_config.git.to_payload()
            # Nothing is pulled for a git build, so never carry over a pull
            # secret the caller set before switching to the GitHub source.
            payload["imagePullSecretSet"] = None
        elif deploy_config.build_id:
            payload["buildId"] = deploy_config.build_id
        else:
            payload["image"] = deploy_config.image

        # Remove None values recursively
        def remove_none_values(d):
            return {
                k: remove_none_values(v) if isinstance(v, dict) else v
                for k, v in d.items()
                if v is not None
            }

        cleaned_payload = remove_none_values(payload)

        if update:
            return await self._base_request("PUT", url, json=cleaned_payload)
        else:
            return await self._base_request("POST", url, json=cleaned_payload)

    @property
    def deploy(self):
        """Lookup agent by name
        Args:
            deploy_config: Deploy config object to send as JSON to deployment
            update: Updated existing deployment
            org: Organization ID
        """
        return self.create_api_method(self._deploy)

    # Agents

    async def _agent(self, agent_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}/{agent_name}"
        result = await self._base_request("GET", url, not_found_is_empty=True)

        if result and "body" in result:
            return result["body"]

        return None

    @property
    def agent(self):
        """Lookup agent by name
        Args:
            agent_name: name of agent to lookup
            org: Organization ID
        """
        return self.create_api_method(self._agent)

    async def _agents(
        self, org: str, region: str | None = None, include: list[str] | None = None
    ) -> list[dict] | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}"

        # Build query params if region filter is specified
        params: dict[str, str] = {}
        if region:
            params["region"] = region
        # Opt-in enrichments (PCC-933: `git` adds the binding + source). The
        # API silently drops values it doesn't know, so an older server just
        # omits the fields rather than failing the whole listing.
        if include:
            params["include"] = ",".join(include)

        result = await self._base_request("GET", url, params=params or None) or {}

        if "services" in result:
            return result["services"]

        return None

    @property
    def agents(self):
        """List agents/services
        Args:
            org: Organization ID
            region: (optional) filter by region
            include: (optional) enrichments to request, e.g. ["git"]
        """
        return self.create_api_method(self._agents)

    async def _start_agent(
        self,
        agent_name: str,
        api_key: str,
        use_daily: bool,
        data: str | None = None,
        daily_properties: str | None = None,
    ) -> dict | None:
        url = f"{self.construct_api_url('start_path').format(service=agent_name)}"

        payload: dict = {"createDailyRoom": use_daily}

        # Add data to payload if provided
        if data is not None:
            payload["body"] = json.loads(data)

        # Add Daily room properties only if use_daily is True
        if use_daily and daily_properties:
            payload["dailyRoomProperties"] = json.loads(daily_properties)

        return await self._base_request(
            "POST", url, override_token=api_key, json=payload, not_found_is_empty=True
        )

    @property
    def start_agent(self):
        return self.create_api_method(self._start_agent)

    async def _agent_delete(self, agent_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}/{agent_name}"
        return await self._base_request("DELETE", url, not_found_is_empty=True)

    @property
    def agent_delete(self):
        return self.create_api_method(self._agent_delete)

    async def _agent_logs(
        self,
        agent_name: str,
        org: str,
        limit: int = 100,
        deployment_id: str | None = None,
        session_id: str | None = None,
    ) -> dict | None:
        url = f"{self.construct_api_url('services_logs_path').format(org=org, service=agent_name)}"
        params: dict[str, int | str] = {"limit": limit}
        if deployment_id:
            params["deploymentId"] = deployment_id
        if session_id:
            params["sessionId"] = session_id
        return await self._base_request("GET", url, params=params)

    @property
    def agent_logs(self):
        return self.create_api_method(self._agent_logs)

    async def _agent_sessions(
        self, agent_name: str, org: str, end_state: str | None = None
    ) -> dict | None:
        url = f"{self.construct_api_url('services_sessions_path').format(org=org, service=agent_name)}"
        params = {"endState": end_state} if end_state else None
        return await self._base_request("GET", url, params=params) or {}

    @property
    def agent_sessions(self):
        return self.create_api_method(self._agent_sessions)

    async def _agent_session(self, agent_name: str, session_id: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('services_sessions_path').format(org=org, service=agent_name)}/{session_id}"
        return await self._base_request("GET", url, not_found_is_empty=True)

    @property
    def agent_session(self):
        """Get details for a specific session including resource metrics.
        Args:
            agent_name: Name of the agent
            session_id: ID of the session
            org: Organization ID
        """
        return self.create_api_method(self._agent_session)

    async def _agent_session_terminate(
        self, agent_name: str, session_id: str, org: str
    ) -> dict | None:
        url = f"{self.construct_api_url('services_sessions_path').format(org=org, service=agent_name)}/{session_id}"
        return await self._base_request("DELETE", url, not_found_is_empty=True)

    @property
    def agent_session_terminate(self):
        """Terminate an active session for an agent.
        Args:
            agent_name: Name of the agent
            session_id: ID of the session to terminate
            org: Organization ID
        """
        return self.create_api_method(self._agent_session_terminate)

    # Regions

    async def _regions(self, org: str) -> list[dict] | None:
        url = self.construct_api_url("regions_path").format(org=org)
        result = await self._base_request("GET", url, not_found_is_empty=True)

        if result and "regions" in result:
            # Return full region objects with code and display_name
            return result["regions"]

        return None

    @property
    def regions(self):
        """List available regions
        Args:
            org: Organization ID
        Returns:
            List of region objects with 'code' and 'display_name' fields
        """
        return self.create_api_method(self._regions)

    # Self-hosted region lifecycle (PCC-1103). Registration is an
    # omit-preserves upsert: fields absent from the payload keep their stored
    # values on the server.

    async def _region_register(self, org: str, payload: dict) -> dict:
        url = self.construct_api_url("regions_path").format(org=org)
        return await self._base_request("POST", url, json=payload) or {}

    @property
    def region_register(self):
        """Register or update a self-hosted region (omit-preserves upsert).
        Args:
            org: Organization ID
            payload: owner-declared fields (regionKey required; workloadsNamespace,
                supportedArchitectures, defaultArchitecture, wsPublicEndpoint,
                displayName all optional — omitted fields are preserved)
        Returns:
            The full region record.
        """
        return self.create_api_method(self._region_register)

    async def _region_get(self, org: str, region_key: str) -> dict | None:
        url = f"{self.construct_api_url('regions_path').format(org=org)}/{region_key}"
        return await self._base_request("GET", url, not_found_is_empty=True)

    @property
    def region_get(self):
        """Fetch one self-hosted region's full record (enrollment status,
        intermediate expiry, ws endpoint, architectures, workloads namespace).
        Args:
            org: Organization ID
            region_key: the region's key
        """
        return self.create_api_method(self._region_get)

    async def _region_delete(self, org: str, region_key: str) -> dict | None:
        url = f"{self.construct_api_url('regions_path').format(org=org)}/{region_key}"
        return await self._base_request("DELETE", url)

    @property
    def region_delete(self):
        """Revoke a self-hosted region. The server refuses (409) while the
        region has live sessions or deployed services, and that guard has no
        bypass (PCC-1141): the region's agents must be deleted first.
        Args:
            org: Organization ID
            region_key: the region's key
        """
        return self.create_api_method(self._region_delete)

    async def _region_enroll_token(self, org: str, region_key: str) -> dict:
        url = f"{self.construct_api_url('regions_path').format(org=org)}/{region_key}/enrollment-tokens"
        return await self._base_request("POST", url, json={}) or {}

    @property
    def region_enroll_token(self):
        """Mint a one-time enrollment token for a self-hosted region. The
        token is returned exactly once.
        Args:
            org: Organization ID
            region_key: the region's key
        """
        return self.create_api_method(self._region_enroll_token)

    # Registry pull keys (PCC-1082/PCC-1103): org-scoped, pull-only
    # credentials for fetching the region package chart from the registry.

    async def _registry_keys(self, org: str) -> dict:
        url = self.construct_api_url("registry_keys_path").format(org=org)
        return await self._base_request("GET", url) or {}

    @property
    def registry_keys(self):
        """List the organization's registry keys (names and lifecycle
        metadata only — key material is shown once, at mint).
        Args:
            org: Organization ID
        """
        return self.create_api_method(self._registry_keys)

    async def _registry_key_mint(self, org: str, name: str) -> dict:
        url = self.construct_api_url("registry_keys_path").format(org=org)
        return await self._base_request("POST", url, json={"name": name}) or {}

    @property
    def registry_key_mint(self):
        """Mint a registry pull key. The key material in the response is
        shown exactly once and cannot be retrieved again.
        Args:
            org: Organization ID
            name: human label for the key (the API requires one)
        """
        return self.create_api_method(self._registry_key_mint)

    async def _registry_key_revoke(self, org: str, key_id: str) -> dict | None:
        url = f"{self.construct_api_url('registry_keys_path').format(org=org)}/{key_id}"
        return await self._base_request("DELETE", url)

    @property
    def registry_key_revoke(self):
        """Revoke a registry key by id.
        Args:
            org: Organization ID
            key_id: the key's id (from registry_keys)
        """
        return self.create_api_method(self._registry_key_revoke)

    # Organization Properties

    async def _properties(self, org: str) -> dict | None:
        url = self.construct_api_url("properties_path").format(org=org)
        result = await self._base_request("GET", url, not_found_is_empty=True)
        if result and "properties" in result:
            return result["properties"]
        return None

    @property
    def properties(self):
        """Get current organization properties
        Args:
            org: Organization ID
        Returns:
            Dict of property names to current values
        """
        return self.create_api_method(self._properties)

    async def _properties_schema(self, org: str) -> dict | None:
        url = f"{self.construct_api_url('properties_path').format(org=org)}/schema"
        result = await self._base_request("GET", url, not_found_is_empty=True)
        if result and "properties" in result:
            return result["properties"]
        return None

    @property
    def properties_schema(self):
        """Get organization properties schema with metadata
        Args:
            org: Organization ID
        Returns:
            Dict of property names to schema info (type, description, currentValue, default, availableValues)
        """
        return self.create_api_method(self._properties_schema)

    async def _properties_update(self, org: str, properties: dict) -> dict | None:
        url = self.construct_api_url("properties_path").format(org=org)
        result = await self._base_request("PATCH", url, json=properties)
        if result and "properties" in result:
            return result["properties"]
        return None

    @property
    def properties_update(self):
        """Update organization properties
        Args:
            org: Organization ID
            properties: Dict of property names to new values
        Returns:
            Updated properties dict
        """
        return self.create_api_method(self._properties_update)

    # Spend Limit

    async def _spend_limit_get(self, org: str) -> dict | None:
        url = self.construct_api_url("spend_limit_path").format(org=org)
        return await self._base_request("GET", url, not_found_is_empty=True)

    @property
    def spend_limit_get(self):
        """Get the current spend limit and usage for an organization.

        Args:
            org: Organization ID
        Returns:
            Dict with limitCents, currentSpendCents, periodStart, periodEnd,
            blocked, blockedAt. Returns None if the endpoint reports 404.
        """
        return self.create_api_method(self._spend_limit_get)

    async def _spend_limit_update(self, org: str, limit_cents: int | None) -> dict | None:
        url = self.construct_api_url("spend_limit_path").format(org=org)
        return await self._base_request("PUT", url, json={"limitCents": limit_cents})

    @property
    def spend_limit_update(self):
        """Set or clear the organization spend limit.

        Args:
            org: Organization ID
            limit_cents: New limit in cents, or None to clear the limit.
        Returns:
            The updated spend-limit response (same shape as spend_limit_get).
        """
        return self.create_api_method(self._spend_limit_update)

    # Eval runs

    async def _eval_request(self, method: str, url: str, **kwargs) -> dict | None:
        """A request to the eval-runs routes.

        While evals are off for an organization every route answers 503 with the
        code `EVALS-NOT-AVAILABLE`, and the suite and bundle routes answer with
        their own codes while those are off. The server's text for each is
        terse, so it is replaced with a sentence that says what it means.
        """
        try:
            return await self._base_request(method, url, **kwargs)
        except aiohttp.ClientResponseError:
            if isinstance(self.error, dict):
                message = EVAL_NOT_AVAILABLE_MESSAGES.get(str(self.error.get("code")))
                if message:
                    self.error = {**self.error, "error": message}
            raise

    def _eval_runs_url(self, org: str, agent: str, run_id: str | None = None) -> str:
        url = self.construct_api_url("eval_runs_path").format(
            org=quote(org, safe=""), service=quote(agent, safe="")
        )
        if run_id is not None:
            url += f"/{quote(run_id, safe='')}"
        return url

    async def _eval_run_create(self, org: str, agent: str, body: dict) -> dict:
        url = self._eval_runs_url(org, agent)
        return await self._eval_request("POST", url, json=body) or {}

    @property
    def eval_run_create(self):
        """Start an eval run on an agent.

        Args:
            org: Organization ID
            agent: Agent name
            body: The request: kind, files, scenarioPath, scenarioName, and
                params, sessionId or deadlineSeconds as they apply.
        Returns:
            The run, queued.
        """
        return self.create_api_method(self._eval_run_create)

    async def _eval_run_get(self, org: str, agent: str, run_id: str) -> dict:
        url = self._eval_runs_url(org, agent, run_id)
        return await self._eval_request("GET", url) or {}

    @property
    def eval_run_get(self):
        """Get an eval run, with its result once it has ended."""
        return self.create_api_method(self._eval_run_get)

    async def _eval_runs_list(
        self,
        org: str,
        agent: str,
        limit: int | None = None,
        offset: int | None = None,
        suite_run_id: str | None = None,
    ) -> dict:
        url = self._eval_runs_url(org, agent)
        params = {
            k: v
            for k, v in (("limit", limit), ("offset", offset), ("suiteRunId", suite_run_id))
            if v is not None
        }
        return await self._eval_request("GET", url, params=params or None) or {}

    @property
    def eval_runs_list(self):
        """List an agent's eval runs, newest first, without their results.

        Args:
            suite_run_id: Only the runs of this suite run's members.
        Returns:
            Dict with `total_count` and `runs`.
        """
        return self.create_api_method(self._eval_runs_list)

    async def _eval_run_cancel(self, org: str, agent: str, run_id: str) -> dict:
        url = f"{self._eval_runs_url(org, agent, run_id)}/cancel"
        return await self._eval_request("POST", url) or {}

    @property
    def eval_run_cancel(self):
        """Ask for an eval run to be cancelled.

        A queued run ends at once as `cancelled`. A running run is asked to stop
        and ends as soon as it can, so the returned run may still be `running`.
        """
        return self.create_api_method(self._eval_run_cancel)

    async def _eval_capabilities(self, org: str) -> dict:
        url = self.construct_api_url("eval_capabilities_path").format(org=quote(org, safe=""))
        return await self._eval_request("GET", url) or {}

    @property
    def eval_capabilities(self):
        """What evals the organization can use.

        Returns:
            Dict with `enabled`, `creation`, and on an API that has them,
            `bundles` and `suites`.
        """
        return self.create_api_method(self._eval_capabilities)

    def _eval_org_runs_url(self, org: str, run_id: str | None = None) -> str:
        url = self.construct_api_url("eval_org_runs_path").format(org=quote(org, safe=""))
        if run_id is not None:
            url += f"/{quote(run_id, safe='')}"
        return url

    async def _eval_suite_member_runs(
        self, org: str, suite_run_id: str, limit: int | None = None, offset: int | None = None
    ) -> dict:
        params = {
            k: v
            for k, v in (("suiteRunId", suite_run_id), ("limit", limit), ("offset", offset))
            if v is not None
        }
        return await self._eval_request("GET", self._eval_org_runs_url(org), params=params) or {}

    @property
    def eval_suite_member_runs(self):
        """A suite run's member runs, newest first, without their results.

        Read through the organization, so they stay readable after the agent
        is deleted or its name is reused.

        Returns:
            Dict with `total_count` and `runs`.
        """
        return self.create_api_method(self._eval_suite_member_runs)

    async def _eval_org_run_get(self, org: str, run_id: str) -> dict:
        return await self._eval_request("GET", self._eval_org_runs_url(org, run_id)) or {}

    @property
    def eval_org_run_get(self):
        """Get an eval run by its ID alone, with its result once it has ended."""
        return self.create_api_method(self._eval_org_run_get)

    # Eval suite runs

    def _eval_suite_runs_url(self, org: str, suite_run_id: str | None = None) -> str:
        url = self.construct_api_url("eval_suite_runs_path").format(org=quote(org, safe=""))
        if suite_run_id is not None:
            url += f"/{quote(suite_run_id, safe='')}"
        return url

    async def _eval_suite_run_create(self, org: str, agent: str, body: dict) -> dict:
        url = self.construct_api_url("eval_suite_runs_create_path").format(
            org=quote(org, safe=""), service=quote(agent, safe="")
        )
        return await self._eval_request("POST", url, json=body) or {}

    @property
    def eval_suite_run_create(self):
        """Start a suite run on an agent.

        Args:
            org: Organization ID
            agent: Agent name
            body: The request: `requestId`; the files (`bundleRevisionId`, or
                `files`); what to run (`suitePath`, or `scenarioPath` with
                optional `scenarioNames`); and optional `repeat`, `concurrency`
                and `deadlineSeconds`. The same `requestId` sent again returns
                the suite run it created, so a request that got no answer can
                be sent again as it was.
        Returns:
            The suite run, accepted.
        """
        return self.create_api_method(self._eval_suite_run_create)

    async def _eval_suite_run_get(self, org: str, suite_run_id: str) -> dict:
        return await self._eval_request("GET", self._eval_suite_runs_url(org, suite_run_id)) or {}

    @property
    def eval_suite_run_get(self):
        """Get a suite run: its status, counts, verdict and pass rates."""
        return self.create_api_method(self._eval_suite_run_get)

    async def _eval_suite_runs_list(
        self,
        org: str,
        agent: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> dict:
        params = {
            k: v
            for k, v in (("serviceName", agent), ("limit", limit), ("offset", offset))
            if v is not None
        }
        url = self._eval_suite_runs_url(org)
        return await self._eval_request("GET", url, params=params or None) or {}

    @property
    def eval_suite_runs_list(self):
        """List suite runs, newest first: the organization's, or one agent's.

        Returns:
            Dict with `total`, `limit`, `offset` and `suiteRuns`.
        """
        return self.create_api_method(self._eval_suite_runs_list)

    async def _eval_suite_run_items(
        self, org: str, suite_run_id: str, limit: int | None = None, offset: int | None = None
    ) -> dict:
        url = f"{self._eval_suite_runs_url(org, suite_run_id)}/items"
        params = {k: v for k, v in (("limit", limit), ("offset", offset)) if v is not None}
        return await self._eval_request("GET", url, params=params or None) or {}

    @property
    def eval_suite_run_items(self):
        """A suite run's planned members, in order, each with its eval run once admitted.

        Returns:
            Dict with `total`, `limit`, `offset` and `items`.
        """
        return self.create_api_method(self._eval_suite_run_items)

    async def _eval_suite_run_cancel(self, org: str, suite_run_id: str) -> dict:
        url = f"{self._eval_suite_runs_url(org, suite_run_id)}/cancel"
        return await self._eval_request("POST", url) or {}

    @property
    def eval_suite_run_cancel(self):
        """Ask for a suite run to be cancelled.

        Members not yet admitted never run, and running ones are asked to
        cancel. Asking again is answered the same way. A suite run that already
        ended some other way is refused with a 409.
        """
        return self.create_api_method(self._eval_suite_run_cancel)

    # Eval bundles

    def _eval_bundles_url(self, org: str, bundle_id: str | None = None) -> str:
        url = self.construct_api_url("eval_bundles_path").format(org=quote(org, safe=""))
        if bundle_id is not None:
            url += f"/{quote(bundle_id, safe='')}"
        return url

    async def _eval_bundles_list(
        self, org: str, limit: int | None = None, offset: int | None = None
    ) -> dict:
        params = {k: v for k, v in (("limit", limit), ("offset", offset)) if v is not None}
        return (
            await self._eval_request("GET", self._eval_bundles_url(org), params=params or None)
            or {}
        )

    @property
    def eval_bundles_list(self):
        """List the organization's bundles by name, each with its latest revision.

        Returns:
            Dict with `total_count` and `bundles`.
        """
        return self.create_api_method(self._eval_bundles_list)

    async def _eval_bundle_create(self, org: str, body: dict) -> dict:
        return await self._eval_request("POST", self._eval_bundles_url(org), json=body) or {}

    @property
    def eval_bundle_create(self):
        """Create a bundle, its files becoming revision 1.

        Args:
            body: `requestId`, `name` and `files`.
        Returns:
            Dict with `bundle` and `revision`.
        """
        return self.create_api_method(self._eval_bundle_create)

    async def _eval_bundle_revision_append(self, org: str, bundle_id: str, body: dict) -> dict:
        url = f"{self._eval_bundles_url(org, bundle_id)}/revisions"
        return await self._eval_request("POST", url, json=body) or {}

    @property
    def eval_bundle_revision_append(self):
        """Publish a bundle's next revision.

        Args:
            body: `requestId` and `files`.
        Returns:
            The revision.
        """
        return self.create_api_method(self._eval_bundle_revision_append)

    async def _eval_bundle_revisions_list(
        self, org: str, bundle_id: str, limit: int | None = None, offset: int | None = None
    ) -> dict:
        url = f"{self._eval_bundles_url(org, bundle_id)}/revisions"
        params = {k: v for k, v in (("limit", limit), ("offset", offset)) if v is not None}
        return await self._eval_request("GET", url, params=params or None) or {}

    @property
    def eval_bundle_revisions_list(self):
        """A bundle's revisions, newest first, without their files.

        Returns:
            Dict with `total_count` and `revisions`.
        """
        return self.create_api_method(self._eval_bundle_revisions_list)

    async def _eval_bundle_revision_get(self, org: str, revision_id: str) -> dict:
        url = self.construct_api_url("eval_bundle_revisions_path").format(org=quote(org, safe=""))
        return await self._eval_request("GET", f"{url}/{quote(revision_id, safe='')}") or {}

    @property
    def eval_bundle_revision_get(self):
        """A revision whole: its files, catalog and how it was checked."""
        return self.create_api_method(self._eval_bundle_revision_get)

    # Builds

    async def _build_upload_url(self, org: str, region: str | None = None) -> dict:
        """Get a presigned URL for uploading build context."""
        url = f"{self.construct_api_url('builds_path').format(org=org)}/upload-url"
        payload = {}
        if region:
            payload["region"] = region
        return await self._base_request("POST", url, json=payload) or {}

    @property
    def build_upload_url(self):
        """Get presigned upload URL for build context.
        Args:
            org: Organization ID
            region: Optional region for the build
        Returns:
            Dict with uploadId, uploadUrl, uploadFields, expiresAt
        """
        return self.create_api_method(self._build_upload_url)

    async def _build_create(
        self,
        org: str,
        upload_id: str,
        region: str | None = None,
        dockerfile_path: str = "Dockerfile",
    ) -> dict:
        """Create a new build from uploaded context."""
        url = self.construct_api_url("builds_path").format(org=org)
        payload = {
            "uploadId": upload_id,
            "dockerfilePath": dockerfile_path,
        }
        if region:
            payload["region"] = region
        return await self._base_request("POST", url, json=payload) or {}

    @property
    def build_create(self):
        """Create a build from uploaded context.
        Args:
            org: Organization ID
            upload_id: ID from upload URL response
            region: Optional region for the build
            dockerfile_path: Path to Dockerfile within context
        Returns:
            Dict with build, contextHash, cached
        """
        return self.create_api_method(self._build_create)

    async def _build_get(self, org: str, build_id: str) -> dict | None:
        """Get build status by ID."""
        url = f"{self.construct_api_url('builds_path').format(org=org)}/{build_id}"
        return await self._base_request("GET", url, not_found_is_empty=True)

    @property
    def build_get(self):
        """Get build by ID.
        Args:
            org: Organization ID
            build_id: Build ID to lookup
        Returns:
            Dict with build data or None if not found
        """
        return self.create_api_method(self._build_get)

    async def _build_list(
        self,
        org: str,
        context_hash: str | None = None,
        region: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> dict:
        """List builds with optional filters."""
        url = self.construct_api_url("builds_path").format(org=org)
        params = {"limit": limit}
        if context_hash:
            params["contextHash"] = context_hash
        if region:
            params["region"] = region
        if status:
            params["status"] = status
        return await self._base_request("GET", url, params=params) or {}

    @property
    def build_list(self):
        """List builds with filters.
        Args:
            org: Organization ID
            context_hash: Filter by context hash (for cache lookup)
            region: Filter by region
            status: Filter by status (pending, building, success, failed, timeout)
            limit: Max results to return (default 20)
        Returns:
            Dict with builds, total, limit, offset
        """
        return self.create_api_method(self._build_list)

    async def _build_logs(self, org: str, build_id: str, limit: int = 500) -> dict | None:
        """Get build logs."""
        url = f"{self.construct_api_url('builds_path').format(org=org)}/{build_id}/logs"
        params = {"limit": limit}
        return await self._base_request("GET", url, params=params, not_found_is_empty=True)

    @property
    def build_logs(self):
        """Get logs for a build.
        Args:
            org: Organization ID
            build_id: Build ID
            limit: Max log lines to return (default 500)
        Returns:
            Dict with logs array or None if not found
        """
        return self.create_api_method(self._build_logs)

    # GitHub (PCC-933)

    async def _github_install_url(self, org: str) -> dict | None:
        """Mint the GitHub App install URL for this org."""
        url = self.construct_api_url("github_install_url_path").format(org=org)
        return await self._base_request("GET", url)

    @property
    def github_install_url(self):
        """Get the GitHub App install URL.

        The URL carries a signed, single-use state that ties the resulting
        installation back to this org, so it must be fetched fresh for every
        connect attempt and never cached.

        Args:
            org: Organization ID
        Returns:
            Dict with `url`
        """
        return self.create_api_method(self._github_install_url)

    async def _github_link_url(self, org: str) -> dict | None:
        """Mint the GitHub URL that links an installation already on GitHub."""
        url = self.construct_api_url("github_link_url_path").format(org=org)
        return await self._base_request("GET", url)

    @property
    def github_link_url(self):
        """Get the GitHub URL for linking an existing installation.

        The URL asks the user to authorize the App on GitHub, with no install
        step. The dashboard then offers the installations GitHub confirms they
        installed or asked for, to link to this org. Like the install URL, it
        carries a single-use flow, so it must be fetched fresh every time.

        Args:
            org: Organization ID
        Returns:
            Dict with `url`
        """
        return self.create_api_method(self._github_link_url)

    async def _github_installation(self, org: str) -> dict | None:
        """The GitHub installation linked to this org, or None when unlinked."""
        url = self.construct_api_url("github_installation_path").format(org=org)
        # A 404 is the normal not-connected state, not an error worth a panel.
        result = await self._base_request("GET", url, not_found_is_empty=True)
        return (result or {}).get("installation")

    @property
    def github_installation(self):
        """Look up the org's GitHub installation.
        Args:
            org: Organization ID
        Returns:
            The installation dict, or None when nothing is linked
        """
        return self.create_api_method(self._github_installation)

    async def _github_disconnect(self, org: str) -> dict | None:
        url = self.construct_api_url("github_installation_path").format(org=org)
        return await self._base_request("DELETE", url)

    @property
    def github_disconnect(self):
        """Remove the org's installation and every repo binding under it.

        Does not uninstall the App on GitHub; that is done from GitHub.

        Args:
            org: Organization ID
        """
        return self.create_api_method(self._github_disconnect)

    async def _github_repositories(self, org: str) -> list[dict]:
        url = self.construct_api_url("github_repositories_path").format(org=org)
        result = await self._base_request("GET", url) or {}
        return result.get("repositories") or []

    @property
    def github_repositories(self):
        """Repositories the org's installation can access.
        Args:
            org: Organization ID
        Returns:
            List of dicts with id, fullName, private, defaultBranch
        """
        return self.create_api_method(self._github_repositories)

    async def _github_branches(
        self, org: str, repo_full_name: str, query: str | None = None
    ) -> list[str]:
        # owner and repo are separate path segments server-side. The caller
        # validates the shape first (see validate_repo_full_name): quoting
        # alone would not stop a ".." segment retargeting the path.
        owner, _, repo = repo_full_name.partition("/")
        base = self.construct_api_url("github_repositories_path").format(org=org)
        url = f"{base}/{quote(owner, safe='')}/{quote(repo, safe='')}/branches"
        params = {"query": query} if query else None
        result = await self._base_request("GET", url, params=params) or {}
        return result.get("branches") or []

    @property
    def github_branches(self):
        """List a repo's branches.

        Without a query this returns the bounded full list; with one, the API
        runs a server-side prefix search, which is how branches past the
        list's cap stay reachable.

        Args:
            org: Organization ID
            repo_full_name: "owner/repo"
            query: Optional prefix to search for
        Returns:
            List of branch names
        """
        return self.create_api_method(self._github_branches)

    async def _agent_git_connect(self, agent_name: str, org: str, payload: dict) -> dict | None:
        url = self.construct_api_url("service_git_path").format(org=org, service=agent_name)
        result = await self._base_request("POST", url, json=payload) or {}
        return result.get("gitConfig")

    @property
    def agent_git_connect(self):
        """Bind a repo/branch to an agent, or update the binding it has.

        An upsert: repoFullName and branch are required on every call even when
        only one field is changing, and omitted optional fields keep their
        stored value.

        Args:
            agent_name: Name of the agent
            org: Organization ID
            payload: repoFullName, branch, and optionally dockerfilePath,
                subdirectory, autoDeploy
        Returns:
            The stored binding, with GitHub's canonical repo casing
        """
        return self.create_api_method(self._agent_git_connect)

    async def _agent_git_disconnect(self, agent_name: str, org: str) -> dict | None:
        url = self.construct_api_url("service_git_path").format(org=org, service=agent_name)
        return await self._base_request("DELETE", url)

    @property
    def agent_git_disconnect(self):
        """Remove an agent's repo binding.

        Pushes stop deploying; the running agent is untouched.

        Args:
            agent_name: Name of the agent
            org: Organization ID
        """
        return self.create_api_method(self._agent_git_disconnect)

    async def _agent_git_deploy(self, agent_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('service_git_path').format(org=org, service=agent_name)}/deploy"
        result = await self._base_request("POST", url, json={}) or {}
        return result.get("deployIntent")

    @property
    def agent_git_deploy(self):
        """Build and deploy the connected branch's current HEAD.

        The API answers 202 as soon as the intent is queued, so the result
        describes a pending deploy, never a finished one.

        Args:
            agent_name: Name of the agent
            org: Organization ID
        Returns:
            The deploy intent: id, commitSha, ref, status
        """
        return self.create_api_method(self._agent_git_deploy)

    # Projects and environments

    async def _environment_link(
        self, agent_name: str, org: str, parent: str, environment_name: str
    ) -> dict | None:
        url = self.construct_api_url("service_parent_path").format(org=org, service=agent_name)
        return await self._base_request(
            "PUT", url, json={"parent": parent, "environmentName": environment_name}
        )

    @property
    def environment_link(self):
        """Link an agent as an environment of a project.

        A PUT, so it also renames an environment or moves it to another
        project. The API refuses a link across regions, a chain in either
        direction, a name another environment holds, and any change to a PR
        preview's own link.

        Args:
            agent_name: Name of the agent to become an environment
            org: Organization ID
            parent: Name of the project it belongs to
            environment_name: What to call it among that project's environments
        Returns:
            serviceName, parent, environmentName
        """
        return self.create_api_method(self._environment_link)

    async def _environment_unlink(self, agent_name: str, org: str) -> dict | None:
        url = self.construct_api_url("service_parent_path").format(org=org, service=agent_name)
        return await self._base_request("DELETE", url)

    @property
    def environment_unlink(self):
        """Unlink an environment from its project.

        It stays an ordinary agent, running as before. Idempotent: an agent
        that isn't an environment answers 200.

        Args:
            agent_name: Name of the environment's agent
            org: Organization ID
        Returns:
            serviceName, parent (null), environmentName (null)
        """
        return self.create_api_method(self._environment_unlink)

    async def _previews_update(
        self, agent_name: str, org: str, secret_set: str | None, bot_pull_requests: bool | None
    ) -> dict | None:
        url = self.construct_api_url("service_previews_path").format(org=org, service=agent_name)
        payload: dict = {"secretSet": secret_set}
        if bot_pull_requests is not None:
            payload["botPullRequests"] = bot_pull_requests
        return await self._base_request("PUT", url, json=payload)

    @property
    def previews_update(self):
        """Turn a project's PR previews on with the secret set they mount, or off.

        Choosing the set is what turns previews on; `secret_set=None` turns
        them off, which also deletes the previews that exist. Leaving
        `bot_pull_requests` as None keeps the stored value.

        Args:
            agent_name: Name of the project
            org: Organization ID
            secret_set: Secret set previews mount, or None to turn them off
            bot_pull_requests: Whether PRs opened by bots get previews too
        """
        return self.create_api_method(self._previews_update)

    async def _watch_paths_update(
        self, agent_name: str, org: str, watch_paths: list[str]
    ) -> dict | None:
        url = self.construct_api_url("service_watch_paths_path").format(org=org, service=agent_name)
        result = await self._base_request("PUT", url, json={"watchPaths": watch_paths}) or {}
        return result

    @property
    def watch_paths_update(self):
        """Set a project's watch paths. An empty list clears them.

        A project setting: its production, its environments and its PR
        previews all follow this one list, so the API refuses it on an
        environment.

        Args:
            agent_name: Name of the project
            org: Organization ID
            watch_paths: Glob patterns a change must match to deploy
        Returns:
            watchPaths, as stored
        """
        return self.create_api_method(self._watch_paths_update)

    async def _agent_update_source(
        self, agent_name: str, org: str, build_id: str | None = None, image: str | None = None
    ) -> dict | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}/{agent_name}"
        payload = {"buildId": build_id} if build_id else {"image": image}
        return await self._base_request("POST", url, json=payload)

    @property
    def agent_update_source(self):
        """Deploy a build or an image onto an agent, changing nothing else.

        The update merges only the artifact into the agent's live manifest, so
        its secrets, scaling and profile are left as they are. This is what
        promotion does.

        Args:
            agent_name: Name of the agent to deploy onto
            org: Organization ID
            build_id: Build to deploy
            image: Image to deploy, when there is no build
        """
        return self.create_api_method(self._agent_update_source)
