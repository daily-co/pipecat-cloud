"""Tests for the spend-limit CLI commands and API client methods."""

import json
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import typer

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.console_utils import PipecatConsole, format_cents
from pipecatcloud.api import _API
from pipecatcloud.cli.commands.spend_limit import (
    _parse_amount_to_cents,
    _render_show,
    clear,
    set_limit,
    show,
)

# ---- API client ----


class TestSpendLimitAPI:
    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_get_calls_correct_path(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "limitCents": 5000,
                "currentSpendCents": 1234,
                "periodStart": "2026-05-01T00:00:00Z",
                "periodEnd": "2026-06-01T00:00:00Z",
                "blocked": False,
                "blockedAt": None,
            }

            result = await api_client._spend_limit_get(org="test-org")

            assert result["limitCents"] == 5000
            assert result["currentSpendCents"] == 1234
            mock_request.assert_called_once()
            args, kwargs = mock_request.call_args
            assert args[0] == "GET"
            assert args[1].endswith("/v1/organizations/test-org/spend-limit")
            assert kwargs.get("not_found_is_empty") is True

    @pytest.mark.asyncio
    async def test_update_sends_put_with_limit_cents(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"limitCents": 5000}

            await api_client._spend_limit_update(org="test-org", limit_cents=5000)

            mock_request.assert_called_once()
            args, kwargs = mock_request.call_args
            assert args[0] == "PUT"
            assert args[1].endswith("/v1/organizations/test-org/spend-limit")
            assert kwargs["json"] == {"limitCents": 5000}

    @pytest.mark.asyncio
    async def test_update_with_none_clears_limit(self, api_client):
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"limitCents": None}

            await api_client._spend_limit_update(org="test-org", limit_cents=None)

            args, kwargs = mock_request.call_args
            assert kwargs["json"] == {"limitCents": None}


# ---- Amount parsing ----


class TestParseAmountToCents:
    @pytest.mark.parametrize(
        ("amount", "expected"),
        [
            ("0", 0),
            ("1", 100),
            ("50", 5000),
            ("12.34", 1234),
            ("0.50", 50),
            ("0.05", 5),
        ],
    )
    def test_dollar_amounts(self, amount, expected):
        assert _parse_amount_to_cents(amount) == expected

    def test_rejects_negative_dollars(self):
        with pytest.raises(typer.BadParameter):
            _parse_amount_to_cents("-5")

    def test_rejects_more_than_two_decimal_places(self):
        with pytest.raises(typer.BadParameter):
            _parse_amount_to_cents("1.234")

    def test_rejects_non_numeric(self):
        with pytest.raises(typer.BadParameter):
            _parse_amount_to_cents("abc")


# ---- Cents formatting ----


class TestFormatCents:
    def test_round_dollars(self):
        assert format_cents(5000) == "$50.00"

    def test_sub_dollar(self):
        assert format_cents(5) == "$0.05"

    def test_zero(self):
        assert format_cents(0) == "$0.00"

    def test_none(self):
        assert format_cents(None) == "no limit"

    def test_large_value_is_exact(self):
        # Integer arithmetic must not lose precision the way float division
        # could for very large cent counts.
        assert format_cents(123_456_789_012_345) == "$1234567890123.45"

    def test_negative(self):
        # Not exercised in practice (parser rejects negatives), but the
        # formatter should not produce something like "$-1.-23".
        assert format_cents(-1234) == "-$12.34"


# ---- SPEND_LIMIT_REACHED wrapping ----


class TestSpendLimitReachedRendering:
    """Verify that api_error renders the wrapped remediation message."""

    def _render(self, error: dict) -> str:
        buffer = StringIO()
        # force_terminal=False so Rich does not emit color codes that would
        # complicate substring matching.
        console = PipecatConsole(file=buffer, force_terminal=False, width=120)
        console.api_error(error)
        return buffer.getvalue()

    def test_includes_usage_when_fields_present(self):
        output = self._render(
            {
                "error": "Spend limit reached",
                "code": "SPEND_LIMIT_REACHED",
                "limitCents": 5000,
                "currentSpendCents": 5234,
            }
        )
        assert "spend limit" in output.lower()
        assert "$52.34" in output
        assert "$50.00" in output
        assert "spend-limit set" in output
        assert "spend-limit clear" in output

    def test_falls_back_without_fields(self):
        output = self._render(
            {
                "error": "Spend limit reached",
                "code": "SPEND_LIMIT_REACHED",
            }
        )
        assert "spend limit" in output.lower()
        assert "spend-limit set" in output

    def test_generic_error_unchanged(self):
        output = self._render(
            {
                "error": "Bad request",
                "code": "BAD_REQUEST",
            }
        )
        assert "spend-limit set" not in output
        assert "Bad request" in output


# ---- CLI command surfaces ----
#
# These tests exercise the sync wrappers produced by @synchronizer.create_blocking,
# matching the pattern in test_agent_stop.py.


class TestRenderShow:
    """Direct render tests guard against divide-by-zero and missing-field issues."""

    def test_zero_limit_does_not_divide_by_zero(self, capsys):
        # Should not raise. $0 is a valid "block everything" state and we want
        # the limit to render even though we cannot compute a percentage.
        _render_show(
            {
                "limitCents": 0,
                "currentSpendCents": 0,
                "blocked": False,
            }
        )
        out = capsys.readouterr().out
        assert "$0.00" in out

    def test_no_limit_renders_without_percentage(self, capsys):
        _render_show(
            {
                "limitCents": None,
                "currentSpendCents": 0,
                "blocked": False,
            }
        )
        out = capsys.readouterr().out
        assert "no limit set" in out
        # The percent suffix should not appear without a positive limit.
        assert "%" not in out


class TestSpendLimitShowCommand:
    def test_show_json_prints_payload(self, capsys):
        payload = {
            "limitCents": 5000,
            "currentSpendCents": 1234,
            "periodStart": "2026-05-01T00:00:00Z",
            "periodEnd": "2026-06-01T00:00:00Z",
            "blocked": False,
            "blockedAt": None,
        }

        mock_api = MagicMock()
        mock_api.bubble_error.return_value = mock_api
        mock_api.spend_limit_get = AsyncMock(return_value=(payload, None))

        with patch("pipecatcloud.cli.commands.spend_limit.API", mock_api):
            show(organization="test-org", output_json=True)

        captured = capsys.readouterr().out
        assert json.loads(captured) == payload


class TestSpendLimitSetCommand:
    def test_set_without_prompt_condition_works_non_interactively(self):
        """A plain limit raise shows no prompt interactively, so it must not
        demand --yes when stdin is not a terminal (guard sits inside the
        prompt branches, not above them)."""
        mock_api = MagicMock()
        mock_api.spend_limit_get = AsyncMock(return_value=({"currentSpendCents": 0}, None))
        mock_api.spend_limit_update = AsyncMock(return_value=({"limitCents": 5000}, None))

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=False),
        ):
            set_limit(amount="50", organization="test-org", yes=False)

            mock_api.spend_limit_update.assert_awaited_once_with("test-org", 5000)

    def test_set_zero_requires_terminal_or_yes(self):
        """Setting $0 blocks all sessions and always prompts, so non-TTY
        without --yes must fail fast with a usage error."""
        mock_api = MagicMock()
        mock_api.spend_limit_get = AsyncMock(return_value=({"currentSpendCents": 0}, None))
        mock_api.spend_limit_update = AsyncMock()

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=False),
        ):
            with pytest.raises(typer.Exit) as exc_info:
                set_limit(amount="0", organization="test-org", yes=False)

            assert exc_info.value.exit_code == 2
            mock_api.spend_limit_update.assert_not_called()

    def test_set_skips_prompt_when_yes_flag(self):
        mock_api = MagicMock()
        mock_api.spend_limit_get = AsyncMock(return_value=({"currentSpendCents": 0}, None))
        mock_api.spend_limit_update = AsyncMock(return_value=({"limitCents": 5000}, None))

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud.cli.commands.spend_limit.questionary") as mock_q,
        ):
            set_limit(
                amount="50",
                organization="test-org",
                yes=True,
            )

            mock_q.confirm.assert_not_called()
            mock_api.spend_limit_update.assert_awaited_once_with("test-org", 5000)

    def test_set_aborts_when_downgrade_rejected(self):
        mock_api = MagicMock()
        mock_api.spend_limit_get = AsyncMock(return_value=({"currentSpendCents": 10000}, None))
        mock_api.spend_limit_update = AsyncMock()

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud.cli.commands.spend_limit.questionary") as mock_q,
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=True),
        ):
            mock_q.confirm.return_value.ask_async = AsyncMock(return_value=False)

            with pytest.raises(typer.Exit) as exc_info:
                set_limit(
                    amount="50",
                    organization="test-org",
                    yes=False,
                )

            mock_q.confirm.assert_called_once()
            mock_api.spend_limit_update.assert_not_called()
            assert exc_info.value.exit_code == 1

    def test_set_aborts_when_zero_rejected(self):
        mock_api = MagicMock()
        mock_api.spend_limit_get = AsyncMock(return_value=({"currentSpendCents": 0}, None))
        mock_api.spend_limit_update = AsyncMock()

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud.cli.commands.spend_limit.questionary") as mock_q,
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=True),
        ):
            mock_q.confirm.return_value.ask_async = AsyncMock(return_value=False)

            with pytest.raises(typer.Exit) as exc_info:
                set_limit(
                    amount="0",
                    organization="test-org",
                    yes=False,
                )

            mock_q.confirm.assert_called_once()
            mock_api.spend_limit_update.assert_not_called()
            assert exc_info.value.exit_code == 1


class TestSpendLimitClearCommand:
    def test_clear_skips_prompt_when_yes_flag(self):
        mock_api = MagicMock()
        mock_api.spend_limit_update = AsyncMock(return_value=({"limitCents": None}, None))

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud.cli.commands.spend_limit.questionary") as mock_q,
        ):
            clear(organization="test-org", yes=True)

            mock_q.confirm.assert_not_called()
            mock_api.spend_limit_update.assert_awaited_once_with("test-org", None)

    def test_clear_aborts_when_user_rejects(self):
        mock_api = MagicMock()
        mock_api.spend_limit_update = AsyncMock()

        with (
            patch("pipecatcloud.cli.commands.spend_limit.API", mock_api),
            patch("pipecatcloud.cli.commands.spend_limit.questionary") as mock_q,
            patch("pipecatcloud._utils.console_utils.stdin_is_interactive", return_value=True),
        ):
            mock_q.confirm.return_value.ask_async = AsyncMock(return_value=False)

            with pytest.raises(typer.Exit) as exc_info:
                clear(organization="test-org", yes=False)

            mock_q.confirm.assert_called_once()
            mock_api.spend_limit_update.assert_not_called()
            assert exc_info.value.exit_code == 1
