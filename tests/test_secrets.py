"""
Unit tests for secrets commands and functionality.

Tests follow AAA pattern and cover secrets management including
region support, validation, and CLI command behavior.
"""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

# Import from source, not installed package
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.constants import REGIONS


class TestSecretsRegionParameter:
    """Test secrets commands with region parameter."""

    def test_set_command_accepts_region_parameter(self):
        """Secrets set command should accept --region parameter."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api:
            mock_api.secrets_list = AsyncMock(return_value=(None, None))
            mock_api.secrets_upsert = AsyncMock(return_value=({"status": "OK"}, None))

            # Act & Assert - Should not raise error
            try:
                secrets_set(
                    name="test-secrets",
                    secrets=["KEY=value"],
                    from_file=None,
                    skip_confirm=True,
                    organization="test-org",
                    region="eu"
                )
            except SystemExit:
                pass  # Expected from typer.Exit()

    def test_set_command_defaults_to_us_when_region_not_specified(self):
        """Secrets set should default to 'us' region when not specified."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api, \
             patch("pipecatcloud.cli.commands.secrets.logger") as mock_logger:

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
                    region=None  # Not specified
                )
            except SystemExit:
                pass

            # Assert - Should warn about defaulting to 'us'
            mock_logger.warning.assert_called()
            warning_message = mock_logger.warning.call_args[0][0]
            assert "defaulting to 'us'" in warning_message.lower()
            assert "future version" in warning_message.lower()

    def test_set_command_passes_region_to_api(self):
        """Secrets set should pass region parameter to API client."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api:
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
                    region="ap"
                )
            except SystemExit:
                pass

            # Assert
            mock_api.secrets_upsert.assert_called()
            call_kwargs = mock_api.secrets_upsert.call_args[1]
            assert call_kwargs["region"] == "ap"

    def test_set_command_passes_default_us_region_to_api(self):
        """Secrets set should pass 'us' to API when region not specified."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api, \
             patch("pipecatcloud.cli.commands.secrets.logger"):

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
                    region=None
                )
            except SystemExit:
                pass

            # Assert - Should pass 'us' to API
            mock_api.secrets_upsert.assert_called()
            call_kwargs = mock_api.secrets_upsert.call_args[1]
            assert call_kwargs["region"] == "us"


class TestSecretsRegionValidation:
    """Test region validation for secrets commands."""

    def test_region_type_limits_valid_values(self):
        """Region parameter should only accept valid region codes."""
        # Arrange & Assert
        assert "us" in REGIONS
        assert "eu" in REGIONS
        assert "ap" in REGIONS
        assert len(REGIONS) == 3

    def test_set_command_with_all_valid_regions(self):
        """Secrets set should accept all valid region codes."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        for region in REGIONS:
            with patch("pipecatcloud.cli.commands.secrets.API") as mock_api:
                mock_api.secrets_list = AsyncMock(return_value=(None, None))
                mock_api.secrets_upsert = AsyncMock(return_value=({"status": "OK"}, None))

                # Act & Assert - Should not raise error
                try:
                    secrets_set(
                        name="test-secrets",
                        secrets=["KEY=value"],
                        from_file=None,
                        skip_confirm=True,
                        organization="test-org",
                        region=region
                    )
                except SystemExit:
                    pass  # Expected from typer.Exit()


class TestSecretsRegionWithMultipleSecrets:
    """Test region handling when creating multiple secrets."""

    def test_set_multiple_secrets_uses_same_region(self):
        """All secrets in a set should use the same region parameter."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api:
            mock_api.secrets_list = AsyncMock(return_value=(None, None))
            mock_api.secrets_upsert = AsyncMock(return_value=({"status": "OK"}, None))

            # Act
            try:
                secrets_set(
                    name="test-secrets",
                    secrets=["KEY1=value1", "KEY2=value2", "KEY3=value3"],
                    from_file=None,
                    skip_confirm=True,
                    organization="test-org",
                    region="eu"
                )
            except SystemExit:
                pass

            # Assert - All calls should have same region
            assert mock_api.secrets_upsert.call_count == 3
            for call in mock_api.secrets_upsert.call_args_list:
                assert call[1]["region"] == "eu"


class TestSecretsRegionBackwardCompatibility:
    """Test backward compatibility for secrets without region."""

    def test_existing_code_without_region_still_works(self):
        """Legacy code not passing region should still work with default."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api, \
             patch("pipecatcloud.cli.commands.secrets.logger"):

            mock_api.secrets_list = AsyncMock(return_value=(None, None))
            mock_api.secrets_upsert = AsyncMock(return_value=({"status": "OK"}, None))

            # Act - Call without region parameter (simulating old usage)
            try:
                secrets_set(
                    name="test-secrets",
                    secrets=["KEY=value"],
                    from_file=None,
                    skip_confirm=True,
                    organization="test-org",
                    region=None
                )
            except SystemExit:
                pass

            # Assert - Should complete successfully with 'us' default
            mock_api.secrets_upsert.assert_called()
            call_kwargs = mock_api.secrets_upsert.call_args[1]
            assert call_kwargs["region"] == "us"


class TestSecretsRegionErrorHandling:
    """Test error handling for region-related issues."""

    def test_api_error_for_region_mismatch_is_propagated(self):
        """API errors about region mismatch should be shown to user."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set
        import typer

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api:
            # Simulate existing secret in different region
            mock_api.secrets_list = AsyncMock(return_value=(
                [{"fieldName": "KEY"}],  # Existing secret
                None
            ))
            # API returns error about region mismatch
            mock_api.secrets_upsert = AsyncMock(return_value=(
                None,
                {"code": "400", "error": "Secret already exists in region 'us'"}
            ))

            # Act
            result = None
            try:
                result = secrets_set(
                    name="test-secrets",
                    secrets=["KEY=value"],
                    from_file=None,
                    skip_confirm=True,
                    organization="test-org",
                    region="eu"  # Different region
                )
            except SystemExit as e:
                result = e

            # Assert - Should return Exit object or raise SystemExit due to error
            assert result is not None
            assert isinstance(result, (SystemExit, typer.Exit))


class TestSecretsRegionIntegration:
    """Integration tests for secrets with region across different scenarios."""

    def test_create_secret_with_region_full_workflow(self):
        """Complete workflow of creating a secret with region specified."""
        # Arrange
        from pipecatcloud.cli.commands.secrets import set as secrets_set

        with patch("pipecatcloud.cli.commands.secrets.API") as mock_api, \
             patch("pipecatcloud.cli.commands.secrets.console"):

            # No existing secrets
            mock_api.secrets_list = AsyncMock(return_value=(None, None))
            mock_api.secrets_upsert = AsyncMock(return_value=({"status": "OK"}, None))

            # Act
            try:
                secrets_set(
                    name="my-api-keys",
                    secrets=["OPENAI_KEY=sk-xxx", "ANTHROPIC_KEY=sk-ant-xxx"],
                    from_file=None,
                    skip_confirm=True,
                    organization="acme-corp",
                    region="eu"
                )
            except SystemExit:
                pass

            # Assert
            assert mock_api.secrets_upsert.call_count == 2

            # Verify first secret call
            first_call = mock_api.secrets_upsert.call_args_list[0]
            assert first_call[1]["region"] == "eu"
            assert first_call[1]["org"] == "acme-corp"
            assert first_call[1]["set_name"] == "my-api-keys"
            assert first_call[1]["data"]["secretKey"] == "OPENAI_KEY"

            # Verify second secret call
            second_call = mock_api.secrets_upsert.call_args_list[1]
            assert second_call[1]["region"] == "eu"
            assert second_call[1]["data"]["secretKey"] == "ANTHROPIC_KEY"
