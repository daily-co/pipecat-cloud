"""
Unit tests for API client.

Tests follow AAA pattern and cover all API client methods including
region support, error handling, and request construction.
"""

# Import from source, not installed package
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import DeployConfigParams
from pipecatcloud.api import _API


class TestAPISecretsRegions:
    """Test API client secrets methods with region support."""

    @pytest.fixture
    def api_client(self):
        """Create an API client instance."""
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_secrets_list_without_region_filter(self, api_client):
        """Secrets list without region should not include region parameter."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"sets": []}

            # Act
            await api_client._secrets_list(org="test-org", secret_set=None, region=None)

            # Assert
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1].get("params") is None

    @pytest.mark.asyncio
    async def test_secrets_list_with_region_filter(self, api_client):
        """Secrets list with region should include region query parameter."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"sets": []}

            # Act
            await api_client._secrets_list(org="test-org", secret_set=None, region="eu")

            # Assert
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["params"] == {"region": "eu"}

    @pytest.mark.asyncio
    async def test_secrets_list_with_specific_set_and_region(self, api_client):
        """Secrets list with both set name and region should include region parameter."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"secrets": []}

            # Act
            await api_client._secrets_list(org="test-org", secret_set="my-secrets", region="ap")

            # Assert
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["params"] == {"region": "ap"}

    @pytest.mark.asyncio
    async def test_secrets_get_returns_full_response(self, api_client):
        """secrets_get returns the whole single-set response, including readiness fields."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "secrets": [{"fieldName": "API_KEY"}],
                "region": "us-west",
                "status": "ready",
            }

            # Act
            result = await api_client._secrets_get(org="test-org", secret_set="my-secrets")

            # Assert
            mock_request.assert_called_once()
            assert result == {
                "secrets": [{"fieldName": "API_KEY"}],
                "region": "us-west",
                "status": "ready",
            }

    @pytest.mark.asyncio
    async def test_secrets_get_returns_none_on_404(self, api_client):
        """secrets_get returns None when the set does not exist."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = None

            # Act
            result = await api_client._secrets_get(org="test-org", secret_set="missing")

            # Assert
            assert result is None

    @pytest.mark.asyncio
    async def test_secrets_upsert_with_region(self, api_client):
        """Secrets upsert with region should include region in payload."""
        # Arrange
        data = {"secretKey": "KEY", "secretValue": "value"}

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"status": "OK"}

            # Act
            await api_client._secrets_upsert(
                data=data, set_name="my-secrets", org="test-org", region="eu"
            )

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert payload["region"] == "eu"

    @pytest.mark.asyncio
    async def test_secrets_upsert_merges_region_into_data(self, api_client):
        """Secrets upsert should merge region into existing data payload."""
        # Arrange
        data = {"secretKey": "KEY", "secretValue": "value", "isImagePullSecret": False}

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"status": "OK"}

            # Act
            await api_client._secrets_upsert(
                data=data, set_name="my-secrets", org="test-org", region="ap"
            )

            # Assert
            payload = mock_request.call_args[1]["json"]
            assert payload["region"] == "ap"
            assert payload["secretKey"] == "KEY"
            assert payload["secretValue"] == "value"
            assert payload["isImagePullSecret"] is False

    @pytest.mark.asyncio
    async def test_secrets_upsert_without_region(self, api_client):
        """Secrets upsert without region should not include region in payload (API uses org default)."""
        # Arrange
        data = {"secretKey": "KEY", "secretValue": "value"}

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"status": "OK", "region": "us-west"}

            # Act
            await api_client._secrets_upsert(
                data=data, set_name="my-secrets", org="test-org", region=None
            )

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert "region" not in payload  # Region omitted, API uses org default


class TestAPIServicesRegions:
    """Test API client services/agents methods with region support."""

    @pytest.fixture
    def api_client(self):
        """Create an API client instance."""
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_agents_list_without_region_filter(self, api_client):
        """Agents list without region should not include region parameter."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"services": []}

            # Act
            await api_client._agents(org="test-org", region=None)

            # Assert
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1].get("params") is None

    @pytest.mark.asyncio
    async def test_agents_list_with_region_filter(self, api_client):
        """Agents list with region should include region query parameter."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"services": []}

            # Act
            await api_client._agents(org="test-org", region="eu")

            # Assert
            mock_request.assert_called_once()
            call_args = mock_request.call_args
            assert call_args[1]["params"] == {"region": "eu"}

    @pytest.mark.asyncio
    async def test_deploy_payload_includes_region(self, api_client):
        """Deploy payload should include region field when specified."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", image="test:latest", region="eu")

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert payload["region"] == "eu"

    @pytest.mark.asyncio
    async def test_deploy_payload_without_region(self, api_client):
        """Deploy payload should omit region field when not specified."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", image="test:latest", region=None)

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            # None values are removed by remove_none_values()
            assert "region" not in payload

    @pytest.mark.asyncio
    async def test_update_payload_includes_region(self, api_client):
        """Update (PUT) payload should include region field."""
        # Arrange
        config = DeployConfigParams(agent_name="test-agent", image="test:latest", region="ap")

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=True)

            # Assert
            mock_request.assert_called_once()
            payload = mock_request.call_args[1]["json"]
            assert payload["region"] == "ap"


class TestAPIRegionWithOtherParameters:
    """Test region parameters work correctly with other API parameters."""

    @pytest.fixture
    def api_client(self):
        """Create an API client instance."""
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_deploy_with_region_and_secrets(self, api_client):
        """Deploy with both region and secrets should include both in payload."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent", image="test:latest", region="eu", secret_set="my-secrets"
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            payload = mock_request.call_args[1]["json"]
            assert payload["region"] == "eu"
            assert payload["secretSet"] == "my-secrets"

    @pytest.mark.asyncio
    async def test_deploy_with_all_parameters_including_region(self, api_client):
        """Deploy with full config including region should preserve all fields."""
        # Arrange
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region="ap",
            secret_set="my-secrets",
            image_credentials="my-creds",
        )

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"success": True}

            # Act
            await api_client._deploy(config, "test-org", update=False)

            # Assert
            payload = mock_request.call_args[1]["json"]
            assert payload["region"] == "ap"
            assert payload["secretSet"] == "my-secrets"
            assert payload["imagePullSecretSet"] == "my-creds"


class TestAPIProperties:
    """Test API client organization properties methods."""

    @pytest.fixture
    def api_client(self):
        """Create an API client instance."""
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_properties_returns_properties_dict(self, api_client):
        """Properties endpoint should return the properties object."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"properties": {"defaultRegion": "us-west"}}

            # Act
            result = await api_client._properties(org="test-org")

            # Assert
            assert result == {"defaultRegion": "us-west"}
            mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_properties_schema_returns_schema_dict(self, api_client):
        """Properties schema endpoint should return schema with metadata."""
        # Arrange
        mock_schema = {
            "defaultRegion": {
                "type": "string",
                "description": "Default region for deployments",
                "readOnly": False,
                "currentValue": "us-west",
                "default": "us-west",
                "availableValues": ["us-west", "eu-central"],
            }
        }

        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"properties": mock_schema}

            # Act
            result = await api_client._properties_schema(org="test-org")

            # Assert
            assert result == mock_schema
            mock_request.assert_called_once()
            # Verify it called the /schema endpoint
            call_url = mock_request.call_args[0][1]
            assert "/schema" in call_url

    @pytest.mark.asyncio
    async def test_properties_update_sends_patch_request(self, api_client):
        """Properties update should send PATCH request with properties payload."""
        # Arrange
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"properties": {"defaultRegion": "eu-central"}}

            # Act
            result = await api_client._properties_update(
                org="test-org", properties={"defaultRegion": "eu-central"}
            )

            # Assert
            assert result == {"defaultRegion": "eu-central"}
            mock_request.assert_called_once()
            # Verify PATCH method
            assert mock_request.call_args[0][0] == "PATCH"
            # Verify payload
            payload = mock_request.call_args[1]["json"]
            assert payload == {"defaultRegion": "eu-central"}


class TestOrganizationsCurrent:
    """Test organizations_current fallback behavior."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_fallback_returns_first_org(self, api_client):
        """Regression: fallback path must index into results['organizations'], not results."""
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "organizations": [
                    {"name": "my-org", "verboseName": "My Organization"},
                    {"name": "other-org", "verboseName": "Other Org"},
                ]
            }

            result = await api_client._organizations_current(org=None)

            assert result == {"name": "my-org", "verbose_name": "My Organization"}

    @pytest.mark.asyncio
    async def test_matching_org_returned(self, api_client):
        """When org is specified, the matching org should be returned."""
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {
                "organizations": [
                    {"name": "first-org", "verboseName": "First Org"},
                    {"name": "target-org", "verboseName": "Target Org"},
                ]
            }

            result = await api_client._organizations_current(org="target-org")

            assert result == {"name": "target-org", "verbose_name": "Target Org"}

    @pytest.mark.asyncio
    async def test_no_orgs_returns_none(self, api_client):
        """When the user has no organizations, return None."""
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = {"organizations": []}

            result = await api_client._organizations_current(org=None)

            assert result is None


class TestRegionDelete:
    """Region revocation request construction (PCC-1141)."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_delete_url_carries_no_guard_bypass(self, api_client):
        """The DELETE must be a bare resource URL. The server's live-session
        and deployed-service guard has no bypass, and the CLI must never
        reintroduce one by hand-appending a query parameter."""
        with patch.object(api_client, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = None

            await api_client._region_delete(org="test-org", region_key="acme-us-east")

            method, url = mock_request.call_args[0][:2]
            assert method == "DELETE"
            assert url.endswith("/acme-us-east")
            assert "?" not in url


class TestNoContentResponses:
    """A 204 must resolve to None deliberately, not via a swallowed parse error
    (cli#198 review). Callers branch on the shape of this value."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_204_returns_none_without_parsing_a_body(self, api_client):
        json_called = False

        async def _json():
            nonlocal json_called
            json_called = True
            raise AssertionError("must not parse a body on 204")

        class _Resp:
            ok = True
            status = 204
            headers = {}
            json = staticmethod(_json)

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def request(self, **_kwargs):
                return _Resp()

        with patch("aiohttp.ClientSession", lambda *a, **k: _Session()):
            result = await api_client._base_request("DELETE", "https://example/x")

        assert result is None
        assert json_called is False

    @pytest.mark.asyncio
    async def test_an_ok_answer_that_is_not_json_is_read_as_no_body(self, api_client):
        """The operation succeeded; there is simply nothing to read. Spelled out
        here because the wrapper no longer swallows the parse error into an
        empty result, and a completed write must not be reported as failed."""

        class _Resp:
            ok = True
            status = 200
            headers = {}

            @staticmethod
            async def json():
                raise aiohttp.ContentTypeError(MagicMock(), ())

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def request(self, **_kwargs):
                return _Resp()

        with patch("aiohttp.ClientSession", lambda *a, **k: _Session()):
            result = await api_client._base_request("PUT", "https://example/x")

        assert result is None
        assert api_client.error is None


def _session_answering(*responses):
    """An aiohttp session stand-in that answers each request with the next
    response."""
    answers = iter(responses)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def request(self, **_kwargs):
            return next(answers)

    return lambda *a, **k: _Session()


def _response(status, body):
    class _Resp:
        def __init__(self):
            self.status = status
            self.ok = status < 400
            self.reason = "Reason"
            self.headers = {}

        async def json(self):
            return body

        def raise_for_status(self):
            if not self.ok:
                raise RuntimeError(f"HTTP {status}")

    return _Resp()


class TestErrorStatus:
    """A failed response's HTTP status is kept beside its body, since the
    body's `code` is the API's own error code and not always the status."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_keeps_the_status_of_a_refusal_and_clears_it_on_the_next_call(self, api_client):
        refusal = {"error": "Not a member", "code": "NOT_A_MEMBER"}
        session = _session_answering(
            _response(403, refusal), _response(200, {"installation": {"id": 1}})
        )
        with patch("aiohttp.ClientSession", session):
            found, error = await api_client.bubble_error().github_installation(org="o")
            assert (found, error) == (None, refusal)
            assert api_client.error_status == 403

            found, error = await api_client.github_installation(org="o")
            assert (found, error) == ({"id": 1}, None)
            assert api_client.error_status is None

    @pytest.mark.asyncio
    async def test_a_not_found_read_as_empty_has_no_status(self, api_client):
        session = _session_answering(_response(404, {"error": "Not found"}))
        with patch("aiohttp.ClientSession", session):
            found, error = await api_client.github_installation(org="o")

        assert (found, error) == (None, None)
        assert api_client.error_status is None


def _session_raising(exc: Exception):
    """An aiohttp session stand-in whose request never gets a response."""

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def request(self, **_kwargs):
            raise exc

    return lambda *a, **k: _Session()


class TestFailuresWithNothingRecorded:
    """A wrapped method that raises without an API error recorded is a failure
    on this side of the call. It used to come back as `(None, None)`, which
    every command reads as success — a build promotion printed "Promoted
    build ..." having sent no request at all (cli#223 review)."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    async def test_a_wrong_keyword_reaches_the_caller(self, api_client):
        """The cli#223 shape exactly: a keyword the method does not take."""
        with patch.object(api_client, "_whoami", side_effect=TypeError("'buildId'")):
            with pytest.raises(TypeError):
                await api_client.whoami()

    @pytest.mark.asyncio
    async def test_bubbling_the_error_does_not_bring_the_swallow_back(self, api_client):
        with patch.object(api_client, "_whoami", side_effect=AttributeError("gone")):
            with pytest.raises(AttributeError):
                await api_client.bubble_error().whoami()
        # And the request to bubble does not leak into the next call.
        assert api_client.bubble_next is False

    @pytest.mark.asyncio
    async def test_the_sdk_sees_the_original_exception(self):
        """Not an AgentStartError: there is no API error to put in one."""
        api = _API(token="test-token", is_cli=False)
        with patch.object(api, "_whoami", side_effect=KeyError("organizations")):
            with pytest.raises(KeyError):
                await api.whoami()

    @pytest.mark.asyncio
    async def test_a_live_display_is_stopped_before_a_local_failure_is_raised(self, api_client):
        """Whatever the failure, the spinner does not outlive the call and leave
        the traceback printing over it."""
        live = MagicMock()
        with patch.object(api_client, "_whoami", side_effect=TypeError("'buildId'")):
            with pytest.raises(TypeError):
                await api_client.whoami(live=live)

        live.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_a_refusal_still_comes_back_as_the_error(self, api_client):
        """Unchanged: what the API answered with is returned, not raised."""
        refusal = {"error": "Not a member", "code": "NOT_A_MEMBER"}
        with patch("aiohttp.ClientSession", _session_answering(_response(403, refusal))):
            found, error = await api_client.bubble_error().github_installation(org="o")

        assert (found, error) == (None, refusal)
        assert api_client.error_status == 403
        assert api_client.no_response() is False


class TestRequestsThatGetNoResponse:
    """A request the API never answered — the host unreachable, the connection
    dropped, the request timed out. There is no status and no body, but it is a
    failure, and it has to read as one rather than as an empty result."""

    @pytest.fixture
    def api_client(self):
        return _API(token="test-token", is_cli=True)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "failure",
        [
            aiohttp.ClientConnectionError("dropped while sending KEY=secret"),
            TimeoutError("timed out on https://api/x?token=secret"),
        ],
        ids=["connection", "timeout"],
    )
    async def test_it_comes_back_as_an_error_naming_no_status(self, api_client, failure):
        with patch("aiohttp.ClientSession", _session_raising(failure)):
            found, error = await api_client.bubble_error().github_installation(org="o")

        assert found is None
        assert "Could not reach the Pipecat Cloud API" in error["error"]
        assert api_client.error_status is None
        assert api_client.no_response() is True
        # The type of the failure, never its text: an exception's message can
        # carry the URL and the body the request was sent with.
        assert type(failure).__name__ in error["error"]
        assert "secret" not in error["error"]

    @pytest.mark.asyncio
    async def test_an_answer_that_times_out_mid_body_is_recorded_the_same_way(self, api_client):
        """aiohttp returns from `request()` once the headers are in, so a body
        read that times out raises after that, with a status that says only that
        the server had begun to answer."""

        class _Resp:
            ok = True
            status = 200
            headers = {}

            @staticmethod
            async def json():
                raise TimeoutError("reading https://api/x?token=secret")

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def request(self, **_kwargs):
                return _Resp()

        with patch("aiohttp.ClientSession", lambda *a, **k: _Session()):
            found, error = await api_client.bubble_error().github_installation(org="o")

        assert found is None
        assert "Could not read the answer from the Pipecat Cloud API" in error["error"]
        assert "TimeoutError" in error["error"]
        assert api_client.error_status is None
        assert api_client.no_response() is True
        assert "secret" not in error["error"]

    @pytest.mark.asyncio
    async def test_the_sdk_sees_the_transport_exception_itself(self):
        """Not an AgentStartError: there is nothing the API said to put in one.
        The rendered error stays on the client for whoever wants it."""
        api = _API(token="test-token", is_cli=False)
        dropped = aiohttp.ClientConnectionError("dropped")

        with patch("aiohttp.ClientSession", _session_raising(dropped)):
            with pytest.raises(aiohttp.ClientConnectionError):
                await api.github_installation(org="o")

        assert api.no_response() is True
