#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import asyncio
import functools
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum

import toml
import typer
from attr import dataclass, field
from loguru import logger

from pipecatcloud.cli import PIPECAT_DEPLOY_CONFIG_PATH
from pipecatcloud.constants import KRISP_VIVA_MODELS, KrispVivaAudioFilter
from pipecatcloud.exception import ConfigFileError

DEPLOY_STATUS_MAP = {
    "Unknown": "[dim]Waiting[/dim]",
    "True": "[green]Ready[/green]",
    "False": "[yellow]Creating[/yellow]",
}


class DeploymentPhase(Enum):
    WAITING_FOR_OPERATOR = "waiting_for_operator"
    PROGRESSING_AVAILABLE = "progressing_available"
    PROGRESSING_NEW = "progressing_new"
    DEGRADED_AVAILABLE = "degraded_available"
    UNAVAILABLE = "unavailable"
    READY = "ready"


@dataclass
class DeploymentStatus:
    phase: DeploymentPhase
    status_message: str
    is_available: bool = False
    is_ready: bool = False
    degraded_reason: str | None = None
    current_revision: dict | None = None
    previous_revision: dict | None = None


def _find_condition(conditions: list, condition_type: str) -> dict | None:
    """Find a condition by type from the conditions array."""
    for c in conditions:
        if c.get("type") == condition_type:
            return c
    return None


def _format_elapsed(phase_started_at: str | None) -> str:
    """Format elapsed time since phaseStartedAt as a human-readable string."""
    if not phase_started_at:
        return ""
    try:
        started = datetime.fromisoformat(phase_started_at.replace("Z", "+00:00"))
        elapsed = datetime.now(UTC) - started
        total_seconds = int(elapsed.total_seconds())
        if total_seconds < 0:
            return ""
        if total_seconds < 60:
            return f"{total_seconds}s"
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}m {seconds}s"
    except (ValueError, TypeError):
        return ""


def _format_revision_line(label: str, rev: dict) -> str:
    """Format a single revision as an indented detail line, with health details if present."""
    deploy_id = rev.get("deploymentID", "unknown")[:8]
    phase = rev.get("phase", "Unknown")
    ready_replicas = rev.get("readyReplicas")

    parts = [f"    [dim]{label}[/dim] [bold]({deploy_id})[/bold] {phase}"]
    if ready_replicas is not None:
        parts.append(f"[dim]·[/dim] {ready_replicas} agents")

    elapsed = _format_elapsed(rev.get("phaseStartedAt"))
    if elapsed:
        parts.append(f"[dim]·[/dim] [dim]{elapsed}[/dim]")

    lines = [" ".join(parts)]

    # Append health details if present and unhealthy
    health = rev.get("health")
    if health and not (health.get("ready") and health.get("restartCount", 0) == 0):
        lines.extend(format_health_lines(health))

    if rev.get("hasInfrastructureIssue"):
        lines.append("      [yellow]Infrastructure issue detected — contact support[/yellow]")

    return "\n".join(lines)


def format_health_lines(health: dict) -> list:
    """Format health details as indented lines under a revision."""
    lines = []
    reason_parts = []

    # Prefer the customer-friendly headline from the API when present. Older API
    # responses don't include it, so fall back to constructing a string from raw
    # k8s fields.
    headline = health.get("headline")
    reason = health.get("reason", "")
    term_reason = health.get("lastTerminationReason", "")
    exit_code = health.get("lastExitCode")

    if headline:
        reason_parts.append(f"[red]{headline}[/red]")
    elif reason:
        detail = reason
        if term_reason and term_reason != reason:
            detail += f" ({term_reason}"
            if exit_code is not None:
                detail += f", exit code {exit_code}"
            detail += ")"
        elif exit_code is not None:
            detail += f" (exit code {exit_code})"
        reason_parts.append(f"[red]{detail}[/red]")

    restarts = health.get("restartCount", 0)
    replicas_started = health.get("replicasStarted", 0)
    if restarts > 0:
        reason_parts.append(f"[dim]·[/dim] {restarts} restarts across {replicas_started} replicas")

    # When using the headline, surface the exit code as its own subhead segment.
    # The fallback path already includes the exit code inline with the reason.
    if headline and exit_code is not None:
        reason_parts.append(f"[dim]·[/dim] exit code {exit_code}")

    if reason_parts:
        lines.append("      " + " ".join(reason_parts))

    message = health.get("message", "")
    if message:
        last_line = message.strip().split("\n")[-1].strip()
        if last_line:
            lines.append(f"      [dim]{last_line}[/dim]")

    return lines


def _build_status_message(headline: str, current_rev=None, previous_rev=None) -> str:
    """Build a multi-line status message with optional revision detail lines."""
    lines = [headline]
    if current_rev:
        lines.append(_format_revision_line("Current ", current_rev))
    if previous_rev:
        lines.append(_format_revision_line("Previous", previous_rev))
    return "\n".join(lines)


def interpret_deployment_status(
    agent_status: dict,
    desired_deployment_id: str | None = None,
) -> DeploymentStatus:
    """Interpret the raw API response into a structured deployment status.

    Examines reconciledDeploymentId, conditions array, available/ready booleans,
    and revision info to determine the current deployment phase and build a
    human-readable status message.
    """
    reconciled_id = agent_status.get("reconciledDeploymentId")
    desired_id = desired_deployment_id or agent_status.get(
        "desiredDeploymentId", agent_status.get("activeDeploymentId")
    )
    conditions = agent_status.get("conditions") or []
    available = agent_status.get("available", agent_status.get("ready", False))
    ready = agent_status.get("ready", False)
    active_deployment_ready = agent_status.get("activeDeploymentReady", False)

    current_rev = agent_status.get("currentRevision")
    previous_rev = agent_status.get("previousRevision")

    # 1. Check if operator has reconciled
    if desired_id and reconciled_id != desired_id:
        return DeploymentStatus(
            phase=DeploymentPhase.WAITING_FOR_OPERATOR,
            status_message="[dim]Waiting for operator to process deployment...[/dim]",
            current_revision=current_rev,
            previous_revision=previous_rev,
        )

    # 2. Fully ready
    if available and (ready or active_deployment_ready):
        return DeploymentStatus(
            phase=DeploymentPhase.READY,
            status_message="[green]Deployment is ready[/green]",
            is_available=True,
            is_ready=True,
            current_revision=current_rev,
            previous_revision=previous_rev,
        )

    # 3. Look at conditions for richer status
    degraded = _find_condition(conditions, "Degraded")
    progressing = _find_condition(conditions, "Progressing")

    degraded_active = degraded and degraded.get("status") == "True"
    degraded_reason = (
        degraded.get("message", degraded.get("reason", "")) if degraded_active else None
    )

    # 4. Degraded but available — warning state
    if degraded_active and available:
        headline = f"[yellow]Degraded · Available[/yellow] [dim]— {degraded_reason}[/dim]"
        return DeploymentStatus(
            phase=DeploymentPhase.DEGRADED_AVAILABLE,
            status_message=_build_status_message(headline, current_rev, previous_rev),
            is_available=True,
            degraded_reason=degraded_reason,
            current_revision=current_rev,
            previous_revision=previous_rev,
        )

    # 5. Available but not ready — rolling update in progress
    if available and not ready:
        headline = "[cyan]Progressing · Available[/cyan]"
        return DeploymentStatus(
            phase=DeploymentPhase.PROGRESSING_AVAILABLE,
            status_message=_build_status_message(headline, current_rev, previous_rev),
            is_available=True,
            current_revision=current_rev,
            previous_revision=previous_rev,
        )

    # 6. Not available, progressing — new service coming up
    if not available and (progressing and progressing.get("status") == "True"):
        headline = "[dim]Progressing[/dim]"
        return DeploymentStatus(
            phase=DeploymentPhase.PROGRESSING_NEW,
            status_message=_build_status_message(headline, current_rev, previous_rev),
            current_revision=current_rev,
            previous_revision=previous_rev,
        )

    # 7. Not available, not progressing — broken
    if not available and not ready:
        reason_suffix = f" [dim]— {degraded_reason}[/dim]" if degraded_reason else ""
        headline = f"[red]Unavailable[/red]{reason_suffix}"
        return DeploymentStatus(
            phase=DeploymentPhase.UNAVAILABLE,
            status_message=_build_status_message(headline, current_rev, previous_rev),
            current_revision=current_rev,
            previous_revision=previous_rev,
        )

    # Fallback — shouldn't normally reach here
    return DeploymentStatus(
        phase=DeploymentPhase.PROGRESSING_NEW,
        status_message="[dim]Waiting for deployment to become ready...[/dim]",
        current_revision=current_rev,
        previous_revision=previous_rev,
    )


@dataclass
class ScalingParams:
    min_agents: int | None = 0
    max_agents: int | None = None

    def __attrs_post_init__(self):
        # Validation
        if self.min_agents is not None:
            if self.min_agents < 0:
                raise ValueError("min_agents must be greater than or equal to 0")

        if self.max_agents is not None:
            if self.max_agents < 1:
                raise ValueError("max_agents must be greater than 0")

            if self.min_agents is not None and self.max_agents < self.min_agents:
                raise ValueError("max_agents must be greater than or equal to min_agents")

    def to_dict(self):
        return {"min_agents": self.min_agents, "max_agents": self.max_agents}


@dataclass
class KrispVivaConfig:
    audio_filter: KrispVivaAudioFilter | None = None

    def __attrs_post_init__(self):
        # Validation against known models
        # IMPORTANT: KRISP_VIVA_MODELS must be kept in sync with API configuration
        if self.audio_filter is not None:
            if self.audio_filter not in KRISP_VIVA_MODELS:
                raise ValueError(
                    f"audio_filter must be one of {KRISP_VIVA_MODELS}, got '{self.audio_filter}'"
                )

    def to_dict(self):
        return {"audio_filter": self.audio_filter}


# Mirrors the server-side k8s quantity validation (positive decimal + optional
# suffix). Kept client-side so a typo fails before any network round-trip.
K8S_QUANTITY_PATTERN = r"^[0-9]+(\.[0-9]+)?(m|k|M|G|T|P|E|Ki|Mi|Gi|Ti|Pi|Ei)?$"


@dataclass
class ResourcesConfig:
    """Explicit sizing for agents in enterprise (self-hosted) regions.

    Mutually exclusive with agent_profile: a deploy either references a named
    profile or states cpu/memory directly. Only accepted by the API for
    services in self-hosted regions.
    """

    cpu: str | None = None
    memory: str | None = None

    def __attrs_post_init__(self):
        import re

        if (self.cpu is None) != (self.memory is None):
            raise ValueError("resources requires both 'cpu' and 'memory'")
        for name, value in (("cpu", self.cpu), ("memory", self.memory)):
            if value is not None and not re.match(K8S_QUANTITY_PATTERN, str(value)):
                raise ValueError(
                    f"Invalid {name} quantity '{value}' (expected e.g. '500m', '2', '4Gi')"
                )

    def is_set(self) -> bool:
        return self.cpu is not None

    def to_dict(self):
        return {"cpu": self.cpu, "memory": self.memory}


def parse_resources_option(value: str) -> "ResourcesConfig":
    """Parse the --resources CLI value ("cpu=2,memory=4Gi") into a ResourcesConfig.

    Raises ValueError with a specific message on malformed input (unknown keys,
    missing cpu/memory, bad quantities) so the caller can surface exactly what
    was wrong rather than a generic usage error.
    """
    parts: dict[str, str] = {}
    for chunk in value.split(","):
        key, sep, val = chunk.partition("=")
        if not sep or not val.strip():
            raise ValueError(
                f"Malformed --resources segment '{chunk.strip()}'. "
                "Expected key=value pairs, e.g. cpu=2,memory=4Gi"
            )
        parts[key.strip()] = val.strip()
    if set(parts.keys()) != {"cpu", "memory"}:
        raise ValueError(
            "--resources requires exactly 'cpu' and 'memory', "
            f"got: {', '.join(sorted(parts.keys())) or 'nothing'}"
        )
    # ResourcesConfig raises its own specific ValueError for bad quantities.
    return ResourcesConfig(cpu=parts["cpu"], memory=parts["memory"])


@dataclass
class GitSourceConfig:
    """A GitHub repo/branch to build the agent from, instead of an image.

    Only valid when creating an agent: the API accepts `git` on create and
    ignores it on update, so a binding change on an existing agent goes
    through `agent link` rather than silently doing nothing here.
    """

    repo: str | None = None
    branch: str | None = None
    dockerfile_path: str | None = None
    subdirectory: str | None = None

    def __attrs_post_init__(self):
        # Same rules the API applies, so a typo fails before the round-trip.
        from pipecatcloud._utils.github_utils import (
            is_valid_branch_name,
            is_valid_repo_full_name,
        )

        if self.repo is not None and not is_valid_repo_full_name(self.repo):
            raise ValueError(f"Invalid repo '{self.repo}'. Expected the form 'owner/repo'.")
        if self.branch is not None and not is_valid_branch_name(self.branch):
            raise ValueError(
                f"Invalid branch '{self.branch}'. A branch must be a valid git ref with no "
                "empty or dot-only segments."
            )
        # A half-specified source would otherwise reach the API as a plain
        # image deploy, which is a confusing way to learn the branch is missing.
        if (self.repo is None) != (self.branch is None):
            raise ValueError("A GitHub source requires both 'repo' and 'branch'")

    def is_set(self) -> bool:
        return self.repo is not None

    def to_payload(self) -> dict:
        """The `git` object for the create-service request."""
        payload = {"repoFullName": self.repo, "branch": self.branch}
        if self.dockerfile_path:
            payload["dockerfilePath"] = self.dockerfile_path
        if self.subdirectory:
            payload["subdirectory"] = self.subdirectory
        return payload

    def to_dict(self):
        return {
            "repo": self.repo,
            "branch": self.branch,
            "dockerfile_path": self.dockerfile_path,
            "subdirectory": self.subdirectory,
        }


@dataclass
class BuildConfig:
    """Configuration for cloud builds."""

    context_dir: str = "."
    dockerfile: str = "Dockerfile"
    exclude_patterns: list[str] = field(factory=list)

    def to_dict(self):
        return {
            "context_dir": self.context_dir,
            "dockerfile": self.dockerfile,
            "exclude_patterns": self.exclude_patterns,
        }


@dataclass
class DeployConfigParams:
    agent_name: str | None = None
    image: str | None = None
    build_id: str | None = None  # For cloud builds
    image_credentials: str | None = None
    secret_set: str | None = None
    region: str | None = None
    scaling: ScalingParams = ScalingParams()
    docker_config: dict = field(factory=dict)
    build_config: BuildConfig = field(factory=BuildConfig)  # Cloud build configuration
    agent_profile: str | None = None
    resources: ResourcesConfig = field(factory=ResourcesConfig)
    krisp_viva: KrispVivaConfig = field(factory=KrispVivaConfig)
    git: GitSourceConfig = field(factory=GitSourceConfig)
    force_redeploy: bool = False
    websocket_auth: str | None = None
    max_session_duration: int | None = None
    # CPU architecture the agent image requires (PCC-1105). Exactly the
    # kubernetes.io/arch vocabulary; omitted = the region's default.
    architecture: str | None = None

    def __attrs_post_init__(self):
        if self.image is not None and ":" not in self.image:
            raise ValueError("Provided image must include tag e.g. my-image:latest")
        # Cannot specify both image and build_id
        if self.image is not None and self.build_id is not None:
            raise ValueError("Cannot specify both 'image' and 'build_id'")
        # A git-sourced agent's image is produced by the build its first deploy
        # triggers, so there is nothing to supply up front. The API rejects the
        # combination too; failing here just does it sooner.
        if self.git.is_set() and (self.image is not None or self.build_id is not None):
            raise ValueError("Cannot specify a GitHub source together with 'image' or 'build_id'")
        if self.max_session_duration is not None and not 60 <= self.max_session_duration <= 14400:
            raise ValueError("max_session_duration must be between 60 and 14400 seconds")
        # Sizing is one of: a named profile, or explicit resources (enterprise
        # regions). The API enforces this too; failing here is just faster.
        if self.agent_profile is not None and self.resources.is_set():
            raise ValueError("Cannot specify both 'agent_profile' and 'resources'")
        if self.architecture is not None and self.architecture not in ("amd64", "arm64"):
            raise ValueError("architecture must be 'amd64' or 'arm64'")

    def to_dict(self):
        return {
            "agent_name": self.agent_name,
            "image": self.image,
            "build_id": self.build_id,
            "image_credentials": self.image_credentials,
            "secret_set": self.secret_set,
            "region": self.region,
            "scaling": self.scaling.to_dict() if self.scaling else None,
            "docker_config": self.docker_config,
            "build_config": self.build_config.to_dict() if self.build_config else None,
            "agent_profile": self.agent_profile,
            "resources": self.resources.to_dict() if self.resources.is_set() else None,
            "krisp_viva": self.krisp_viva.to_dict() if self.krisp_viva else None,
            "git": self.git.to_dict() if self.git.is_set() else None,
            "websocket_auth": self.websocket_auth,
            "max_session_duration": self.max_session_duration,
            "architecture": self.architecture,
        }


def validate_git_source_combination(config: DeployConfigParams) -> str | None:
    """What a GitHub source cannot be combined with, or None when it is fine.

    A separate check from DeployConfigParams' own validation because the deploy
    command mutates the config field by field after constructing it, so
    __attrs_post_init__ never sees the merged result.
    """
    if not config.git.is_set():
        return None
    if config.image or config.build_id:
        return (
            "Cannot deploy from a GitHub repository and an image or build at the same "
            "time. Drop --image/--build-id, or drop --repo."
        )
    # A git agent's first-deploy config is stashed on its binding, and explicit
    # resources are not plumbed through that path yet, so the API refuses the
    # pair. Saying so here saves the round-trip.
    if config.resources.is_set():
        return (
            "Explicit resources are not yet supported for GitHub-sourced agents. "
            "Use [bold]--profile[/bold] instead."
        )
    return None


def load_deploy_config_file() -> DeployConfigParams | None:
    from pipecatcloud.cli.config import deploy_config_path

    logger.debug(f"Deploy config path: {deploy_config_path}")
    logger.debug(f"Deploy config path exists: {os.path.exists(deploy_config_path)}")

    try:
        with open(deploy_config_path) as f:
            config_data = toml.load(f)
    except Exception:
        return None

    try:
        # Extract scaling parameters if present
        scaling_data = config_data.pop("scaling", {})
        scaling_params = ScalingParams(**scaling_data)

        # Extract docker configuration if present
        docker_data = config_data.pop("docker", {})

        # Extract krisp_viva configuration if present
        krisp_viva_data = config_data.pop("krisp_viva", {})
        krisp_viva_config = KrispVivaConfig(**krisp_viva_data)

        # Extract explicit resources if present (enterprise regions)
        resources_data = config_data.pop("resources", {})
        resources_config = ResourcesConfig(**resources_data)

        # Extract GitHub source if present (PCC-933)
        git_data = config_data.pop("git", {})
        git_config = GitSourceConfig(**git_data)

        # Extract build configuration if present
        build_data = config_data.pop("build", {})
        exclude_data = build_data.pop("exclude", {})
        build_config = BuildConfig(
            context_dir=build_data.get("context_dir", "."),
            dockerfile=build_data.get("dockerfile", "Dockerfile"),
            exclude_patterns=exclude_data.get("patterns", []),
        )

        # Check for unexpected keys before constructing the config, so users get a clear
        # message instead of a raw constructor TypeError.
        expected_keys = {
            "agent_name",
            "image",
            "build_id",
            "image_credentials",
            "secret_set",
            "region",
            "scaling",
            "docker",
            "build",
            "agent_profile",
            "krisp_viva",
            "git",
            "websocket_auth",
            "max_session_duration",
            "resources",
            "architecture",
        }

        # TODO: Remove this enable_krisp migration hint in the 2.0.0 release.
        if "enable_krisp" in config_data:
            raise ConfigFileError(
                "'enable_krisp' is no longer supported. Krisp is now configured via the "
                "[krisp_viva] section in pcc-deploy.toml. Remove 'enable_krisp' from your config."
            )
        unexpected_keys = set(config_data.keys()) - expected_keys
        if unexpected_keys:
            raise ConfigFileError(f"Unexpected keys in config file: {unexpected_keys}")

        # Create DeployConfigParams with validated data
        validated_config = DeployConfigParams(
            **config_data,
            scaling=scaling_params,
            docker_config=docker_data,
            build_config=build_config,
            krisp_viva=krisp_viva_config,
            resources=resources_config,
            git=git_config,
        )

        return validated_config

    except Exception as e:
        logger.debug(e)
        raise ConfigFileError(str(e))


CONFIG_FILE_OPTION: str | None = typer.Option(
    None,
    "--config-file",
    help=f"Path to deploy config file (default: {PIPECAT_DEPLOY_CONFIG_PATH})",
)


def with_deploy_config(func: Callable) -> Callable:
    """
    Decorator that loads the deploy config file and injects it into the function.
    If the config file exists, it will be loaded and passed to the function as `deploy_config`.

    If the wrapped function receives a `config_file` kwarg (from a --config-file typer option),
    it will override the default deploy config path before loading.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        config_file = kwargs.pop("config_file", None)
        if config_file:
            import pipecatcloud.cli.config as config_module

            if not os.path.exists(config_file):
                from pipecatcloud._utils.console_utils import console

                console.error(f"Deploy config file not found: {config_file}")
                raise typer.Exit(1)
            config_module.deploy_config_path = config_file
        try:
            deploy_config = load_deploy_config_file()
            kwargs["deploy_config"] = deploy_config
        except Exception as e:
            from pipecatcloud._utils.console_utils import console

            console.error(f"Error loading deploy config: {e}")
            raise typer.Exit(1)
        return func(*args, **kwargs)

    return wrapper


# How long `--wait` follows a GitHub deploy before handing it back. A GitHub
# deploy builds an image first, so it is legitimately slower than an image
# deploy; the cap exists so a wedged build can't hang a CI job forever, not to
# express an expected duration.
GIT_DEPLOY_WAIT_SECONDS = 20 * 60
GIT_DEPLOY_POLL_SECONDS = 5


class GitDeployWait(Enum):
    """How a `--wait` ended. Four outcomes, because three of them are not
    "succeeded" and collapsing them loses the distinction that matters."""

    # A status we actually observed reach a terminal state.
    TERMINAL = "terminal"
    # Observed, still moving when the budget ran out. Says nothing bad about
    # the deploy.
    IN_FLIGHT = "in_flight"
    # A newer attempt became the agent's latest, so ours is no longer
    # observable (and, unless it had already reached `deploying`, was
    # cancelled by the supersede).
    SUPERSEDED = "superseded"
    # Never saw our attempt at all: every poll failed, or no attempt was ever
    # enqueued. We know nothing, which is different from knowing it is fine.
    UNOBSERVED = "unobserved"


@dataclass
class GitDeployResult:
    outcome: GitDeployWait
    deploy: dict | None = None
    superseded_by: str | None = None


async def follow_git_deploy(agent_name: str, org: str | None, commit_sha: str) -> GitDeployResult:
    """Poll an agent's latest deploy attempt until it resolves.

    Polls the service read endpoint rather than a deploy-intent endpoint: the
    API surfaces the attempt as `latestDeploy` (PCC-978), which is also what
    `agent status` reads, so both agree on what a deploy is doing.

    `commit_sha` is the attempt we are entitled to report on. Empty means
    "whatever attempt exists", which is only correct for an agent's first
    deploy, where there is nothing that could have superseded it.
    """
    # Imported at call time: api.py imports this module for DeployConfigParams,
    # so a module-level import of the API client would close that cycle.
    from pipecatcloud._utils.console_utils import console
    from pipecatcloud._utils.github_utils import is_deploy_in_flight
    from pipecatcloud.cli.api import API

    deadline = time.monotonic() + GIT_DEPLOY_WAIT_SECONDS
    latest: dict | None = None
    last_status: str | None = None

    with console.status("[dim]Waiting for the deploy...[/dim]", spinner="bouncingBar") as live:
        while time.monotonic() < deadline:
            # A blip mid-deploy should not abort a wait that is otherwise
            # healthy; keep polling and let the deadline decide.
            data, error = await API.bubble_error().agent(agent_name=agent_name, org=org)
            if not error and data:
                candidate = data.get("latestDeploy")
                if candidate:
                    found_sha = candidate.get("commitSha")
                    # A different commit is positive evidence, not an absence
                    # of it. The API reads this from the primary ordered by
                    # created_at DESC, and our own intent was already
                    # committed when the trigger answered 202 — so anything
                    # else holding "latest" is strictly newer, and ours can
                    # never reclaim the spot. Waiting out the budget here
                    # would report a stale snapshot twenty minutes later.
                    if commit_sha and found_sha != commit_sha:
                        return GitDeployResult(
                            GitDeployWait.SUPERSEDED,
                            deploy=latest,
                            superseded_by=found_sha,
                        )
                    latest = candidate
                    status_value = candidate.get("status")
                    if status_value != last_status:
                        last_status = status_value
                        live.update(f"[dim]Deploy {status_value}...[/dim]")
                    if not is_deploy_in_flight(candidate):
                        return GitDeployResult(GitDeployWait.TERMINAL, deploy=latest)
            await asyncio.sleep(GIT_DEPLOY_POLL_SECONDS)

    if latest is None:
        return GitDeployResult(GitDeployWait.UNOBSERVED)
    return GitDeployResult(GitDeployWait.IN_FLIGHT, deploy=latest)


def report_git_deploy_result(
    result: GitDeployResult, agent_name: str, *, first_deploy: bool = False
) -> None:
    """Render a finished `--wait` and set the exit code.

    Shared by both wait call sites so the four outcomes can't be interpreted
    differently in two places.

    On exit codes: only an observed failure and a never-observed deploy exit
    non-zero. A deploy still building when the budget runs out has not failed,
    and neither has one that a newer push took over. But a wait that never saw
    anything cannot tell a healthy build from an API that was down the whole
    time, and reporting that as success is what would make `--wait` unsafe as
    a deploy gate.
    """
    from pipecatcloud._utils.console_utils import console
    from pipecatcloud._utils.github_utils import short_sha
    from pipecatcloud.cli import PIPECAT_CLI_NAME

    check_hint = (
        f"[dim]Check it with [bold]{PIPECAT_CLI_NAME} agent status {agent_name}[/bold].[/dim]"
    )
    label = "First deploy" if first_deploy else "Deploy"

    if result.outcome is GitDeployWait.TERMINAL:
        deploy = result.deploy or {}
        status_value = deploy.get("status")
        commit = deploy.get("commitSha")
        if status_value == "succeeded":
            console.success(
                f"Deployed '{agent_name}'" + (f" from commit {short_sha(commit)}" if commit else "")
            )
            return
        reason = deploy.get("reason") or "No reason reported."
        console.error(f"{label} of '{agent_name}' {status_value}.\n{reason}")
        raise typer.Exit(1)

    if result.outcome is GitDeployWait.SUPERSEDED:
        newer = short_sha(result.superseded_by) if result.superseded_by else "a newer commit"
        console.print(
            f"[yellow]Superseded: a newer deploy ({newer}) is now the latest attempt for "
            f"'{agent_name}', so this one's result is no longer reported.[/yellow]\n" + check_hint
        )
        return

    if result.outcome is GitDeployWait.IN_FLIGHT:
        status_value = (result.deploy or {}).get("status") or "in progress"
        console.print(
            f"[yellow]Still {status_value} after waiting. The deploy continues "
            f"server-side.[/yellow]\n" + check_hint
        )
        return

    # UNOBSERVED. Never assert progress we did not see.
    console.error(
        f"Could not confirm the deploy of '{agent_name}': no deploy attempt was visible "
        "while waiting.\n"
        "[dim]The API may have been unreachable, or the attempt may never have been "
        f"queued.[/dim]\n" + check_hint
    )
    raise typer.Exit(1)
