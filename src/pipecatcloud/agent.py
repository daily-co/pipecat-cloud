#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Agent session argument types.

.. deprecated:: 1.0.0
    The ``*SessionArguments`` types only add ``session_id`` on top of pipecat-ai's
    runner argument types, which now carry ``session_id`` themselves. Use
    ``pipecat.runner.types.RunnerArguments`` (and its ``DailyRunnerArguments`` /
    ``WebSocketRunnerArguments`` / ``SmallWebRTCRunnerArguments`` subclasses)
    directly. These types will be removed in 2.0.0.
"""

import warnings
from dataclasses import dataclass

from pipecat.runner.types import (
    DailyRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)


def _warn_deprecated(name: str) -> None:
    warnings.warn(
        f"{name} is deprecated and will be removed in 2.0.0. Use the pipecat-ai "
        "runner argument types directly (RunnerArguments / DailyRunnerArguments / "
        "WebSocketRunnerArguments / SmallWebRTCRunnerArguments from "
        "pipecat.runner.types) — they already provide `session_id`.",
        DeprecationWarning,
        stacklevel=3,
    )


@dataclass
class SessionArguments:
    """Base class for common agent session arguments.

    The arguments are received by the bot() entry point.

    .. deprecated:: 1.0.0
        Use pipecat-ai's ``RunnerArguments`` (and its transport-specific
        subclasses) directly — they already carry ``session_id``. Removed in 2.0.0.

    Parameters:
        session_id (str | None): The unique identifier for the session.
            This is used to track the session across requests.
    """

    session_id: str | None

    def __post_init__(self) -> None:
        _warn_deprecated(type(self).__name__)


@dataclass
class PipecatSessionArguments(RunnerArguments, SessionArguments):
    """Standard Pipecat Cloud agent session arguments.

    .. deprecated:: 1.0.0
        Use ``pipecat.runner.types.RunnerArguments`` directly. Removed in 2.0.0.
    """

    def __post_init__(self) -> None:
        RunnerArguments.__post_init__(self)
        _warn_deprecated(type(self).__name__)


@dataclass
class DailySessionArguments(DailyRunnerArguments, SessionArguments):
    """Daily based agent session arguments.

    .. deprecated:: 1.0.0
        Use ``pipecat.runner.types.DailyRunnerArguments`` directly. Removed in 2.0.0.
    """

    def __post_init__(self) -> None:
        DailyRunnerArguments.__post_init__(self)
        _warn_deprecated(type(self).__name__)


@dataclass
class WebSocketSessionArguments(WebSocketRunnerArguments, SessionArguments):
    """WebSocket based agent session arguments.

    .. deprecated:: 1.0.0
        Use ``pipecat.runner.types.WebSocketRunnerArguments`` directly. Removed in 2.0.0.
    """

    def __post_init__(self) -> None:
        WebSocketRunnerArguments.__post_init__(self)
        _warn_deprecated(type(self).__name__)


@dataclass
class SmallWebRTCSessionArguments(SmallWebRTCRunnerArguments, SessionArguments):
    """SmallWebRTCTransport based agent session arguments.

    .. deprecated:: 1.0.0
        Use ``pipecat.runner.types.SmallWebRTCRunnerArguments`` directly. Removed
        in 2.0.0.
    """

    def __post_init__(self) -> None:
        SmallWebRTCRunnerArguments.__post_init__(self)
        _warn_deprecated(type(self).__name__)
