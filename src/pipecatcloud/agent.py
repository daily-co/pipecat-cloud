#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from typing import Any, Optional, TypedDict

from fastapi import WebSocket
from loguru import Logger


class SessionArguments(TypedDict):
    """Base class for common agent session arguments. The arguments are received
    by the bot() entry point.

    """

    session_id: Optional[str]
    session_logger: Optional[Logger]


class DailySessionArguments(SessionArguments):
    """Daily based agent session arguments. The arguments are received by the
    bot() entry point.

    """

    room_url: str
    token: str
    body: Any


class WebSocketSessionArguments(SessionArguments):
    """Websocket based agent session arguments. The arguments are received by
    the bot() entry point.

    """

    websocket: WebSocket
