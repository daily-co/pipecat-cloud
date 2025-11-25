#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Tests for dynamic region fetching and validation.
"""

import pytest
from unittest.mock import patch, AsyncMock
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


@pytest.mark.asyncio
class TestRegionsAPI:
    """Test regions API client functionality."""

    async def test_regions_endpoint_parses_response(self):
        """API client should correctly parse regions from /v1/organizations/{org}/regions endpoint."""
        # Arrange
        from pipecatcloud.api import _API

        mock_response = {
            "regions": [
                {"code": "us-west", "display_name": "US West"},
                {"code": "eu-central", "display_name": "EU Central"},
                {"code": "ap-south", "display_name": "AP South"}
            ]
        }

        api = _API("fake-token", is_cli=True)

        with patch.object(api, "_base_request", new_callable=AsyncMock) as mock_request:
            mock_request.return_value = mock_response

            # Act
            result = await api._regions(org="test-org")

            # Assert
            assert result == [
                {"code": "us-west", "display_name": "US West"},
                {"code": "eu-central", "display_name": "EU Central"},
                {"code": "ap-south", "display_name": "AP South"}
            ]
            mock_request.assert_called_once()


@pytest.mark.asyncio
class TestRegionsCaching:
    """Test that regions are cached correctly."""

    async def test_get_regions_caches_result(self):
        """get_regions should cache the API result and not call API multiple times."""
        # Arrange
        from pipecatcloud._utils import regions

        # Reset cache
        regions._regions_cache = None

        mock_regions = [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"}
        ]

        with patch("pipecatcloud._utils.regions.API") as mock_api, \
             patch("pipecatcloud._utils.regions.config") as mock_config:
            # API returns (data, error) tuple
            mock_api.regions = AsyncMock(return_value=(mock_regions, None))
            mock_config.get.return_value = "test-org"

            # Act - Call multiple times
            result1 = await regions.get_regions()
            result2 = await regions.get_regions()
            result3 = await regions.get_regions()

            # Assert
            assert result1 == mock_regions
            assert result2 == result1
            assert result3 == result1
            # API should only be called once
            mock_api.regions.assert_called_once()


@pytest.mark.asyncio
class TestRegionValidation:
    """Test region validation logic."""

    async def test_validate_region_accepts_valid_regions(self):
        """validate_region should return True for regions in the API list."""
        # Arrange
        from pipecatcloud._utils import regions

        regions._regions_cache = None

        mock_regions = [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"},
            {"code": "ap-south", "display_name": "AP South"}
        ]

        with patch("pipecatcloud._utils.regions.API") as mock_api, \
             patch("pipecatcloud._utils.regions.config") as mock_config:
            # API returns (data, error) tuple
            mock_api.regions = AsyncMock(return_value=(mock_regions, None))
            mock_config.get.return_value = "test-org"

            # Act & Assert
            assert await regions.validate_region("us-west") is True
            assert await regions.validate_region("eu-central") is True
            assert await regions.validate_region("ap-south") is True

    async def test_validate_region_rejects_invalid_regions(self):
        """validate_region should return False for regions NOT in the API list."""
        # Arrange
        from pipecatcloud._utils import regions

        regions._regions_cache = None

        mock_regions = [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"},
            {"code": "ap-south", "display_name": "AP South"}
        ]

        with patch("pipecatcloud._utils.regions.API") as mock_api, \
             patch("pipecatcloud._utils.regions.config") as mock_config:
            # API returns (data, error) tuple
            mock_api.regions = AsyncMock(return_value=(mock_regions, None))
            mock_config.get.return_value = "test-org"

            # Act & Assert
            assert await regions.validate_region("invalid") is False
            assert await regions.validate_region("us") is False  # Old format
            assert await regions.validate_region("mars-north") is False


@pytest.mark.asyncio
class TestCLIRegionValidation:
    """Test CLI commands validate regions correctly."""

    async def test_secrets_set_rejects_invalid_region(self):
        """Secrets set should reject invalid regions with error message."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set
        from pipecatcloud._utils import regions

        regions._regions_cache = None

        mock_regions = [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"}
        ]

        with patch("pipecatcloud._utils.regions.API") as mock_regions_api, \
             patch("pipecatcloud._utils.regions.config") as mock_config, \
             patch("pipecatcloud.cli.commands.secrets.API") as mock_api:

            # API returns (data, error) tuple
            mock_regions_api.regions = AsyncMock(return_value=(mock_regions, None))
            mock_config.get.return_value = "test-org"
            mock_api.secrets_list = AsyncMock(return_value=(None, None))

            # Act
            result = secrets_set(
                name="test-secrets",
                secrets=["KEY=value"],
                from_file=None,
                skip_confirm=True,
                organization="test-org",
                region="invalid-region"
            )

            # Assert - Should return early without calling upsert
            assert mock_api.secrets_upsert.called is False

    async def test_secrets_set_accepts_valid_region(self):
        """Secrets set should accept valid regions from API."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set
        from pipecatcloud._utils import regions

        regions._regions_cache = None

        mock_regions = [
            {"code": "us-west", "display_name": "US West"},
            {"code": "eu-central", "display_name": "EU Central"}
        ]

        with patch("pipecatcloud._utils.regions.API") as mock_regions_api, \
             patch("pipecatcloud._utils.regions.config") as mock_config, \
             patch("pipecatcloud.cli.commands.secrets.API") as mock_api:

            # API returns (data, error) tuple
            mock_regions_api.regions = AsyncMock(return_value=(mock_regions, None))
            mock_config.get.return_value = "test-org"
            mock_api.secrets_list = AsyncMock(return_value=(None, None))
            mock_api.secrets_upsert = AsyncMock(return_value=({"status": "OK"}, None))

            # Act
            try:
                secrets_set(
                    name="test-secrets",
                    secrets=["KEY=value"],
                    from_file=None,
                    skip_confirm=True,
                    organization="test-org",
                    region="us-west"  # Valid region from API
                )
            except SystemExit:
                pass  # Command completes

            # Assert - Should have called upsert with the region
            mock_api.secrets_upsert.assert_called()
            call_kwargs = mock_api.secrets_upsert.call_args[1]
            assert call_kwargs["region"] == "us-west"
