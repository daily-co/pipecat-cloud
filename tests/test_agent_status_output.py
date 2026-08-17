"""Tests for `pipecat cloud agent status` output modes.

Plain mode (piped output) must carry the same rows as the rich table
(PCC-1064). Both the Architecture row (PCC-1105) and the Resources row
(PCC-1063) once existed only in the rich path, so scripted output silently
dropped them (PCC-1114).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.agent import status

BASE = {
    "name": "my-agent",
    "ready": True,
    "activeSessionCount": 0,
    "activeDeploymentId": "dep-1",
    "createdAt": "2026-08-17T00:00:00Z",
    "updatedAt": "2026-08-17T00:00:00Z",
    "autoScaling": {"minReplicas": 0, "maxReplicas": 10},
    "deployment": {"manifest": {"spec": {"image": "repo/img:1"}}},
}


def _plain_lines(data: dict) -> list[str]:
    with (
        patch("pipecatcloud.cli.commands.agent.API") as mock_api,
        patch("pipecatcloud.cli.commands.agent.console") as mock_console,
        patch("pipecatcloud.cli.commands.agent.config") as mock_config,
    ):
        mock_config.get.return_value = "test-org"
        mock_console.rich_output = False
        mock_console.json_output = False
        mock_api.agent = AsyncMock(return_value=(data, None))
        status(agent_name="my-agent", organization="test-org")
        return [str(c.args[0]) for c in mock_console.print.call_args_list if c.args]


class TestPlainOutputRows:
    def test_architecture_and_resources_render_in_plain_mode(self):
        data = {
            **BASE,
            "resources": {"cpu": "500m", "memory": "1Gi"},
            "deployment": {"manifest": {"spec": {"image": "repo/img:1", "arch": "arm64"}}},
        }
        lines = _plain_lines(data)
        assert "Architecture: arm64" in lines
        assert "Resources: cpu=500m, memory=1Gi" in lines

    def test_rows_absent_when_fields_absent(self):
        lines = _plain_lines(BASE)
        assert not any(line.startswith("Architecture:") for line in lines)
        assert not any(line.startswith("Resources:") for line in lines)

    @pytest.mark.parametrize("resources", [None, "not-a-dict", {}])
    def test_resources_row_requires_a_dict(self, resources):
        lines = _plain_lines({**BASE, "resources": resources})
        assert not any(line.startswith("Resources:") for line in lines)
