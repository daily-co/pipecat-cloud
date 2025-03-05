#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
from typing import Optional

import toml

from pipecatcloud.cli import PIPECAT_CREDENTIALS_PATH, PIPECAT_DEPLOY_CONFIG_PATH
from pipecatcloud.config import _SETTINGS, Config, _Setting
from pipecatcloud.exception import ConfigError

# ---- Constants

user_config_path: str = os.environ.get("PIPECAT_CONFIG_PATH") or os.path.expanduser(
    PIPECAT_CREDENTIALS_PATH
)
deploy_config_path: str = os.environ.get("PIPECAT_DEPLOY_CONFIG_PATH") or os.path.expanduser(
    PIPECAT_DEPLOY_CONFIG_PATH
)

# ---- Config TOML methods


def _read_user_config():
    config_data = {}
    if os.path.exists(user_config_path):
        try:
            with open(user_config_path) as f:
                config_data = toml.load(f)
        except Exception as exc:
            config_problem = str(exc)
        else:
            top_level_keys = {"token", "org"}
            org_sections = {k: v for k, v in config_data.items() if k not in top_level_keys}

            if not all(isinstance(e, dict) for e in org_sections.values()):
                raise ConfigError(
                    "Pipecat Cloud config file is not valid TOML. Organization sections must be dictionaries. Please log out and log back in."
                )
            else:
                config_problem = ""
        if config_problem:
            raise ConfigError(config_problem)

    return config_data


user_config = _read_user_config()


def _write_user_config(new_config):
    dir_path = os.path.dirname(user_config_path)
    os.makedirs(dir_path, exist_ok=True)

    with open(user_config_path, "w") as f:
        toml.dump(new_config, f)


def remove_user_config():
    os.remove(user_config_path)


def update_user_config(
    token: Optional[str] = None,
    active_org: Optional[str] = None,
    additional_data: Optional[dict] = None,
):
    # Load the existing toml (if it exists)
    existing_config = _read_user_config()

    # Only update top level token if provided
    if token:
        existing_config["token"] = token

    if active_org:
        existing_config["org"] = active_org
        if active_org not in existing_config:
            existing_config[active_org] = {}
        if additional_data:
            existing_config[active_org].update(additional_data)
    elif additional_data:
        raise ValueError("Attempt to store additional data without specifying namespace")

    try:
        _write_user_config(existing_config)
    except Exception:
        raise ConfigError


# --- Config

_CLI_SETTINGS = {
    **_SETTINGS,
    "user_config_path": _Setting(user_config_path),
    "token": _Setting(),
    "org": _Setting(),
    "default_public_key": _Setting(),
    "default_public_key_name": _Setting(),
    "cli_log_level": _Setting("INFO"),
}


class ConfigCLI(Config):
    def get(self, key, default=None, use_env=True):
        """Looks up a configuration value.

        Will check (in decreasing order of priority):
        1. Any environment variable of the form PIPECAT_FOO_BAR (when use_env is True)
        2. Settings in the user's .toml configuration file
        3. The default value of the setting
        """
        org_profile = user_config.get(user_config.get("org", ""), {}) if user_config else {}

        s = _CLI_SETTINGS[key]
        env_var_key = "PIPECAT_" + key.upper()
        if use_env and env_var_key in os.environ:
            return s.transform(os.environ[env_var_key])
        # Obtain any top level config items from the user config
        elif user_config is not None and key in user_config:
            return s.transform(user_config[key])
        # Obtain any current org specific values
        elif org_profile is not None and key in org_profile:
            return s.transform(org_profile[key])
        elif s.default:
            return s.default
        else:
            return default


config = ConfigCLI(_CLI_SETTINGS)
