"""
Tests for multi-environment credential storage (PCC-695).

Covers reading, writing, logout, and backwards/forwards compatibility
of the environments-keyed config format.
"""

import os
from unittest.mock import patch

import toml
import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud.cli.config import (
    _DEFAULT_API_HOST,
    _get_active_api_host,
    _resolve_environment,
)

STAGING_HOST = "https://staging.qa.pipecat.cloud"


# ---- Helpers


def _write_toml(path, data):
    with open(path, "w") as f:
        toml.dump(data, f)


def _read_toml(path):
    with open(path) as f:
        return toml.load(f)


@pytest.fixture
def tmp_config(tmp_path):
    """Provide a temp config path and patch it into the config module."""
    config_file = tmp_path / "pipecatcloud.toml"
    config_file.touch()
    path_str = str(config_file)
    with patch("pipecatcloud.cli.config.user_config_path", path_str):
        yield path_str


@pytest.fixture
def _unset_api_host():
    """Ensure PIPECAT_API_HOST is not set during the test."""
    old = os.environ.pop("PIPECAT_API_HOST", None)
    yield
    if old is not None:
        os.environ["PIPECAT_API_HOST"] = old
    else:
        os.environ.pop("PIPECAT_API_HOST", None)


@pytest.fixture
def _staging_host():
    """Set PIPECAT_API_HOST to a staging URL during the test."""
    old = os.environ.pop("PIPECAT_API_HOST", None)
    os.environ["PIPECAT_API_HOST"] = STAGING_HOST
    yield
    if old is not None:
        os.environ["PIPECAT_API_HOST"] = old
    else:
        os.environ.pop("PIPECAT_API_HOST", None)


# ---- _get_active_api_host


class TestGetActiveApiHost:
    def test_returns_default_when_no_env_var(self, _unset_api_host):
        assert _get_active_api_host() == _DEFAULT_API_HOST

    def test_returns_env_var_when_set(self, _staging_host):
        assert _get_active_api_host() == STAGING_HOST


# ---- _resolve_environment


class TestResolveEnvironment:
    def test_old_format_passthrough(self, _unset_api_host):
        """Old-style config with no environments key returns unchanged."""
        old_config = {"token": "tok", "org": "my-org"}
        assert _resolve_environment(old_config) == old_config

    def test_overlays_default_host(self, _unset_api_host):
        raw = {
            "token": "stale",
            "org": "stale-org",
            "environments": {
                _DEFAULT_API_HOST: {
                    "token": "fresh-prod",
                    "org": "prod-org",
                    "refresh_token": "rt",
                    "token_expires_at": 999.0,
                }
            },
        }
        resolved = _resolve_environment(raw)
        assert resolved["token"] == "fresh-prod"
        assert resolved["org"] == "prod-org"
        assert resolved["refresh_token"] == "rt"
        assert resolved["token_expires_at"] == 999.0

    def test_overlays_staging_host(self, _staging_host):
        raw = {
            "token": "prod-tok",
            "org": "prod-org",
            "environments": {
                _DEFAULT_API_HOST: {"token": "prod-tok", "org": "prod-org"},
                STAGING_HOST: {"token": "stg-tok", "org": "stg-org"},
            },
        }
        resolved = _resolve_environment(raw)
        assert resolved["token"] == "stg-tok"
        assert resolved["org"] == "stg-org"

    def test_missing_host_falls_back_to_top_level(self, _staging_host):
        """If the active host has no env section, top-level keys remain."""
        raw = {
            "token": "prod-tok",
            "org": "prod-org",
            "environments": {
                _DEFAULT_API_HOST: {"token": "prod-tok", "org": "prod-org"},
            },
        }
        resolved = _resolve_environment(raw)
        assert resolved["token"] == "prod-tok"

    def test_preserves_non_credential_keys(self, _unset_api_host):
        raw = {
            "token": "old",
            "environments": {
                _DEFAULT_API_HOST: {"token": "new"},
            },
            "my-org": {"default_public_key": "pk_123"},
        }
        resolved = _resolve_environment(raw)
        assert resolved["token"] == "new"
        assert resolved["my-org"]["default_public_key"] == "pk_123"


# ---- update_user_config


class TestUpdateUserConfig:
    def test_creates_environment_section_default_host(self, tmp_config, _unset_api_host):
        from pipecatcloud.cli.config import update_user_config

        update_user_config(
            token="tok1",
            active_org="org1",
            refresh_token="rt1",
            token_expires_at=1000.0,
        )
        on_disk = _read_toml(tmp_config)

        # Environment section should exist
        assert _DEFAULT_API_HOST in on_disk["environments"]
        env = on_disk["environments"][_DEFAULT_API_HOST]
        assert env["token"] == "tok1"
        assert env["org"] == "org1"
        assert env["refresh_token"] == "rt1"
        assert env["token_expires_at"] == 1000.0

        # Top-level should be mirrored for default host
        assert on_disk["token"] == "tok1"
        assert on_disk["org"] == "org1"

    def test_creates_environment_section_staging_host(self, tmp_config, _staging_host):
        from pipecatcloud.cli.config import update_user_config

        # Pre-populate with default host credentials
        _write_toml(
            tmp_config,
            {
                "token": "prod-tok",
                "org": "prod-org",
                "environments": {
                    _DEFAULT_API_HOST: {"token": "prod-tok", "org": "prod-org"},
                },
            },
        )

        update_user_config(
            token="stg-tok",
            active_org="stg-org",
            refresh_token="stg-rt",
            token_expires_at=2000.0,
        )
        on_disk = _read_toml(tmp_config)

        # Staging env should exist
        assert STAGING_HOST in on_disk["environments"]
        stg = on_disk["environments"][STAGING_HOST]
        assert stg["token"] == "stg-tok"

        # Top-level should NOT be touched (non-default host)
        assert on_disk["token"] == "prod-tok"
        assert on_disk["org"] == "prod-org"

        # Default host section should be preserved
        assert on_disk["environments"][_DEFAULT_API_HOST]["token"] == "prod-tok"

    def test_token_refresh_updates_environment_section(self, tmp_config, _unset_api_host):
        """Simulate token refresh: updates token without active_org."""
        from pipecatcloud.cli.config import update_user_config

        _write_toml(
            tmp_config,
            {
                "token": "old-tok",
                "org": "org1",
                "environments": {
                    _DEFAULT_API_HOST: {
                        "token": "old-tok",
                        "org": "org1",
                        "refresh_token": "old-rt",
                        "token_expires_at": 100.0,
                    }
                },
            },
        )

        update_user_config(
            token="new-tok",
            refresh_token="new-rt",
            token_expires_at=9999.0,
        )
        on_disk = _read_toml(tmp_config)
        env = on_disk["environments"][_DEFAULT_API_HOST]
        assert env["token"] == "new-tok"
        assert env["refresh_token"] == "new-rt"
        assert on_disk["token"] == "new-tok"

    def test_org_switch_updates_environment_section(self, tmp_config, _unset_api_host):
        """Simulate org switch: updates active_org without token."""
        from pipecatcloud.cli.config import update_user_config

        _write_toml(
            tmp_config,
            {
                "token": "tok",
                "org": "old-org",
                "environments": {
                    _DEFAULT_API_HOST: {"token": "tok", "org": "old-org"},
                },
            },
        )

        update_user_config(None, "new-org")
        on_disk = _read_toml(tmp_config)
        assert on_disk["environments"][_DEFAULT_API_HOST]["org"] == "new-org"
        assert on_disk["org"] == "new-org"

    def test_additional_data_stays_at_top_level(self, tmp_config, _unset_api_host):
        from pipecatcloud.cli.config import update_user_config

        update_user_config(
            token="tok",
            active_org="org1",
            additional_data={"default_public_key": "pk_abc"},
        )
        on_disk = _read_toml(tmp_config)
        assert on_disk["org1"]["default_public_key"] == "pk_abc"
        # Should NOT be inside environment section
        env = on_disk["environments"][_DEFAULT_API_HOST]
        assert "default_public_key" not in env

    def test_updates_in_memory_config(self, tmp_config, _unset_api_host):
        import pipecatcloud.cli.config as cfg

        update_user_config = cfg.update_user_config
        update_user_config(token="mem-tok", active_org="mem-org")
        assert cfg.user_config["token"] == "mem-tok"
        assert cfg.user_config["org"] == "mem-org"


# ---- remove_user_config


class TestRemoveUserConfig:
    def test_logout_staging_preserves_production(self, tmp_config, _staging_host):
        from pipecatcloud.cli.config import remove_user_config

        _write_toml(
            tmp_config,
            {
                "token": "prod-tok",
                "org": "prod-org",
                "environments": {
                    _DEFAULT_API_HOST: {"token": "prod-tok", "org": "prod-org"},
                    STAGING_HOST: {"token": "stg-tok", "org": "stg-org"},
                },
            },
        )

        remove_user_config()

        on_disk = _read_toml(tmp_config)
        # Staging section should be gone
        assert STAGING_HOST not in on_disk["environments"]
        # Production should be intact
        assert on_disk["environments"][_DEFAULT_API_HOST]["token"] == "prod-tok"
        # Top-level should still have production creds
        assert on_disk["token"] == "prod-tok"

    def test_logout_default_clears_top_level(self, tmp_config, _unset_api_host):
        from pipecatcloud.cli.config import remove_user_config

        _write_toml(
            tmp_config,
            {
                "token": "prod-tok",
                "org": "prod-org",
                "environments": {
                    _DEFAULT_API_HOST: {"token": "prod-tok", "org": "prod-org"},
                    STAGING_HOST: {"token": "stg-tok", "org": "stg-org"},
                },
            },
        )

        remove_user_config()

        on_disk = _read_toml(tmp_config)
        assert _DEFAULT_API_HOST not in on_disk["environments"]
        assert "token" not in on_disk
        assert "org" not in on_disk
        # Staging should be preserved
        assert on_disk["environments"][STAGING_HOST]["token"] == "stg-tok"

    def test_logout_last_env_deletes_file(self, tmp_config, _unset_api_host):
        from pipecatcloud.cli.config import remove_user_config

        _write_toml(
            tmp_config,
            {
                "token": "tok",
                "org": "org1",
                "environments": {
                    _DEFAULT_API_HOST: {"token": "tok", "org": "org1"},
                },
            },
        )

        remove_user_config()

        assert not os.path.exists(tmp_config)


# ---- Backwards / forwards compatibility


class TestCompatibility:
    def test_old_format_read(self, _unset_api_host):
        """New CLI reads old-format config (no environments key)."""
        old_config = {
            "token": "old-tok",
            "org": "old-org",
            "refresh_token": "old-rt",
            "token_expires_at": 555.0,
        }
        resolved = _resolve_environment(old_config)
        assert resolved["token"] == "old-tok"
        assert resolved["org"] == "old-org"

    def test_new_format_validation_passes_for_old_cli(self):
        """The environments key is a dict-of-dicts, which passes old validation."""
        new_config = {
            "token": "tok",
            "org": "org1",
            "environments": {
                _DEFAULT_API_HOST: {"token": "tok", "org": "org1"},
            },
        }
        # Simulate old CLI validation logic
        top_level_keys = {"token", "org", "refresh_token", "token_expires_at"}
        org_sections = {k: v for k, v in new_config.items() if k not in top_level_keys}
        assert all(isinstance(e, dict) for e in org_sections.values())

    def test_first_write_creates_environments_from_old_format(self, tmp_config, _unset_api_host):
        """Writing to an old-format file creates the environments section."""
        from pipecatcloud.cli.config import update_user_config

        _write_toml(tmp_config, {"token": "old-tok", "org": "old-org"})

        update_user_config(token="new-tok", active_org="new-org")

        on_disk = _read_toml(tmp_config)
        assert "environments" in on_disk
        assert _DEFAULT_API_HOST in on_disk["environments"]
        assert on_disk["environments"][_DEFAULT_API_HOST]["token"] == "new-tok"
        # Top-level mirrored
        assert on_disk["token"] == "new-tok"


# ---- End-to-end


class TestEndToEnd:
    def test_login_prod_then_staging(self, tmp_config, _unset_api_host):
        """Login to production, then staging, both credentials preserved."""
        from pipecatcloud.cli.config import update_user_config

        # Login to production
        update_user_config(
            token="prod-tok",
            active_org="prod-org",
            refresh_token="prod-rt",
            token_expires_at=1000.0,
        )

        # Switch to staging
        os.environ["PIPECAT_API_HOST"] = STAGING_HOST
        try:
            update_user_config(
                token="stg-tok",
                active_org="stg-org",
                refresh_token="stg-rt",
                token_expires_at=2000.0,
            )
        finally:
            os.environ.pop("PIPECAT_API_HOST", None)

        on_disk = _read_toml(tmp_config)

        # Both environments present
        assert on_disk["environments"][_DEFAULT_API_HOST]["token"] == "prod-tok"
        assert on_disk["environments"][STAGING_HOST]["token"] == "stg-tok"

        # Top-level mirrors production (default host)
        assert on_disk["token"] == "prod-tok"
        assert on_disk["org"] == "prod-org"
