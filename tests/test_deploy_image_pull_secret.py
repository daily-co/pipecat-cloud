"""
Regression tests for the image pull secret existence check in `pcc deploy`.

PCC-195: the deploy command used to verify an image pull secret by calling the
per-set secrets GET endpoint and treating its HTTP 400 as "exists". That hack
also passed a regular (non-image-pull) secret that happened to share the name,
and the "not found" branch never actually aborted the deploy. The check now
lists secret sets and matches on name + type == "imagePullSecret", aborting when
no match is found.
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
        image_credentials="mycreds",
    )


@pytest.fixture
def deploy_mocks():
    """Patch the external collaborators of the CLI `_deploy` flow.

    `console` and `Live` are stubbed so nothing renders; `API.agent` reports no
    existing deployment (so no confirmation prompt), and `API.deploy` returns an
    error to halt the flow immediately after the image pull secret guard.
    """
    with (
        patch("pipecatcloud.cli.commands.deploy.API") as mock_api,
        patch("pipecatcloud.cli.commands.deploy.Live"),
        patch("pipecatcloud.cli.commands.deploy.console"),
    ):
        mock_api.agent = AsyncMock(return_value=(None, None))
        mock_api.deploy = AsyncMock(return_value=(None, {"code": "stop"}))
        yield mock_api


@pytest.mark.asyncio
async def test_matching_image_pull_secret_passes_guard(deploy_mocks):
    """A set with matching name and type 'imagePullSecret' lets the deploy proceed."""
    # Arrange
    deploy_mocks.secrets_list = AsyncMock(
        return_value=(
            [{"name": "mycreds", "type": "imagePullSecret", "region": "us-west-2"}],
            None,
        )
    )

    # Act
    await _deploy(_make_params(), "test-org", force=True)

    # Assert: guard passed, so deployment was attempted
    deploy_mocks.deploy.assert_awaited_once()


@pytest.mark.asyncio
async def test_same_name_wrong_type_is_rejected(deploy_mocks):
    """A regular secret sharing the name must NOT satisfy the image pull check (PCC-195)."""
    # Arrange
    deploy_mocks.secrets_list = AsyncMock(
        return_value=(
            [{"name": "mycreds", "type": "secret", "region": "us-west-2"}],
            None,
        )
    )

    # Act
    result = await _deploy(_make_params(), "test-org", force=True)

    # Assert: aborted before attempting deployment
    assert isinstance(result, typer.Exit)
    deploy_mocks.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_secret_is_rejected(deploy_mocks):
    """No matching set aborts the deploy before calling API.deploy."""
    # Arrange
    deploy_mocks.secrets_list = AsyncMock(return_value=([], None))

    # Act
    result = await _deploy(_make_params(), "test-org", force=True)

    # Assert
    assert isinstance(result, typer.Exit)
    deploy_mocks.deploy.assert_not_awaited()


@pytest.mark.asyncio
async def test_secrets_list_error_aborts(deploy_mocks):
    """An error while listing secrets aborts the deploy."""
    # Arrange
    deploy_mocks.secrets_list = AsyncMock(return_value=(None, {"code": "500"}))

    # Act
    result = await _deploy(_make_params(), "test-org", force=True)

    # Assert
    assert isinstance(result, typer.Exit)
    deploy_mocks.deploy.assert_not_awaited()
