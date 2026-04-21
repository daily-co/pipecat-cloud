"""
Unit tests for auth flow internals.

Covers PKCE generation, callback server edge cases, token refresh,
discovery validation, and logout resilience.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.commands.auth import (
    _fetch_oidc_discovery,
    _generate_code_challenge,
    _generate_code_verifier,
    _start_callback_server,
    refresh_access_token,
)

# ---- PKCE (RFC 7636) ----


class TestPKCE:
    """Test PKCE code verifier and challenge generation."""

    def test_rfc7636_appendix_b_vector(self):
        """RFC 7636 Appendix B reference vector."""
        verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
        expected_challenge = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assert _generate_code_challenge(verifier) == expected_challenge

    def test_verifier_length_in_range(self):
        """RFC 7636 §4.1: verifier must be 43-128 characters."""
        verifier = _generate_code_verifier()
        assert 43 <= len(verifier) <= 128

    def test_verifier_is_url_safe(self):
        """Verifier must only contain unreserved URI characters."""
        import re

        verifier = _generate_code_verifier()
        assert re.fullmatch(r"[A-Za-z0-9_-]+", verifier)

    def test_challenge_has_no_padding(self):
        """BASE64URL encoding must strip '=' padding per RFC 7636 §4.2."""
        challenge = _generate_code_challenge("test-verifier-string-that-is-long-enough")
        assert "=" not in challenge


# ---- Callback server ----


class TestCallbackServer:
    """Test the localhost OAuth callback server edge cases."""

    @pytest.mark.asyncio
    async def test_callback_error_sets_none_result(self):
        """OAuth error in callback should resolve the future with (None, None)."""
        runner, port, result_future = await _start_callback_server()
        try:
            async with __import__("aiohttp").ClientSession() as session:
                await session.get(f"http://127.0.0.1:{port}/oauth_callback?error=access_denied")
            code, state = result_future.result()
            assert code is None
            assert state is None
        finally:
            await runner.cleanup()

    @pytest.mark.asyncio
    async def test_callback_missing_code_returns_none_code(self):
        """Callback with no code or error should return (None, state)."""
        runner, port, result_future = await _start_callback_server()
        try:
            async with __import__("aiohttp").ClientSession() as session:
                await session.get(f"http://127.0.0.1:{port}/oauth_callback?state=test-state")
            code, state = result_future.result()
            # code is None because query param "code" is absent
            assert code is None
            assert state == "test-state"
        finally:
            await runner.cleanup()

    @pytest.mark.asyncio
    async def test_callback_success_returns_code_and_state(self):
        """Successful callback should return the code and state."""
        runner, port, result_future = await _start_callback_server()
        try:
            async with __import__("aiohttp").ClientSession() as session:
                await session.get(
                    f"http://127.0.0.1:{port}/oauth_callback?code=auth-code-123&state=state-456"
                )
            code, state = result_future.result()
            assert code == "auth-code-123"
            assert state == "state-456"
        finally:
            await runner.cleanup()

    @pytest.mark.asyncio
    async def test_callback_timeout(self):
        """The future should raise TimeoutError if no callback arrives."""
        runner, _port, result_future = await _start_callback_server()
        try:
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(result_future, timeout=0.1)
        finally:
            await runner.cleanup()


# ---- Discovery validation ----


class TestOIDCDiscoveryValidation:
    """Test OIDC discovery metadata validation."""

    def _make_valid_doc(self, issuer="https://auth.example.com"):
        return {
            "issuer": issuer,
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
            "code_challenge_methods_supported": ["S256"],
            "response_types_supported": ["code"],
        }

    def _mock_fetch(self, doc):
        """Create a mock aiohttp response returning the given doc."""
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=doc)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        return patch(
            "pipecatcloud.cli.commands.auth.aiohttp.ClientSession", return_value=mock_session
        )

    @pytest.mark.asyncio
    async def test_valid_document_passes(self):
        issuer = "https://auth.example.com"
        doc = self._make_valid_doc(issuer)
        with self._mock_fetch(doc):
            result = await _fetch_oidc_discovery(issuer)
        assert result["authorization_endpoint"] == "https://auth.example.com/authorize"

    @pytest.mark.asyncio
    async def test_issuer_mismatch_raises(self):
        doc = self._make_valid_doc("https://wrong-issuer.example.com")
        with self._mock_fetch(doc):
            with pytest.raises(RuntimeError, match="issuer mismatch"):
                await _fetch_oidc_discovery("https://auth.example.com")

    @pytest.mark.asyncio
    async def test_http_endpoint_raises(self):
        doc = self._make_valid_doc()
        doc["token_endpoint"] = "http://auth.example.com/token"  # HTTP, not HTTPS
        with self._mock_fetch(doc):
            with pytest.raises(RuntimeError, match="must use HTTPS"):
                await _fetch_oidc_discovery("https://auth.example.com")

    @pytest.mark.asyncio
    async def test_missing_s256_raises(self):
        doc = self._make_valid_doc()
        doc["code_challenge_methods_supported"] = ["plain"]
        with self._mock_fetch(doc):
            with pytest.raises(RuntimeError, match="S256"):
                await _fetch_oidc_discovery("https://auth.example.com")

    @pytest.mark.asyncio
    async def test_missing_code_response_type_raises(self):
        doc = self._make_valid_doc()
        doc["response_types_supported"] = ["token"]
        with self._mock_fetch(doc):
            with pytest.raises(RuntimeError, match="code"):
                await _fetch_oidc_discovery("https://auth.example.com")

    @pytest.mark.asyncio
    async def test_absent_optional_fields_passes(self):
        """When code_challenge_methods_supported / response_types_supported
        are absent, validation should pass (they're optional in the spec)."""
        doc = self._make_valid_doc()
        del doc["code_challenge_methods_supported"]
        del doc["response_types_supported"]
        with self._mock_fetch(doc):
            result = await _fetch_oidc_discovery("https://auth.example.com")
        assert result["issuer"] == "https://auth.example.com"


# ---- Token refresh ----


class TestTokenRefresh:
    """Test refresh_access_token success and failure paths."""

    def _mock_oauth_and_oidc(self):
        """Patch both discovery fetches to return valid config."""
        oauth_config = {
            "issuer": "https://auth.example.com",
            "client_id": "test-client-id",
            "scopes": "openid profile",
        }
        oidc_doc = {
            "issuer": "https://auth.example.com",
            "authorization_endpoint": "https://auth.example.com/authorize",
            "token_endpoint": "https://auth.example.com/token",
        }
        return (
            patch(
                "pipecatcloud.cli.commands.auth._fetch_oauth_config",
                new_callable=AsyncMock,
                return_value=oauth_config,
            ),
            patch(
                "pipecatcloud.cli.commands.auth._fetch_oidc_discovery",
                new_callable=AsyncMock,
                return_value=oidc_doc,
            ),
        )

    @pytest.mark.asyncio
    async def test_refresh_success(self):
        mock_oauth, mock_oidc = self._mock_oauth_and_oidc()
        token_response = {
            "access_token": "new-token",
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value=token_response)
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with mock_oauth, mock_oidc:
            with patch(
                "pipecatcloud.cli.commands.auth.aiohttp.ClientSession", return_value=mock_session
            ):
                result = await refresh_access_token("old-refresh-token")

        assert result["access_token"] == "new-token"

    @pytest.mark.asyncio
    async def test_refresh_failure_returns_none(self):
        mock_oauth, mock_oidc = self._mock_oauth_and_oidc()

        mock_resp = AsyncMock()
        mock_resp.status = 401
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=False)

        mock_session = AsyncMock()
        mock_session.post = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with mock_oauth, mock_oidc:
            with patch(
                "pipecatcloud.cli.commands.auth.aiohttp.ClientSession", return_value=mock_session
            ):
                result = await refresh_access_token("bad-refresh-token")

        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_config_fetch_failure_returns_none(self):
        with patch(
            "pipecatcloud.cli.commands.auth._fetch_oauth_config",
            new_callable=AsyncMock,
            side_effect=RuntimeError("unreachable"),
        ):
            result = await refresh_access_token("some-refresh-token")

        assert result is None


# ---- Logout without config file ----


class TestLogoutWithoutConfig:
    """Test that remove_user_config is idempotent."""

    def test_remove_missing_config_does_not_raise(self):
        from pipecatcloud.cli.config import remove_user_config

        with patch("pipecatcloud.cli.config.user_config_path", "/nonexistent/path/config.toml"):
            # Should not raise
            remove_user_config()
