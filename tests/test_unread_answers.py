#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""An answer whose headers arrive and whose body does not (cli#224 review).

aiohttp returns from `session.request()` as soon as the headers are in, so a
connection lost mid-body raises from the body read rather than from the request.
These tests use a real HTTP server on loopback that promises a body length it
never sends, so the real client raises what it really raises for that, rather
than what a fake says it would.
"""

import json as jsonlib
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.api import _API
from pipecatcloud.cli.commands.deploy import _poll_failure_is_transient
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

# An answer that stops part way through its body: the headers promise more than
# the server sends, then it closes the connection.
TRUNCATE = "truncate"


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server's spelling
        self._answer()

    def do_PUT(self):  # noqa: N802
        self._answer()

    def do_POST(self):  # noqa: N802
        self._answer()

    def do_DELETE(self):  # noqa: N802
        self._answer()

    def log_message(self, *_args):
        """Quiet: the test asserts on the CLI's output."""

    def _answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append((self.command, self.path, body.decode() or None))

        answer = self.server.answers.pop(0) if self.server.answers else (200, {})
        if answer == TRUNCATE:
            partial = b'{"region": "r1"'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # More than is sent: the client reaches EOF still waiting for it.
            self.send_header("Content-Length", str(len(partial) + 32))
            self.end_headers()
            self.wfile.write(partial)
            self.wfile.flush()
            self.close_connection = True
            return

        status, payload = answer
        raw = jsonlib.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def api_server():
    """A real server the client talks to, as `PIPECAT_API_HOST`. Answers each
    request with the next of `answers`: a (status, body) pair, or TRUNCATE."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.answers = []
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with patch.dict(os.environ, {"PIPECAT_API_HOST": f"http://127.0.0.1:{server.server_port}"}):
            yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.asyncio
async def test_an_answer_that_stops_part_way_is_recorded_with_no_status(api_server):
    """Not a local bug to raise out of the client: there is no answer, which the
    caller has to be able to see as one."""
    api_server.answers = [TRUNCATE]
    api = _API(token="test-token", is_cli=True)

    data, error = await api.bubble_error().whoami()

    assert data is None
    assert api.no_response() is True
    assert api.error_status is None
    assert "Could not read the answer from the Pipecat Cloud API" in error["error"]
    assert "ClientPayloadError" in error["error"]


@pytest.mark.asyncio
async def test_a_poll_whose_answer_stops_part_way_is_still_transient(api_server):
    """A blip mid-deploy must keep the poll waiting, as it did when the client
    returned an empty result for this."""
    api_server.answers = [TRUNCATE]
    api = _API(token="test-token", is_cli=True)

    _data, error = await api.bubble_error().agent(agent_name="foo", org="acme")

    assert _poll_failure_is_transient(error, api.no_response()) is True


def test_a_write_truncated_after_one_key_says_how_far_it_got(api_server):
    """`secrets set` writes a key per request against a server that does not
    take them together. The second answer stops part way through, so the command
    has to say that key may or may not have been stored, and which keys are
    already in."""
    api_server.answers = [
        (404, {"error": "Not found"}),  # the existence check: no set yet
        (200, {"region": "r1"}),  # key A, written
        TRUNCATE,  # key B, no answer to read
    ]

    result = runner.invoke(
        entrypoint_cli_typer,
        ["--output", "plain", "secrets", "set", "my-set", "A=1", "B=2", "--skip", "-o", "acme"],
    )

    flat = " ".join(result.output.split())
    assert result.exit_code == 1
    assert "may not have been stored" in flat
    assert "Stored before the failure: A" in flat
    assert "Run the same command again to finish it" in flat
    assert [method for method, _path, _body in api_server.requests] == ["GET", "PUT", "PUT"]
