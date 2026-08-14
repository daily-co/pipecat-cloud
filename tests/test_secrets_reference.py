"""
Tests for `secrets reference` (PCC-1021): referencing an existing Kubernetes
Secret in a self-hosted (enterprise) region.

Backward compatibility is the load-bearing property: this is a net-new
subcommand, so these tests also pin that the existing secrets commands are
untouched (the command registry still exposes them unchanged).
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.secrets import reference_secret, secrets_cli


@pytest.fixture
def reference_mocks():
    with (
        patch("pipecatcloud.cli.commands.secrets.API") as mock_api,
        patch("pipecatcloud.cli.commands.secrets.console"),
        patch(
            "pipecatcloud.cli.commands.secrets.validate_region",
            new=AsyncMock(return_value=True),
        ),
    ):
        mock_api.secrets_reference = AsyncMock(
            return_value=(
                {"name": "bot-keys", "region": "onprem-a", "type": "secret", "status": "ready"},
                None,
            )
        )
        yield mock_api


@pytest.mark.asyncio
async def test_reference_calls_api_with_name_region_org(reference_mocks):
    await reference_secret.aio(name="bot-keys", region="onprem-a", organization="acme")

    reference_mocks.secrets_reference.assert_awaited_once_with(
        name="bot-keys", region="onprem-a", org="acme"
    )


@pytest.mark.asyncio
async def test_reference_api_error_exits_nonzero(reference_mocks):
    """Server-side rejections (secret missing, cloud region, unreachable) exit 1."""
    reference_mocks.secrets_reference = AsyncMock(return_value=(None, {"code": "400"}))

    with pytest.raises(typer.Exit) as excinfo:
        await reference_secret.aio(name="nope", region="onprem-a", organization="acme")

    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_invalid_region_rejected_client_side():
    """A region the org can't use fails before any API call."""
    with (
        patch("pipecatcloud.cli.commands.secrets.API") as mock_api,
        patch("pipecatcloud.cli.commands.secrets.console"),
        patch(
            "pipecatcloud.cli.commands.secrets.validate_region",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "pipecatcloud.cli.commands.secrets.get_region_codes",
            new=AsyncMock(return_value=["us-west"]),
        ),
    ):
        mock_api.secrets_reference = AsyncMock()

        with pytest.raises(typer.Exit) as excinfo:
            await reference_secret.aio(name="bot-keys", region="bogus", organization="acme")

        assert excinfo.value.exit_code == 1
        mock_api.secrets_reference.assert_not_awaited()


def test_existing_commands_are_untouched():
    """Backward compatibility: the pre-existing command surface is unchanged and
    `reference` is purely additive."""
    names = {cmd.name for cmd in secrets_cli.registered_commands}
    assert {"set", "unset", "list", "delete"}.issubset(names)
    assert "reference" in names
