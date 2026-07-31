"""Tests for the machine-readable JSON output mode (PCC-1064).

Contract: with --output json, stdout carries exactly one JSON object; all
human-facing output goes to stderr; failures exit non-zero, with API errors
also emitted as {"error": ...} on stdout.
"""

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

AGENT_LIST_PAYLOAD = [
    {
        "name": "site-survey",
        "region": "us-east-1",
        "id": "4bb223c4-9f31-44f5-9b3c-c6b8ea9c3a01",
        "activeDeploymentId": "7c9d1e55-8a76-4a2b-b6ce-2f0d4a9be777",
        "createdAt": "2026-07-01T00:00:00.000Z",
        "updatedAt": "2026-07-20T00:00:00.000Z",
    }
]

AGENT_STATUS_PAYLOAD = {
    "ready": True,
    "activeSessionCount": 2,
    "deployment": {"manifest": {"spec": {"image": "registry.example.com/img:1"}}},
    "activeDeploymentId": "dep-1",
    "createdAt": "2026-01-01T00:00:00.000Z",
    "updatedAt": "2026-01-01T00:00:00.000Z",
    "autoScaling": {"minReplicas": 0, "maxReplicas": 5},
    "errors": [],
}


class TestJsonMode:
    def test_agent_list_emits_single_json_object_on_stdout(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agents = AsyncMock(return_value=(AGENT_LIST_PAYLOAD, None))
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "agent", "list"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload == {"agents": AGENT_LIST_PAYLOAD}

    def test_agent_status_round_trips_payload(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agent = AsyncMock(return_value=(AGENT_STATUS_PAYLOAD, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["--output", "json", "agent", "status", "site-survey"]
            )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == AGENT_STATUS_PAYLOAD

    def test_human_chatter_goes_to_stderr(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agents = AsyncMock(return_value=(AGENT_LIST_PAYLOAD, None))
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "agent", "list"])
        # The status line ("Fetching agents...") must not pollute stdout
        assert "Fetching agents" not in result.stdout
        assert "Fetching agents" in result.stderr

    def test_api_error_emits_error_object_and_exits_nonzero(self):
        api_error = {"code": "500", "error": "boom"}
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            # The command sees (None, error); the real wrapper prints via
            # API.print_error, which the mock replaces, so assert on the
            # spend-limit path below for the stdout error object instead.
            mock_api.agents = AsyncMock(return_value=(None, api_error))
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "agent", "list"])
        assert result.exit_code == 1

    def test_spend_limit_json_flag_is_alias_for_json_mode(self):
        payload = {"limitCents": 5000, "currentSpendCents": 100}
        with patch("pipecatcloud.cli.commands.spend_limit.API") as mock_api:
            mock_api.bubble_error.return_value.spend_limit_get = AsyncMock(
                return_value=(payload, None)
            )
            result = runner.invoke(entrypoint_cli_typer, ["spend-limit", "show", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == payload

    def test_spend_limit_json_error_object_on_stdout(self):
        api_error = {"code": "500", "error": "boom"}
        with patch("pipecatcloud.cli.commands.spend_limit.API") as mock_api:
            mock_api.bubble_error.return_value.spend_limit_get = AsyncMock(
                return_value=(None, api_error)
            )
            result = runner.invoke(entrypoint_cli_typer, ["spend-limit", "show", "--json"])
        assert result.exit_code == 1
        assert json.loads(result.stdout) == {"error": api_error}

    def test_print_error_emits_json_error_in_json_mode(self, capsys):
        """The _API.print_error hook writes {"error": ...} to stdout in json mode."""
        from pipecatcloud._utils.console_utils import OutputMode, console
        from pipecatcloud.api import _API

        console.set_output_mode(OutputMode.json)
        api = _API(token="test-token", is_cli=True)
        api.error = {"code": "429", "error": "rate limited"}
        api.print_error()
        captured = capsys.readouterr()
        assert json.loads(captured.out) == {"error": {"code": "429", "error": "rate limited"}}
        # Human rendering went to stderr
        assert "rate limited" in captured.err

    def test_regions_list_json(self):
        regions = [{"code": "us-west-2", "display_name": "US West (Oregon)"}]
        with patch(
            "pipecatcloud.cli.commands.regions.get_regions",
            new_callable=AsyncMock,
            return_value=regions,
        ):
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "regions", "list"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"regions": regions}


class TestJsonEmptyResults:
    """Empty result sets must emit a well-formed JSON payload, not zero bytes.

    The pre-existing empty-result early returns print to the console (stderr
    in json mode); if the json branch sits below them, stdout is empty and
    `jq` errors on the output of a command that exited 0.
    """

    def test_build_list_empty(self):
        with patch("pipecatcloud.cli.commands.build.API") as mock_api:
            mock_api.build_list = AsyncMock(return_value=({"builds": [], "total": 0}, None))
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "build", "list"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"builds": [], "total": 0}

    def test_regions_list_empty(self):
        with patch(
            "pipecatcloud.cli.commands.regions.get_regions",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "regions", "list"])
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"regions": []}

    def test_properties_list_empty(self):
        with patch("pipecatcloud.cli.commands.organizations.API") as mock_api:
            mock_api.properties = AsyncMock(return_value=(None, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["--output", "json", "organizations", "properties", "list"],
            )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {}

    def test_properties_schema_empty(self):
        with patch("pipecatcloud.cli.commands.organizations.API") as mock_api:
            mock_api.properties_schema = AsyncMock(return_value=(None, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["--output", "json", "organizations", "properties", "schema"],
            )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {}


class TestWhoamiJson:
    """whoami exercises the real _API wrapper, which is where double-emission
    of {"error": ...} objects can occur (the wrapper reports errors itself)."""

    @staticmethod
    def _fake_base_request(responses: dict):
        """Route _base_request by URL suffix; a suffix mapped to an Exception
        sets self.error (as the real _base_request does) and raises."""

        async def fake(self, method, url, **kwargs):
            for suffix, value in responses.items():
                if url.endswith(suffix):
                    if isinstance(value, Exception):
                        self.error = {"code": "500", "error": str(value)}
                        raise value
                    return value
            raise AssertionError(f"unexpected URL in test: {url}")

        return fake

    def test_org_lookup_failure_emits_exactly_one_error_object(self):
        from pipecatcloud.api import _API

        fake = self._fake_base_request(
            {
                "/users": {"user": {"userId": "u1", "emails": []}},
                "/organizations": RuntimeError("org lookup failed"),
            }
        )
        with patch.object(_API, "_base_request", fake):
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "auth", "whoami"])
        assert result.exit_code == 1
        # json.loads fails if stdout contains two concatenated objects
        assert json.loads(result.stdout) == {"error": {"code": "500", "error": "org lookup failed"}}

    def test_nonfatal_daily_key_failure_keeps_stdout_clean(self):
        from pipecatcloud.api import _API

        fake = self._fake_base_request(
            {
                "/daily": RuntimeError("daily key unavailable"),
                "/users": {"user": {"userId": "u1", "emails": []}},
                "/organizations": {
                    "organizations": [{"name": "test-org", "verboseName": "Test Org"}]
                },
            }
        )
        with patch.object(_API, "_base_request", fake):
            result = runner.invoke(entrypoint_cli_typer, ["--output", "json", "auth", "whoami"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["organization"] == {"name": "test-org", "verbose_name": "Test Org"}
        assert payload["dailyApiKey"] is None
        assert "error" not in payload
