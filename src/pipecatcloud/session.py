from dataclasses import dataclass
from typing import Optional

import aiohttp
from loguru import logger

from pipecatcloud._utils.agent_utils import handle_agent_start_error
from pipecatcloud.api import API
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
            raise AgentStartError(error_code="PCC-1002")

        logger.debug(f"Starting agent {self.agent_name}")

        start_error_code = None
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{API.construct_api_url('start_path').format(service=self.agent_name)}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "createDailyRoom": bool(self.params.use_daily),
                        "body": self.params.data
                    }
                )
                if response.status != 200:
                    start_error_code = handle_agent_start_error(response.status)
                    response.raise_for_status()
                else:
                    logger.debug(f"Agent {self.agent_name} started successfully")
        except Exception as e:
            logger.error(f"Error starting agent {self.agent_name}: {e}")
            raise AgentStartError(error_code=start_error_code)

        return True
