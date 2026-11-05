#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Tests for the bounded retry on secrets writes (503 with Retry-After) and for
region validation against the organization actually in use.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
import typer
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud import api as api_module
from pipecatcloud._utils import regions
from pipecatcloud.api import _API, SECRETS_WRITE_MAX_RETRIES
from pipecatcloud.cli.commands.secrets import create_set
from pipecatcloud.cli.entry_point import entrypoint_cli_typer


def _http_error(status: int) -> aiohttp.ClientResponseError:
    return aiohttp.ClientResponseError(MagicMock(), (), status=status)


def _fake_request(responses: list):
    """Build a stand-in for `_base_request` that replays `responses` in order.

    Each item is a dict (success body) or (status, retry_after) for a failure,
    mirroring what the real method records on the client before raising.
    """
    calls = []

    async def fake(self, method, url, **kwargs):
        item = responses[len(calls)]
        calls.append((method, url, kwargs.get("json")))
        if isinstance(item, tuple):
            status, retry_after = item
            self.error = {"error": "Unavailable", "code": str(status)}
            self.error_status = status
            self.error_retry_after = retry_after
            raise _http_error(status)
        return item

    return fake, calls


@pytest.fixture
def sleeps():
    with patch.object(api_module.asyncio, "sleep", new=AsyncMock()) as mock_sleep:
        yield mock_sleep


@pytest.fixture
def notices():
    with patch("pipecatcloud._utils.console_utils.console") as mock_console:
        yield mock_console


async def _upsert(api: _API, responses: list):
    fake, calls = _fake_request(responses)
    with patch.object(_API, "_base_request", new=fake):
        result = await api.secrets_upsert(
            data={"secretKey": "K", "secretValue": "v"}, set_name="s", org="o"
        )
    return result, calls


@pytest.mark.asyncio
class TestSecretsWriteRetry:
    async def test_503_then_success_retries_and_tells_user(self, sleeps, notices):
        api = _API("t", is_cli=True)
        (data, error), calls = await _upsert(api, [(503, "5"), {"region": "r1"}])

        assert error is None
        assert data == {"region": "r1"}
        assert len(calls) == 2
        sleeps.assert_awaited_once_with(5)
        notices.print.assert_called_once()
        assert (
            "could not be completed yet. Retrying in 5s (attempt 1 of 3)"
            in (notices.print.call_args[0][0])
        )

    async def test_503_past_the_bound_fails(self, sleeps, notices):
        api = _API("t", is_cli=True)
        (data, error), calls = await _upsert(api, [(503, "5")] * (SECRETS_WRITE_MAX_RETRIES + 1))

        assert data is None
        assert error["code"] == "503"
        assert "Still unavailable after 3 retries" in error["error"]
        assert len(calls) == SECRETS_WRITE_MAX_RETRIES + 1
        assert sleeps.await_count == SECRETS_WRITE_MAX_RETRIES

    async def test_503_without_retry_after_is_not_retried(self, sleeps, notices):
        api = _API("t", is_cli=True)
        (data, error), calls = await _upsert(api, [(503, None), {"region": "r1"}])

        assert data is None
        assert error["code"] == "503"
        assert len(calls) == 1
        sleeps.assert_not_awaited()

    @pytest.mark.parametrize("value", ["soon", "Wed, 21 Oct 2026 07:28:00 GMT", "-1"])
    async def test_unusable_retry_after_is_not_retried(self, sleeps, notices, value):
        api = _API("t", is_cli=True)
        (_, error), calls = await _upsert(api, [(503, value), {}])

        assert error["code"] == "503"
        assert "asked to wait" not in error["error"]
        assert len(calls) == 1
        sleeps.assert_not_awaited()

    async def test_note_keeps_the_servers_text_when_it_is_under_message(self, sleeps, notices):
        fake_responses = [(503, "5")] * (SECRETS_WRITE_MAX_RETRIES + 1)
        fake, _ = _fake_request(fake_responses)

        async def with_message(self, method, url, **kwargs):
            try:
                return await fake(self, method, url, **kwargs)
            finally:
                self.error = {"message": "Try later", "code": "503"}

        api = _API("t", is_cli=True)
        with patch.object(_API, "_base_request", new=with_message):
            _, error = await api.secrets_upsert(
                data={"secretKey": "K", "secretValue": "v"}, set_name="s", org="o"
            )

        assert error["error"].startswith("Try later Still unavailable after 3 retries.")
        assert "None" not in error["error"]

    async def test_short_then_long_retry_after_gets_both_sentences(self, sleeps, notices):
        api = _API("t", is_cli=True)
        (_, error), calls = await _upsert(api, [(503, "5"), (503, "60"), {}])

        assert len(calls) == 2
        sleeps.assert_awaited_once_with(5)
        assert "Still unavailable after 1 retry." in error["error"]
        assert "The region asked to wait 60 seconds" in error["error"]

    async def test_retry_after_above_the_cap_is_not_waited_out_and_says_why(self, sleeps, notices):
        api = _API("t", is_cli=True)
        (_, error), calls = await _upsert(api, [(503, "60"), {}])

        assert error["code"] == "503"
        assert (
            "The region asked to wait 60 seconds, longer than the CLI will wait (30)"
            in (error["error"])
        )
        assert "Run the command again after that." in error["error"]
        assert len(calls) == 1  # not clamped to 30 either: that would be early
        sleeps.assert_not_awaited()
        notices.print.assert_not_called()

    @pytest.mark.parametrize("status", [400, 429, 500, 502])
    async def test_other_statuses_are_not_retried(self, sleeps, notices, status):
        api = _API("t", is_cli=True)
        (_, error), calls = await _upsert(api, [(status, "5"), {}])

        assert error["code"] == str(status)
        assert len(calls) == 1
        sleeps.assert_not_awaited()

    async def test_retry_after_header_is_recorded_from_the_response(self):
        api = _API("t", is_cli=True)
        response = MagicMock(ok=False, status=503, reason="Service Unavailable")
        response.headers = {"Retry-After": "5"}
        response.json = AsyncMock(return_value={"error": "x", "code": "503"})
        response.raise_for_status = MagicMock(side_effect=_http_error(503))
        session = MagicMock()
        session.request = AsyncMock(return_value=response)
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)

        with patch.object(aiohttp, "ClientSession", return_value=session):
            with pytest.raises(aiohttp.ClientResponseError):
                await api._base_request("PUT", "http://x")

        assert api.error_retry_after == "5"


@pytest.mark.asyncio
class TestSecretsSetMidWayFailure:
    async def _run(self, responses: list):
        api = _API("t", is_cli=True)
        fake, calls = _fake_request(responses)
        with (
            patch.object(_API, "_base_request", new=fake),
            patch("pipecatcloud.cli.commands.secrets.API", api),
            patch("pipecatcloud.cli.commands.secrets.console") as secrets_console,
            patch("pipecatcloud._utils.console_utils.console"),
            patch(
                "pipecatcloud.cli.commands.secrets.region_error",
                new=AsyncMock(return_value=None),
            ),
            patch.object(_API, "secrets_get", new=AsyncMock(return_value=(None, None))),
        ):
            self.console = secrets_console
            exit_code = None
            try:
                await create_set.aio(
                    name="s",
                    secrets=["A=1", "B=2", "C=3"],
                    from_file=None,
                    skip_confirm=True,
                    organization="o",
                    region=None,
                )
            except typer.Exit as e:
                exit_code = e.exit_code
        return exit_code, calls

    async def test_retry_on_second_key_continues_with_the_third(self, sleeps):
        exit_code, calls = await self._run(
            [{"region": "r"}, (503, "5"), {"region": "r"}, {"region": "r"}]
        )

        assert exit_code is None
        assert [c[2]["secretKey"] for c in calls] == ["A", "B", "B", "C"]

    async def test_failure_on_second_key_stops_and_leaves_first_stored(self, sleeps):
        exit_code, calls = await self._run(
            [{"region": "r"}] + [(503, "5")] * (SECRETS_WRITE_MAX_RETRIES + 1)
        )

        assert exit_code == 1
        keys = [c[2]["secretKey"] for c in calls]
        assert keys[0] == "A"
        assert set(keys[1:]) == {"B"}
        assert "C" not in keys
        printed = " ".join(str(c) for c in self.console.print.call_args_list)
        assert "stopped at 'B'" in printed
        assert "Stored before the failure: A." in printed
        assert "Wait for the region to become ready, then run the same command again" in printed
        assert "Fix the error" not in printed

    async def test_non_503_failure_says_to_fix_the_error_first(self, sleeps):
        exit_code, calls = await self._run([{"region": "r"}, (400, None)])

        assert exit_code == 1
        assert [c[2]["secretKey"] for c in calls] == ["A", "B"]
        printed = " ".join(str(c) for c in self.console.print.call_args_list)
        assert "Stored before the failure: A." in printed
        assert "Fix the error above, then run the same command again." in printed
        assert "finish it" not in printed


@pytest.mark.asyncio
class TestRegionsPerOrganization:
    async def test_validates_against_the_organization_in_use(self):
        regions._regions_cache = None
        lists = {
            "config-org": [{"code": "us-west", "display_name": "US West"}],
            "other-org": [{"code": "self-hosted-a", "display_name": "Self Hosted A"}],
        }

        async def fetch(org):
            return lists[org], None

        with (
            patch("pipecatcloud._utils.regions.API") as mock_api,
            patch("pipecatcloud._utils.regions.config") as mock_config,
        ):
            mock_api.regions = AsyncMock(side_effect=fetch)
            mock_config.get.return_value = "config-org"

            assert await regions.region_error("self-hosted-a", org="other-org") is None
            assert await regions.region_error("us-west", org="other-org") is not None
            assert await regions.region_error("self-hosted-a") is not None
            assert await regions.region_error("us-west") is None
            assert mock_api.regions.await_count == 2  # one fetch per organization

    async def test_unfetchable_list_still_rejects(self):
        regions._regions_cache = None
        with (
            patch("pipecatcloud._utils.regions.API") as mock_api,
            patch("pipecatcloud._utils.regions.config") as mock_config,
        ):
            mock_api.regions = AsyncMock(return_value=(None, {"code": "500"}))
            mock_config.get.return_value = "config-org"

            assert await regions.region_error("us-west", org="other-org") is not None

    async def test_region_error_fetches_once_and_names_a_failed_fetch(self):
        regions._regions_cache = None
        with (
            patch("pipecatcloud._utils.regions.API") as mock_api,
            patch("pipecatcloud._utils.regions.config") as mock_config,
        ):
            mock_api.regions = AsyncMock(return_value=(None, {"code": "403"}))
            mock_config.get.return_value = "config-org"

            message = await regions.region_error("us-west", org="other-org")

        assert mock_api.regions.await_count == 1
        assert "Could not list the regions of organization 'other-org'" in message
        assert "Invalid" not in message

    async def test_region_error_invalid_region_lists_the_valid_ones(self):
        regions._regions_cache = None
        with (
            patch("pipecatcloud._utils.regions.API") as mock_api,
            patch("pipecatcloud._utils.regions.config") as mock_config,
        ):
            mock_api.regions = AsyncMock(return_value=([{"code": "a", "display_name": "A"}], None))
            mock_config.get.return_value = "config-org"

            assert await regions.region_error("a", org="o") is None
            message = await regions.region_error("x", org="o")

        assert message == "Invalid region 'x'. Valid regions are: a"

    async def test_secrets_set_passes_organization_to_validation(self):
        validate = AsyncMock(return_value=None)
        with (
            patch("pipecatcloud.cli.commands.secrets.API") as mock_api,
            patch("pipecatcloud.cli.commands.secrets.console"),
            patch("pipecatcloud.cli.commands.secrets.region_error", new=validate),
        ):
            mock_api.secrets_get = AsyncMock(return_value=(None, None))
            mock_api.secrets_upsert = AsyncMock(return_value=({"region": "self-hosted-a"}, None))

            await create_set.aio(
                name="s",
                secrets=["A=1"],
                from_file=None,
                skip_confirm=True,
                organization="other-org",
                region="self-hosted-a",
            )

        validate.assert_awaited_once_with("self-hosted-a", org="other-org")


runner = CliRunner()


@pytest.fixture
def json_mode():
    from pipecatcloud._utils.console_utils import console

    yield
    console._explicit_output_mode = None
    console.file = sys.stdout


class TestRegionCommandsPassTheOrganization:
    """Dropping `org=` from any region check must fail a test: the check would
    silently fall back to the config file's organization."""

    @pytest.mark.parametrize(
        "module,args",
        [
            ("secrets", ["secrets", "set", "s", "A=1", "--skip", "-r", "rgn", "-o", "other"]),
            ("secrets", ["secrets", "list", "-r", "rgn", "-o", "other"]),
            (
                "secrets",
                [
                    "secrets",
                    "image-pull-secret",
                    "s",
                    "h",
                    "u:p",
                    "--skip",
                    "-r",
                    "rgn",
                    "-o",
                    "other",
                ],
            ),
            ("secrets", ["secrets", "reference", "s", "-r", "rgn", "-o", "other"]),
            ("secrets", ["secrets", "keys", "-r", "rgn", "-o", "other"]),
            ("agent", ["agent", "list", "-r", "rgn", "-o", "other"]),
            ("deploy", ["deploy", "my-agent", "-r", "rgn", "-o", "other"]),
        ],
    )
    def test_region_checked_against_the_organization_given(self, module, args):
        check = AsyncMock(return_value="stop here")
        with patch(f"pipecatcloud.cli.commands.{module}.region_error", new=check):
            result = runner.invoke(entrypoint_cli_typer, args)

        assert result.exit_code == 1
        check.assert_awaited_once_with("rgn", org="other")

    def test_deploy_architecture_lookup_uses_the_organization_given(self):
        listing = AsyncMock(
            return_value=[
                {"code": "rgn", "display_name": "R", "supported_architectures": ["arm64"]}
            ]
        )
        with (
            patch(
                "pipecatcloud.cli.commands.deploy.region_error",
                new=AsyncMock(return_value=None),
            ),
            patch("pipecatcloud.cli.commands.deploy.get_regions", new=listing),
        ):
            result = runner.invoke(
                entrypoint_cli_typer,
                ["deploy", "my-agent", "-r", "rgn", "-o", "other", "--architecture", "amd64"],
            )

        assert result.exit_code == 1
        listing.assert_awaited_once_with(org="other")


class TestFailedRegionFetchOutput:
    """The region list cannot be fetched (the real client and console run; only
    the HTTP call is replaced). The client prints its own error first, then the
    command says it stopped. In json mode stdout carries exactly one document,
    the client's error object, and the human message is on stderr."""

    @staticmethod
    def _refuse_with_403():
        calls = []

        async def refused(self, method, url, **kwargs):
            calls.append(url)
            self.error = {"error": "Not a member of this organization", "code": "403"}
            self.error_status = 403
            raise _http_error(403)

        return refused, calls

    @pytest.mark.parametrize("output", ["plain", "json"])
    def test_unlistable_regions_say_so(self, output, json_mode):
        regions._regions_cache = None
        refused, calls = self._refuse_with_403()
        with patch.object(_API, "_base_request", new=refused):
            result = runner.invoke(
                entrypoint_cli_typer,
                ["--output", output, "agent", "list", "-r", "rgn", "-o", "other"],
            )

        assert result.exit_code == 1
        assert len(calls) == 1  # fetched once
        message = "Could not list the regions of organization 'other'"
        assert message in result.output
        assert "could not be checked. Not continuing." in result.output
        assert "Valid regions are" not in result.output
        # The client's own error comes first.
        assert result.output.index("Not a member") < result.output.index(message)
        if output == "json":
            assert json.loads(result.stdout) == {
                "error": {"error": "Not a member of this organization", "code": "403"}
            }
            assert message in result.stderr
            assert message not in result.stdout
