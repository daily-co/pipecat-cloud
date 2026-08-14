"""Tests for the self-hosted region lifecycle verbs (PCC-1103):
`regions register/show/delete/enroll-token` and
`organizations registry-keys mint/list/revoke`.

Pins the load-bearing properties: omit-preserves registration payloads (only
what the caller passed goes on the wire), show-once values reaching JSON
output, real non-zero exits on API errors, and the non-interactive guard on
destructive verbs.
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import typer

# Import from source, not installed package
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.regions import (
    delete_region,
    enroll_token,
    regions_cli,
    register_region,
    show_region,
)
from pipecatcloud.cli.commands.registry_keys import mint_key, registry_keys_cli


@pytest.fixture
def region_mocks():
    with (
        patch("pipecatcloud.cli.commands.regions.API") as mock_api,
        patch("pipecatcloud.cli.commands.regions.console") as mock_console,
    ):
        mock_console.is_terminal = False
        mock_console.json_output = False
        mock_console.rich_output = False
        yield mock_api, mock_console


@pytest.mark.asyncio
async def test_register_sends_only_provided_fields(region_mocks):
    """Omit-preserves: absent options must be absent from the payload, or a
    partial re-register would clobber stored fields with nulls."""
    mock_api, _ = region_mocks
    mock_api.region_register = AsyncMock(return_value=({"region_key": "acme"}, None))

    await register_region.aio(
        "acme",
        workloads_namespace=None,
        architectures=None,
        default_architecture=None,
        ws_public_endpoint="wss://ws.acme.example",
        display_name=None,
        organization=None,
    )

    payload = mock_api.region_register.await_args.kwargs["payload"]
    assert payload == {
        "regionKey": "acme",
        "wsPublicEndpoint": "wss://ws.acme.example",
    }


@pytest.mark.asyncio
async def test_register_splits_architectures(region_mocks):
    mock_api, _ = region_mocks
    mock_api.region_register = AsyncMock(return_value=({}, None))

    await register_region.aio(
        "acme",
        workloads_namespace=None,
        architectures=" amd64, arm64 ",
        default_architecture="amd64",
        ws_public_endpoint=None,
        display_name=None,
        organization=None,
    )

    payload = mock_api.region_register.await_args.kwargs["payload"]
    assert payload["supportedArchitectures"] == ["amd64", "arm64"]
    assert payload["defaultArchitecture"] == "amd64"


@pytest.mark.asyncio
async def test_register_api_error_exits_nonzero(region_mocks):
    mock_api, _ = region_mocks
    mock_api.region_register = AsyncMock(return_value=(None, {"error": "nope"}))

    with pytest.raises(typer.Exit) as excinfo:
        await register_region.aio(
            "acme",
            workloads_namespace=None,
            architectures=None,
            default_architecture=None,
            ws_public_endpoint=None,
            display_name=None,
            organization=None,
        )
    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_show_unknown_region_exits_nonzero(region_mocks):
    mock_api, _ = region_mocks
    mock_api.region_get = AsyncMock(return_value=(None, None))

    with pytest.raises(typer.Exit) as excinfo:
        await show_region.aio("ghost", organization=None)
    assert excinfo.value.exit_code == 1


@pytest.mark.asyncio
async def test_delete_without_force_requires_interactive(region_mocks):
    """Non-interactive + no --force must fail fast (exit 2 via
    require_interactive), never hang on a prompt (PCC-1064 rules)."""
    _, mock_console = region_mocks
    mock_console.require_interactive.side_effect = typer.Exit(2)

    with pytest.raises(typer.Exit) as excinfo:
        await delete_region.aio("acme", force=False, organization=None)
    assert excinfo.value.exit_code == 2
    mock_console.require_interactive.assert_called_once_with("--force")


@pytest.mark.asyncio
async def test_delete_force_passes_force_to_api(region_mocks):
    mock_api, _ = region_mocks
    mock_api.region_delete = AsyncMock(return_value=({}, None))

    await delete_region.aio("acme", force=True, organization=None)

    kwargs = mock_api.region_delete.await_args.kwargs
    assert kwargs["force"] is True
    assert kwargs["region_key"] == "acme"


@pytest.mark.asyncio
async def test_enroll_token_reaches_json_output(region_mocks):
    """The one-time token is shown once: it must land in the JSON payload
    together with the ready-to-paste kubectl command."""
    mock_api, mock_console = region_mocks
    mock_console.json_output = True
    mock_api.region_enroll_token = AsyncMock(return_value=({"token": "tok-123"}, None))

    await enroll_token.aio("acme", organization=None)

    out = mock_console.output_json.call_args.args[0]
    assert out["token"] == "tok-123"
    assert "tok-123" in out["kubectlCommand"]
    assert "pipecat-region-enroll-token" in out["kubectlCommand"]


@pytest.mark.asyncio
async def test_registry_key_mint_reaches_json_output():
    with (
        patch("pipecatcloud.cli.commands.registry_keys.API") as mock_api,
        patch("pipecatcloud.cli.commands.registry_keys.console") as mock_console,
    ):
        mock_console.json_output = True
        mock_api.registry_key_mint = AsyncMock(
            return_value=({"id": "k1", "name": "ws", "key": "pcc_reg_abc", "username": "pcc"}, None)
        )

        await mint_key.aio(name="ws", organization=None)

        out = mock_console.output_json.call_args.args[0]
        assert out["key"] == "pcc_reg_abc"
        assert "pcc_reg_abc" in out["helmLoginCommand"]
        assert "registry.pipecat.daily.co" in out["helmLoginCommand"]


def test_existing_regions_commands_untouched():
    """The new verbs are additive: `list` survives, and the lifecycle verbs
    are registered under the expected names."""
    names = {cmd.name for cmd in regions_cli.registered_commands}
    assert {"list", "register", "show", "delete", "enroll-token"} <= names
    rk_names = {cmd.name for cmd in registry_keys_cli.registered_commands}
    assert rk_names == {"mint", "list", "revoke"}
