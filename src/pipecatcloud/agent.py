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

``MOQSessionArguments`` needs pipecat-ai 1.12.0 or newer. On an older pipecat-ai
the rest of this module imports as usual, and reaching for
``MOQSessionArguments`` raises an ``ImportError`` that says what to install.
Detect it by catching that ``ImportError``::

    try:
        from pipecatcloud.agent import MOQSessionArguments
    except ImportError:
        ...  # this environment cannot serve MoQ sessions

``hasattr`` and ``getattr(..., default)`` raise that ``ImportError`` too, rather
than reporting the name as missing, and so do ``help(pipecatcloud)`` and
``inspect.getmembers(pipecatcloud)``, which list the name and then fetch it.
"""

from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from typing import TYPE_CHECKING

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

# Imported on its own so that this module keeps importing on the older pipecat-ai
# the other types support. MOQRunnerArguments arrived in pipecat-ai 1.6.0, and
# gained ``relay_url``, the URL Pipecat Cloud hands the bot, in 1.12.0.
try:
    from pipecat.runner.types import MOQRunnerArguments as _MOQRunnerArguments
except ImportError:  # pipecat-ai older than 1.6.0
    _MOQRunnerArguments = None

_MOQ_REQUIREMENT = "pipecat-ai[moq]>=1.12.0"


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


# Read from __dataclass_fields__ rather than dataclasses.fields(), which raises
# TypeError, out of this module's import, for a class that is not a dataclass.
if _MOQRunnerArguments is not None and "relay_url" in getattr(
    _MOQRunnerArguments, "__dataclass_fields__", {}
):

    @dataclass
    class MOQSessionArguments(_MOQRunnerArguments, SessionArguments):
        """Media over QUIC (MoQ) agent session arguments.

        On Pipecat Cloud the bot of a MoQ session dials the region's relay.
        ``relay_url`` is that relay's URL with the session's token in its query
        string, so treat it as a secret and keep it out of logs. ``namespace``
        scopes the session: the bot publishes under
        ``<namespace>/<participant_id>`` and subscribes to
        ``<namespace>/<peer_id>``. Pass the arguments to ``create_transport`` as
        for any other transport.

        Needs pipecat-ai 1.12.0 or newer, with the ``moq`` extra for the
        transport itself.
        """

        # Redeclared only to keep the token out of repr(), and so out of any log
        # line or traceback that prints the arguments. Same default and
        # keyword-only as pipecat-ai's field, so the signature is unchanged.
        relay_url: str | None = field(default=None, kw_only=True, repr=False)


# Hidden from type checkers so a misspelt name imported from this module is still
# reported; to them MOQSessionArguments is simply defined above.
if not TYPE_CHECKING:

    def __getattr__(name: str):
        # Reached only for names this module does not define, so only when the
        # installed pipecat-ai cannot back MOQSessionArguments.
        if name == "MOQSessionArguments":
            try:
                installed = f"pipecat-ai {version('pipecat-ai')}"
            except PackageNotFoundError:  # pragma: no cover - e.g. a source checkout
                installed = "an unknown pipecat-ai"
            raise ImportError(
                "MOQSessionArguments needs pipecat-ai 1.12.0 or newer, where "
                f"MOQRunnerArguments carries relay_url; this environment has {installed}. "
                f'Install it with `pip install "{_MOQ_REQUIREMENT}"`.'
            )
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
