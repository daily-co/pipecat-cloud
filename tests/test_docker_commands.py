"""
Unit tests for Docker build-push command.

Tests the core functionality: image parsing, registry detection, and error handling.
"""

# Import from source, not installed package
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams
from pipecatcloud.cli.commands.docker import (
    RegistryType,
    _build_image_name,
    _is_auth_error,
    _suggest_and_install_binfmt,
    _suggest_docker_login,
)


class TestImageParsing:
    """Test parsing of image names for different registry formats."""

    def test_build_image_name_dockerhub(self):
        """Test building Docker Hub image names."""
        result = _build_image_name(RegistryType.DOCKERHUB, "my_username", "test-image")
        assert result == "my_username/test-image"

    def test_build_image_name_custom_registry(self):
        """Test building custom registry image names."""
        result = _build_image_name(RegistryType.CUSTOM, "myuser", "myapp", "gcr.io")
        assert result == "gcr.io/myuser/myapp"

    def test_build_image_name_custom_registry_missing_url(self):
        """Test custom registry without URL raises error."""
        with pytest.raises(ValueError, match="registry_url is required"):
            _build_image_name(RegistryType.CUSTOM, "user", "app")


class TestConfigParsing:
    """Test parsing configuration from deploy config."""

    def test_parse_dockerhub_image(self):
        """Test parsing Docker Hub format image."""
        # Simulate parsing "my_username/test-image:0.5"
        image = "my_username/test-image:0.5"
        parts = image.split("/")

        assert len(parts) == 2
        assert parts[0] == "my_username"  # username

        repo_and_tag = parts[1]
        repo, tag = repo_and_tag.split(":", 1)
        assert repo == "test-image"
        assert tag == "0.5"

    def test_parse_custom_registry_image(self):
        """Test parsing custom registry format image."""
        # Simulate parsing "gcr.io/my-project/app:v1.0"
        image = "gcr.io/my-project/app:v1.0"
        parts = image.split("/")

        assert len(parts) == 3
        assert parts[0] == "gcr.io"  # registry URL
        assert parts[1] == "my-project"  # username/project

        repo_and_tag = parts[2]
        repo, tag = repo_and_tag.split(":", 1)
        assert repo == "app"
        assert tag == "v1.0"

    def test_parse_image_no_tag(self):
        """Test parsing image without explicit tag."""
        image = "my_username/test-image"
        parts = image.split("/")

        repo_and_tag = parts[1]
        if ":" in repo_and_tag:
            repo, tag = repo_and_tag.split(":", 1)
        else:
            repo = repo_and_tag
            tag = "latest"

        assert repo == "test-image"
        assert tag == "latest"


class TestErrorHandling:
    """Test error detection and hint generation."""

    def test_is_auth_error_unauthorized(self):
        """Test detection of unauthorized error."""
        stderr = "Error: unauthorized: authentication required"
        assert _is_auth_error(stderr.lower()) is True

    def test_is_auth_error_access_denied(self):
        """Test detection of access denied error."""
        stderr = "Error: access denied to repository"
        assert _is_auth_error(stderr.lower()) is True

    def test_is_auth_error_denied(self):
        """Test detection of denied error."""
        stderr = "denied: requested access to the resource is denied"
        assert _is_auth_error(stderr.lower()) is True

    def test_is_auth_error_false(self):
        """Test non-auth errors are not detected as auth errors."""
        stderr = "Error: failed to build: syntax error in Dockerfile"
        assert _is_auth_error(stderr.lower()) is False

    @patch("pipecatcloud.cli.commands.docker.console")
    def test_suggest_docker_login_dockerhub(self, mock_console):
        """Test Docker Hub login suggestion."""
        registry_info = {"type": "dockerhub"}
        _suggest_docker_login(registry_info)

        mock_console.print.assert_any_call(
            "\n[yellow]💡 You need to authenticate with the registry[/yellow]"
        )
        mock_console.print.assert_any_call("[yellow]   docker login[/yellow]")

    @patch("pipecatcloud.cli.commands.docker.console")
    def test_suggest_docker_login_custom_registry(self, mock_console):
        """Test custom registry login suggestion."""
        registry_info = {"type": "custom", "url": "gcr.io"}
        _suggest_docker_login(registry_info)

        mock_console.print.assert_any_call(
            "\n[yellow]💡 You need to authenticate with the registry[/yellow]"
        )
        mock_console.print.assert_any_call("[yellow]   docker login gcr.io[/yellow]")

    @patch("pipecatcloud.cli.commands.docker.console")
    def test_suggest_docker_login_no_registry_info(self, mock_console):
        """Test login suggestion with no registry info."""
        _suggest_docker_login(None)

        mock_console.print.assert_any_call(
            "\n[yellow]💡 You need to authenticate with the registry[/yellow]"
        )
        mock_console.print.assert_any_call("[yellow]   docker login[/yellow]")


class TestBinfmtErrorHandling:
    """Test binfmt error detection and messaging."""

    @patch("pipecatcloud.cli.commands.docker.typer.confirm")
    @patch("pipecatcloud.cli.commands.docker.run_docker_command")
    @patch("pipecatcloud.cli.commands.docker.console")
    def test_suggest_and_install_binfmt_user_accepts(
        self, mock_console, mock_run_cmd, mock_confirm
    ):
        """Test binfmt installation when user accepts."""
        mock_confirm.return_value = True
        mock_run_cmd.return_value = True

        _suggest_and_install_binfmt()

        mock_console.print.assert_any_call(
            "\n[yellow]⚠️  Cross-platform build support missing[/yellow]"
        )
        mock_console.print.assert_any_call(
            "\n[yellow]   This is a one-time setup that persists across Docker restarts.[/yellow]"
        )
        mock_confirm.assert_called_once()
        mock_run_cmd.assert_called_once()
        mock_console.success.assert_called_once()

    @patch("pipecatcloud.cli.commands.docker.typer.confirm")
    @patch("pipecatcloud.cli.commands.docker.console")
    def test_suggest_and_install_binfmt_user_declines(self, mock_console, mock_confirm):
        """Test binfmt installation when user declines."""
        mock_confirm.return_value = False

        _suggest_and_install_binfmt()

        mock_console.print.assert_any_call(
            "\n[yellow]Skipping installation. To install manually, run:[/yellow]"
        )

    @patch("pipecatcloud.cli.commands.docker.typer.confirm")
    @patch("pipecatcloud.cli.commands.docker.run_docker_command")
    @patch("pipecatcloud.cli.commands.docker.console")
    def test_suggest_and_install_binfmt_installation_fails(
        self, mock_console, mock_run_cmd, mock_confirm
    ):
        """Test binfmt installation when the installation command fails."""
        mock_confirm.return_value = True
        mock_run_cmd.return_value = False

        _suggest_and_install_binfmt()

        mock_console.error.assert_called_once()
        mock_console.print.assert_any_call(
            "\n[yellow]You can install it manually by running:[/yellow]"
        )


class TestDeployConfigIntegration:
    """Test integration with deploy config system."""

    def test_deploy_config_with_docker_section(self):
        """Test deploy config parses docker section correctly."""
        config = DeployConfigParams(
            agent_name="test-agent",
            image="my_username/test:1.0",
            docker_config={
                "registry": "dockerhub",
                "registry_username": "my_username",
                "auto_latest": False,
            },
        )

        assert config.agent_name == "test-agent"
        assert config.image == "my_username/test:1.0"
        assert config.docker_config["registry"] == "dockerhub"
        assert config.docker_config["auto_latest"] is False

    def test_deploy_config_minimal(self):
        """Test minimal deploy config works."""
        config = DeployConfigParams(agent_name="test-agent", image="my_username/test:1.0")

        assert config.agent_name == "test-agent"
        assert config.image == "my_username/test:1.0"
        assert config.docker_config == {}  # Empty dict by default
