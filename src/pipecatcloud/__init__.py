#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import sys

from loguru import logger

from .exception import (
    AgentNotHealthyError,
    AgentStartError,
    AuthError,
    ConfigError,
    ConfigFileError,
    Error,
    InvalidError,
)
from .session import Session, SessionParams

logger.remove()
logger.add(sys.stderr, level=str(os.getenv("PCC_LOG_LEVEL", "INFO")))


__all__ = [
    # Session classes
    "Session",
    "SessionParams",
    # Exception classes
    "Error",
    "ConfigFileError",
    "AuthError",
    "InvalidError",
    "ConfigError",
    "AgentNotHealthyError",
    "AgentStartError",
]
