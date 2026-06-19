#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Agent session argument types.

These are the argument types passed to a bot's ``bot()`` entry point. Each is a
thin subclass of the matching pipecat-ai runner argument type
(``pipecat.runner.types.*RunnerArguments``), so they interoperate with pipecat's
runner machinery (``create_transport``, ``isinstance`` checks) while giving
Pipecat Cloud a stable, branded session-argument API.

These types require **pipecat-ai** (>= 1.0.0). pipecat-ai is an optional
dependency of pipecatcloud (the SDK and CLI do not need it), so a bot environment
must provide it — install with ``pip install "pipecatcloud[pipecat]"`` or bring
your own ``pipecat-ai``. Importing this module without pipecat-ai raises a clear
``ImportError``.
"""

from dataclasses import dataclass

try:
    from pipecat.runner.types import (
        DailyRunnerArguments,
        RunnerArguments,
        SmallWebRTCRunnerArguments,
        WebSocketRunnerArguments,
    )
except ImportError as e:  # pragma: no cover - exercised only without pipecat-ai
    raise ImportError(
        "pipecatcloud's agent session-argument types require pipecat-ai>=1.0.0. "
        'Install it with `pip install "pipecatcloud[pipecat]"`, or add pipecat-ai '
        "to your environment."
    ) from e


@dataclass
class SessionArguments:
    """Base class / marker for Pipecat Cloud agent session arguments.

    The arguments are received by the bot() entry point.

    Parameters:
        session_id (str | None): The unique identifier for the session, used to
            track it across requests.
    """

    # ``session_id`` is intentionally declared here as a compatibility shim:
    # pipecat-ai only added ``session_id`` to the base ``RunnerArguments`` in
    # v1.2.0. For pipecat-ai in the [1.0.0, 1.2.0) range, this mixin supplies
    # ``session_id``. When pipecat-ai *does* define it on the base, that field
    # overrides this one because ``RunnerArguments`` appears later than
    # ``SessionArguments`` in the MRO of the subclasses below. That is why every
    # subclass must list the ``*RunnerArguments`` base FIRST — e.g.
    # ``(DailyRunnerArguments, SessionArguments)``. Do not reorder the bases.
    # (No default here on purpose: giving it one would order a defaulted field
    # before ``room_url`` on older pipecat-ai and raise "non-default argument
    # follows default argument".)
    session_id: str | None


@dataclass
class PipecatSessionArguments(RunnerArguments, SessionArguments):
    """Standard Pipecat Cloud agent session arguments (no specific transport).

    Used for HTTP activations that did not request a Daily room — for example the
    room-less SmallWebRTC flow, where the WebRTC connection arrives separately.
    """


@dataclass
class DailySessionArguments(DailyRunnerArguments, SessionArguments):
    """Daily-based agent session arguments."""


@dataclass
class WebSocketSessionArguments(WebSocketRunnerArguments, SessionArguments):
    """WebSocket-based agent session arguments."""


@dataclass
class SmallWebRTCSessionArguments(SmallWebRTCRunnerArguments, SessionArguments):
    """SmallWebRTCTransport-based agent session arguments."""
