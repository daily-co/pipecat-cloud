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
from pipecatcloud.cli.commands.registry_keys import list_keys, mint_key, registry_keys_cli


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
async def test_delete_without_yes_requires_interactive(region_mocks):
    """Non-interactive + no --yes must fail fast (exit 2 via
    require_interactive), never hang on a prompt (PCC-1064 rules)."""
    _, mock_console = region_mocks
    mock_console.require_interactive.side_effect = typer.Exit(2)

    with pytest.raises(typer.Exit) as excinfo:
        await delete_region.aio("acme", yes=False, organization=None)
    assert excinfo.value.exit_code == 2
    # The hint must name --yes, not a flag that also bypasses a server guard:
    # this message is what pushes CI callers onto their next command.
    mock_console.require_interactive.assert_called_once_with("--yes")


@pytest.mark.asyncio
async def test_delete_never_sends_a_guard_bypass(region_mocks):
    """--yes skips the prompt only (PCC-1141). No force reaches the API, so
    the server's live-session/deployed-service guard always applies."""
    mock_api, _ = region_mocks
    mock_api.region_delete = AsyncMock(return_value=({}, None))

    await delete_region.aio("acme", yes=True, organization=None)

    kwargs = mock_api.region_delete.await_args.kwargs
    assert kwargs["region_key"] == "acme"
    assert "force" not in kwargs


@pytest.mark.asyncio
async def test_delete_reports_pending_propagation(region_mocks):
    """A 202 means the revocation is recorded but the cutoff was still landing.
    Reporting a flat 'revoked' would claim more than the API did."""
    mock_api, mock_console = region_mocks
    mock_api.region_delete = AsyncMock(
        return_value=({"status": "revoked", "propagation": "pending"}, None)
    )

    await delete_region.aio("acme", yes=True, organization=None)

    printed = " ".join(str(c.args[0]) for c in mock_console.print.call_args_list)
    assert "propagating" in printed


@pytest.mark.asyncio
async def test_delete_says_nothing_extra_on_a_clean_revoke(region_mocks):
    """204 is the API keeping its promise in full, so no caveat. The API client
    maps that to None explicitly (see TestNoContentResponses); this pins what
    the command does with it."""
    mock_api, mock_console = region_mocks
    mock_api.region_delete = AsyncMock(return_value=(None, None))

    await delete_region.aio("acme", yes=True, organization=None)

    mock_console.print.assert_not_called()


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


@pytest.mark.asyncio
async def test_registry_key_list_reads_registry_keys_envelope():
    """The API's list envelope is `registry_keys`, not `keys` — reading the
    wrong field renders every org as having no keys (cli#192 review)."""
    with (
        patch("pipecatcloud.cli.commands.registry_keys.API") as mock_api,
        patch("pipecatcloud.cli.commands.registry_keys.console") as mock_console,
    ):
        mock_console.json_output = True
        mock_api.registry_keys = AsyncMock(
            return_value=({"registry_keys": [{"id": "k1", "name": "ws"}]}, None)
        )

        await list_keys.aio(organization=None)

        out = mock_console.output_json.call_args.args[0]
        assert out["keys"] == [{"id": "k1", "name": "ws"}]


@pytest.mark.asyncio
async def test_registry_key_mint_requires_name_when_noninteractive():
    """The API's mint schema requires a name; without a terminal to prompt in,
    the CLI must exit 2 before the request instead of collecting a 400."""
    with (
        patch("pipecatcloud.cli.commands.registry_keys.API") as mock_api,
        patch("pipecatcloud.cli.commands.registry_keys.console") as mock_console,
    ):
        mock_console.is_terminal = False
        mock_console.json_output = False
        mock_api.registry_key_mint = AsyncMock()

        with pytest.raises(typer.Exit) as excinfo:
            await mint_key.aio(name=None, organization=None)
        assert excinfo.value.exit_code == 2
        mock_api.registry_key_mint.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_does_not_prompt_in_json_mode(region_mocks):
    """`--output json` from a terminal is a scripting context: the display-name
    question must not interrupt it (cli#192 review)."""
    mock_api, mock_console = region_mocks
    mock_console.is_terminal = True
    mock_console.json_output = True
    mock_api.region_register = AsyncMock(return_value=({}, None))

    with patch("pipecatcloud.cli.commands.regions.questionary") as mock_questionary:
        await register_region.aio(
            "acme",
            workloads_namespace=None,
            architectures=None,
            default_architecture=None,
            ws_public_endpoint=None,
            display_name=None,
            organization=None,
        )

    mock_questionary.text.assert_not_called()
    assert "displayName" not in mock_api.region_register.await_args.kwargs["payload"]


def test_existing_regions_commands_untouched():
    """The new verbs are additive: `list` survives, and the lifecycle verbs
    are registered under the expected names."""
    names = {cmd.name for cmd in regions_cli.registered_commands}
    assert {"list", "register", "show", "delete", "enroll-token"} <= names
    rk_names = {cmd.name for cmd in registry_keys_cli.registered_commands}
    assert rk_names == {"mint", "list", "revoke"}
