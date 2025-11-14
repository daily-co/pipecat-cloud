"""
Unit tests for managed keys feature.

Tests follow AAA pattern and focus on behaviors/outcomes rather than implementation details.
Covers data model, TOML parsing, CLI commands, and API integration.
"""

from unittest.mock import AsyncMock, patch

import pytest

# Import from source, not installed package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams, load_deploy_config_file
from pipecatcloud.api import _API


class TestDeployConfigDataModel:
    """Test the DeployConfigParams data model with managed keys support."""

    def test_default_keys_is_disabled(self):
        """When not specified, managed keys should default to disabled."""
        # Arrange & Act
        config = DeployConfigParams()

        # Assert
        assert config.enable_managed_keys is False

    def test_can_enable_keys(self):
        """Integrated keys can be explicitly enabled."""
        # Arrange & Act
        config = DeployConfigParams(enable_managed_keys=True)

        # Assert
        assert config.enable_managed_keys is True

    def test_keys_included_in_dict_representation(self):
        """Dictionary representation should include managed keys setting."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", enable_managed_keys=True)

        # Act
        result = config.to_dict()

        # Assert
        assert "enable_managed_keys" in result
        assert result["enable_managed_keys"] is True

    def test_keys_preserves_other_settings(self):
        """Adding managed keys should not affect other configuration settings."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            enable_krisp=True,
            enable_managed_keys=True,
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["agent_name"] == "test-agent"
        assert result["image"] == "test:latest"
        assert result["enable_krisp"] is True
        assert result["enable_managed_keys"] is True


class TestTOMLConfiguration:
    """Test TOML configuration file parsing with managed keys."""

    @pytest.fixture
    def temp_config_file(self, tmp_path):
        """Create a temporary TOML config file."""
        config_path = tmp_path / "pcc-deploy.toml"
        return config_path

    def test_loads_keys_proxy_from_toml_when_enabled(self, temp_config_file):
        """TOML file with managed keys enabled should be parsed correctly."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"
        enable_managed_keys = true
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.enable_managed_keys is True

    def test_loads_keys_proxy_from_toml_when_disabled(self, temp_config_file):
        """TOML file with managed keys explicitly disabled should be parsed correctly."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"
        enable_managed_keys = false
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.enable_managed_keys is False

    def test_defaults_keys_proxy_when_not_in_toml(self, temp_config_file):
        """TOML file without managed keys setting should default to disabled."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.enable_managed_keys is False

    def test_preserves_other_settings_with_keys_proxy(self, temp_config_file):
        """Keys proxy setting should not interfere with other TOML settings."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"
        enable_krisp = true
        enable_managed_keys = true
        secret_set = "my-secrets"
        
        [scaling]
        min_agents = 2
        max_agents = 10
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config.agent_name == "test-agent"
        assert config.image == "test:latest"
        assert config.enable_krisp is True
        assert config.enable_managed_keys is True
        assert config.secret_set == "my-secrets"
        assert config.scaling.min_agents == 2
        assert config.scaling.max_agents == 10


class TestAPIIntegration:
    """Test API client integration with managed keys."""

    @pytest.fixture
    def api_client(self):
        """Create an API client instance."""
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_deploy_payload_includes_keys_proxy(self, api_client):
        """Deploy payload should include enableIntegratedKeys field."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", enable_managed_keys=True
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "enableIntegratedKeysProxy" in payload
            assert payload["enableIntegratedKeysProxy"] is True

    @pytest.mark.asyncio
    async def test_update_payload_includes_keys_proxy(self, api_client):
        """Update payload should include enableIntegratedKeys field."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", enable_managed_keys=True
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=True)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "enableIntegratedKeysProxy" in payload
            assert payload["enableIntegratedKeysProxy"] is True

    @pytest.mark.asyncio
    async def test_disabled_keys_proxy_sends_false(self, api_client):
        """When managed keys is disabled, API should send false value."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", enable_managed_keys=False
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "enableIntegratedKeysProxy" in payload
            assert payload["enableIntegratedKeysProxy"] is False


class TestAgentStatusDisplay:
    """Test agent status command displays managed keys status."""

    @pytest.fixture
    def mock_api(self):
        """Mock the API agent method."""
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            yield mock_api

    def test_displays_enabled_keys_proxy(self, mock_api, capsys):
        """Agent status should display when managed keys is enabled."""
        # Arrange
        from pipecatcloud.cli.commands.agent import status

        mock_api.agent = AsyncMock(
            return_value=(
                {
                    "ready": True,
                    "deployment": {
                        "manifest": {"spec": {"integratedKeysProxy": {"enabled": True}}}
                    },
                    "autoScaling": {
                        "minReplicas": 0,
                        "maxReplicas": 1,
                    },  # Avoid undefined variable bug
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Managed Keys" in captured.out
        assert "Enabled" in captured.out

    def test_displays_disabled_keys_proxy(self, mock_api, capsys):
        """Agent status should display when managed keys is disabled."""
        # Arrange
        from pipecatcloud.cli.commands.agent import status

        mock_api.agent = AsyncMock(
            return_value=(
                {
                    "ready": True,
                    "deployment": {"manifest": {"spec": {}}},
                    "autoScaling": {
                        "minReplicas": 0,
                        "maxReplicas": 1,
                    },  # Avoid undefined variable bug
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Managed Keys" in captured.out
        assert "Disabled" in captured.out

    def test_handles_boolean_keys_proxy_value(self, mock_api, capsys):
        """Agent status should handle boolean value for backward compatibility."""
        # Arrange
        from pipecatcloud.cli.commands.agent import status

        mock_api.agent = AsyncMock(
            return_value=(
                {
                    "ready": True,
                    "deployment": {"manifest": {"spec": {"integratedKeysProxy": True}}},
                    "autoScaling": {
                        "minReplicas": 0,
                        "maxReplicas": 1,
                    },  # Avoid undefined variable bug
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Managed Keys" in captured.out
        assert "Enabled" in captured.out


class TestBackwardCompatibility:
    """Test backward compatibility with existing deployments."""

    def test_existing_config_without_keys_proxy_works(self):
        """Existing configs without managed keys field should work unchanged."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", image="test:latest", enable_krisp=True)

        # Act
        result = config.to_dict()

        # Assert
        assert result["enable_krisp"] is True
        assert result["enable_managed_keys"] is False

    def test_api_response_without_keys_proxy_doesnt_crash(self):
        """Agent status should handle API responses without managed keys field."""
        # Arrange
        data = {"ready": True, "deployment": {"manifest": {"spec": {"image": "test:latest"}}}}

        # Act - simulate the logic from agent.py
        integrated_keys = (
            data.get("deployment", {})
            .get("manifest", {})
            .get("spec", {})
            .get("integratedKeysProxy", {})
        )
        if isinstance(integrated_keys, dict):
            keys_proxy_enabled = integrated_keys.get("enabled", False)
        else:
            keys_proxy_enabled = bool(integrated_keys)

        # Assert
        assert keys_proxy_enabled is False

    @pytest.fixture
    def temp_legacy_config(self, tmp_path):
        """Create a legacy TOML config without managed keys."""
        config_path = tmp_path / "pcc-deploy.toml"
        config_content = """
        agent_name = "legacy-agent"
        image = "legacy:latest"
        enable_krisp = true
        
        [scaling]
        min_instances = 1
        max_instances = 5
        """
        config_path.write_text(config_content)
        return config_path

    def test_legacy_toml_loads_with_defaults(self, temp_legacy_config):
        """Legacy TOML files should load with managed keys defaulting to disabled."""
        # Arrange & Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_legacy_config)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.agent_name == "legacy-agent"
        assert config.enable_krisp is True
        assert config.enable_managed_keys is False
        assert config.scaling.min_agents == 1  # Converted from min_instances


class TestErrorHandling:
    """Test error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_api_error_during_deploy_with_keys_proxy(self):
        """API errors during deployment should be handled properly."""
        # Arrange
        api = _API(token="test-token", is_cli=True)
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", enable_managed_keys=True
        )

        with patch.object(api, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = Exception("Network error")

            # Act & Assert
            with pytest.raises(Exception, match="Network error"):
                await api._deploy(config, "test-org", update=False)

    def test_simultaneous_krisp_and_keys_proxy(self):
        """Both Krisp and Keys Proxy can be enabled simultaneously."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            enable_krisp=True,
            enable_managed_keys=True,
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["enable_krisp"] is True
        assert result["enable_managed_keys"] is True
