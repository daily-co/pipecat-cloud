"""Tests for build-ID surfacing when the image URI is redacted (PCC-1064).

Cloud-built deployments have manifest.spec.image stripped by the API
(internal ECR URI); the CLI used to render "Image: N/A". The build ID is
the customer-facing artifact reference and must be shown instead.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.agent import _image_display
from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

BUILD_ID = "b-4bb223c4-9f31-44f5-9b3c-c6b8ea9c3a01"


class TestImageDisplay:
    def test_image_present_wins(self):
        deployment = {
            "buildId": BUILD_ID,
            "manifest": {"spec": {"image": "docker.io/me/bot:1"}},
        }
        assert _image_display(deployment) == ("Image", "docker.io/me/bot:1")

    def test_redacted_image_falls_back_to_build_id(self):
        deployment = {"buildId": BUILD_ID, "manifest": {"spec": {}}}
        assert _image_display(deployment) == ("Build", BUILD_ID)

    def test_neither_stays_na(self):
        assert _image_display({}) == ("Image", "N/A")


class TestAgentStatusBuildId:
    def test_status_shows_build_id_for_cloud_build(self):
        payload = {
            "ready": True,
            "activeSessionCount": 0,
            "deployment": {"buildId": BUILD_ID, "manifest": {"spec": {}}},
            "activeDeploymentId": "dep-1",
            "createdAt": "2026-01-01T00:00:00.000Z",
            "updatedAt": "2026-01-01T00:00:00.000Z",
            "autoScaling": {"minReplicas": 0, "maxReplicas": 5},
            "errors": [],
        }
        with patch("pipecatcloud.cli.commands.agent.API") as mock_api:
            mock_api.agent = AsyncMock(return_value=(payload, None))
            result = runner.invoke(entrypoint_cli_typer, ["agent", "status", "some-agent"])
        assert result.exit_code == 0
        assert f"Build: {BUILD_ID}" in result.output
        assert "Image: N/A" not in result.output
