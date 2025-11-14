"""
Unit tests for Krisp VIVA feature.

Tests follow AAA pattern and focus on behaviors/outcomes rather than implementation details.
Covers data model, TOML parsing, CLI commands, API integration, and validation.
"""

from unittest.mock import AsyncMock, patch

import pytest

# Import from source, not installed package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import (
    DeployConfigParams,
    KrispVivaConfig,
    load_deploy_config_file,
)
from pipecatcloud.api import _API
from pipecatcloud.constants import KRISP_VIVA_MODELS


class TestKrispVivaDataModel:
    """Test the KrispVivaConfig data model."""

    def test_default_is_disabled(self):
        """When not specified, Krisp VIVA should default to disabled."""
        # Arrange & Act
        config = KrispVivaConfig()

        # Assert
        assert config.audio_filter is None

    def test_can_set_tel_model(self):
        """Tel audio filter model can be explicitly set."""
        # Arrange & Act
        config = KrispVivaConfig(audio_filter="tel")

        # Assert
        assert config.audio_filter == "tel"

    def test_can_set_pro_model(self):
        """Pro audio filter model can be explicitly set."""
        # Arrange & Act
        config = KrispVivaConfig(audio_filter="pro")

        # Assert
        assert config.audio_filter == "pro"

    def test_rejects_invalid_model(self):
        """Invalid audio filter models should be rejected."""
        # Arrange & Act & Assert
        with pytest.raises(ValueError, match="audio_filter must be one of"):
            KrispVivaConfig(audio_filter="invalid-model")

    def test_validation_uses_constants(self):
        """Validation should use KRISP_VIVA_MODELS constant."""
        # Arrange & Act & Assert
        with pytest.raises(ValueError) as exc_info:
            KrispVivaConfig(audio_filter="bad-model")

        # Assert error message includes the models from constants
        error_message = str(exc_info.value)
        for model in KRISP_VIVA_MODELS:
            assert model in error_message

    def test_to_dict_when_disabled(self):
        """Dictionary representation when disabled should have null audio_filter."""
        # Arrange
        config = KrispVivaConfig()

        # Act
        result = config.to_dict()

        # Assert
        assert result == {"audio_filter": None}

    def test_to_dict_when_enabled(self):
        """Dictionary representation when enabled should include the model."""
        # Arrange
        config = KrispVivaConfig(audio_filter="tel")

        # Act
        result = config.to_dict()

        # Assert
        assert result == {"audio_filter": "tel"}


class TestDeployConfigIntegration:
    """Test DeployConfigParams integration with Krisp VIVA."""

    def test_default_krisp_viva_is_disabled(self):
        """When not specified, Krisp VIVA should default to disabled."""
        # Arrange & Act
        config = DeployConfigParams()

        # Assert
        assert config.krisp_viva is not None
        assert config.krisp_viva.audio_filter is None

    def test_can_enable_krisp_viva(self):
        """Krisp VIVA can be explicitly enabled with a model."""
        # Arrange & Act
        config = DeployConfigParams(krisp_viva=KrispVivaConfig(audio_filter="tel"))

        # Assert
        assert config.krisp_viva.audio_filter == "tel"

    def test_krisp_viva_included_in_dict_representation(self):
        """Dictionary representation should include Krisp VIVA configuration."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", krisp_viva=KrispVivaConfig(audio_filter="pro")
        )

        # Act
        result = config.to_dict()

        # Assert
        assert "krisp_viva" in result
        assert result["krisp_viva"]["audio_filter"] == "pro"

    def test_krisp_viva_preserves_other_settings(self):
        """Adding Krisp VIVA should not affect other configuration settings."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            enable_krisp=True,
            enable_managed_keys=True,
            krisp_viva=KrispVivaConfig(audio_filter="tel"),
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["agent_name"] == "test-agent"
        assert result["image"] == "test:latest"
        assert result["enable_krisp"] is True
        assert result["enable_managed_keys"] is True
        assert result["krisp_viva"]["audio_filter"] == "tel"


class TestTOMLConfiguration:
    """Test TOML configuration file parsing with Krisp VIVA."""

    @pytest.fixture
    def temp_config_file(self, tmp_path):
        """Create a temporary TOML config file."""
        config_path = tmp_path / "pcc-deploy.toml"
        return config_path

    def test_loads_krisp_viva_tel_from_toml(self, temp_config_file):
        """TOML file with Krisp VIVA tel model should be parsed correctly."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"

        [krisp_viva]
        audio_filter = "tel"
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.krisp_viva.audio_filter == "tel"

    def test_loads_krisp_viva_pro_from_toml(self, temp_config_file):
        """TOML file with Krisp VIVA pro model should be parsed correctly."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"

        [krisp_viva]
        audio_filter = "pro"
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.krisp_viva.audio_filter == "pro"

    def test_rejects_invalid_model_from_toml(self, temp_config_file):
        """TOML file with invalid Krisp VIVA model should raise error."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"

        [krisp_viva]
        audio_filter = "invalid-model"
        """
        temp_config_file.write_text(config_content)

        # Act & Assert
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            with pytest.raises(Exception):  # ConfigFileError wraps ValueError
                load_deploy_config_file()

    def test_defaults_when_not_in_toml(self, temp_config_file):
        """TOML file without Krisp VIVA should default to disabled."""
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
        assert config.krisp_viva.audio_filter is None

    def test_preserves_other_settings_with_krisp_viva(self, temp_config_file):
        """Krisp VIVA setting should not interfere with other TOML settings."""
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

        [krisp_viva]
        audio_filter = "pro"
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
        assert config.krisp_viva.audio_filter == "pro"


class TestAPIIntegration:
    """Test API client integration with Krisp VIVA."""

    @pytest.fixture
    def api_client(self):
        """Create an API client instance."""
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_deploy_payload_includes_krisp_viva_tel(self, api_client):
        """Deploy payload should include krispViva with tel model."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            krisp_viva=KrispVivaConfig(audio_filter="tel"),
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "krispViva" in payload
            assert payload["krispViva"]["audioFilter"] == "tel"

    @pytest.mark.asyncio
    async def test_deploy_payload_includes_krisp_viva_pro(self, api_client):
        """Deploy payload should include krispViva with pro model."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            krisp_viva=KrispVivaConfig(audio_filter="pro"),
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "krispViva" in payload
            assert payload["krispViva"]["audioFilter"] == "pro"

    @pytest.mark.asyncio
    async def test_update_payload_includes_krisp_viva(self, api_client):
        """Update payload should include krispViva field."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            krisp_viva=KrispVivaConfig(audio_filter="tel"),
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=True)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "krispViva" in payload
            assert payload["krispViva"]["audioFilter"] == "tel"

    @pytest.mark.asyncio
    async def test_disabled_krisp_viva_sends_empty_object(self, api_client):
        """When Krisp VIVA is disabled, API should send empty krispViva object."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", krisp_viva=KrispVivaConfig()
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "krispViva" in payload
            # None values are filtered out by remove_none_values, leaving empty dict
            assert payload["krispViva"] == {}


class TestAgentStatusDisplay:
    """Test agent status command displays Krisp VIVA status."""

    @pytest.fixture
    def mock_api(self):
        """Mock the API agent method."""
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            yield mock_api

    def test_displays_enabled_krisp_viva_tel(self, mock_api, capsys):
        """Agent status should display when Krisp VIVA is enabled with tel model."""
        # Arrange
        from pipecatcloud.cli.commands.agent import status

        mock_api.agent = AsyncMock(
            return_value=(
                {
                    "ready": True,
                    "deployment": {"manifest": {"spec": {}}},
                    "krispViva": {"audioFilter": "tel"},
                    "autoScaling": {
                        "minReplicas": 0,
                        "maxReplicas": 1,
                    },
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Krisp VIVA" in captured.out
        assert "Enabled" in captured.out
        assert "tel" in captured.out

    def test_displays_enabled_krisp_viva_pro(self, mock_api, capsys):
        """Agent status should display when Krisp VIVA is enabled with pro model."""
        # Arrange
        from pipecatcloud.cli.commands.agent import status

        mock_api.agent = AsyncMock(
            return_value=(
                {
                    "ready": True,
                    "deployment": {"manifest": {"spec": {}}},
                    "krispViva": {"audioFilter": "pro"},
                    "autoScaling": {
                        "minReplicas": 0,
                        "maxReplicas": 1,
                    },
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Krisp VIVA" in captured.out
        assert "Enabled" in captured.out
        assert "pro" in captured.out

    def test_displays_disabled_krisp_viva(self, mock_api, capsys):
        """Agent status should display when Krisp VIVA is disabled."""
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
                    },
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Krisp VIVA" in captured.out
        assert "Disabled" in captured.out

    def test_handles_null_audio_filter(self, mock_api, capsys):
        """Agent status should handle krispViva with null audioFilter."""
        # Arrange
        from pipecatcloud.cli.commands.agent import status

        mock_api.agent = AsyncMock(
            return_value=(
                {
                    "ready": True,
                    "deployment": {"manifest": {"spec": {}}},
                    "krispViva": {"audioFilter": None},
                    "autoScaling": {
                        "minReplicas": 0,
                        "maxReplicas": 1,
                    },
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Krisp VIVA" in captured.out
        assert "Disabled" in captured.out

    def test_handles_missing_krisp_viva_field(self, mock_api, capsys):
        """Agent status should handle responses without krispViva field."""
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
                    },
                },
                None,
            )
        )

        # Act
        status(agent_name="test-agent", organization="test-org")

        # Assert
        captured = capsys.readouterr()
        assert "Krisp VIVA" in captured.out
        assert "Disabled" in captured.out


class TestBackwardCompatibility:
    """Test backward compatibility with existing deployments."""

    def test_existing_config_without_krisp_viva_works(self):
        """Existing configs without Krisp VIVA field should work unchanged."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", image="test:latest", enable_krisp=True)

        # Act
        result = config.to_dict()

        # Assert
        assert result["enable_krisp"] is True
        assert result["krisp_viva"]["audio_filter"] is None

    def test_api_response_without_krisp_viva_doesnt_crash(self):
        """Agent status should handle API responses without Krisp VIVA field."""
        # Arrange
        data = {"ready": True, "deployment": {"manifest": {"spec": {"image": "test:latest"}}}}

        # Act - simulate the logic from agent.py
        krisp_viva = data.get("krispViva")
        krisp_viva_status = "[dim]Disabled[/dim]"

        if krisp_viva and isinstance(krisp_viva, dict):
            audio_filter = krisp_viva.get("audioFilter")
            if audio_filter:
                krisp_viva_status = f"[green]Enabled ({audio_filter})[/green]"

        # Assert
        assert krisp_viva_status == "[dim]Disabled[/dim]"

    @pytest.fixture
    def temp_legacy_config(self, tmp_path):
        """Create a legacy TOML config without Krisp VIVA."""
        config_path = tmp_path / "pcc-deploy.toml"
        config_content = """
        agent_name = "legacy-agent"
        image = "legacy:latest"
        enable_krisp = true
        enable_managed_keys = true

        [scaling]
        min_agents = 1
        max_agents = 5
        """
        config_path.write_text(config_content)
        return config_path

    def test_legacy_toml_loads_with_defaults(self, temp_legacy_config):
        """Legacy TOML files should load with Krisp VIVA defaulting to disabled."""
        # Arrange & Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_legacy_config)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.agent_name == "legacy-agent"
        assert config.enable_krisp is True
        assert config.enable_managed_keys is True
        assert config.krisp_viva.audio_filter is None


class TestErrorHandling:
    """Test error handling and edge cases."""

    @pytest.mark.asyncio
    async def test_api_error_during_deploy_with_krisp_viva(self):
        """API errors during deployment with Krisp VIVA should be handled properly."""
        # Arrange
        api = _API(token="test-token", is_cli=True)
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            krisp_viva=KrispVivaConfig(audio_filter="tel"),
        )

        with patch.object(api, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.side_effect = Exception("Network error")

            # Act & Assert
            with pytest.raises(Exception, match="Network error"):
                await api._deploy(config, "test-org", update=False)

    def test_simultaneous_krisp_and_krisp_viva(self):
        """Both Krisp and Krisp VIVA can be enabled simultaneously."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            enable_krisp=True,
            krisp_viva=KrispVivaConfig(audio_filter="tel"),
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["enable_krisp"] is True
        assert result["krisp_viva"]["audio_filter"] == "tel"

    def test_all_features_enabled_together(self):
        """Krisp, Managed Keys, and Krisp VIVA can all be enabled together."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            enable_krisp=True,
            enable_managed_keys=True,
            krisp_viva=KrispVivaConfig(audio_filter="pro"),
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["enable_krisp"] is True
        assert result["enable_managed_keys"] is True
        assert result["krisp_viva"]["audio_filter"] == "pro"


class TestConstantsSynchronization:
    """Test that constants are properly synchronized and used."""

    def test_all_models_in_constants_are_valid(self):
        """All models in KRISP_VIVA_MODELS should be accepted."""
        # Arrange & Act & Assert
        for model in KRISP_VIVA_MODELS:
            config = KrispVivaConfig(audio_filter=model)
            assert config.audio_filter == model

    def test_constants_not_empty(self):
        """KRISP_VIVA_MODELS should not be empty."""
        # Arrange & Act & Assert
        assert len(KRISP_VIVA_MODELS) > 0

    def test_constants_contain_expected_models(self):
        """KRISP_VIVA_MODELS should contain expected default models."""
        # Arrange & Act & Assert
        assert "tel" in KRISP_VIVA_MODELS
        assert "pro" in KRISP_VIVA_MODELS
