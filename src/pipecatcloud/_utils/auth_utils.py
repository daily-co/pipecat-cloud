#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import functools
from typing import Optional

import aiohttp

from pipecatcloud.__version__ import version
from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.config import config


async def _resolve_default_org(token: str) -> Optional[str]:
    """Fetch the user's default organization using the given token."""
    from pipecatcloud.config import config as base_config

    api_host = base_config.get("api_host")
    org_path = base_config.get("organization_path")
    url = f"{api_host}{org_path}"

    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": f"PipecatCloudCLI/{version}",
            },
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                organizations = data.get("organizations", [])
                if organizations:
                    return organizations[0]["name"]
    return None


def requires_login(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        token = config.get("token")
        if token is None:
            console.error(
                f"You are not logged in. Please run `{PIPECAT_CLI_NAME} auth login` first.",
            )
            return

        # When org is not set locally (e.g. PIPECAT_TOKEN without a config file),
        # resolve the user's default organization from the API.
        if config.get("org") is None:
            org_name = await _resolve_default_org(token)
            if org_name:
                config.override_locally("org", org_name)
            else:
                console.error(
                    "Could not determine your organization. "
                    f"Set PIPECAT_ORG or run `{PIPECAT_CLI_NAME} auth login`.",
                )
                return

        return await func(*args, **kwargs)

    return wrapper
