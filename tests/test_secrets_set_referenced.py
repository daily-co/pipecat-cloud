"""
Regression tests for the existence check in `secrets set` (sibling of the
`pcc deploy` guard fixed in cli#197).

The check fetched the set's key names and tested them for truthiness, but a
referenced secret set (PCC-1020/1021) carries no key-name rows by design, so
an existing referenced set read as "does not exist": the command would say
"Creating secret set" and then hit the server's cross-backend name-uniqueness
409. The check now fetches the set itself; a referenced set gets a clear
client-side refusal pointing at the cluster as the place its contents are
managed.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.secrets import create_set


def _run(mock_api):
    return create_set.aio(
        name="bot-keys",
        secrets=["KEY1=value1"],
        from_file=None,
        skip_confirm=True,
        organization="acme",
        region=None,
    )


@pytest.fixture
def set_mocks():
    with (
        patch("pipecatcloud.cli.commands.secrets.API") as mock_api,
        patch("pipecatcloud.cli.commands.secrets.console"),
    ):
        mock_api.secrets_upsert = AsyncMock(return_value=({"region": "us-west"}, None))
        yield mock_api


@pytest.mark.asyncio
async def test_referenced_set_is_refused_client_side(set_mocks):
    """An existing referenced set must refuse, not attempt a managed write."""
    set_mocks.secrets_get = AsyncMock(
        return_value=(
            {"secrets": [], "region": "onprem-a", "source": "referenced", "status": "ready"},
            None,
        )
    )

    with pytest.raises(typer.Exit) as excinfo:
        await _run(set_mocks)

    assert excinfo.value.exit_code == 1
    set_mocks.secrets_upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_managed_set_still_updates(set_mocks):
    """A managed set keeps the modify flow, key names read from the set object."""
    set_mocks.secrets_get = AsyncMock(
        return_value=(
            {
                "secrets": [{"fieldName": "OTHER_KEY"}],
                "region": "us-west",
                "source": "managed",
                "status": "ready",
            },
            None,
        )
    )

    await _run(set_mocks)

    set_mocks.secrets_upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_set_is_created(set_mocks):
    """No set of that name -> the create path proceeds as before."""
    set_mocks.secrets_get = AsyncMock(return_value=(None, None))

    await _run(set_mocks)

    set_mocks.secrets_upsert.assert_awaited_once()
