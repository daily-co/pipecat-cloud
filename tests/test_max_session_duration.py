"""
Unit tests for the --max-session-duration flag on `pcc deploy`.

Covers the data model, TOML parsing, and API payload construction.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams, load_deploy_config_file
from pipecatcloud.api import _API


class TestDeployConfigModel:
    """DeployConfigParams stores and round-trips max_session_duration."""

    def test_default_is_none(self):
        # Arrange & Act
        config = DeployConfigParams()

        # Assert
        assert config.max_session_duration is None

    def test_can_be_set(self):
        # Arrange & Act
        config = DeployConfigParams(max_session_duration=3600)

        # Assert
        assert config.max_session_duration == 3600

    def test_round_trips_through_to_dict(self):
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", max_session_duration=3600)

        # Act
        result = config.to_dict()

        # Assert
        assert result["max_session_duration"] == 3600

    def test_preserves_other_settings(self):
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            max_session_duration=600,
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["agent_name"] == "test-agent"
        assert result["image"] == "test:latest"
        assert result["max_session_duration"] == 600

    @pytest.mark.parametrize("value", [59, 14401, 0, -1])
    def test_rejects_out_of_range(self, value):
        with pytest.raises(ValueError, match="max_session_duration"):
            DeployConfigParams(max_session_duration=value)

    @pytest.mark.parametrize("value", [60, 7200, 14400])
    def test_accepts_boundary_values(self, value):
        config = DeployConfigParams(max_session_duration=value)
        assert config.max_session_duration == value


class TestTOMLConfiguration:
    """max_session_duration can be loaded from pcc-deploy.toml."""

    @pytest.fixture
    def temp_config_file(self, tmp_path):
        config_path = tmp_path / "pcc-deploy.toml"
        return config_path

    def test_loads_from_toml(self, temp_config_file):
        # Arrange
        config_content = """
agent_name = "my-agent"
image = "test:latest"
max_session_duration = 300
"""
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.max_session_duration == 300

    def test_absent_in_toml_is_none(self, temp_config_file):
        # Arrange
        config_content = """
agent_name = "my-agent"
image = "test:latest"
"""
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.max_session_duration is None


class TestAPIPayload:
    """API client sends maxSessionDuration in camelCase."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_payload_includes_max_session_duration(self, api_client):
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", max_session_duration=600
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            payload = mock_request.call_args[1]["json"]
            assert payload["maxSessionDuration"] == 600

    @pytest.mark.asyncio
    async def test_payload_omits_field_when_unset(self, api_client):
        """When max_session_duration is None, remove_none_values strips it."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", image="test:latest")

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            payload = mock_request.call_args[1]["json"]
            assert "maxSessionDuration" not in payload
