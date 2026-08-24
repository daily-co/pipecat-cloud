"""
Regression tests for the secret set existence check in `pcc deploy`.

The check used to call the per-set endpoint through `API.secrets_list`, which
returns the set's array of key NAMES, and then tested that array for
truthiness. Referenced secret sets (self-hosted regions, PCC-1020) carry no
key-name rows at all by design: Daily stores name + region + readiness and
never the contents. So a perfectly healthy referenced set came back as `[]`
and the deploy aborted with "not found in organization", making every
referenced set undeployable.

The check now fetches the set itself and only aborts when nothing comes back.
Readiness remains a server-side deploy gate.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams
from pipecatcloud.cli.commands.deploy import _deploy


def _make_params():
    return DeployConfigParams(
        agent_name="test-agent",
        image="test:latest",
        secret_set="mysecrets",
    )


@pytest.fixture
def deploy_mocks():
    """Patch the external collaborators of the CLI `_deploy` flow.

    `console` is stubbed so nothing renders; `API.agent` reports no existing
    deployment (so no confirmation prompt), and `API.deploy` returns an error to
    halt the flow immediately after the secret set guard.
    """
    with (
        patch("pipecatcloud.cli.commands.deploy.API") as mock_api,
        patch("pipecatcloud.cli.commands.deploy.console"),
    ):
        mock_api.agent = AsyncMock(return_value=(None, None))
        mock_api.deploy = AsyncMock(return_value=(None, {"code": "stop"}))
        yield mock_api


@pytest.mark.asyncio
async def test_referenced_set_with_no_key_names_passes_guard(deploy_mocks):
    """A referenced set reports an empty `secrets` array and must still deploy."""
    # Arrange: exactly what the API returns for backend='reference'
    deploy_mocks.secrets_get = AsyncMock(
        return_value=(
            {"secrets": [], "region": "hush-0", "source": "referenced", "status": "ready"},
            None,
        )
    )

    # Act: the mocked API.deploy error halts the flow right after the guard,
    # which surfaces as a raised typer.Exit.
    with pytest.raises(typer.Exit):
        await _deploy(_make_params(), "test-org", force=True)

    # Assert: guard passed, so deployment was attempted
    deploy_mocks.deploy.assert_awaited_once()


@pytest.mark.asyncio
async def test_managed_set_passes_guard(deploy_mocks):
    """A managed set with key names keeps working."""
    # Arrange
    deploy_mocks.secrets_get = AsyncMock(
        return_value=(
            {
                "secrets": ["OPENAI_API_KEY"],
                "region": "us-west",
                "source": "managed",
                "status": "ready",
            },
            None,
        )
    )

    # Act & Assert
    with pytest.raises(typer.Exit):
        await _deploy(_make_params(), "test-org", force=True)
    deploy_mocks.deploy.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_set_is_rejected(deploy_mocks):
    """A set that does not exist still aborts before calling API.deploy."""
    # Arrange: `not_found_is_empty=True` turns a 404 into None
    deploy_mocks.secrets_get = AsyncMock(return_value=(None, None))

    # Act & Assert
    with pytest.raises(typer.Exit):
        await _deploy(_make_params(), "test-org", force=True)
    deploy_mocks.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_secrets_get_error_aborts(deploy_mocks):
    """An error while fetching the set aborts the deploy."""
    # Arrange
    deploy_mocks.secrets_get = AsyncMock(return_value=(None, {"code": "500"}))

    # Act & Assert
    with pytest.raises(typer.Exit):
        await _deploy(_make_params(), "test-org", force=True)
    deploy_mocks.deploy.assert_not_awaited()
