"""
Tests for the `whatsapp_auth` deploy setting.

It mirrors `websocket_auth` (a pcc-deploy.toml key, no CLI flag), with three
differences: the value is validated here, the field is sent only when set, and a
request for "token" that the API does not echo back is reported, because an API
without the feature drops the field without an error.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiohttp
import pytest
import typer
from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import (
    DeployConfigParams,
    load_deploy_config_file,
    whatsapp_auth_unconfirmed,
)
from pipecatcloud.api import _API
from pipecatcloud.cli.commands.deploy import _deploy
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()


def _write_toml(tmp_path, extra: str) -> Path:
    path = tmp_path / "pcc-deploy.toml"
    path.write_text(f'agent_name = "my-agent"\nimage = "test:latest"\n{extra}\n')
    return path


class TestConfigModel:
    def test_default_is_none(self):
        assert DeployConfigParams().whatsapp_auth is None

    @pytest.mark.parametrize("value", ["none", "token"])
    def test_accepts_valid_values(self, value):
        config = DeployConfigParams(whatsapp_auth=value)
        assert config.whatsapp_auth == value
        assert config.to_dict()["whatsapp_auth"] == value

    @pytest.mark.parametrize("value", ["", "Token", "basic", "true"])
    def test_rejects_invalid_values(self, value):
        with pytest.raises(ValueError, match="whatsapp_auth must be one of: none, token"):
            DeployConfigParams(whatsapp_auth=value)


class TestToml:
    def test_loads_from_toml(self, tmp_path):
        path = _write_toml(tmp_path, 'whatsapp_auth = "token"')
        with patch("pipecatcloud.cli.config.deploy_config_path", str(path)):
            config = load_deploy_config_file()
        assert config is not None
        assert config.whatsapp_auth == "token"

    def test_absent_is_none(self, tmp_path):
        path = _write_toml(tmp_path, "")
        with patch("pipecatcloud.cli.config.deploy_config_path", str(path)):
            config = load_deploy_config_file()
        assert config is not None
        assert config.whatsapp_auth is None

    def test_invalid_value_is_a_config_error(self, tmp_path):
        from pipecatcloud.exception import ConfigFileError

        path = _write_toml(tmp_path, 'whatsapp_auth = "basic"')
        with patch("pipecatcloud.cli.config.deploy_config_path", str(path)):
            with pytest.raises(ConfigFileError, match="whatsapp_auth must be one of"):
                load_deploy_config_file()


class TestPayload:
    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    async def _payload(self, api_client, config):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}
            await api_client._deploy(config, "test-org", update=False)
        return mock_request.call_args[1]["json"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["none", "token"])
    async def test_includes_field_when_set(self, api_client, value):
        config = DeployConfigParams(agent_name="a", image="t:latest", whatsapp_auth=value)
        assert (await self._payload(api_client, config))["whatsappAuth"] == value

    @pytest.mark.asyncio
    async def test_omits_field_when_unset(self, api_client):
        config = DeployConfigParams(agent_name="a", image="t:latest")
        assert "whatsappAuth" not in await self._payload(api_client, config)

    @pytest.mark.asyncio
    async def test_is_independent_of_websocket_auth(self, api_client):
        config = DeployConfigParams(agent_name="a", image="t:latest", websocket_auth="token")
        payload = await self._payload(api_client, config)
        assert payload["websocketAuth"] == "token"
        assert "whatsappAuth" not in payload


class TestUnconfirmedCheck:
    @pytest.mark.parametrize(
        "response",
        [
            {"status": "OK", "service": {"websocketAuth": "none"}},  # older API: no echo
            {"status": "OK", "service": {"whatsappAuth": "none"}},
            {"status": "OK", "service": {"whatsappAuth": None}},  # unreadable
            {"status": "OK"},
            {},
            None,
        ],
    )
    def test_token_not_echoed_is_unconfirmed(self, response):
        assert whatsapp_auth_unconfirmed("token", response) is True

    def test_token_echoed_is_confirmed(self):
        response = {"status": "OK", "service": {"whatsappAuth": "token"}}
        assert whatsapp_auth_unconfirmed("token", response) is False

    @pytest.mark.parametrize("requested", [None, "none"])
    def test_other_requests_are_never_checked(self, requested):
        assert whatsapp_auth_unconfirmed(requested, {"service": {}}) is False


@pytest.fixture
def deploy_flow():
    """Run `_deploy` as a first deploy and end it when the readiness poll starts.

    The first `API.agent` call is the existing-agent check (none exists); the
    second is the poll's first status read, which raises to stop the flow.
    """
    with (
        patch("pipecatcloud.cli.commands.deploy.API") as mock_api,
        patch("pipecatcloud.cli.commands.deploy.console") as mock_console,
    ):
        mock_api.agent = AsyncMock(side_effect=[(None, None), _StopPoll()])
        mock_console.json_output = False
        yield mock_api, mock_console


class _StopPoll(Exception):
    """Raised by the stubbed status read so a test ends once the poll begins."""


def _warnings(mock_console) -> list[str]:
    return [
        str(call.args[0])
        for call in mock_console.print.call_args_list
        if call.args and "whatsapp_auth" in str(call.args[0])
    ]


class TestDeployWarning:
    async def _run(self, deploy_flow, whatsapp_auth, response):
        mock_api, mock_console = deploy_flow
        mock_api.deploy = AsyncMock(return_value=(response, None))
        params = DeployConfigParams(
            agent_name="my-agent", image="test:latest", whatsapp_auth=whatsapp_auth
        )
        try:
            await _deploy(params, "test-org", force=True)
        except (_StopPoll, typer.Exit):
            pass
        return mock_console

    @pytest.mark.asyncio
    async def test_warns_when_older_api_ignores_token(self, deploy_flow):
        console = await self._run(
            deploy_flow, "token", {"status": "OK", "service": {"websocketAuth": "none"}}
        )
        warnings = _warnings(console)
        assert len(warnings) == 1
        assert "did not confirm" in warnings[0]
        assert "without your public API key" in warnings[0]

    @pytest.mark.asyncio
    async def test_checks_the_update_of_an_existing_agent(self, deploy_flow):
        """An update (and a no-op redeploy, which answers through the same path)
        is checked like a create: an existing agent goes through `update=True`."""
        mock_api, _ = deploy_flow
        mock_api.agent = AsyncMock(side_effect=[({"name": "my-agent"}, None), _StopPoll()])
        console = await self._run(
            deploy_flow,
            "token",
            {"status": "OK", "service": {}, "warning": "No configuration changes detected"},
        )
        assert mock_api.deploy.await_args.kwargs["update"] is True
        assert len(_warnings(console)) == 1

    @pytest.mark.asyncio
    async def test_no_warning_when_api_confirms(self, deploy_flow):
        console = await self._run(
            deploy_flow, "token", {"status": "OK", "service": {"whatsappAuth": "token"}}
        )
        assert _warnings(console) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("requested", [None, "none"])
    async def test_no_warning_when_token_not_requested(self, deploy_flow, requested):
        console = await self._run(deploy_flow, requested, {"status": "OK", "service": {}})
        assert _warnings(console) == []

    @pytest.mark.asyncio
    async def test_warning_does_not_fail_the_deploy(self, deploy_flow):
        """The flow goes on to the readiness poll after warning."""
        mock_api, _ = deploy_flow
        await self._run(deploy_flow, "token", {"status": "OK", "service": {}})
        assert mock_api.agent.await_count == 2


class TestRefusedByTheRegion:
    """A 4xx with a message from the API (the region's operator does not support
    the setting yet) is shown as the API worded it, and the deploy fails."""

    @pytest.mark.asyncio
    async def test_api_message_is_shown(self, capsys):
        message = "WhatsApp token auth is not supported in this region yet"
        client = _API(token="test-token", is_cli=True)

        async def refused(self, method, url, **kwargs):
            self.error = {"error": message, "code": "400"}
            self.error_status = 400
            raise aiohttp.ClientResponseError(None, (), status=400)  # type: ignore[arg-type]

        params = DeployConfigParams(
            agent_name="my-agent", image="test:latest", whatsapp_auth="token"
        )
        with (
            patch("pipecatcloud.cli.commands.deploy.API", client),
            patch.object(_API, "_agent", new=AsyncMock(return_value=None)),
            patch.object(_API, "_base_request", new=refused),
        ):
            with pytest.raises(typer.Exit) as exc:
                await _deploy(params, "test-org", force=True)

        assert exc.value.exit_code == 1
        assert message in capsys.readouterr().out


class TestDeployCommand:
    def _invoke(self, tmp_path, toml_extra, deploy_mock):
        path = _write_toml(tmp_path, f'region = "rgn"\n{toml_extra}')
        with (
            patch("pipecatcloud.cli.commands.deploy._deploy", new=deploy_mock),
            patch(
                "pipecatcloud.cli.commands.deploy.region_error",
                new=AsyncMock(return_value=None),
            ),
        ):
            return runner.invoke(
                entrypoint_cli_typer,
                ["deploy", "--config-file", str(path), "--no-credentials", "--yes"],
            )

    def test_summary_shows_the_setting_and_passes_it_on(self, tmp_path):
        deploy_mock = AsyncMock()
        result = self._invoke(tmp_path, 'whatsapp_auth = "token"', deploy_mock)

        assert result.exit_code == 0, result.output
        assert "WhatsApp auth: token" in result.output
        assert deploy_mock.await_args.args[0].whatsapp_auth == "token"

    def test_summary_omits_the_line_when_unset(self, tmp_path):
        deploy_mock = AsyncMock()
        result = self._invoke(tmp_path, "", deploy_mock)

        assert result.exit_code == 0, result.output
        assert "WhatsApp auth" not in result.output
        assert deploy_mock.await_args.args[0].whatsapp_auth is None

    def test_invalid_value_fails_before_deploying(self, tmp_path):
        deploy_mock = AsyncMock()
        result = self._invoke(tmp_path, 'whatsapp_auth = "basic"', deploy_mock)

        assert result.exit_code == 1
        assert "whatsapp_auth must be one of: none, token" in result.output
        deploy_mock.assert_not_awaited()
