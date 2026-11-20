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
        "https://github.com/pipecat-ai/pipecat-quickstart/archive/refs/heads/main.zip"
    ),
    "onboarding_path": _Setting("/v1/onboarding"),
    "auth_config_path": _Setting("/auth/config"),
    "logout_path": _Setting("/auth/logout"),
    "whoami_path": _Setting("/v1/users"),
    "organization_path": _Setting("/v1/organizations"),
    "daily_key_path": _Setting("/v1/organizations/{org}/daily"),
    "services_path": _Setting("/v1/organizations/{org}/services"),
    "services_logs_path": _Setting("/v1/organizations/{org}/services/{service}/logs"),
    "services_deployments_path": _Setting("/v1/organizations/{org}/services/{service}/deployments"),
    "services_sessions_path": _Setting("/v1/organizations/{org}/services/{service}/sessions"),
    "start_path": _Setting("/v1/public/{service}/start"),
    "api_keys_path": _Setting("/v1/organizations/{org}/apiKeys"),
    "secrets_path": _Setting("/v1/organizations/{org}/secrets"),
    "regions_path": _Setting("/v1/organizations/{org}/regions"),
    "registry_keys_path": _Setting("/v1/organizations/{org}/registry-keys"),
    "agent_profiles_path": _Setting("/v1/organizations/{org}/agent-profiles"),
    "properties_path": _Setting("/v1/organizations/{org}/properties"),
    "spend_limit_path": _Setting("/v1/organizations/{org}/spend-limit"),
    "eval_runs_path": _Setting("/v1/organizations/{org}/services/{service}/eval-runs"),
    "eval_capabilities_path": _Setting("/v1/organizations/{org}/eval-capabilities"),
    "eval_org_runs_path": _Setting("/v1/organizations/{org}/eval-runs"),
    "eval_suite_runs_create_path": _Setting(
        "/v1/organizations/{org}/services/{service}/eval-suite-runs"
    ),
    "eval_suite_runs_path": _Setting("/v1/organizations/{org}/eval-suite-runs"),
    "eval_bundles_path": _Setting("/v1/organizations/{org}/eval-bundles"),
    "eval_bundle_revisions_path": _Setting("/v1/organizations/{org}/eval-bundle-revisions"),
    "builds_path": _Setting("/v1/organizations/{org}/builds"),
    # GitHub App integration (PCC-933). The org-scoped management endpoints
    # and the per-environment repo binding. An agent has exactly one
    # `environments` row, named "production", so the binding path hardcodes it
    # the same way the dashboard does. A project's environments are agents of
    # their own, each with its own production binding, so this stays right.
    "github_install_url_path": _Setting("/v1/organizations/{org}/github/install-url"),
    "github_link_url_path": _Setting("/v1/organizations/{org}/github/link-url"),
    "github_installation_path": _Setting("/v1/organizations/{org}/github/installation"),
    "github_repositories_path": _Setting("/v1/organizations/{org}/github/repositories"),
    "service_git_path": _Setting(
        "/v1/organizations/{org}/services/{service}/environments/production/git"
    ),
    # Projects and environments (PCC-1255). An environment is an agent of its
    # own, linked to the project it belongs to; previews and watch paths are
    # project settings on the project's own binding.
    "service_parent_path": _Setting("/v1/organizations/{org}/services/{service}/parent"),
    "service_previews_path": _Setting("/v1/organizations/{org}/services/{service}/previews"),
    "service_watch_paths_path": _Setting("/v1/organizations/{org}/services/{service}/watch-paths"),
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
