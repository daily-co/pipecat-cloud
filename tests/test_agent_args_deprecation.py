"""The agent *SessionArguments types are deprecated (removed in 2.0.0).

Constructing one emits a DeprecationWarning steering developers to pipecat-ai's
runner argument types, which already carry session_id. Construction still works.
"""

import pytest

from pipecatcloud.agent import (
    DailySessionArguments,
    PipecatSessionArguments,
    SessionArguments,
    SmallWebRTCSessionArguments,
    WebSocketSessionArguments,
)


def test_each_session_arguments_warns_on_construction():
    with pytest.warns(DeprecationWarning, match="removed in 2.0.0"):
        PipecatSessionArguments(session_id="s")
    with pytest.warns(DeprecationWarning):
        DailySessionArguments(room_url="https://x.daily.co/r", token="t", session_id="s")
    with pytest.warns(DeprecationWarning):
        WebSocketSessionArguments(websocket=None, session_id="s")
    with pytest.warns(DeprecationWarning):
        SmallWebRTCSessionArguments(webrtc_connection=None, session_id="s")
    with pytest.warns(DeprecationWarning):
        SessionArguments(session_id="s")


def test_session_arguments_still_construct_correctly():
    with pytest.warns(DeprecationWarning):
        d = DailySessionArguments(room_url="https://x.daily.co/r", token="t", session_id="s1")
    assert d.session_id == "s1"
    assert d.room_url == "https://x.daily.co/r"
    # The inherited RunnerArguments __post_init__ still runs.
    assert d.handle_sigint is False
