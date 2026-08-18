#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Shared helpers for the GitHub integration commands (PCC-933).

Reading an agent's binding: the API's `git` object is the *configured* intent,
while `deployedCommit` is the provenance of what is actually running. The two
can legitimately disagree — a freshly linked agent still serves its pre-link
image until the next deploy — so they are reported separately rather than
collapsed into one "GitHub" status.
"""

import re

# Mirrors the API's isValidRepoFullName. Encoding alone is not enough on the
# wire: a pure-dot segment still retargets the path after normalization, so the
# shape is checked rather than escaped.
_REPO_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]+$")

# Matches the git_configs.dockerfile_path column default.
DEFAULT_DOCKERFILE_PATH = "Dockerfile"

_REF_BRANCH_PREFIX = "refs/heads/"

# Deploy-intent states that mean an attempt is still in flight. Anything else
# is terminal, which is what the --wait polling keys off.
IN_FLIGHT_DEPLOY_STATUSES = ("pending", "building", "deploying")


def is_valid_repo_full_name(repo_full_name: str) -> bool:
    """True for exactly two URL-safe `owner/repo` segments, no pure-dot segment."""
    segments = repo_full_name.split("/")
    return len(segments) == 2 and all(
        _REPO_SEGMENT.match(segment) and not re.fullmatch(r"\.+", segment) for segment in segments
    )


def is_valid_branch_name(branch: str) -> bool:
    """True for a git ref with no empty or pure-dot segment.

    Rejecting these at bind time is what stops a branch that can never be
    resolved from failing on the first deploy instead.
    """
    if not branch:
        return False
    return all(segment and not re.fullmatch(r"\.+", segment) for segment in branch.split("/"))


def short_sha(sha: str) -> str:
    return sha[:7]


def ref_to_branch(ref: str) -> str:
    """`refs/heads/main` -> `main`. Anything else is returned unchanged."""
    return ref[len(_REF_BRANCH_PREFIX) :] if ref.startswith(_REF_BRANCH_PREFIX) else ref


def is_deploy_in_flight(deploy: dict | None) -> bool:
    return bool(deploy) and (deploy or {}).get("status") in IN_FLIGHT_DEPLOY_STATUSES


def installation_settings_url(installation: dict) -> str:
    """The GitHub page for managing this installation's repo access.

    The path differs for organization and user accounts; the API normalizes
    `githubAccountType` to lowercase.
    """
    login = installation.get("githubAccountLogin", "")
    installation_id = installation.get("githubInstallationId", "")
    if str(installation.get("githubAccountType", "")).lower() == "organization":
        return f"https://github.com/organizations/{login}/settings/installations/{installation_id}"
    return f"https://github.com/settings/installations/{installation_id}"


def repo_url(repo_full_name: str) -> str:
    return f"https://github.com/{repo_full_name}"


def commit_url(repo_full_name: str, sha: str) -> str:
    return f"https://github.com/{repo_full_name}/commit/{sha}"


def binding_summary(git: dict | None) -> str:
    """One-line `owner/repo@branch` for a binding, or a dash when unlinked."""
    if not git:
        return "—"
    return f"{git.get('repoFullName', '?')}@{git.get('branch', '?')}"


def describe_deploy(deploy: dict | None) -> str:
    """A deploy attempt as `status (abc1234)`, with its reason when it failed."""
    if not deploy:
        return "—"
    status = deploy.get("status", "unknown")
    sha = deploy.get("commitSha")
    text = f"{status} ({short_sha(sha)})" if sha else status
    reason = deploy.get("reason")
    if reason and status in ("failed", "cancelled"):
        text += f": {reason}"
    return text


def is_running_linked_binding(git: dict | None, deployed_commit: dict | None) -> bool:
    """Whether what is running actually came from the linked repo and branch.

    False for the three states worth calling out: an agent just linked and
    still serving its pre-link image, one whose repo was re-pointed, and one
    whose branch was changed. All are legitimate, and all mean nothing from
    this binding is live yet.

    Repo names compare case-insensitively (GitHub treats owner/repo that way);
    branches compare exactly, since git refs are case-sensitive. A missing
    `ref` means "unknown", not "mismatched" — an older build recorded without
    one says nothing about the branch.
    """
    if not git or not deployed_commit:
        return False
    deployed_repo = deployed_commit.get("repoFullName")
    if not deployed_repo:
        return False
    if deployed_repo.lower() != str(git.get("repoFullName", "")).lower():
        return False
    ref = deployed_commit.get("ref")
    if not ref:
        return True
    return ref_to_branch(ref) == git.get("branch")
