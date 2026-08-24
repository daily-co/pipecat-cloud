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


def _run(mock_api, skip_confirm=True):
    return create_set.aio(
        name="bot-keys",
        secrets=["KEY1=value1"],
        from_file=None,
        skip_confirm=skip_confirm,
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
async def test_referenced_set_refuses_before_the_confirmation_prompt(set_mocks):
    """The refusal must land before the confirmation, not after it (cli#197
    review). Asking "proceed with these secrets?" and only then saying the
    command does not apply to this set wastes the one answer we ask for."""
    set_mocks.secrets_get = AsyncMock(
        return_value=(
            {"secrets": [], "region": "onprem-a", "source": "referenced", "status": "ready"},
            None,
        )
    )
    set_mocks.properties = AsyncMock(return_value=({"defaultRegion": "us-west"}, None))

    with patch("pipecatcloud.cli.commands.secrets.questionary") as mock_questionary:
        # Answer "yes" if we are asked, so reaching the prompt fails on the
        # assertion below rather than on an unawaitable mock.
        mock_questionary.confirm.return_value.ask_async = AsyncMock(return_value=True)
        with pytest.raises(typer.Exit) as excinfo:
            await _run(set_mocks, skip_confirm=False)

    assert excinfo.value.exit_code == 1
    mock_questionary.confirm.assert_not_called()
    # The region lookup only exists to render the confirmation panel, so it is
    # a second witness that nothing was shown before the refusal.
    set_mocks.properties.assert_not_awaited()
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
