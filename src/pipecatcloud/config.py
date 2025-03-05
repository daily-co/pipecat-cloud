#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import typing

# ---- Constants


class _Setting(typing.NamedTuple):
    default: typing.Any = None
    transform: typing.Callable[[str], typing.Any] = lambda x: x  # noqa: E731


_SETTINGS = {
    "api_host": _Setting("https://api.pipecat.daily.co"),
    "dashboard_host": _Setting("https://pipecat.daily.co"),
    "init_zip_url": _Setting(
        "https://github.com/daily-co/pipecat-cloud-starter/archive/refs/heads/main.zip"
    ),
    "onboarding_path": _Setting("/v1/onboarding"),
    "login_path": _Setting("/auth/login"),
    "login_status_path": _Setting("/auth/status"),
    "whoami_path": _Setting("/v1/users"),
    "organization_path": _Setting("/v1/organizations"),
    "daily_key_path": _Setting("/v1/organizations/{org}/daily"),
    "services_path": _Setting("/v1/organizations/{org}/services"),
    "services_logs_path": _Setting("/v1/organizations/{org}/services/{service}/logs"),
    "services_deployments_path": _Setting("/v1/organizations/{org}/services/{service}/deployments"),
    "start_path": _Setting("/v1/public/{service}/start"),
    "api_keys_path": _Setting("/v1/organizations/{org}/apiKeys"),
    "secrets_path": _Setting("/v1/organizations/{org}/secrets"),
}


class Config:
    def __init__(self, settings):
        self.settings = settings

    def get(self, key, default=None, use_env=True):
        s = _SETTINGS[key]
        env_var_key = "PIPECAT_" + key.upper()
        if use_env and env_var_key in os.environ:
            return s.transform(os.environ[env_var_key])
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
        return {key: self.get(key) for key in self.settings.keys()}


config = Config(_SETTINGS)
