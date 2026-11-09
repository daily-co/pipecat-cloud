#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Tests for writing several values of a secret set in one request, and for falling
back to one request per key when the server does not say it takes that. The real
client and commands run; only the HTTP call is replaced.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud import api as api_module
from pipecatcloud.api import (
    _API,
    CAPABILITIES_HEADER,
    SECRETS_BULK_WRITE_CAPABILITY,
    SECRETS_WRITE_MAX_RETRIES,
)
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

BULK = {CAPABILITIES_HEADER: SECRETS_BULK_WRITE_CAPABILITY}


def _http_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(MagicMock(), (), status=status)


class FakeServer:
    """Answers `_base_request` as the API would: the set's read with its headers,
    then each write from `writes` in turn, a dict or (status, retry_after, body), or
    (status, retry_after, body, headers) for one from a server unlike the read's."""

    def __init__(self, headers: dict | None = None, writes: list | None = None, existing=None):
        self.headers = headers or {}
        self.writes = list(writes or [])
        self.existing = existing
        self.reads = []
        self.puts = []

    async def __call__(self, api, method, url, **kwargs):
        api.capabilities_header = self.headers.get(CAPABILITIES_HEADER)
        if method == "GET":
            self.reads.append(url)
            return self.existing
        self.puts.append(kwargs["json"])
        answer = self.writes.pop(0) if self.writes else {"status": "OK", "region": "r1"}
        if isinstance(answer, Exception):
            # A request that got no response. `_base_request` records the
            # failure with no status, since there was no response to take one
            # from, and raises; without the error the wrapper would let the
            # exception out as a failure on the client's side.
            api.error = {"error": "Could not reach the Pipecat Cloud API (ClientError)."}
            api.error_status = None
            raise answer
        if isinstance(answer, tuple):
            status, retry_after, body, *headers = answer
            if headers:
                api.capabilities_header = headers[0].get(CAPABILITIES_HEADER)
            api.error = body
            api.error_status = status
            api.error_retry_after = retry_after
            raise _http_error(status)
        return answer

    def install(self):
        server = self

        async def fake(api, method, url, **kwargs):
            return await server(api, method, url, **kwargs)

        return patch.object(_API, "_base_request", new=fake)


@pytest.fixture
def sleeps():
    with patch.object(api_module.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        yield mock_sleep


def _set(server: FakeServer, *args):
    with server.install():
        return runner.invoke(
            entrypoint_cli_typer,
            ["--output", "plain", "secrets", "set", "my-set", *args, "--skip", "-o", "acme"],
        )


def _flat(output: str) -> str:
    """The output with its line wrapping undone, to match a sentence."""
    return " ".join(output.split())


class TestSecretsSetInOneRequest:
    def test_several_values_are_one_request_with_every_key(self):
        server = FakeServer(BULK)
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 0
        assert server.puts == [
            {
                "secrets": [
                    {"secretKey": "A", "secretValue": "1"},
                    {"secretKey": "B", "secretValue": "2"},
                    {"secretKey": "C", "secretValue": "3"},
                ]
            }
        ]
        assert "created successfully in r1" in result.output

    def test_a_100_key_file_is_one_write_and_one_read(self, tmp_path):
        env = tmp_path / "big.env"
        env.write_text("".join(f"KEY_{i}=value-{i}\n" for i in range(100)))
        server = FakeServer(BULK)

        result = _set(server, "--file", str(env))

        assert result.exit_code == 0
        assert len(server.reads) == 1
        assert len(server.puts) == 1
        assert len(server.puts[0]["secrets"]) == 100
        assert server.puts[0]["secrets"][99] == {"secretKey": "KEY_99", "secretValue": "value-99"}

    def test_the_region_goes_with_the_write(self):
        server = FakeServer(BULK)
        with patch(
            "pipecatcloud.cli.commands.secrets.region_error", new=AsyncMock(return_value=None)
        ):
            result = _set(server, "A=1", "B=2", "-r", "eu-central")

        assert result.exit_code == 0
        assert server.puts[0]["region"] == "eu-central"

    def test_one_value_keeps_the_one_key_form(self):
        server = FakeServer(BULK)
        result = _set(server, "A=1")

        assert result.exit_code == 0
        assert len(server.puts) == 1
        assert server.puts[0]["secretKey"] == "A"
        assert "secrets" not in server.puts[0]

    def test_an_existing_set_says_modified(self):
        server = FakeServer(BULK, existing={"secrets": [{"fieldName": "OLD"}], "region": "r1"})
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 0
        assert len(server.puts) == 1
        assert "modified successfully" in result.output

    def test_a_refusal_prints_the_servers_words_with_nothing_about_part_of_a_set(self):
        error = {"error": "Secret sets are limited to about 1 MiB in total.", "code": "400"}
        server = FakeServer(BULK, writes=[(400, None, error)])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 1
        assert len(server.puts) == 1
        assert "limited to about 1 MiB" in result.output
        assert "not fully written" not in result.output
        assert "Stored before the failure" not in result.output

    def test_a_503_with_retry_after_repeats_the_whole_write(self, sleeps):
        unavailable = {"error": "Still being set up.", "code": "503"}
        server = FakeServer(BULK, writes=[(503, "5", unavailable), {"region": "r1"}])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 0
        assert len(server.puts) == 2
        assert server.puts[0] == server.puts[1]
        sleeps.assert_awaited_once_with(5)
        assert "could not be completed yet. Retrying in 5s" in result.output

    def test_a_503_past_the_bound_fails_with_nothing_about_part_of_a_set(self, sleeps):
        unavailable = {"error": "Still being set up.", "code": "503"}
        server = FakeServer(
            BULK, writes=[(503, "5", unavailable)] * (SECRETS_WRITE_MAX_RETRIES + 1)
        )
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 1
        assert len(server.puts) == SECRETS_WRITE_MAX_RETRIES + 1
        assert "Still unavailable after 3 retries" in result.output
        assert "not fully written" not in result.output


class TestOlderServer:
    def test_with_no_capability_each_key_goes_alone_and_nothing_alarming_is_said(self):
        server = FakeServer()
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 0
        assert [p["secretKey"] for p in server.puts] == ["A", "B", "C"]
        assert all("secrets" not in p for p in server.puts)
        for word in ("older", "fall", "upgrade", "warning", "not support", "one at a time"):
            assert word not in result.output.lower()

    def test_another_capability_is_not_this_one(self):
        server = FakeServer({CAPABILITIES_HEADER: "something-else, secrets-bulk-write-v2"})
        result = _set(server, "A=1", "B=2")

        assert [p["secretKey"] for p in server.puts] == ["A", "B"]

    def test_among_several_capabilities_it_is_found(self):
        server = FakeServer(
            {CAPABILITIES_HEADER: f"something-else, {SECRETS_BULK_WRITE_CAPABILITY}"}
        )
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 0
        assert len(server.puts) == 1

    def test_the_mid_way_message_stays_for_one_at_a_time(self):
        error = {"error": "Refused.", "code": "400"}
        server = FakeServer(writes=[{"region": "r1"}, (400, None, error)])
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 1
        assert "was not fully written, stopped at 'B'" in result.output
        assert "Stored before the failure: A." in result.output

    def test_a_write_with_no_answer_is_a_failure_one_key_at_a_time(self):
        dropped = aiohttp.ClientConnectionError("connection dropped")
        server = FakeServer(writes=[{"region": "r1"}, dropped])
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 1
        assert [p["secretKey"] for p in server.puts] == ["A", "B"]
        assert "got no answer from Pipecat Cloud" in result.output
        assert "Stored before the failure: A." in result.output
        assert "successfully" not in result.output

    def test_a_first_key_with_no_answer_does_not_claim_nothing_was_stored(self):
        dropped = aiohttp.ClientConnectionError("connection dropped")
        server = FakeServer(writes=[dropped])
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 1
        assert "may not have been stored" in result.output
        assert "No keys were stored" not in _flat(result.output)
        assert "Fix the error" not in _flat(result.output)
        assert "Run the same command again to finish it." in _flat(result.output)

    def test_a_first_key_a_server_failed_on_does_not_claim_nothing_was_stored(self):
        failed = {"error": "Something went wrong.", "code": "500"}
        server = FakeServer(writes=[(500, None, failed)])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 1
        assert "No keys were stored" not in _flat(result.output)

    def test_a_first_key_refused_stored_nothing_and_says_so(self):
        error = {"error": "Refused.", "code": "400"}
        server = FakeServer(writes=[(400, None, error)])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 1
        assert "No keys were stored." in _flat(result.output)


class TestRollingUpdate:
    """The read that offers the bulk form comes from a new server, the write from an old one."""

    OLD_POD = {}
    REFUSED = {"error": "Invalid parameters", "code": "400"}

    def test_an_old_servers_400_goes_on_a_key_at_a_time_and_says_nothing(self):
        server = FakeServer(BULK, writes=[(400, None, self.REFUSED, self.OLD_POD)])
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 0
        assert len(server.puts) == 4
        assert "secrets" in server.puts[0]
        assert [p["secretKey"] for p in server.puts[1:]] == ["A", "B", "C"]
        assert "Invalid parameters" not in result.output
        assert "successfully" in result.output

    def test_an_old_servers_413_on_a_body_past_its_limit_does_too(self):
        too_large = {"error": "Payload too large", "code": "413"}
        server = FakeServer(BULK, writes=[(413, None, too_large, self.OLD_POD)])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 0
        assert [p["secretKey"] for p in server.puts[1:]] == ["A", "B"]

    def test_a_400_from_a_server_that_takes_it_is_a_refusal_with_nothing_more_sent(self):
        server = FakeServer(BULK, writes=[(400, None, self.REFUSED, BULK)])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 1
        assert len(server.puts) == 1
        assert "Invalid parameters" in result.output

    @pytest.mark.parametrize("status", [403, 404, 409, 429, 500, 502, 503, 504])
    def test_another_failure_with_no_header_is_not_taken_for_an_old_server(self, status):
        refused = {"error": "Not allowed.", "code": str(status)}
        server = FakeServer(BULK, writes=[(status, None, refused, self.OLD_POD)])
        result = _set(server, "A=1", "B=2")

        assert result.exit_code == 1
        assert len(server.puts) == 1
        assert "Not allowed." in result.output

    def test_a_failure_part_way_after_going_on_says_how_far_it_got(self):
        refused = {"error": "Refused.", "code": "400"}
        server = FakeServer(
            BULK,
            writes=[
                (400, None, self.REFUSED, self.OLD_POD),
                {"region": "r1"},
                (400, None, refused),
            ],
        )
        result = _set(server, "A=1", "B=2", "C=3")

        assert result.exit_code == 1
        assert "Stored before the failure: A." in _flat(result.output)


class TestConfirmation:
    """Without `--skip`, what the command shows of the values before it asks."""

    SECRET = "s3cretvalue"

    def _run(self, server: FakeServer):
        with (
            server.install(),
            patch(
                "pipecatcloud.cli.commands.secrets.region_error", new=AsyncMock(return_value=None)
            ),
        ):
            return runner.invoke(
                entrypoint_cli_typer,
                [
                    "--output",
                    "plain",
                    "secrets",
                    "set",
                    "my-set",
                    f"A={self.SECRET}",
                    "B=abc",
                    "-r",
                    "eu-central",
                    "-o",
                    "acme",
                ],
            )

    def test_with_no_terminal_it_fails_before_printing_any_of_them(self):
        server = FakeServer(BULK)
        result = self._run(server)

        assert result.exit_code == 2
        assert "--skip" in result.output
        assert "Secrets to create" not in result.output
        assert self.SECRET[:5] not in result.output
        assert "abc" not in result.output
        assert server.puts == []

    def test_at_a_terminal_it_shows_the_keys_and_a_mask_of_no_length(self):
        server = FakeServer(BULK)
        with (
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=True),
            patch(
                "pipecatcloud.cli.commands.secrets.questionary.confirm",
                return_value=MagicMock(ask_async=AsyncMock(return_value=True)),
            ),
        ):
            result = self._run(server)

        assert result.exit_code == 0
        assert "Secrets to create" in result.output
        assert self.SECRET[:5] not in result.output
        assert "abc" not in result.output
        # Values of 11 and 3 characters show the same.
        assert result.output.count("*****") == 2
        assert "******" not in result.output
        assert len(server.puts) == 1


def test_a_write_with_no_answer_is_a_failure_in_one_request():
    dropped = aiohttp.ClientConnectionError("connection dropped")
    server = FakeServer(BULK, writes=[dropped])
    result = _set(server, "A=1", "B=2")

    assert result.exit_code == 1
    assert len(server.puts) == 1
    assert "got no answer from Pipecat Cloud" in result.output
    assert "successfully" not in result.output


@pytest.mark.asyncio
class TestCapabilityIsRead:
    @staticmethod
    def _session(status: int, headers: dict):
        response = MagicMock(ok=status < 400, status=status, reason="x")
        response.headers = headers
        response.json = AsyncMock(return_value={"secrets": []})
        response.raise_for_status = MagicMock(side_effect=_http_error(status))
        session = MagicMock()
        session.request = AsyncMock(return_value=response)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    async def _read(self, status: int, headers: dict) -> _API:
        api = _API("t", is_cli=True)
        with patch.object(aiohttp, "ClientSession", return_value=self._session(status, headers)):
            await api.secrets_get(org="o", secret_set="s")
        return api

    async def test_from_a_set_found(self):
        assert (await self._read(200, BULK)).secrets_bulk_write is True

    async def test_from_a_set_not_found(self):
        assert (await self._read(404, BULK)).secrets_bulk_write is True

    async def test_a_response_without_it_means_no(self):
        assert (await self._read(200, {})).secrets_bulk_write is False
        assert (await self._read(404, {})).secrets_bulk_write is False

    async def test_a_failed_read_means_no(self):
        api = _API("t", is_cli=True)
        api.secrets_bulk_write = True
        with (
            patch.object(aiohttp, "ClientSession", return_value=self._session(500, {})),
            patch("pipecatcloud._utils.console_utils.console"),
        ):
            _, error = await api.secrets_get(org="o", secret_set="s")
        assert error is not None
        assert api.secrets_bulk_write is False

    async def test_a_later_read_without_it_forgets_it(self):
        api = _API("t", is_cli=True)
        with patch.object(aiohttp, "ClientSession", return_value=self._session(200, BULK)):
            await api.secrets_get(org="o", secret_set="s")
        assert api.secrets_bulk_write is True
        with patch.object(aiohttp, "ClientSession", return_value=self._session(200, {})):
            await api.secrets_get(org="o", secret_set="s")
        assert api.secrets_bulk_write is False


def test_a_client_that_has_read_nothing_says_no():
    assert _API("t", is_cli=True).secrets_bulk_write is False
