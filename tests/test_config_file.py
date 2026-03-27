"""
Unit tests for the --config-file flag behavior via @with_deploy_config.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest
import typer

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.deploy_utils import with_deploy_config


@with_deploy_config
def dummy_command(deploy_config=None, config_file=None):
    """Minimal command decorated with @with_deploy_config."""
    return deploy_config


class TestConfigFileFlag:
    """Test --config-file override behavior in @with_deploy_config."""

    def test_loads_alternate_config_file(self):
        """--config-file should load the specified file instead of the default."""
        with tempfile.NamedTemporaryFile(suffix=".toml", mode="w", delete=False) as f:
            f.write('agent_name = "alt-agent"\n')
            f.flush()
            try:
                result = dummy_command(config_file=f.name)
                assert result is not None
                assert result.agent_name == "alt-agent"
            finally:
                os.unlink(f.name)

    def test_nonexistent_config_file_exits(self):
        """--config-file pointing to missing file should exit with error."""
        with pytest.raises(typer.Exit):
            dummy_command(config_file="/nonexistent/path.toml")

    def test_no_config_file_uses_default(self):
        """Without --config-file, falls back to default pcc-deploy.toml behavior."""
        result = dummy_command(config_file=None)
        # No pcc-deploy.toml in test dir, so deploy_config should be None
        assert result is None
