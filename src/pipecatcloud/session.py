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


class TransportParams:
    """Base class for transport parameters in Pipecat Cloud.

    Transport parameters configure how communication is established with an agent.
    This serves as the base class that specific transport types extend.
    """

    @property
    def transport_type(self) -> str:
        """Return the identifier for this transport type."""
        raise NotImplementedError("Subclasses must implement transport_type")

    @property
    def create_room(self) -> bool:
        """Whether to create a room for this transport."""
        return False

    @property
    def room_properties(self) -> Optional[Dict[str, Any]]:
        """Properties to configure the room, if applicable."""
        return None


@dataclass
class DailyTransportParams(TransportParams):
    """Parameters for Daily.co WebRTC transport configuration.

    Args:
        create_room: Whether to create a Daily room for this session.
        room_properties: Optional dictionary of properties to configure the Daily room.
            See Daily.co API documentation for available properties:
            https://docs.daily.co/reference/rest-api/rooms/config
    """

    create_room: bool = False
    room_properties: Optional[Dict[str, Any]] = None

    @property
    def transport_type(self) -> str:
        return "daily"


@dataclass
class SessionParams:
    """Parameters for configuring a Pipecat Cloud agent session.

    Args:
        data: Optional dictionary of data to pass to the agent.
        transport_params: Optional TransportParams for configuring communication.
            Currently supports DailyTransportParams.
    """

    data: Optional[Dict[str, Any]] = None
    transport_params: Optional[TransportParams] = None


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
            dict: Response data containing session information. If a transport is configured,
                  includes transport-specific access details.

        Raises:
            AgentStartError: If the session fails to start, including:
                - Missing API key
                - Agent not found
                - Agent not ready
                - Capacity limits reached
                - Transport configuration errors
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

        # Initialize transport parameters in a generic way
        transport_type = None
        create_room = False
        room_properties_param = None

        # Extract transport configuration if available
        if self.params.transport_params:
            transport = self.params.transport_params
            transport_type = transport.transport_type
            create_room = transport.create_room

            if create_room and transport.room_properties:
                room_properties_param = json.dumps(transport.room_properties)

        # Call the method to start the agent with the appropriate parameters
        result, error = await api.start_agent(
            agent_name=self.agent_name,
            api_key=self.api_key,
            use_daily=create_room if transport_type == "daily" else False,
            data=data_param,
            daily_properties=room_properties_param if transport_type == "daily" else None,
        )

        if error:
            raise AgentStartError(error=error)

        return result
