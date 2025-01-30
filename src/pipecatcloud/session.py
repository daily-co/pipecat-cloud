#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

from loguru import logger

from pipecatcloud.api import _API
from pipecatcloud.exception import AgentStartError


@dataclass
class SessionParams:
    """Parameters for configuring a Pipecat Cloud agent session.

    Args:
        data: Optional dictionary of data to pass to the agent.
        use_daily: If True, creates a Daily WebRTC room for the session.
    """

    data: Optional[Dict[str, Any]] = None
    use_daily: Optional[bool] = False


class Session:
    """Client for starting and managing Pipecat Cloud agent sessions.

    This class provides methods to start agent sessions and interact with running agents.

    Args:
        agent_name: Name of the deployed agent to interact with.
        api_key: Public API key for authentication.
        params: Optional SessionParams object to configure the session.

    Raises:
        ValueError: If agent_name is not provided.
    """

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

    async def start(self):
        """Start a new session with the specified agent.

        Initiates a new agent session with the configuration provided during initialization.
        If use_daily is True, creates a Daily room for WebRTC communication.

        Returns:
            dict: Response data containing session information. If use_daily is True,
                  includes 'dailyRoom' URL and 'dailyToken' for room access.

        Raises:
            AgentStartError: If the session fails to start, including:
                - Missing API key
                - Agent not found
                - Agent not ready
                - Capacity limits reached
        """
        if not self.api_key:
            raise AgentStartError({"code": "PCC-1002", "error": "No API key provided"})

        logger.debug(f"Starting agent {self.agent_name}")

        # Create the API class instance
        api = _API()

        # Convert data dict to JSON string if it's a dictionary
        data_param = None
        if self.params.data is not None:
            # Convert dictionary to JSON string
            if isinstance(self.params.data, dict):
                data_param = json.dumps(self.params.data)
            else:
                # If it's already a string or other type, use as is
                data_param = self.params.data

        # Call the method similar to how the CLI does it
        result, error = await api.start_agent(
            agent_name=self.agent_name,
            api_key=self.api_key,
            use_daily=bool(self.params.use_daily),
            data=data_param,
        )

        if error:
            raise AgentStartError(error=error)

        return result
