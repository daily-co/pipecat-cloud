from functools import wraps
from typing import Callable, Optional

import aiohttp
from loguru import logger

from pipecatcloud.config import config


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


class _API():
    def __init__(self):
        self.error = None

    @staticmethod
    def construct_api_url(path: str) -> str:
        if not config.get("server_url", ""):
            raise ValueError("Server URL is not set")

        if not config.get(path, ""):
            raise ValueError(f"Endpoint {path} is not set")

        return f"{config.get('server_url', '')}{config.get(path, '')}"

    def _configure_headers(self) -> dict:
        token = config.get("token")
        if not token:
            return {}
        return {"Authorization": f"Bearer {token}"}

    async def _base_request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
    ) -> Optional[dict]:
        async with aiohttp.ClientSession() as session:
            response = await session.request(
                method=method,
                url=url,
                headers=self._configure_headers(),
                params=params,
                json=json
            )
            if not response.ok:
                self.error = response.status
                response.raise_for_status()
            response.raise_for_status()

            return await response.json()

    def create_api_method(self, method_func: Callable) -> Callable:
        """Factory method that wraps API methods with error handling and live context"""
        @wraps(method_func)
        async def wrapper(*args, live=None, **kwargs):
            self.error = None
            try:
                result = await method_func(*args, **kwargs)
                return result, self.error
            except Exception as e:
                if live:
                    live.stop()
                logger.debug(e)

                if self.error:
                    self.print_error()
                return None, self.error
        return wrapper

    def print_error(self):
        from pipecatcloud._utils.console_utils import console

        # @TODO: handle coded errors here

        if not self.error:
            return
        if self.error == 401:
            console.unauthorized()
        else:
            console.api_error(str(self.error))

    # Auth

    async def _whoami(self) -> dict:
        url = self.construct_api_url("whoami_path")
        return await self._base_request("GET", url) or {}

    @property
    def whoami(self):
        return self.create_api_method(self._whoami)

    # Organizations

    async def _organizations_current(self) -> dict | None:
        url = self.construct_api_url("organization_path")
        active_org = config.get("org")

        results = await self._base_request("GET", url)

        if not results or not len(results["organizations"]):
            return None

        # If active_org is specified, try to find it in the list
        if active_org:
            for org in results["organizations"]:
                if org["name"] == active_org:
                    return {"name": org["name"], "verbose_name": org["verboseName"]}

        # Default to first organization if active_org not found or not specified
        return {"name": results[0]["name"], "verbose_name": results[0]["verboseName"]}

    @property
    def organizations_current(self):
        return self.create_api_method(self._organizations_current)

    async def _organizations(self) -> list:
        url = self.construct_api_url("organization_path")
        results = await self._base_request("GET", url)

        if not results or not results.get("organizations", None):
            raise

        return results.get("organizations", None) or []

    @property
    def organizations(self):
        return self.create_api_method(self._organizations)

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
        return await self._base_request("POST", url, json={"name": api_key_name, "type": "public"}) or {}

    @property
    def api_key_create(self):
        """Create API keys for an organization.
        Args:
            api_key_name: Human readable name for API key
            org: Organization ID
        """
        return self.create_api_method(self._api_key_create)

    async def _api_key_delete(self, api_key_id: str, org: str) -> dict:
        url = f"{self.construct_api_url('api_keys_path').format(org=org)}/{api_key_id}"
        return await self._base_request("DELETE", url) or {}

    @property
    def api_key_delete(self):
        """Delete API keys for an organization.
        Args:
            api_key_id: Human readable name for API key
            org: Organization ID
        """
        return self.create_api_method(self._api_key_delete)
    """

    # Secrets

    async def secrets_list(self, organization: Optional[str] = None) -> dict:
        org = organization or config.get("org")
        url = self.construct_api_url("secrets_path").format(org=org)
        return await self._request("GET", url) or {}
    """


API = _API()
