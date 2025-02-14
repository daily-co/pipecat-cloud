import os
import typing
from typing import Optional

from pipecatcloud import PIPECAT_CREDENTIALS_PATH, PIPECAT_DEPLOY_CONFIG_PATH
from pipecatcloud.exception import ConfigError

# ---- Constants


user_config_path: str = os.environ.get("PIPECAT_CONFIG_PATH") or os.path.expanduser(
    PIPECAT_CREDENTIALS_PATH
)
deploy_config_path: str = os.environ.get("PIPECAT_DEPLOY_CONFIG_PATH") or os.path.expanduser(
    PIPECAT_DEPLOY_CONFIG_PATH
)
api_host: str = os.environ.get("PIPECAT_API_HOST") or "https://api.pipecat.daily.co"
dashboard_host: str = os.environ.get("PIPECAT_DASHBOARD_HOST") or "https://pipecat.daily.co"


# ---- Config TOML methods

def _read_user_config():
    config_data = {}
    if os.path.exists(user_config_path):
        import toml
        try:
            with open(user_config_path) as f:
                config_data = toml.load(f)
        except Exception as exc:
            config_problem = str(exc)
        else:
            top_level_keys = {'token', 'org'}
            org_sections = {k: v for k, v in config_data.items() if k not in top_level_keys}

            if not all(isinstance(e, dict) for e in org_sections.values()):
                raise ConfigError(
                    "Pipecat Cloud config file is not valid TOML. Organization sections must be dictionaries. Please log out and log back in.")
            else:
                config_problem = ""
        if config_problem:
            raise ConfigError(config_problem)

    return config_data


user_config = _read_user_config()


def _write_user_config(new_config):
    import toml

    with open(user_config_path, "w") as f:
        toml.dump(new_config, f)


def remove_user_config():
    os.remove(user_config_path)


def update_user_config(
        token: Optional[str] = None,
        active_org: Optional[str] = None,
        additional_data: Optional[dict] = None):
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


def _store_user_config(token: str, org: str, additional_data: Optional[dict] = None):
    # @TODO: Make method more robust
    if not org:
        raise ValueError("Account organization is required")
    if not token:
        raise ValueError("Token is required")
    config_data = {
        org: {
            "token": token,
        }
    }
    if additional_data is not None:
        config_data[org].update(additional_data)

    try:
        _write_user_config(config_data)
    except Exception:
        raise ConfigError


# ---- Setting configuration methods


class _Setting(typing.NamedTuple):
    default: typing.Any = None
    transform: typing.Callable[[str], typing.Any] = lambda x: x  # noqa: E731


_SETTINGS = {
    "server_url": _Setting(api_host),
    "onboarding_path": _Setting("/v1/onboarding"),
    "login_path": _Setting("/auth/login"),
    "login_status_path": _Setting("/auth/status"),
    "whoami_path": _Setting("/v1/users"),
    "organization_path": _Setting("/v1/organizations"),
    "services_path": _Setting("/v1/organizations/{org}/services"),
    "services_logs_path": _Setting("/v1/organizations/{org}/services/{service}/logs"),
    "services_deployments_path": _Setting("/v1/organizations/{org}/services/{service}/deployments"),
    "start_path": _Setting("/v1/public/{service}/proxy"),
    "api_keys_path": _Setting("/v1/organizations/{org}/apiKeys"),
    "secrets_path": _Setting("/v1/organizations/{org}/secrets"),
    "user_config_path": _Setting(user_config_path),
    "token": _Setting(),
    "org": _Setting(),
    "default_public_key": _Setting(),
    "default_public_key_name": _Setting(),
    "cli_log_level": _Setting("INFO"),
}


class Config:
    """Singleton that holds configuration used by PipecatCloud internally."""

    def __init__(self):
        pass

    def get(self, key, default=None, use_env=True):
        """Looks up a configuration value.

        Will check (in decreasing order of priority):
        1. Any environment variable of the form PIPECAT_FOO_BAR (when use_env is True)
        2. Settings in the user's .toml configuration file
        3. The default value of the setting
        """
        org_profile = user_config.get(user_config.get("org", ""), {}) if user_config else {}

        s = _SETTINGS[key]
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

    def override_locally(self, key: str, value: str):
        try:
            self.get(key)
            os.environ["PIPECAT_" + key.upper()] = value
        except KeyError:
            os.environ[key.upper()] = value

    def __getitem__(self, key):
        return self.get(key)

    def __repr__(self):
        return repr(self.to_dict())

    def to_dict(self):
        return {key: self.get(key) for key in _SETTINGS.keys()}


config = Config()
