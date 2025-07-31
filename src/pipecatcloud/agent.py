#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from dataclasses import dataclass
from typing import Any, Optional

try:
    from pipecat.runner.types import (
        DailyRunnerArguments,
        SmallWebRTCRunnerArguments,
        WebSocketRunnerArguments,
    )
except ImportError:
    raise ImportError(
        "The 'pipecat-ai' package is required for this module. "
        "Please install it using 'pip install pipecatcloud[pipecat]'."
    )


@dataclass
class SessionArguments:
    """Base class for common agent session arguments.

    The arguments are received by the bot() entry point.

    Parameters:
        session_id (Optional[str]): The unique identifier for the session.
            This is used to track the session across requests.
    """

    session_id: Optional[str]


@dataclass
class PipecatSessionArguments(SessionArguments):
    """Standard Pipecat Cloud agent session arguments.

    The arguments are received by the bot() entry point.

    Parameters:
        body (Any): The body of the request.
    """

    body: Any


@dataclass
class DailySessionArguments(DailyRunnerArguments, SessionArguments):
    """Daily based agent session arguments.

    The arguments are received by the bot() entry point. Inherits from
    DailyRunnerArguments for compatibility with pipecat-ai runner.
    """

    pass


@dataclass
class WebSocketSessionArguments(WebSocketRunnerArguments, SessionArguments):
    """Websocket based agent session arguments.

    The arguments are received by the bot() entry point. Inherits from
    WebSocketRunnerArguments for compatibility with pipecat-ai runner.
    """

    pass


@dataclass
class SmallWebRTCSessionArguments(SmallWebRTCRunnerArguments, SessionArguments):
    """Small WebRTC based agent session arguments.

    The arguments are received by the bot() entry point. Inherits from
    SmallWebRTCRunnerArguments for compatibility with pipecat-ai runner.
    """

    pass
