"""Session.start() must raise when the request never reaches the API."""

import sys
from pathlib import Path
from unittest.mock import patch

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud import Session
from pipecatcloud.api import _API


class _FailingSession:
    def __init__(self, error: Exception):
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def request(self, **_kwargs):
        raise self._error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        aiohttp.ServerDisconnectedError(),
        aiohttp.ClientOSError(111, "Connect call failed"),
        TimeoutError(),
    ],
)
async def test_start_raises_when_the_request_fails_before_a_response(error):
    with patch("aiohttp.ClientSession", lambda *a, **k: _FailingSession(error)):
        with pytest.raises(type(error)):
            await Session("my-agent", "pk_test").start()


@pytest.mark.asyncio
async def test_cli_client_still_returns_no_result_on_transport_error():
    api = _API(token="test-token", is_cli=True)
    error = aiohttp.ServerDisconnectedError()

    with patch("aiohttp.ClientSession", lambda *a, **k: _FailingSession(error)):
        result, api_error = await api.start_agent(agent_name="a", api_key="k", use_daily=False)

    assert result is None
    assert api_error is None
