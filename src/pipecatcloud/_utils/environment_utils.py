#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Shared helpers for projects and environments (PCC-1255).

An environment is an agent of its own, linked to the project it belongs to.
The project doubles as its own `production` environment, so a project is
simply an agent with no parent. These helpers read that shape out of the
service payloads the API already returns, and mirror the name rules it
enforces so a typo fails before the round trip.
"""

import re

#: What the project itself is called among its environments.
PRODUCTION_ENVIRONMENT_NAME = "production"

#: PR previews are named `pr-<n>`, so the API reserves the prefix.
PREVIEW_ENVIRONMENT_PREFIX = "pr-"

#: The width of the environments.name column.
MAX_ENVIRONMENT_NAME_LENGTH = 50

# The agent-name rule, which environment names follow too.
_ENVIRONMENT_NAME_SHAPE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])$")

_PREVIEW_NAME = re.compile(r"^pr-(\d+)$")


def environment_name_problem(name: str) -> str | None:
    """Why the API would refuse this environment name, or None when it's fine."""
    if not name:
        return "Enter a name for the environment."
    if name == PRODUCTION_ENVIRONMENT_NAME:
        return f"'{PRODUCTION_ENVIRONMENT_NAME}' is the project itself, so it can't be an environment name."
    if name.startswith(PREVIEW_ENVIRONMENT_PREFIX):
        return f"Names starting with '{PREVIEW_ENVIRONMENT_PREFIX}' are reserved for PR previews."
    if len(name) > MAX_ENVIRONMENT_NAME_LENGTH:
        return f"Use at most {MAX_ENVIRONMENT_NAME_LENGTH} characters."
    if not _ENVIRONMENT_NAME_SHAPE.match(name):
        return (
            "Use two or more lowercase letters, digits and dashes, starting and "
            "ending with a letter or digit."
        )
    return None


def is_preview_name(name: str | None) -> bool:
    """Whether an environment's name is a PR preview's."""
    return bool(name and _PREVIEW_NAME.match(name))


def environment_sort_key(name: str) -> tuple[int, int, str]:
    """Named environments by name, then PR previews, newest pull request first."""
    preview = _PREVIEW_NAME.match(name or "")
    if preview:
        return (1, -int(preview.group(1)), "")
    return (0, 0, name or "")


def is_environment(service: dict) -> bool:
    """Whether this agent is an environment of another agent."""
    return bool(service.get("parentServiceId"))


def projects_in(services: list[dict]) -> list[dict]:
    """The agents that are projects: the ones that are not environments."""
    return [service for service in services if not is_environment(service)]


def environments_of(services: list[dict], project: dict) -> list[dict]:
    """A project's environments, named ones first and then its PR previews."""
    children = [
        service for service in services if service.get("parentServiceId") == project.get("id")
    ]
    return sorted(children, key=lambda s: environment_sort_key(s.get("environmentName") or ""))


def promotion_source(environment: dict) -> dict | None:
    """What promoting this environment deploys: its build, or the image it runs.

    None when it has nothing deployed. Promotion moves code only, so this is
    the whole payload the project is updated with.
    """
    deployment = environment.get("deployment") or {}
    build_id = deployment.get("buildId")
    if build_id:
        return {"buildId": build_id}
    image = ((deployment.get("manifest") or {}).get("spec") or {}).get("image")
    if image:
        return {"image": image}
    return None


def describe_promotion_source(source: dict) -> str:
    """A promotion source as a short phrase, for confirmations and messages."""
    build_id = source.get("buildId")
    return f"build {build_id}" if build_id else str(source.get("image"))


def previews_on(service: dict) -> bool:
    """Whether this project has PR previews on.

    Choosing the secret set previews mount is what turns them on, so the set
    being present is the setting.
    """
    git = service.get("git") or {}
    return bool(git.get("previewSecretSet"))


def project_label(service: dict, project_names: dict[str, str]) -> str:
    """`project > environment` for an environment, or a dash for a project.

    `project_names` maps agent id to name, which is how a parent's name is
    resolved: the service payload carries only `parentServiceId`.
    """
    if not is_environment(service):
        return "—"
    parent = project_names.get(service.get("parentServiceId") or "", "?")
    return f"{parent} > {service.get('environmentName') or '?'}"
