"""
Unit tests for deployment functionality.

Tests follow AAA pattern and cover deployment config, TOML parsing,
and region support for deploy commands.
"""

from unittest.mock import AsyncMock, patch
from pathlib import Path

import pytest

# Import from source, not installed package
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams, load_deploy_config_file
from pipecatcloud.constants import REGIONS


class TestDeployConfigRegion:
    """Test deployment configuration with region support."""

    def test_default_region_is_none(self):
        """When not specified, region should default to None."""
        # Arrange & Act
        config = DeployConfigParams()

        # Assert
        assert config.region is None

    def test_can_set_region(self):
        """Region can be explicitly set."""
        # Arrange & Act
        config = DeployConfigParams(region="eu")

        # Assert
        assert config.region == "eu"

    def test_region_included_in_dict_representation(self):
        """Dictionary representation should include region setting."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", region="ap")

        # Act
        result = config.to_dict()

        # Assert
        assert "region" in result
        assert result["region"] == "ap"

    def test_region_preserves_other_settings(self):
        """Adding region should not affect other configuration settings."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            secret_set="my-secrets",
            region="eu"
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["agent_name"] == "test-agent"
        assert result["image"] == "test:latest"
        assert result["secret_set"] == "my-secrets"
        assert result["region"] == "eu"


class TestDeployRegionTOML:
    """Test TOML configuration file parsing with region."""

    @pytest.fixture
    def temp_config_file(self, tmp_path):
        """Create a temporary TOML config file."""
        config_path = tmp_path / "pcc-deploy.toml"
        return config_path

    def test_loads_region_from_toml(self, temp_config_file):
        """TOML file with region should be parsed correctly."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"
        region = "eu"
        """
        temp_config_file.write_text(config_content)

        # Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_config_file)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.region == "eu"

    def test_defaults_region_when_not_in_toml(self, temp_config_file):
        """TOML file without region should have None region."""
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
        assert config.region is None

    def test_preserves_other_settings_with_region(self, temp_config_file):
        """Region setting should not interfere with other TOML settings."""
        # Arrange
        config_content = """
        agent_name = "test-agent"
        image = "test:latest"
        region = "ap"
        secret_set = "my-secrets"
        enable_managed_keys = true

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
        assert config.region == "ap"
        assert config.secret_set == "my-secrets"
        assert config.enable_managed_keys is True
        assert config.scaling.min_agents == 2
        assert config.scaling.max_agents == 10


class TestDeployRegionValidation:
    """Test region validation for deploy commands."""

    def test_all_valid_regions_accepted(self):
        """All valid region codes should be accepted."""
        # Arrange & Act & Assert
        for region in REGIONS:
            config = DeployConfigParams(
                agent_name="test-agent",
                image="test:latest",
                region=region
            )
            assert config.region == region


class TestDeployRegionBackwardCompatibility:
    """Test backward compatibility for deployments without region."""

    def test_existing_config_without_region_works(self):
        """Existing configs without region field should work unchanged."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            enable_managed_keys=True
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["enable_managed_keys"] is True
        assert result["region"] is None

    @pytest.fixture
    def temp_legacy_config(self, tmp_path):
        """Create a legacy TOML config without region."""
        config_path = tmp_path / "pcc-deploy.toml"
        config_content = """
        agent_name = "legacy-agent"
        image = "legacy:latest"
        enable_managed_keys = true

        [scaling]
        min_agents = 1
        max_agents = 5
        """
        config_path.write_text(config_content)
        return config_path

    def test_legacy_toml_loads_with_defaults(self, temp_legacy_config):
        """Legacy TOML files should load with region defaulting to None."""
        # Arrange & Act
        with patch("pipecatcloud.cli.config.deploy_config_path", str(temp_legacy_config)):
            config = load_deploy_config_file()

        # Assert
        assert config is not None
        assert config.agent_name == "legacy-agent"
        assert config.enable_managed_keys is True
        assert config.region is None


class TestDeployRegionWithOtherFeatures:
    """Test region compatibility with other deployment features."""

    def test_region_with_managed_keys(self):
        """Region and managed keys can be used together."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region="eu",
            enable_managed_keys=True
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["region"] == "eu"
        assert result["enable_managed_keys"] is True

    def test_region_with_krisp_viva(self):
        """Region and Krisp VIVA can be used together."""
        # Arrange
        from pipecatcloud._utils.deploy_utils import KrispVivaConfig

        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region="ap",
            krisp_viva=KrispVivaConfig(audio_filter="pro")
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["region"] == "ap"
        assert result["krisp_viva"]["audio_filter"] == "pro"

    def test_region_with_agent_profile(self):
        """Region and agent profile can be used together."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region="us",
            agent_profile="high-performance"
        )

        # Act
        result = config.to_dict()

        # Assert
        assert result["region"] == "us"
        assert result["agent_profile"] == "high-performance"
