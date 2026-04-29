#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Session argument types for Pipecat Cloud bots.

The canonical runner argument types live in :mod:`pipecat.runner.types`.
The legacy ``*SessionArguments`` names defined here are deprecated aliases
kept for backwards compatibility; new code should import the corresponding
``*RunnerArguments`` from pipecat-ai directly.
"""

import warnings
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

# The runner types are owned by pipecat-ai. Fall back to in-package
# definitions when pipecat-ai is not installed so the CLI can still import
# this module; the fallback is deprecated and will be removed.
try:
    from pipecat.runner.types import (
        DailyRunnerArguments,
        RunnerArguments,
        SmallWebRTCRunnerArguments,
        WebSocketRunnerArguments,
    )
except ImportError:

    @dataclass
    class RunnerArguments:
        """Fallback base class used when pipecat-ai is not installed.

        .. deprecated:: 0.2.2
            Install ``pipecatcloud[pipecat]`` for full compatibility. The
            standalone fallback will be removed in a future release.
        """

        handle_sigint: bool = field(init=False, kw_only=True)
        handle_sigterm: bool = field(init=False, kw_only=True)
        pipeline_idle_timeout_secs: int = field(init=False, kw_only=True)
        body: Any | None = field(default_factory=dict, kw_only=True)
        session_id: str | None = field(default=None, kw_only=True)

        def __post_init__(self):
            self.handle_sigint = False
            self.handle_sigterm = False
            self.pipeline_idle_timeout_secs = 300
            warnings.warn(
                "Using standalone pipecatcloud session arguments without "
                "pipecat-ai. For full compatibility, install: "
                "pip install pipecatcloud[pipecat]. Standalone mode will be "
                "removed in a future release.",
                DeprecationWarning,
                stacklevel=3,
            )

    @dataclass
    class DailyRunnerArguments(RunnerArguments):
        """Fallback Daily runner arguments when pipecat-ai is not available."""

        room_url: str
        token: str

    @dataclass
    class WebSocketRunnerArguments(RunnerArguments):
        """Fallback WebSocket runner arguments when pipecat-ai is not available."""

        websocket: WebSocket

    @dataclass
    class SmallWebRTCRunnerArguments(RunnerArguments):
        """Fallback SmallWebRTC runner arguments when pipecat-ai is not available."""

        webrtc_connection: Any


# Deprecated session-argument aliases. The runner types now carry the
# ``session_id`` field directly, so the legacy ``*SessionArguments`` classes
# collapse to straight aliases. Existing imports keep working unchanged
# (``isinstance`` and type annotations both resolve identically because each
# alias is the same class object as its target). New code should import the
# canonical names from :mod:`pipecat.runner.types`.
SessionArguments = RunnerArguments
PipecatSessionArguments = RunnerArguments
DailySessionArguments = DailyRunnerArguments
WebSocketSessionArguments = WebSocketRunnerArguments
SmallWebRTCSessionArguments = SmallWebRTCRunnerArguments
