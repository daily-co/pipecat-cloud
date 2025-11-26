"""
Unit tests for the 'pcc agent stop' command.

Tests focus on core behaviors and edge cases, not implementation details.
"""

import pytest
import typer
from unittest.mock import patch, AsyncMock, MagicMock

# Import from source, not installed package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.agent import stop

# Test constants
TEST_ORG = "test-org"
TEST_AGENT = "test-agent"
TEST_SESSION_ID = "session-123"


class TestAgentStopCommand:
    """Test the 'pcc agent stop' command behaviors."""

    def test_stop_respects_force_flag(self):
        """Verify force flag skips confirmation when set to True."""
        with patch('pipecatcloud.cli.commands.agent.console') as mock_console, \
             patch('pipecatcloud.cli.commands.agent.config') as mock_config, \
             patch('pipecatcloud.cli.commands.agent.questionary') as mock_questionary, \
             patch('pipecatcloud.cli.commands.agent.DeployConfigParams') as mock_params:

            mock_config.get.return_value = TEST_ORG
            mock_params.return_value = MagicMock(agent_name=TEST_AGENT)
            # Mock questionary - should NOT be called when force=True
            mock_questionary.confirm.return_value.ask_async = AsyncMock()

            # Act with force=True
            stop(
                deploy_config=None,
                agent_name=TEST_AGENT,
                session_id=TEST_SESSION_ID,
                organization=TEST_ORG,
                force=True,
            )

            # Assert
            # questionary.confirm should not be called when force=True
            mock_questionary.confirm.assert_not_called()

    def test_stop_shows_confirmation_without_force(self):
        """Verify confirmation prompt is shown when force is False."""
        with patch('pipecatcloud.cli.commands.agent.console') as mock_console, \
             patch('pipecatcloud.cli.commands.agent.config') as mock_config, \
             patch('pipecatcloud.cli.commands.agent.questionary') as mock_questionary, \
             patch('pipecatcloud.cli.commands.agent.DeployConfigParams') as mock_params:

            mock_config.get.return_value = TEST_ORG
            mock_params.return_value = MagicMock(agent_name=TEST_AGENT)
            # User agrees to the confirmation
            mock_questionary.confirm.return_value.ask_async = AsyncMock(return_value=True)

            # Act with force=False
            stop(
                deploy_config=None,
                agent_name=TEST_AGENT,
                session_id=TEST_SESSION_ID,
                organization=TEST_ORG,
                force=False,
            )

            # Assert
            # questionary.confirm should be called when force=False
            mock_questionary.confirm.assert_called_once()

    def test_stop_aborts_on_user_rejection(self):
        """Verify command aborts when user rejects the confirmation."""
        with patch('pipecatcloud.cli.commands.agent.console') as mock_console, \
             patch('pipecatcloud.cli.commands.agent.config') as mock_config, \
             patch('pipecatcloud.cli.commands.agent.questionary') as mock_questionary, \
             patch('pipecatcloud.cli.commands.agent.DeployConfigParams') as mock_params:

            mock_config.get.return_value = TEST_ORG
            mock_params.return_value = MagicMock(agent_name=TEST_AGENT)
            # User rejects the confirmation
            mock_questionary.confirm.return_value.ask_async = AsyncMock(return_value=False)

            # Act
            result = stop(
                deploy_config=None,
                agent_name=TEST_AGENT,
                session_id=TEST_SESSION_ID,
                organization=TEST_ORG,
                force=False,
            )

            # Assert
            # Should exit with code 1 (abort)
            assert isinstance(result, typer.Exit)
            assert result.exit_code == 1
            # Should display abort message
            mock_console.print.assert_any_call("[bold]Aborting stop request[/bold]")
