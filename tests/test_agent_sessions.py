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

from pipecatcloud.cli.commands.agent import (
    SessionEndState,
    _session_status_display,
    sessions,
)

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

    def test_end_state_filter_is_passed_to_the_api(self, mock_api):
        """--end-state reaches the API as the endState query param value (PCC-1163)."""
        mock_api.return_value = {"sessions": []}

        sessions(
            deploy_config=None,
            agent_name=TEST_AGENT,
            session_id=None,
            end_state=SessionEndState.ENDED_BEFORE_AGENT_START,
            organization=TEST_ORG,
        )

        assert mock_api.call_args.kwargs["end_state"] == "ended_before_agent_start"

    def test_no_end_state_filter_passes_none(self, mock_api):
        mock_api.return_value = {"sessions": []}

        sessions(deploy_config=None, agent_name=TEST_AGENT, session_id=None, organization=TEST_ORG)

        assert mock_api.call_args.kwargs["end_state"] is None

    def test_detail_view_renders_lifecycle_events(self, capsys):
        """Session detail lists lifecycle steps from the API's events array (PCC-1163)."""
        session_detail = {
            "sessionId": "11111111-2222-3333-4444-555555555555",
            "createdAt": "2026-08-31T00:02:34.000Z",
            "endedAt": "2026-08-31T00:02:47.000Z",
            "completionStatus": "WS_CONNECTION_CLOSED",
            "endState": "ended_before_agent_start",
            "coldStart": False,
            "botStartSeconds": None,
            "events": [
                {"eventCode": "SESSION_START_INITIATED", "eventTs": "2026-08-31T00:02:34.000Z"},
                {"eventCode": "SESSION_COMPLETED", "eventTs": "2026-08-31T00:02:47.000Z"},
            ],
        }
        with patch("pipecatcloud.cli.commands.agent.API._agent_session") as mock_detail:
            mock_detail.return_value = session_detail
            sessions(
                deploy_config=None,
                agent_name=TEST_AGENT,
                session_id=session_detail["sessionId"],
                organization=TEST_ORG,
            )

        out = capsys.readouterr().out
        assert "Start request received" in out
        assert "Session completed" in out
        assert "Ended before agent start" in out

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


class TestSessionStatusDisplay:
    """The status cell prefers the derived endState with a legacy fallback (PCC-1163)."""

    def test_end_state_wins_over_legacy_wording(self):
        session = {
            "endedAt": "2026-08-31T00:02:47.000Z",
            "completionStatus": "WS_CONNECTION_CLOSED",
            "endState": "ended_before_agent_start",
        }
        assert _session_status_display(session, rich=False) == "Ended before agent start"
        assert "Ended before agent start" in _session_status_display(session)

    def test_timeouts_are_no_longer_reported_as_complete(self):
        session = {
            "endedAt": "2026-08-31T00:02:47.000Z",
            "completionStatus": "BOT_CONNECTION_TIMEOUT",
            "endState": "agent_start_timeout",
        }
        assert _session_status_display(session, rich=False) == "Timeout"

    def test_legacy_fallback_without_end_state(self):
        assert (
            _session_status_display({"endedAt": "2026-08-31T00:02:47.000Z"}, rich=False)
            == "Complete"
        )
        assert _session_status_display({"endedAt": None}, rich=False) == "Active"

    def test_error_500_special_case_is_preserved(self):
        session = {"endedAt": "2026-08-31T00:02:47.000Z", "completionStatus": "500"}
        assert _session_status_display(session, rich=False) == "Error (500)"

    def test_unrecognized_future_end_state_renders_as_ended(self):
        session = {"endedAt": "2026-08-31T00:02:47.000Z", "endState": "some_future_state"}
        assert _session_status_display(session, rich=False) == "Ended"
