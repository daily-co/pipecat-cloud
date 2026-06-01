#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Agent session argument types.

These extend pipecat-ai's runner argument types (a core dependency) with a
Pipecat Cloud ``session_id``.
"""

from dataclasses import dataclass

from pipecat.runner.types import (
    DailyRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)


@dataclass
class SessionArguments:
    """Base class for common agent session arguments.

    The arguments are received by the bot() entry point.

    Parameters:
        session_id (str | None): The unique identifier for the session.
            This is used to track the session across requests.
    """

    session_id: str | None


@dataclass
class PipecatSessionArguments(RunnerArguments, SessionArguments):
    """Standard Pipecat Cloud agent session arguments."""


@dataclass
class DailySessionArguments(DailyRunnerArguments, SessionArguments):
    """Daily based agent session arguments."""


@dataclass
class WebSocketSessionArguments(WebSocketRunnerArguments, SessionArguments):
    """WebSocket based agent session arguments."""


@dataclass
class SmallWebRTCSessionArguments(SmallWebRTCRunnerArguments, SessionArguments):
    """SmallWebRTCTransport based agent session arguments."""
