#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import sys

from loguru import logger

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


# The agent session-argument types subclass pipecat-ai's runner argument types,
# so importing them pulls in pipecat-ai (an optional dependency). Resolve them
# lazily (PEP 562) so plain `import pipecatcloud` — the SDK and CLI surface — does
# not require pipecat-ai. Accessing one of these names imports pipecatcloud.agent,
# which raises a clear ImportError if pipecat-ai is not installed.
_LAZY_AGENT_EXPORTS = (
    "DailySessionArguments",
    "PipecatSessionArguments",
    "SessionArguments",
    "SmallWebRTCSessionArguments",
    "WebSocketSessionArguments",
)


def __getattr__(name: str):
    if name in _LAZY_AGENT_EXPORTS:
        from pipecatcloud import agent

        return getattr(agent, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals()) + list(_LAZY_AGENT_EXPORTS))


__all__ = [
    # Agent classes
    "DailySessionArguments",
    "PipecatSessionArguments",
    "SessionArguments",
    "SmallWebRTCSessionArguments",
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
