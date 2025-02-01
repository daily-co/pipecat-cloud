import os
import typing
from typing import Optional

from pipecatcloud import PIPECAT_CREDENTIALS_PATH, PIPECAT_DEPLOY_CONFIG_PATH
from pipecatcloud.exception import ConfigError

user_config_path: str = os.environ.get("PIPECAT_CONFIG_PATH") or os.path.expanduser(
    PIPECAT_CREDENTIALS_PATH
)

deploy_config_path: str = os.environ.get("PIPECAT_DEPLOY_CONFIG_PATH") or os.path.expanduser(
    PIPECAT_DEPLOY_CONFIG_PATH
)

api_host: str = os.environ.get("PIPECAT_API_HOST") or "https://api.pipecat.cloud"


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
            if not all(isinstance(e, dict) for e in config_data.values()):
                raise ConfigError(
                    "Pipecat Cloud config file is not valid TOML. Please log out and log back in.")
            else:
                config_problem = ""
        if config_problem:
            raise ConfigError(config_problem)
    return config_data


# Initialize _user_config first
_user_config = _read_user_config()


def config_profiles():
    """List the available profiles in the toml file."""
    return _user_config.keys()


def _config_active_profile() -> typing.Optional[str]:
    if not _user_config:
        return None
    return next(iter(_user_config.keys()))


def _write_user_config(user_config):
    import toml

    with open(user_config_path, "w") as f:
        toml.dump(user_config, f)


def _remove_user_config():
    os.remove(user_config_path)


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

    _write_user_config(config_data)


# Set _profile after _config_active_profile is defined
_profile = os.environ.get("PIPECAT_PROFILE") or _config_active_profile()


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

    def get(self, key, profile=None, use_env=True):
        """Looks up a configuration value.

        Will check (in decreasing order of priority):
        1. Any environment variable of the form PIPECAT_FOO_BAR (when use_env is True)
        2. Settings in the user's .toml configuration file
        3. The default value of the setting
        """
        if profile is None:
            profile = _profile

        s = _SETTINGS[key]
        env_var_key = "PIPECAT_" + key.upper()
        if use_env and env_var_key in os.environ:
            return s.transform(os.environ[env_var_key])
        elif profile is not None and profile in _user_config and key in _user_config[profile]:
            return s.transform(_user_config[profile][key])
        elif key == "org":
            return profile
        else:
            return s.default

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
