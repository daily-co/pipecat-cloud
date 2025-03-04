from dataclasses import dataclass
from typing import Optional

import aiohttp
from loguru import logger

from pipecatcloud.api import _API as API
from pipecatcloud.exception import AgentStartError


@dataclass
class SessionParams:
    data: Optional[dict] = None
    use_daily: Optional[bool] = False


class Session:
    def __init__(
        self,
        agent_name: str,
        api_key: str,
        params: Optional[SessionParams] = None,
    ):
        self.agent_name = agent_name
        self.api_key = api_key

        if not self.agent_name:
            raise ValueError("Agent name is required")

        self.params = params or SessionParams()

    async def start(self) -> bool:
        if not self.api_key:
            raise AgentStartError({"code": "PCC-1002", "error": "No API key provided"})

        logger.debug(f"Starting agent {self.agent_name}")

        start_error = None

        payload: dict = {"createDailyRoom": bool(self.params.use_daily)}
        if self.params.data is not None:
            payload["body"] = self.params.data

        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{API.construct_api_url('start_path').format(service=self.agent_name)}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json=payload
                )

                if response.status != 200:
                    # Attempt to parse the error code from the response
                    try:
                        error_data = await response.json()
                        start_error = error_data
                    except Exception:
                        start_error = {"code": "PCC-1000",
                                       "error": "Unknown error occurred. Please contact support."}

                    response.raise_for_status()
                else:
                    logger.debug(f"Agent {self.agent_name} started successfully")
        except Exception as e:
            logger.error(f"Error starting agent {self.agent_name}: {e}")
            raise AgentStartError(error=start_error)

        return True
