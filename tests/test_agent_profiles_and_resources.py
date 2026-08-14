"""
Unit tests for enterprise sizing (PCC-1063): the --resources deploy flag,
the [resources] pcc-deploy.toml section, and the agent-profiles API methods.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import (
    DeployConfigParams,
    ResourcesConfig,
    load_deploy_config_file,
    parse_resources_option,
)
from pipecatcloud.api import _API
from pipecatcloud.exception import ConfigFileError


class TestResourcesConfig:
    """ResourcesConfig validates k8s quantities and pairing."""

    def test_default_is_unset(self):
        config = ResourcesConfig()
        assert not config.is_set()

    @pytest.mark.parametrize(
        "cpu,memory",
        [("2", "4Gi"), ("500m", "1Gi"), ("1.5", "512Mi"), ("4", "8G")],
    )
    def test_accepts_valid_quantities(self, cpu, memory):
        config = ResourcesConfig(cpu=cpu, memory=memory)
        assert config.is_set()
        assert config.to_dict() == {"cpu": cpu, "memory": memory}

    @pytest.mark.parametrize(
        "cpu,memory",
        [("two", "4Gi"), ("2", "lots"), ("-1", "4Gi"), ("2", "4GiB"), ("", "4Gi")],
    )
    def test_rejects_invalid_quantities(self, cpu, memory):
        with pytest.raises(ValueError, match="quantity"):
            ResourcesConfig(cpu=cpu, memory=memory)

    def test_rejects_partial_pair(self):
        with pytest.raises(ValueError, match="both"):
            ResourcesConfig(cpu="2")
        with pytest.raises(ValueError, match="both"):
            ResourcesConfig(memory="4Gi")


class TestParseResourcesOption:
    """--resources cpu=X,memory=Y parsing."""

    def test_parses_valid_value(self):
        parsed = parse_resources_option("cpu=2,memory=4Gi")
        assert parsed.to_dict() == {"cpu": "2", "memory": "4Gi"}

    def test_order_does_not_matter_and_spaces_tolerated(self):
        parsed = parse_resources_option("memory=4Gi, cpu=500m")
        assert parsed.to_dict() == {"cpu": "500m", "memory": "4Gi"}

    @pytest.mark.parametrize(
        "value,match",
        [
            ("cpu=2", "requires exactly"),  # missing memory
            ("memory=4Gi", "requires exactly"),  # missing cpu
            ("cpu=2,memory=4Gi,gpu=1", "requires exactly"),  # unknown key
            ("cpu=,memory=4Gi", "Malformed"),  # empty value
            ("2,4Gi", "Malformed"),  # no keys
            ("cpu=two,memory=4Gi", "quantity"),  # bad quantity, specific message
            ("", "Malformed"),
        ],
    )
    def test_rejects_malformed_values_with_specific_errors(self, value, match):
        # #187 review: the specific reason must survive to the caller — the
        # deploy command prints str(e), not a generic usage error.
        with pytest.raises(ValueError, match=match):
            parse_resources_option(value)


class TestDeployConfigMutualExclusion:
    """agent_profile and resources are mutually exclusive."""

    def test_profile_alone_ok(self):
        config = DeployConfigParams(agent_profile="agent-1x")
        assert config.agent_profile == "agent-1x"

    def test_resources_alone_ok(self):
        config = DeployConfigParams(resources=ResourcesConfig(cpu="2", memory="4Gi"))
        assert config.resources.is_set()

    def test_both_rejected(self):
        with pytest.raises(ValueError, match="agent_profile.*resources"):
            DeployConfigParams(
                agent_profile="agent-1x",
                resources=ResourcesConfig(cpu="2", memory="4Gi"),
            )

    def test_round_trips_through_to_dict(self):
        config = DeployConfigParams(resources=ResourcesConfig(cpu="2", memory="4Gi"))
        assert config.to_dict()["resources"] == {"cpu": "2", "memory": "4Gi"}

    def test_to_dict_omits_unset_resources(self):
        config = DeployConfigParams(agent_name="a")
        assert config.to_dict()["resources"] is None


class TestTOMLConfiguration:
    """[resources] can be loaded from pcc-deploy.toml."""

    @pytest.fixture
    def temp_config_file(self, tmp_path):
        return tmp_path / "pcc-deploy.toml"

    def test_loads_resources_section(self, temp_config_file):
        temp_config_file.write_text(
            """
agent_name = "my-agent"
image = "test:latest"

[resources]
cpu = "2"
memory = "4Gi"
"""
        )
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        assert config is not None
        assert config.resources.is_set()
        assert config.resources.to_dict() == {"cpu": "2", "memory": "4Gi"}

    def test_absent_section_is_unset(self, temp_config_file):
        temp_config_file.write_text(
            """
agent_name = "my-agent"
image = "test:latest"
"""
        )
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        assert config is not None
        assert not config.resources.is_set()

    def test_profile_and_resources_together_is_config_error(self, temp_config_file):
        temp_config_file.write_text(
            """
agent_name = "my-agent"
image = "test:latest"
agent_profile = "agent-1x"

[resources]
cpu = "2"
memory = "4Gi"
"""
        )
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            with pytest.raises(ConfigFileError, match="agent_profile.*resources"):
                load_deploy_config_file()

    def test_bad_quantity_is_config_error(self, temp_config_file):
        temp_config_file.write_text(
            """
agent_name = "my-agent"
image = "test:latest"

[resources]
cpu = "two"
memory = "4Gi"
"""
        )
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            with pytest.raises(ConfigFileError, match="quantity"):
                load_deploy_config_file()


class TestDeployPayload:
    """API client sends resources in the deploy payload."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_payload_includes_resources(self, api_client):
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            resources=ResourcesConfig(cpu="2", memory="4Gi"),
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}
            await api_client._deploy(config, "test-org", update=False)

            payload = mock_request.call_args[1]["json"]
            assert payload["resources"] == {"cpu": "2", "memory": "4Gi"}
            assert "agentProfile" not in payload

    @pytest.mark.asyncio
    async def test_payload_omits_resources_when_unset(self, api_client):
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", agent_profile="agent-1x"
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}
            await api_client._deploy(config, "test-org", update=False)

            payload = mock_request.call_args[1]["json"]
            assert "resources" not in payload
            assert payload["agentProfile"] == "agent-1x"


class TestAgentProfilesAPI:
    """Agent-profile CRUD methods hit the org-scoped routes."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_list(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"agentProfiles": []}
            result = await api_client._agent_profiles_list("my-org")

            method, url = mock_request.call_args[0][:2]
            assert method == "GET"
            assert url.endswith("/v1/organizations/my-org/agent-profiles")
            assert result == {"agentProfiles": []}

    @pytest.mark.asyncio
    async def test_create(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"api_name": "telephony-large"}
            await api_client._agent_profiles_create(
                api_name="telephony-large",
                display_name="Telephony Large",
                cpu="2",
                memory="4Gi",
                org="my-org",
            )

            method, url = mock_request.call_args[0][:2]
            assert method == "POST"
            assert url.endswith("/v1/organizations/my-org/agent-profiles")
            assert mock_request.call_args[1]["json"] == {
                "apiName": "telephony-large",
                "displayName": "Telephony Large",
                "resources": {"cpu": "2", "memory": "4Gi"},
            }

    @pytest.mark.asyncio
    async def test_update_sizing(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            await api_client._agent_profiles_update(
                api_name="telephony-large", org="my-org", cpu="4", memory="8Gi"
            )

            method, url = mock_request.call_args[0][:2]
            assert method == "PATCH"
            assert url.endswith("/v1/organizations/my-org/agent-profiles/telephony-large")
            assert mock_request.call_args[1]["json"] == {"resources": {"cpu": "4", "memory": "8Gi"}}

    @pytest.mark.asyncio
    async def test_disable(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {}
            await api_client._agent_profiles_update(
                api_name="telephony-large", org="my-org", enabled=False
            )

            assert mock_request.call_args[1]["json"] == {"enabled": False}
