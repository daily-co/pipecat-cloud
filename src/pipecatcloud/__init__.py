#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import sys

from loguru import logger

from pipecatcloud.agent import (
    DailySessionArguments,
    PipecatSessionArguments,
    SessionArguments,
    SmallWebRTCRunnerArguments,
    WebSocketSessionArguments,
)
from pipecatcloud.exception import (
    AgentNotHealthyError,
    AgentStartError,
    AuthError,
    ConfigError,
    ConfigFileError,
    Error,
    InvalidError,
)
from pipecatcloud.session import Session, SessionParams
from pipecatcloud.smallwebrtc.session_manager import SmallWebRTCSessionManager

logger.remove()
logger.add(sys.stderr, level=str(os.getenv("PCC_LOG_LEVEL", "INFO")))


__all__ = [
    # Agent classes
    "DailySessionArguments",
    "PipecatSessionArguments",
    "SessionArguments",
    "SmallWebRTCRunnerArguments",
    "WebSocketSessionArguments",
    # Session classes
    "Session",
    "SessionParams",
    "SmallWebRTCSessionManager",
    # Exception classes
    "AgentNotHealthyError",
    "AgentStartError",
    "AuthError",
    "ConfigError",
    "ConfigFileError",
    "Error",
    "InvalidError",
]
