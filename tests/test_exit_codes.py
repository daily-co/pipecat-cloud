"""End-to-end process exit-code tests (PCC-1064).

The CLI used to ``return typer.Exit(1)`` from command bodies. Typer discards a
command's return value, so every failure path exited 0 — CI wrappers could not
detect that a command failed at all. These tests invoke the real Typer app via
CliRunner and assert the actual exit code, which the old suite never did.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

TEST_AGENT = "some-agent"

# Minimal healthy payload for `agent status` rendering. autoScaling is included
# because the render path references scaling_panel only when it is present.
AGENT_STATUS_READY = {
    "ready": True,
    "activeSessionCount": 0,
    "deployment": {"manifest": {"spec": {"image": "registry.example.com/img:1"}}},
    "activeDeploymentId": "dep-1",
    "createdAt": "2026-01-01T00:00:00.000Z",
    "updatedAt": "2026-01-01T00:00:00.000Z",
    "autoScaling": {"minReplicas": 0, "maxReplicas": 5},
    "errors": [],
}


class TestProcessExitCodes:
    def test_not_logged_in_exits_nonzero(self):
        """The ticket's headline repro: no token must not exit 0."""
        with patch("pipecatcloud._utils.auth_utils.config") as mock_config:
            mock_config.get.return_value = None
            result = runner.invoke(entrypoint_cli_typer, ["agent", "status", TEST_AGENT])
        assert result.exit_code == 1

    def test_agent_status_api_error_exits_nonzero(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agent = AsyncMock(return_value=(None, {"code": "500", "error": "boom"}))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "status", TEST_AGENT])
        assert result.exit_code == 1

    def test_agent_status_missing_agent_exits_nonzero(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agent = AsyncMock(return_value=(None, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "status", TEST_AGENT])
        assert result.exit_code == 1

    def test_agent_status_success_exits_zero(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agent = AsyncMock(return_value=(AGENT_STATUS_READY, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "status", TEST_AGENT])
        assert result.exit_code == 0

    def test_agent_list_api_error_exits_nonzero(self):
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agents = AsyncMock(return_value=(None, {"code": "500", "error": "boom"}))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "list"])
        assert result.exit_code == 1

    def test_deploy_without_agent_name_exits_nonzero(self):
        """No agent name from args or config file must fail, not exit 0."""
        with patch("pipecatcloud._utils.deploy_utils.load_deploy_config_file", return_value=None):
            result = runner.invoke(entrypoint_cli_typer, ["deploy"])
        assert result.exit_code == 1

    def test_secrets_set_invalid_name_exits_nonzero(self):
        result = runner.invoke(
            entrypoint_cli_typer, ["secrets", "set", "invalid_name!", "KEY=value"]
        )
        assert result.exit_code == 1

    def test_spend_limit_json_api_error_exits_nonzero(self):
        with patch("pipecatcloud.cli.commands.spend_limit.API") as mock_api:
            mock_api.bubble_error.return_value.spend_limit_get = AsyncMock(
                return_value=(None, {"code": "500", "error": "boom"})
            )
            result = runner.invoke(entrypoint_cli_typer, ["spend-limit", "show", "--json"])
        assert result.exit_code == 1


def test_no_discarded_typer_exit_in_source():
    """Regression guard: `return typer.Exit(...)` never fires — Typer discards
    return values — and a bare `typer.Exit(...)` statement is constructed and
    thrown away. Both silently exit 0. Exits must be raised."""
    import re

    discarded_statement = re.compile(r"^\s+typer\.(Exit|Abort)\(")
    src_root = Path(__file__).parent.parent / "src"
    offenders = []
    for py_file in src_root.rglob("*.py"):
        for lineno, line in enumerate(py_file.read_text().splitlines(), start=1):
            if (
                "return typer.Exit" in line
                or "return typer.Abort" in line
                or discarded_statement.match(line)
            ):
                offenders.append(f"{py_file.relative_to(src_root)}:{lineno}")
    assert not offenders, (
        "Found discarded typer.Exit/Abort (must be raised, not returned or dropped): "
        + ", ".join(offenders)
    )
