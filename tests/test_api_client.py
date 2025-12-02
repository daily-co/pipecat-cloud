"""
Unit tests for API client.

Tests follow AAA pattern and cover all API client methods including
region support, error handling, and request construction.
"""

from unittest.mock import AsyncMock, patch

import pytest

# Import from source, not installed package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.api import _API
from pipecatcloud._utils.deploy_utils import DeployConfigParams


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
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region="eu"
        )

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
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region=None
        )

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
        config = DeployConfigParams(
            agent_name="test-agent",
            image="test:latest",
            region="ap"
        )

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
            agent_name="test-agent",
            image="test:latest",
            region="eu",
            secret_set="my-secrets"
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
            enable_managed_keys=True
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
            assert payload["enableIntegratedKeysProxy"] is True


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
                "availableValues": ["us-west", "eu-central"]
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
                org="test-org",
                properties={"defaultRegion": "eu-central"}
            )

            # Assert
            assert result == {"defaultRegion": "eu-central"}
            mock_request.assert_called_once()
            # Verify PATCH method
            assert mock_request.call_args[0][0] == "PATCH"
            # Verify payload
            payload = mock_request.call_args[1]["json"]
            assert payload == {"defaultRegion": "eu-central"}
