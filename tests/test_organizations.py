"""Tests for the organizations commands.

Covers the column labels shared with the dashboard and the API-key create
flow's default-key messaging.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.entry_point import entrypoint_cli_typer

runner = CliRunner()

ORG_LIST_PAYLOAD = [
    {"name": "gradientbang", "verboseName": "Gradient Bang"},
    {"name": "test-org", "verboseName": "Default Workspace"},
]

KEY_CREATE_ARGS = ["organizations", "keys", "create", "--name", "Pipecat Key"]


class TestOrganizationColumnLabels:
    """`verboseName` is "Organization Name" and `name` is "Organization ID",
    matching the dashboard."""

    def test_rich_list_labels_both_columns(self):
        with patch("pipecatcloud.cli.commands.organizations.API") as mock_api:
            mock_api.organizations = AsyncMock(return_value=(ORG_LIST_PAYLOAD, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["--output", "rich", "organizations", "list"]
            )
        assert result.exit_code == 0
        assert "Organization Name" in result.output
        assert "Organization ID" in result.output

    def test_rich_id_cell_holds_only_the_identifier(self):
        # conftest pins the active org to "test-org". The active marker must
        # not end up inside the cell users copy into --organization.
        with patch("pipecatcloud.cli.commands.organizations.API") as mock_api:
            mock_api.organizations = AsyncMock(return_value=(ORG_LIST_PAYLOAD, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["--output", "rich", "organizations", "list"]
            )
        assert result.exit_code == 0
        assert "test-org (active)" not in result.output
        assert "active" in result.output

    def test_plain_list_labels_both_columns(self):
        with patch("pipecatcloud.cli.commands.organizations.API") as mock_api:
            mock_api.organizations = AsyncMock(return_value=(ORG_LIST_PAYLOAD, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["--output", "plain", "organizations", "list"]
            )
        assert result.exit_code == 0
        assert "Organization Name\tOrganization ID\tActive" in result.output

    def test_json_list_keeps_server_field_names(self):
        import json

        with patch("pipecatcloud.cli.commands.organizations.API") as mock_api:
            mock_api.organizations = AsyncMock(return_value=(ORG_LIST_PAYLOAD, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["--output", "json", "organizations", "list"]
            )
        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"organizations": ORG_LIST_PAYLOAD}


class TestApiKeyCreateDefaultMessaging:
    """The success panel's subtitle must agree with whether the key was
    actually stored as the local default."""

    def _create(self, extra_args=()):
        with (
            patch("pipecatcloud.cli.commands.organizations.API") as mock_api,
            patch("pipecatcloud.cli.commands.organizations.update_user_config") as mock_update,
        ):
            mock_api.api_key_create = AsyncMock(return_value=({"key": "pk_abc123"}, None))
            result = runner.invoke(
                entrypoint_cli_typer, ["--output", "rich", *KEY_CREATE_ARGS, *extra_args]
            )
        return result, mock_update

    def test_declined_default_is_not_reported_as_active(self):
        # Without --default and with a non-interactive stdin the key is not
        # stored, so the panel must not claim otherwise.
        result, mock_update = self._create()
        assert result.exit_code == 0
        assert "Bypassing using key as default" in result.output
        assert "Using as default in local config" not in result.output
        mock_update.assert_not_called()

    def test_default_flag_is_reported_as_active(self):
        result, mock_update = self._create(["--default"])
        assert result.exit_code == 0
        assert "Using as default in local config" in result.output
        mock_update.assert_called_once()
