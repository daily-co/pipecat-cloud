"""The agent *SessionArguments types are thin subclasses of pipecat-ai's runner
argument types.

They are Pipecat Cloud's stable session-argument API (not deprecated): each
subclasses the matching ``pipecat.runner.types.*RunnerArguments``, so it carries
``session_id`` and interoperates with pipecat's runner machinery.
"""

import warnings

from pipecat.runner.types import (
    DailyRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)

from pipecatcloud.agent import (
    DailySessionArguments,
    PipecatSessionArguments,
    SessionArguments,
    SmallWebRTCSessionArguments,
    WebSocketSessionArguments,
)


def test_construction_does_not_warn():
    """The types are supported, not deprecated — constructing must not warn."""
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning becomes an error
        PipecatSessionArguments(session_id="s")
        DailySessionArguments(room_url="https://x.daily.co/r", token="t", session_id="s")
        WebSocketSessionArguments(websocket=None, session_id="s")
        SmallWebRTCSessionArguments(webrtc_connection=None, session_id="s")


def test_session_arguments_construct_and_expose_fields():
    d = DailySessionArguments(room_url="https://x.daily.co/r", token="t", session_id="s1")
    assert d.session_id == "s1"
    assert d.room_url == "https://x.daily.co/r"
    # The inherited RunnerArguments __post_init__ runs (sets handle_sigint default).
    assert d.handle_sigint is False


def test_session_id_is_optional_via_runnerarguments_override():
    """Guard for the MRO override: when pipecat-ai provides session_id on the base
    (v1.2.0+), it overrides the SessionArguments mixin's required field, making
    session_id optional. If someone reorders the bases, this breaks loudly here."""
    d = DailySessionArguments(room_url="https://x.daily.co/r")
    assert d.session_id is None


def test_subclasses_are_runner_argument_types():
    """Subclass relationship → interop with create_transport / isinstance checks."""
    daily = DailySessionArguments(room_url="https://x.daily.co/r", session_id="s")
    assert isinstance(daily, DailyRunnerArguments)
    assert isinstance(daily, RunnerArguments)
    assert isinstance(daily, SessionArguments)

    assert isinstance(PipecatSessionArguments(session_id="s"), RunnerArguments)
    assert isinstance(WebSocketSessionArguments(websocket=None), WebSocketRunnerArguments)
    assert isinstance(
        SmallWebRTCSessionArguments(webrtc_connection=None), SmallWebRTCRunnerArguments
    )
