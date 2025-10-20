#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import warnings
from dataclasses import dataclass, field
from typing import Any, Optional

from fastapi import WebSocket

# Try to import new types from pipecat-ai
try:
    from pipecat.runner.types import (
        DailyRunnerArguments,
        RunnerArguments,
        SmallWebRTCRunnerArguments,
        WebSocketRunnerArguments,
    )

    _PIPECAT_RUNNER_TYPES_AVAILABLE = True
except ImportError:
    _PIPECAT_RUNNER_TYPES_AVAILABLE = False

    # Fallback definitions
    @dataclass
    class RunnerArguments:
        """Base class for runner session arguments.

        .. deprecated:: 0.2.2
            Install pipecatcloud[pipecat] for better compatibility. This class
            will be removed in a future release.
        """

        handle_sigint: bool = field(init=False, kw_only=True)
        handle_sigterm: bool = field(init=False, kw_only=True)
        pipeline_idle_timeout_secs: int = field(init=False, kw_only=True)
        body: Optional[Any] = field(default_factory=dict, kw_only=True)

        def __post_init__(self):
            self.handle_sigint = False
            self.handle_sigterm = False
            self.pipeline_idle_timeout_secs = 300

    @dataclass
    class DailyRunnerArguments(RunnerArguments):
        """Fallback Daily runner arguments when pipecat-ai not available.

        .. deprecated:: 0.2.1
            Install pipecatcloud[pipecat] for better compatibility.
        """

        room_url: str
        token: str

    @dataclass
    class WebSocketRunnerArguments(RunnerArguments):
        """Fallback WebSocket runner arguments when pipecat-ai not available.

        .. deprecated:: 0.2.1
            Install pipecatcloud[pipecat] for better compatibility.
        """

        websocket: WebSocket

    @dataclass
    class SmallWebRTCRunnerArguments(RunnerArguments):
        """Fallback Small WebRTC transport session arguments for the runner.

        Parameters:
            webrtc_connection: Pre-configured WebRTC peer connection
        """

        webrtc_connection: Any


def _warn_standalone_usage():
    """Warn users about standalone session arguments usage."""
    if not _PIPECAT_RUNNER_TYPES_AVAILABLE:
        warnings.warn(
            "Using standalone pipecatcloud session arguments without pipecat-ai. "
            "For better compatibility, install: pip install pipecatcloud[pipecat]. "
            "Standalone mode will be removed in a future release.",
            DeprecationWarning,
            stacklevel=3,
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
class PipecatSessionArguments(RunnerArguments, SessionArguments):
    """Standard Pipecat Cloud agent session arguments.

    Inherits from RunnerArguments for compatibility with pipecat-ai runner.
    When pipecat-ai is not installed, uses a fallback implementation (deprecated).

    For best compatibility, install: pip install pipecatcloud[pipecat]
    """

    def __post_init__(self):
        RunnerArguments.__post_init__(self)
        _warn_standalone_usage()


@dataclass
class DailySessionArguments(DailyRunnerArguments, SessionArguments):
    """Daily based agent session arguments.

    Inherits from DailyRunnerArguments for compatibility with pipecat-ai runner.
    When pipecat-ai is not installed, uses a fallback implementation (deprecated).

    For best compatibility, install: pip install pipecatcloud[pipecat]
    """

    def __post_init__(self):
        DailyRunnerArguments.__post_init__(self)
        _warn_standalone_usage()


@dataclass
class WebSocketSessionArguments(WebSocketRunnerArguments, SessionArguments):
    """WebSocket based agent session arguments.

    Inherits from WebSocketRunnerArguments for compatibility with pipecat-ai runner.
    When pipecat-ai is not installed, uses a fallback implementation (deprecated).

    For best compatibility, install: pip install pipecatcloud[pipecat]
    """

    def __post_init__(self):
        WebSocketRunnerArguments.__post_init__(self)
        _warn_standalone_usage()


@dataclass
class SmallWebRTCSessionArguments(SmallWebRTCRunnerArguments, SessionArguments):
    """SmallWebRTCTransport based agent session arguments.

    Inherits from SmallWebRTCRunnerArguments for compatibility with pipecat-ai runner.
    When pipecat-ai is not installed, uses a fallback implementation (deprecated).

    For best compatibility, install: pip install pipecatcloud[pipecat]
    """

    def __post_init__(self):
        SmallWebRTCRunnerArguments.__post_init__(self)
        _warn_standalone_usage()
