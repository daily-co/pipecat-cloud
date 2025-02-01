from dataclasses import dataclass
from typing import Optional

import aiohttp
from loguru import logger

from pipecatcloud._utils.agent_utils import handle_agent_start_error
from pipecatcloud._utils.http_utils import construct_api_url
from pipecatcloud.exception import AgentStartError


@dataclass
class AgentParams:
    data: Optional[dict] = None
    use_daily: Optional[bool] = False


class Agent:
    def __init__(
        self,
        agent_name: str,
        organization: str,
        api_key: str,
        params: Optional[AgentParams] = None,
    ):
        self.agent_name = agent_name
        self.api_key = api_key
        self.organization = organization

        if not self.organization or not self.agent_name:
            raise ValueError("Organization and agent name are required")

        self.params = params or AgentParams()

    async def start(self) -> bool:
        if not self.api_key:
            raise AgentStartError(error_code="PCC-1002")

        # @TODO: Public route not yet implemented
        """
        # Check deployment heath
        error_code = None
        agent_data = None

        try:
            async with aiohttp.ClientSession() as session:
                response = await session.get(
                    f"{construct_api_url('services_path').format(org=self.organization)}/{self.agent_name}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if response.status != 200:
                    error_code = str(response.status)
                    response.raise_for_status()
                agent_data = await response.json()
                if agent_data is None or not agent_data["ready"]:
                    raise AgentNotHealthyError(error_code=error_code)
        except Exception as e:
            raise Exception(f"Error checking agent health: {e}")
        """

        logger.debug(f"Starting agent {self.agent_name}")

        start_error_code = None
        try:
            async with aiohttp.ClientSession() as session:
                response = await session.post(
                    f"{construct_api_url('start_path').format(service=self.agent_name)}",
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
