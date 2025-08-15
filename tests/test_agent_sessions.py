"""
Unit tests for the 'pcc agent sessions' command.

Tests focus on core behaviors and edge cases, not implementation details.
"""

import pytest
from unittest.mock import patch
import typer

# Import the function under test from local source
from src.pipecatcloud.cli.commands.agent import sessions


class TestAgentSessionsCommand:
    """Test the 'pcc agent sessions' command behaviors."""

    @pytest.fixture
    def mock_api(self):
        """Mock the underlying API agent_sessions method."""
        with patch('src.pipecatcloud.cli.commands.agent.API._agent_sessions') as mock_api:
            yield mock_api

    def test_handles_zero_sessions_without_crash(self, mock_api):
        """Agent with zero sessions should not cause ZeroDivisionError."""
        # Arrange: API returns empty sessions list
        empty_sessions_response = {"sessions": []}
        mock_api.return_value = empty_sessions_response
        
        # Act & Assert: Should complete without ZeroDivisionError
        sessions(
            deploy_config=None,
            agent_name="test-agent",
            session_id=None, 
            organization="test-org"
        )

    def test_handles_api_error_gracefully(self, mock_api):
        """API errors should be handled without crashing.""" 
        # Arrange: _agent_sessions raises exception (API error)
        mock_api.side_effect = Exception("Agent not found")
        
        # Act: Call with API error  
        result = sessions(
            deploy_config=None,
            agent_name="nonexistent-agent",
            session_id=None,
            organization="test-org"
        )
        
        # Assert: Should return typer.Exit on error
        assert isinstance(result, type(typer.Exit()))