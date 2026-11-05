#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Tests for `secrets keys`: an organization's secret keys in a region, with their
fingerprints in full, as text and as JSON. The real client and console run; only
the HTTP call is replaced.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.api import _API
from pipecatcloud.cli.commands.secrets import secrets_cli
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

FINGERPRINT = "9d8c81b29648f231be0073c374ab4660"
RETIRED = "00112233445566778899aabbccddeeff"
ANSWER = {
    "region": "self-hosted-a",
    "status": "active",
    "detail": None,
    "regionChecked": True,
    "activeFingerprint": FINGERPRINT,
    "keys": [
        {
            "fingerprint": RETIRED,
            "state": "retired",
            "regionState": "retired",
            "createdAt": "2026-10-23T10:00:00.000Z",
            "retiredAt": "2026-11-01T10:00:00.000Z",
            "fields": 3,
            "sets": 1,
        },
        {
            "fingerprint": FINGERPRINT,
            "state": "active",
            "regionState": "active",
            "createdAt": "2026-11-01T10:00:00.000Z",
            "retiredAt": None,
            "fields": 100,
            "sets": 2,
        },
    ],
}


@pytest.fixture
def json_mode():
    from pipecatcloud._utils.console_utils import console

    yield
    console._explicit_output_mode = None
    console.file = sys.stdout


def _keys(answer, *args, output: str = "plain"):
    """Run the command with `answer` as the server's reply: a dict, None for a
    404 the client reads as empty, or an exception to raise after setting it."""
    calls = []

    async def fake(api, method, url, **kwargs):
        calls.append((method, url, kwargs))
        if isinstance(answer, tuple):
            status, body, retry_after = answer
            api.error = body
            api.error_status = status
            api.error_retry_after = retry_after
            raise aiohttp.ClientResponseError(MagicMock(), (), status=status)
        return answer

    with (
        patch.object(_API, "_base_request", new=fake),
        patch("pipecatcloud.cli.commands.secrets.region_error", new=AsyncMock(return_value=None)),
    ):
        result = runner.invoke(
            entrypoint_cli_typer,
            ["--output", output, "secrets", "keys", "-r", "self-hosted-a", "-o", "acme", *args],
        )
    return result, calls


class TestSecretsKeys:
    def test_reads_the_region_of_the_organization_given(self):
        result, calls = _keys(ANSWER)

        assert result.exit_code == 0
        assert len(calls) == 1
        method, url, kwargs = calls[0]
        assert method == "GET"
        assert url.endswith("/v1/organizations/acme/regions/self-hosted-a/secret-keys")
        assert kwargs["not_found_is_empty"] is True

    def test_plain_lists_each_key_in_full_for_scripts(self):
        result, _ = _keys(ANSWER)

        assert result.exit_code == 0
        assert "Status: active" in result.output
        assert f"Active fingerprint: {FINGERPRINT}" in result.output
        lines = result.output.splitlines()
        assert "Fingerprint\tState\tRegion reports\tCreated\tRetired\tFields\tSets" in lines
        assert (
            f"{RETIRED}\tretired\tretired\t2026-10-23 10:00:00\t2026-11-01 10:00:00\t3\t1" in lines
        )
        assert f"{FINGERPRINT}\tactive\tactive\t2026-11-01 10:00:00\t\t100\t2" in lines

    def test_json_is_the_servers_answer_alone_on_stdout(self, json_mode):
        result, _ = _keys(ANSWER, output="json")

        assert result.exit_code == 0
        assert json.loads(result.stdout) == ANSWER

    def test_rich_shows_the_fingerprint_whole(self):
        from pipecatcloud._utils.console_utils import console

        try:
            with patch.object(type(console), "is_terminal", new=True):
                result, _ = _keys(ANSWER, output="rich")
        finally:
            console._explicit_output_mode = None

        assert result.exit_code == 0
        assert FINGERPRINT in result.output

    @pytest.mark.parametrize(
        "status,detail",
        [
            ("off", "Managed secrets are off in region 'self-hosted-a'."),
            ("none", "This organization has no secret key in region 'self-hosted-a'."),
            ("pending", "This organization's secret key in region 'self-hosted-a' is being made."),
        ],
    )
    def test_no_keys_says_why_and_is_not_a_failure(self, status, detail):
        result, _ = _keys(
            {
                "region": "self-hosted-a",
                "status": status,
                "detail": detail,
                "regionChecked": True,
                "activeFingerprint": None,
                "keys": [],
            }
        )

        assert result.exit_code == 0
        assert f"Status: {status}" in result.output
        assert "Active fingerprint: none" in result.output
        assert detail in result.output
        assert "Fingerprint\t" not in result.output

    def test_a_region_that_could_not_be_checked_shows_the_record_and_says_so(self):
        detail = "Region 'self-hosted-a' could not be checked, so this is what Pipecat Cloud has on record."
        result, _ = _keys(
            {
                **ANSWER,
                "status": "unknown",
                "regionChecked": False,
                "detail": detail,
                "keys": [{**ANSWER["keys"][1], "regionState": None}],
            }
        )

        assert result.exit_code == 0
        assert "Status: unknown" in result.output
        assert detail in result.output
        assert f"{FINGERPRINT}\tactive\t\t" in result.output

    def test_a_server_without_the_route_says_so_and_exits_1(self):
        result, _ = _keys(None)

        assert result.exit_code == 1
        assert "cannot be shown for region" in result.output

    def test_a_server_error_prints_it_and_exits_1(self):
        result, _ = _keys((503, {"error": "Try later.", "code": "503"}, None))

        assert result.exit_code == 1
        assert "Try later." in result.output

    def test_a_region_is_required(self):
        result = runner.invoke(entrypoint_cli_typer, ["secrets", "keys", "-o", "acme"])

        assert result.exit_code == 2

    def test_a_region_the_organization_cannot_use_stops_before_any_call(self):
        calls = []

        async def fake(api, method, url, **kwargs):
            calls.append(url)
            return ANSWER

        check = AsyncMock(return_value="Invalid region 'bogus'. Valid regions are: us-west")
        with (
            patch.object(_API, "_base_request", new=fake),
            patch("pipecatcloud.cli.commands.secrets.region_error", new=check),
        ):
            result = runner.invoke(
                entrypoint_cli_typer,
                ["--output", "plain", "secrets", "keys", "-r", "bogus", "-o", "acme"],
            )

        assert result.exit_code == 1
        check.assert_awaited_once_with("bogus", org="acme")
        assert calls == []

    def test_it_is_listed_with_the_others(self):
        names = {cmd.name for cmd in secrets_cli.registered_commands}
        assert {"set", "unset", "list", "delete", "reference", "keys"}.issubset(names)
