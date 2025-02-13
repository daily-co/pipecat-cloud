from typing import Optional, Tuple

import aiohttp

from pipecatcloud._utils.console_utils import console
from pipecatcloud.config import config
from pipecatcloud.exception import AuthError


class _API():
    def __init__(self, is_cli: bool = True):
        self.is_cli = is_cli

    def construct_api_url(self, path: str) -> str:
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

    async def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None
    ) -> Optional[dict]:
        response = None
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.request(
                    method=method,
                    url=url,
                    headers=self._configure_headers(),
                    params=params,
                    json=json
                )
                if response.status == 401:
                    raise AuthError
                response.raise_for_status()
                return await response.json()
        except AuthError:
            console.unauthorized()

    async def whoami(self) -> dict:
        url = self.construct_api_url("whoami_path")
        return await self._request("GET", url, params={}) or {}

    async def secrets_list(self, organization: Optional[str] = None) -> dict:
        org = organization or config.get("org")
        url = self.construct_api_url("secrets_path").format(org=org)
        return await self._request("GET", url, params={}) or {}

    async def organizations_current(self) -> Tuple[Optional[str], Optional[str]]:
        url = self.construct_api_url("organization_path")
        active_org = config.get("org")

        results = await self._request("GET", url, params={})

        if not results or not len(results["organizations"]):
            return None, None

        # If active_org is specified, try to find it in the list
        if active_org:
            for org in results["organizations"]:
                if org["name"] == active_org:
                    return org["name"], org["verboseName"]

        # Default to first organization if active_org not found or not specified
        return results[0]["name"], results[0]["verboseName"]


API = _API()
