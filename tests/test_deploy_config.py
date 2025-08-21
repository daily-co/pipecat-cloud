"""
Unit tests for the deploy command's --config flag functionality.

Tests focus on config file loading, validation, and integration with CLI arguments.
"""

import pytest
import tempfile
import os
from unittest.mock import patch, MagicMock
from pathlib import Path

# Import the functions under test
from src.pipecatcloud._utils.deploy_utils import load_deploy_config_file, with_deploy_config, DeployConfigParams, ScalingParams
from src.pipecatcloud.exception import ConfigFileError


class TestLoadDeployConfigFile:
    """Test the load_deploy_config_file function with custom paths."""

    def test_load_config_with_custom_path(self):
        """Should load config from custom path when provided."""
        # Arrange: Create temporary config file
        config_content = """
                        agent_name = "test-agent"
                        image = "test:latest"
                        image_credentials = "test-secret"
                        secret_set = "test-secrets"
                        enable_krisp = true

                        [scaling]
                        min_agents = 2
                        max_agents = 10
                        """
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(config_content)
            temp_path = f.name

        try:
            # Act: Load config from custom path
            result = load_deploy_config_file(custom_path=temp_path)
            
            # Assert: Config loaded correctly
            assert result is not None
            assert result.agent_name == "test-agent"
            assert result.image == "test:latest"
            assert result.image_credentials == "test-secret"
            assert result.secret_set == "test-secrets"
            assert result.enable_krisp is True
            assert result.scaling.min_agents == 2
            assert result.scaling.max_agents == 10
        finally:
            os.unlink(temp_path)

    def test_load_config_with_nonexistent_path(self):
        """Should return None when custom path doesn't exist."""
        # Act: Try to load from non-existent path
        result = load_deploy_config_file(custom_path="/path/that/does/not/exist.toml")
        
        # Assert: Returns None gracefully
        assert result is None

    def test_load_config_with_invalid_toml(self):
        """Should return None for invalid TOML syntax (handled by outer try/except)."""
        # Arrange: Create file with invalid TOML
        invalid_toml = "invalid toml syntax ["
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(invalid_toml)
            temp_path = f.name

        try:
            # Act: Try to load invalid TOML
            result = load_deploy_config_file(custom_path=temp_path)
            
            # Assert: Returns None for invalid TOML (caught by outer try/except)
            assert result is None
        finally:
            os.unlink(temp_path)



    def test_load_config_falls_back_to_default_path(self):
        """Should use default path when custom_path is None."""
        # Arrange: Mock the import of deploy_config_path
        with patch('src.pipecatcloud.cli.config.deploy_config_path', '/mock/default/path.toml'):
            with patch('builtins.open', side_effect=FileNotFoundError):
                # Act: Load config without custom path
                result = load_deploy_config_file(custom_path=None)
                
                # Assert: Returns None when default doesn't exist
                assert result is None

    def test_load_config_with_minimal_valid_config(self):
        """Should load config with only required fields."""
        # Arrange: Create minimal config
        config_content = """
agent_name = "minimal-agent"
image = "minimal:latest"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(config_content)
            temp_path = f.name

        try:
            # Act: Load minimal config
            result = load_deploy_config_file(custom_path=temp_path)
            
            # Assert: Config loaded with defaults
            assert result is not None
            assert result.agent_name == "minimal-agent"
            assert result.image == "minimal:latest"
            assert result.image_credentials is None
            assert result.secret_set is None
            assert result.enable_krisp is False
            assert result.scaling.min_agents == 0  # Default value
        finally:
            os.unlink(temp_path)


class TestWithDeployConfigDecorator:
    """Test the @with_deploy_config decorator with custom config paths."""

    def test_decorator_passes_custom_config_path(self):
        """Decorator should extract config_file from kwargs and load custom config."""
        # Arrange: Create test config file
        config_content = """
agent_name = "decorator-test"
image = "decorator:latest"
"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.toml', delete=False) as f:
            f.write(config_content)
            temp_path = f.name

        # Create mock function to decorate
        mock_func = MagicMock(return_value="success")
        decorated_func = with_deploy_config(mock_func)

        try:
            # Act: Call decorated function with config_file parameter
            result = decorated_func(
                agent_name="cli-agent",
                config_file=temp_path,
                other_param="test"
            )
            
            # Assert: Function was called with loaded deploy_config
            assert result == "success"
            mock_func.assert_called_once()
            
            # Check that deploy_config was injected
            call_kwargs = mock_func.call_args[1]
            assert 'deploy_config' in call_kwargs
            assert call_kwargs['deploy_config'] is not None
            assert call_kwargs['deploy_config'].agent_name == "decorator-test"
            
            # Check that config_file is still passed through
            assert call_kwargs['config_file'] == temp_path
            
        finally:
            os.unlink(temp_path)

    def test_decorator_handles_no_config_file(self):
        """Decorator should work normally when no config_file is provided."""
        # Arrange: Mock the default config loading
        mock_func = MagicMock(return_value="success")
        decorated_func = with_deploy_config(mock_func)
        
        with patch('src.pipecatcloud._utils.deploy_utils.load_deploy_config_file') as mock_load:
            mock_load.return_value = None
            
            # Act: Call without config_file
            result = decorated_func(agent_name="test")
            
            # Assert: Function called normally
            assert result == "success"
            mock_func.assert_called_once()
            mock_load.assert_called_once_with(None)  # Called with None for default behavior





