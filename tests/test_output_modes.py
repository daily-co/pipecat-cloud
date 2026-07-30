"""Tests for the non-interactive output modes (PCC-1064).

Covers mode resolution (--output flag, PIPECAT_OUTPUT env, auto-detection),
the plain-mode status sink that replaces silent Rich spinners in non-TTY,
untruncated plain-mode listings, and the non-interactive confirmation guard.
"""

import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer
from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.console_utils import OutputMode, PipecatConsole, console
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

LONG_ID = "4bb223c4-9f31-44f5-9b3c-c6b8ea9c3a01"
LONG_DEPLOYMENT_ID = "7c9d1e55-8a76-4a2b-b6ce-2f0d4a9be777"

AGENT_LIST_PAYLOAD = [
    {
        "name": "site-survey-agent-with-long-name",
        "region": "us-east-1",
        "id": LONG_ID,
        "activeDeploymentId": LONG_DEPLOYMENT_ID,
        "createdAt": "2026-07-01T00:00:00.000Z",
        "updatedAt": "2026-07-20T00:00:00.000Z",
    }
]


# Note: the autouse fixture restoring the console singleton's output mode
# lives in conftest.py so it protects every test module.


def make_console(**kwargs) -> tuple[PipecatConsole, StringIO]:
    buffer = StringIO()
    test_console = PipecatConsole(file=buffer, width=80, **kwargs)
    return test_console, buffer


class TestModeResolution:
    def test_defaults_to_plain_when_not_a_terminal(self):
        test_console, _ = make_console()
        assert not test_console.is_terminal
        assert test_console.output_mode == OutputMode.plain

    def test_explicit_mode_overrides_detection(self):
        test_console, _ = make_console()
        test_console.set_output_mode(OutputMode.rich)
        assert test_console.output_mode == OutputMode.rich

    def test_output_flag_forces_rich(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agents = AsyncMock(return_value=(AGENT_LIST_PAYLOAD, None))
            result = runner.invoke(entrypoint_cli_typer, ["--output", "rich", "agent", "list"])
        assert result.exit_code == 0
        # Rich table output truncates the long IDs at the default 80 columns
        assert LONG_ID not in result.output

    def test_env_var_selects_mode(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agents = AsyncMock(return_value=(AGENT_LIST_PAYLOAD, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["agent", "list"], env={"PIPECAT_OUTPUT": "plain"}
            )
        assert result.exit_code == 0
        assert LONG_ID in result.output

    def test_invalid_env_mode_exits_2(self):
        result = runner.invoke(
            entrypoint_cli_typer, ["agent", "list"], env={"PIPECAT_OUTPUT": "yaml"}
        )
        assert result.exit_code == 2

    def test_invalid_flag_mode_exits_2(self):
        result = runner.invoke(entrypoint_cli_typer, ["--output", "yaml", "agent", "list"])
        assert result.exit_code == 2

    def test_json_mode_moves_console_to_stderr(self):
        test_console, _ = make_console()
        test_console.set_output_mode(OutputMode.json)
        assert test_console.file is sys.stderr


class TestPlainStatus:
    def test_distinct_messages_are_printed_with_timestamps(self):
        test_console, buffer = make_console()
        with test_console.status("Waiting for deployment...") as status:
            status.update("Starting: 0/2 instances ready")
            status.update("Starting: 1/2 instances ready")
        output = buffer.getvalue()
        assert "Waiting for deployment..." in output
        assert "Starting: 0/2 instances ready" in output
        assert "Starting: 1/2 instances ready" in output
        # Timestamped prefix like [12:34:56]
        assert output.count("[") >= 3

    def test_repeated_messages_are_deduplicated(self):
        test_console, buffer = make_console()
        with test_console.status("polling...") as status:
            for _ in range(10):
                status.update("still waiting")
        assert buffer.getvalue().count("still waiting") == 1

    def test_rich_mode_uses_rich_status(self):
        test_console, _ = make_console(force_terminal=True)
        test_console.set_output_mode(OutputMode.rich)
        status = test_console.status("working...")
        # rich's Status, not our plain sink
        from rich.status import Status

        assert isinstance(status, Status)
        status.stop()


class TestPlainListings:
    def test_agent_list_piped_shows_full_ids(self):
        """The Finding-3 repro: piped `agent list` must not ellipsize IDs."""
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agents = AsyncMock(return_value=(AGENT_LIST_PAYLOAD, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "list"])
        assert result.exit_code == 0
        assert LONG_ID in result.output
        assert LONG_DEPLOYMENT_ID in result.output
        assert "…" not in result.output

    def test_print_records_emits_tab_separated_rows(self):
        test_console, buffer = make_console()
        test_console.print_records(["ID", "Name"], [(LONG_ID, "value with spaces")], title="Things")
        lines = buffer.getvalue().splitlines()
        assert lines[0] == "Things"
        assert lines[1] == "ID\tName"
        assert lines[2] == f"{LONG_ID}\tvalue with spaces"

    def test_error_is_flat_in_plain_mode(self):
        test_console, buffer = make_console()
        test_console.error("something broke")
        output = buffer.getvalue()
        assert "something broke" in output
        assert "╭" not in output  # no panel box art

    def test_error_is_boxed_in_rich_mode(self):
        test_console, buffer = make_console(force_terminal=True)
        test_console.set_output_mode(OutputMode.rich)
        test_console.error("something broke")
        assert "╭" in buffer.getvalue()


class TestRequireInteractive:
    def test_exits_2_when_stdin_not_a_terminal(self):
        test_console, buffer = make_console()
        with patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=False):
            with pytest.raises(typer.Exit) as exc_info:
                test_console.require_interactive("--yes")
        assert exc_info.value.exit_code == 2
        assert "--yes" in buffer.getvalue()

    def test_noop_when_interactive(self):
        test_console, _ = make_console()
        with patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=True):
            test_console.require_interactive("--yes")

    def test_agent_stop_without_force_exits_2_non_interactively(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agent_session_terminate = AsyncMock(return_value=({}, None))
            result = runner.invoke(
                entrypoint_cli_typer,
                ["agent", "stop", "some-agent", "--session-id", "sess-1"],
            )
        assert result.exit_code == 2
