#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
from functools import wraps
from typing import Callable, List, Optional, Union

import aiohttp
from loguru import logger

from pipecatcloud._utils.deploy_utils import DeployConfigParams
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


class _API:
    def __init__(self, token: Optional[str] = None, is_cli: bool = False):
        self.token = token
        self.error = None
        self.bubble_next = False
        self.is_cli = is_cli

    @staticmethod
    def construct_api_url(path: str) -> str:
        if not config.get("api_host", ""):
            raise ValueError("API host config variable is not set")

        if not config.get(path, ""):
            raise ValueError(f"Endpoint {path} is not set")

        return f"{config.get('api_host', '')}{config.get(path, '')}"

    def _configure_headers(self, override_token: Optional[str] = None) -> dict:
        if not self.token and not override_token:
            return {}
        return {"Authorization": f"Bearer {override_token or self.token}"}

    async def _base_request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        json: Optional[dict] = None,
        not_found_is_empty: bool = False,
        override_token: Optional[str] = None,
    ) -> Optional[dict]:
        async with aiohttp.ClientSession() as session:
            logger.debug(f"Request: {method} {url} {params} {json}")

            response = await session.request(
                method=method,
                url=url,
                headers=self._configure_headers(override_token),
                params=params,
                json=json,
            )

            if not response.ok:
                logger.debug(f"Response not ok: {response.status} {response.reason}")
                if self.is_cli and not_found_is_empty and response.status == 404:
                    return None

                # Extract PCC error code, where applicable
                try:
                    # Try to parse the error as JSON
                    error_data = await response.json()
                    self.error = error_data
                except Exception:
                    # Fallback structure matching API format
                    self.error = {
                        "error": response.reason or "Bad Request",
                        "code": str(
                            response.status)}
                response.raise_for_status()

            return await response.json()

    def create_api_method(self, method_func: Callable) -> Callable:
        """Factory method that wraps API methods with error handling and live context"""

        @wraps(method_func)
        async def wrapper(*args, live=None, **kwargs):
            self.error = None
            try:
                result = await method_func(*args, **kwargs)
                self.bubble_next = False
                return result, self.error
            except Exception as e:
                if live and not self.bubble_next:
                    live.stop()

                if not self.is_cli and self.error and not self.bubble_next:
                    if isinstance(self.error, dict) and self.error.get("status", "429"):
                        raise AgentStartError(self.error)
                    else:
                        raise e

                if self.error and not self.bubble_next:
                    logger.debug(e)
                    self.print_error()

                self.bubble_next = False
                return None, self.error

        return wrapper

    def print_error(self):
        from pipecatcloud._utils.console_utils import console

        if not self.error:
            return
        if isinstance(self.error, dict) and self.error.get("code", "400") == "401":
            console.unauthorized()
        else:
            console.api_error(self.error)

    def bubble_error(self):
        self.bubble_next = True
        return self

    # Auth

    async def _whoami(self) -> dict:
        url = self.construct_api_url("whoami_path")
        return await self._base_request("GET", url) or {}

    @property
    def whoami(self):
        return self.create_api_method(self._whoami)

    # Organizations

    async def _organizations_current(self, org: Optional[str] = None) -> dict | None:
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

    # Secret

    async def _secrets_list(self, org: str, secret_set: Optional[str] = None) -> dict | None:
        if secret_set:
            url = f"{self.construct_api_url('secrets_path').format(org=org)}/{secret_set}"
        else:
            url = f"{self.construct_api_url('secrets_path').format(org=org)}"

        result = await self._base_request("GET", url, not_found_is_empty=True) or {}

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

        """
        return self.create_api_method(self._secrets_list)

    async def _secrets_upsert(self, data: dict, set_name: str, org: str) -> dict:
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/{set_name}"
        return await self._base_request("PUT", url, json=data) or {}

    @property
    def secrets_upsert(self):
        """Create / modify secret set.
        Args:
            data: key and value of secret to add (or credentials for image pull secrets)
            set_name: name of set to create or update
            org: Organization ID
        """
        return self.create_api_method(self._secrets_upsert)

    async def _secrets_delete(self, set_name: str, secret_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('secrets_path').format(org=org)}/{set_name}/{secret_name}"
        return await self._base_request("DELETE", url, not_found_is_empty=True)

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

    # Deploy

    async def _deploy(
        self, deploy_config: DeployConfigParams, org: str, update: bool = False
    ) -> dict | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}"

        # Create base payload and filter out None values
        payload = {
            "serviceName": deploy_config.agent_name,
            "image": deploy_config.image,
            "imagePullSecretSet": deploy_config.image_credentials,
            "secretSet": deploy_config.secret_set,
            "autoScaling": {
                "minAgents": deploy_config.scaling.min_agents,
                "maxAgents": deploy_config.scaling.max_agents,
            },
            "enableKrisp": deploy_config.enable_krisp,
        }

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

    async def _agents(self, org: str) -> List[dict] | None:
        url = f"{self.construct_api_url('services_path').format(org=org)}"
        result = await self._base_request("GET", url) or {}

        if "services" in result:
            return result["services"]

        return None

    @property
    def agents(self):
        return self.create_api_method(self._agents)

    async def _start_agent(
        self,
        agent_name: str,
        api_key: str,
        use_daily: bool,
        data: Optional[str] = None,
        daily_properties: Optional[str] = None,
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

    async def _agent_logs(self, agent_name: str, org: str, limit: int = 100,
                          deployment_id: Optional[str] = None) -> dict | None:
        url = f"{self.construct_api_url('services_logs_path').format(org=org, service=agent_name)}"
        params: dict[str, Union[int, str]] = {"limit": limit}
        if deployment_id:
            params["deploymentId"] = deployment_id
        return await self._base_request("GET", url, params=params)

    @property
    def agent_logs(self):
        return self.create_api_method(self._agent_logs)

    async def _agent_sessions(self, agent_name: str, org: str) -> dict | None:
        url = f"{self.construct_api_url('services_sessions_path').format(org=org, service=agent_name)}"
        return await self._base_request("GET", url) or {}

    @property
    def agent_sessions(self):
        return self.create_api_method(self._agent_sessions)
