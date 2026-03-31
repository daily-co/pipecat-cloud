#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import stat
import sys
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

_CREDENTIAL_KEYS = {"token", "refresh_token", "token_expires_at", "org"}
_DEFAULT_API_HOST = _SETTINGS["api_host"].default


def _get_active_api_host() -> str:
    return os.environ.get("PIPECAT_API_HOST", _DEFAULT_API_HOST)


def _resolve_environment(raw_config: dict) -> dict:
    """Overlay the active environment's credentials onto top-level keys.

    If the config contains an ``environments`` section with a sub-section
    matching the active API host, those credential values are copied to the
    top level of a shallow copy so that ``ConfigCLI.get()`` reads them
    without any changes to its lookup logic.

    If there is no ``environments`` section or no matching host, the config
    is returned unchanged (backwards compat with the old single-env format).
    """
    envs = raw_config.get("environments")
    if not isinstance(envs, dict):
        return raw_config

    host = _get_active_api_host()
    env_section = envs.get(host)
    if not isinstance(env_section, dict):
        return raw_config

    resolved = dict(raw_config)
    for key in _CREDENTIAL_KEYS:
        if key in env_section:
            resolved[key] = env_section[key]
    return resolved


# ---- Config TOML methods


def _read_user_config():
    config_data = {}
    config_problem = ""

    if os.path.exists(user_config_path):
        try:
            with open(user_config_path) as f:
                config_data = toml.load(f)
        except toml.TomlDecodeError as exc:
            config_problem = f"Invalid TOML syntax in config file: {exc}"
        except PermissionError:
            config_problem = f"Permission denied when reading config file: {user_config_path}"
        except IOError as exc:
            config_problem = f"I/O error when reading config file: {exc}"
        except Exception as exc:
            config_problem = f"Error reading config file: {exc}"
        else:
            top_level_keys = {"token", "org", "refresh_token", "token_expires_at", "environments"}
            org_sections = {k: v for k, v in config_data.items() if k not in top_level_keys}

            if not all(isinstance(e, dict) for e in org_sections.values()):
                config_problem = "Pipecat Cloud config file is not valid TOML. Organization sections must be dictionaries. Please log out and log back in."

            # Repair credentials file if readable by group or others (Unix only).
            if os.name != "nt":
                file_mode = os.stat(user_config_path).st_mode
                if file_mode & (stat.S_IRWXG | stat.S_IRWXO):
                    try:
                        os.chmod(user_config_path, stat.S_IRUSR | stat.S_IWUSR)
                        print(
                            f"Warning: Credentials file '{user_config_path}' had overly "
                            f"permissive permissions ({stat.filemode(file_mode)}). "
                            f"Permissions have been repaired to 600.",
                            file=sys.stderr,
                        )
                    except OSError:
                        config_problem = (
                            f"Credentials file '{user_config_path}' has overly permissive "
                            f"permissions ({stat.filemode(file_mode)}) and could not be "
                            f"repaired. Your token may be compromised. Please run "
                            f"'chmod 600 {user_config_path}' or login again."
                        )
        if config_problem:
            raise ConfigError(f"{config_problem} Run `pcc auth login` to fix.")

    return config_data


user_config = _resolve_environment(_read_user_config())


def _write_user_config(new_config):
    dir_path = os.path.dirname(user_config_path)
    os.makedirs(dir_path, exist_ok=True)

    with open(user_config_path, "w") as f:
        toml.dump(new_config, f)

    # Restrict permissions so only the file owner can access credentials.
    # On Windows os.chmod is limited but home directories are already
    # protected by ACLs, so this is effectively a Unix-only hardening.
    os.chmod(dir_path, stat.S_IRWXU)  # 0o700
    os.chmod(user_config_path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600


def remove_user_config():
    global user_config

    existing_config = _read_user_config()
    host = _get_active_api_host()
    is_default = host == _DEFAULT_API_HOST

    # Remove the active environment's section
    envs = existing_config.get("environments", {})
    envs.pop(host, None)

    # If default host, clear top-level credential keys
    if is_default:
        for key in _CREDENTIAL_KEYS:
            existing_config.pop(key, None)

    # If no environments remain and no org-specific data, delete the file
    remaining_envs = {k: v for k, v in envs.items() if isinstance(v, dict)}
    top_level_org_sections = {
        k: v
        for k, v in existing_config.items()
        if k not in (_CREDENTIAL_KEYS | {"environments"}) and isinstance(v, dict)
    }

    if not remaining_envs and not top_level_org_sections:
        os.remove(user_config_path)
        user_config = {}
    else:
        if remaining_envs:
            existing_config["environments"] = remaining_envs
        else:
            existing_config.pop("environments", None)
        _write_user_config(existing_config)
        user_config = _resolve_environment(existing_config)


def update_user_config(
    token: Optional[str] = None,
    active_org: Optional[str] = None,
    additional_data: Optional[dict] = None,
    refresh_token: Optional[str] = None,
    token_expires_at: Optional[float] = None,
):
    global user_config

    # Load the existing toml (if it exists)
    existing_config = _read_user_config()

    host = _get_active_api_host()
    is_default = host == _DEFAULT_API_HOST

    # Ensure the environments structure exists
    if "environments" not in existing_config:
        existing_config["environments"] = {}
    if host not in existing_config["environments"]:
        existing_config["environments"][host] = {}

    env_section = existing_config["environments"][host]

    # Update credential fields in the environment section
    if token:
        env_section["token"] = token
    if refresh_token is not None:
        env_section["refresh_token"] = refresh_token
    if token_expires_at is not None:
        env_section["token_expires_at"] = token_expires_at
    if active_org:
        env_section["org"] = active_org

    # Mirror to top-level keys when using the default host (forwards compat)
    if is_default:
        if token:
            existing_config["token"] = token
        if refresh_token is not None:
            existing_config["refresh_token"] = refresh_token
        if token_expires_at is not None:
            existing_config["token_expires_at"] = token_expires_at
        if active_org:
            existing_config["org"] = active_org

    # Org-specific additional_data stays at the top level
    if active_org:
        org_key = active_org
        if org_key not in existing_config:
            existing_config[org_key] = {}
        if additional_data:
            existing_config[org_key].update(additional_data)
    elif additional_data:
        raise ValueError("Attempt to store additional data without specifying namespace")

    try:
        _write_user_config(existing_config)
        # Update in-memory config so subsequent reads (e.g. after token refresh)
        # see the new values without re-reading from disk
        user_config = _resolve_environment(existing_config)
    except PermissionError:
        raise ConfigError(f"Permission denied when writing to {user_config_path}")
    except FileNotFoundError:
        raise ConfigError(f"Cannot create configuration directory for {user_config_path}")
    except IOError as e:
        raise ConfigError(f"IO error when writing configuration: {str(e)}")
    except Exception as e:
        raise ConfigError(f"Unexpected error updating configuration: {str(e)}")


# --- Config

_CLI_SETTINGS = {
    **_SETTINGS,
    "user_config_path": _Setting(user_config_path),
    "token": _Setting(),
    "org": _Setting(),
    "refresh_token": _Setting(),
    "token_expires_at": _Setting(),
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
