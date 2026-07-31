"""
Unit tests for the 'pipecat cloud agent sessions' command.

Tests focus on core behaviors and edge cases, not implementation details.
"""

# Import from source, not installed package
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import typer

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.agent import sessions

# Test constants
TEST_ORG = "test-org"
TEST_AGENT = "test-agent"


class TestAgentSessionsCommand:
    """Test the 'pipecat cloud agent sessions' command behaviors."""

    @pytest.fixture
    def mock_api(self):
        """Mock the underlying API agent_sessions method."""
        with patch("pipecatcloud.cli.commands.agent.API._agent_sessions") as mock_api:
            yield mock_api

    def test_handles_zero_sessions_without_crash(self, mock_api):
        """Agent with zero sessions should not cause ZeroDivisionError."""
        # Arrange: API returns empty sessions list
        empty_sessions_response = {"sessions": []}
        mock_api.return_value = empty_sessions_response

        # Act & Assert: Should complete without ZeroDivisionError
        sessions(deploy_config=None, agent_name=TEST_AGENT, session_id=None, organization=TEST_ORG)

    def test_handles_api_error_gracefully(self, mock_api):
        """API errors should be handled without crashing."""
        # Arrange: _agent_sessions raises exception (API error)
        mock_api.side_effect = Exception("Agent not found")

        # Act & Assert: Should raise typer.Exit on error
        with pytest.raises(typer.Exit):
            sessions(
                deploy_config=None,
                agent_name="nonexistent-agent",
                session_id=None,
                organization=TEST_ORG,
            )

    @patch("pipecatcloud._utils.deploy_utils.load_deploy_config_file")
    def test_handles_missing_agent_name_gracefully(self, mock_load_config):
        """Command should exit gracefully when no agent name is provided."""
        # Arrange: Mock the config loader to return None (no config file)
        mock_load_config.return_value = None

        # Act & Assert: Should raise typer.Exit(1) with error message
        with pytest.raises(typer.Exit) as exc_info:
            sessions(
                deploy_config=None,  # No deploy config
                agent_name=None,  # No agent name argument
                session_id=None,
                organization=TEST_ORG,
            )
        assert exc_info.value.exit_code == 1
