"""Loading ``pcc-deploy.toml``: a missing file is no config, an unreadable one is an error."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pipecatcloud._utils.deploy_utils import load_deploy_config_file
from pipecatcloud.exception import ConfigFileError


def load(path: Path):
    with patch("pipecatcloud.cli.config.deploy_config_path", str(path)):
        return load_deploy_config_file()


def test_missing_file_is_no_config(tmp_path: Path):
    assert load(tmp_path / "pcc-deploy.toml") is None


def test_utf8_file_with_non_ascii_loads(tmp_path: Path):
    path = tmp_path / "pcc-deploy.toml"
    path.write_text('# Café bot\nagent_name = "cafe-bot"\n', encoding="utf-8")

    config = load(path)

    assert config is not None
    assert config.agent_name == "cafe-bot"


def test_non_utf8_file_is_an_error(tmp_path: Path):
    # Saved in the Windows code page: "é" is the single byte 0xe9.
    path = tmp_path / "pcc-deploy.toml"
    path.write_bytes('# Café bot\nagent_name = "cafe-bot"\n'.encode("cp1252"))

    with pytest.raises(ConfigFileError, match="not valid UTF-8"):
        load(path)


def test_malformed_toml_is_an_error(tmp_path: Path):
    path = tmp_path / "pcc-deploy.toml"
    path.write_text('agent_name = "unterminated\n', encoding="utf-8")

    with pytest.raises(ConfigFileError, match="not valid TOML"):
        load(path)
