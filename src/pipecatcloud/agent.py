#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from dataclasses import dataclass
from typing import Any, Optional

from fastapi import WebSocket


@dataclass
class SessionArguments:
    """Base class for common agent session arguments. The arguments are received
    by the bot() entry point.

    """

    session_id: Optional[str]


@dataclass
class PipecatSessionArguments(SessionArguments):
    """Standard Pipecat Cloud agent session arguments. The arguments are
    received by the bot() entry point.

    """

    body: Any


@dataclass
class DailySessionArguments(SessionArguments):
    """Daily based agent session arguments. The arguments are received by the
    bot() entry point.

    """

    room_url: str
    token: str
    body: Any


@dataclass
class WebSocketSessionArguments(SessionArguments):
    """Websocket based agent session arguments. The arguments are received by
    the bot() entry point.

    """

    websocket: WebSocket
