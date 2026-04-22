"""
Unit tests for Personal Access Token (PAT) support.

Tests cover:
- API client PAT detection and OAuth refresh bypass
- use-pat command validation
"""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.api import _API


class TestAPIPATDetection:
    """Test API client correctly identifies and handles PATs."""

    @pytest.fixture
    def pat_client(self):
        return _API(token="pcc_pat_60ee796dc5bade735a3b0ef1b5730618", is_cli=True)

    @pytest.fixture
    def oauth_client(self):
        return _API(token="oat_SOME_CLERK_TOKEN", is_cli=True)

    @pytest.fixture
    def no_token_client(self):
        return _API(token=None, is_cli=True)

    def test_is_pat_with_pat_token(self, pat_client):
        assert pat_client._is_pat() is True

    def test_is_pat_with_oauth_token(self, oauth_client):
        assert oauth_client._is_pat() is False

    def test_is_pat_with_no_token(self, no_token_client):
        assert no_token_client._is_pat() is False

    def test_is_pat_with_sk_token(self):
        client = _API(token="sk_some-private-key", is_cli=True)
        assert client._is_pat() is False

    def test_pat_token_never_expires(self, pat_client):
        """PAT tokens should never be considered expired (no OAuth refresh)."""
        assert pat_client._is_token_expired() is False

    @patch("pipecatcloud.api.config")
    def test_oauth_token_checks_expiry(self, mock_config, oauth_client):
        """OAuth tokens should still check expiry normally."""
        # Simulate expired token
        mock_config.get.return_value = "0"  # epoch 0 = long expired
        # _is_token_expired imports cli config lazily, mock that too
        with patch("pipecatcloud.api.time") as mock_time:
            mock_time.time.return_value = 9999999999
            # Need to mock the lazy import of cli config
            with patch.dict("sys.modules", {"pipecatcloud.cli.config": MagicMock()}):
                from pipecatcloud.cli import config as cli_config_mod

                cli_config_mod.config = MagicMock()
                cli_config_mod.config.get.return_value = 0  # expired
                # The method does a lazy import, so we patch at that level
                assert oauth_client._is_pat() is False

    @pytest.mark.asyncio
    async def test_pat_skips_refresh_in_base_request(self, pat_client):
        """PAT requests should never trigger OAuth token refresh."""
        with patch.object(
            pat_client, "_refresh_oauth_token", new_callable=AsyncMock
        ) as mock_refresh:
            with patch("aiohttp.ClientSession") as mock_session_cls:
                mock_response = AsyncMock()
                mock_response.ok = True
                mock_response.json = AsyncMock(return_value={"data": "test"})
                mock_response.status = 200

                mock_session = AsyncMock()
                mock_session.request = AsyncMock(return_value=mock_response)
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session_cls.return_value = mock_session

                await pat_client._base_request("GET", "https://example.com/test")

                # OAuth refresh should never be called for PATs
                mock_refresh.assert_not_called()

    @pytest.mark.asyncio
    async def test_pat_sends_bearer_header(self, pat_client):
        """PAT should be sent as Bearer token in Authorization header."""
        headers = pat_client._configure_headers()
        assert headers["Authorization"] == "Bearer pcc_pat_60ee796dc5bade735a3b0ef1b5730618"
        assert headers["User-Agent"].startswith("PipecatCloudCLI/")


class TestUsePATCommand:
    """Test the auth use-pat command validation."""

    @pytest.mark.asyncio
    async def test_use_pat_rejects_non_pat_token(self):
        """use-pat should reject tokens without pcc_pat_ prefix."""
        from pipecatcloud.cli.commands.auth import _use_pat_impl

        with patch("pipecatcloud.cli.commands.auth.console") as mock_console:
            await _use_pat_impl("sk_not-a-pat-token")
            mock_console.error.assert_called_once()
            assert "pcc_pat_" in str(mock_console.error.call_args)

    @pytest.mark.asyncio
    async def test_use_pat_validates_and_stores_token(self):
        """use-pat should verify token against API and store credentials."""
        from pipecatcloud.cli.commands.auth import _use_pat_impl

        with (
            patch(
                "pipecatcloud.cli.commands.auth._get_account_org", new_callable=AsyncMock
            ) as mock_get_org,
            patch("pipecatcloud.cli.commands.auth.update_user_config") as mock_update,
            patch("pipecatcloud.cli.commands.auth.console") as mock_console,
            patch("pipecatcloud.cli.commands.auth.config", {"org": None}),
        ):
            mock_get_org.return_value = ("my-org", "My Organization")

            await _use_pat_impl("pcc_pat_60ee796dc5bade735a3b0ef1b5730618")

            # Should verify token (active_org=None when no org in config)
            mock_get_org.assert_called_once_with("pcc_pat_60ee796dc5bade735a3b0ef1b5730618", None)

            # Should store to config with cleared OAuth fields
            mock_update.assert_called_once_with(
                token="pcc_pat_60ee796dc5bade735a3b0ef1b5730618",
                active_org="my-org",
                refresh_token="",
                token_expires_at=0,
            )

            # Should show success
            mock_console.success.assert_called_once()

    @pytest.mark.asyncio
    async def test_use_pat_handles_invalid_token(self):
        """use-pat should show error for invalid/expired tokens."""
        from pipecatcloud.cli.commands.auth import _use_pat_impl

        with (
            patch(
                "pipecatcloud.cli.commands.auth._get_account_org", new_callable=AsyncMock
            ) as mock_get_org,
            patch("pipecatcloud.cli.commands.auth.update_user_config") as mock_update,
            patch("pipecatcloud.cli.commands.auth.console") as mock_console,
        ):
            mock_get_org.side_effect = Exception("401 Unauthorized")

            await _use_pat_impl("pcc_pat_0000000000000000000000000000dead")

            mock_console.error.assert_called_once()
            mock_update.assert_not_called()
