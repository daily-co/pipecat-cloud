#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import functools
import os
from enum import Enum
from typing import Callable, List, Optional

import toml
from attr import dataclass, field
from loguru import logger

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
    degraded_reason: Optional[str] = None
    current_revision: Optional[dict] = None
    previous_revision: Optional[dict] = None


def _find_condition(conditions: list, condition_type: str) -> Optional[dict]:
    """Find a condition by type from the conditions array."""
    for c in conditions:
        if c.get("type") == condition_type:
            return c
    return None


def _format_elapsed(phase_started_at: Optional[str]) -> str:
    """Format elapsed time since phaseStartedAt as a human-readable string."""
    if not phase_started_at:
        return ""
    try:
        from datetime import datetime, timezone

        started = datetime.fromisoformat(phase_started_at.replace("Z", "+00:00"))
        elapsed = datetime.now(timezone.utc) - started
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
    """Format a single revision as an indented detail line."""
    deploy_id = rev.get("deploymentID", "unknown")[:8]
    phase = rev.get("phase", "Unknown")
    ready_replicas = rev.get("readyReplicas")

    parts = [f"    [dim]{label}[/dim] [bold]({deploy_id})[/bold] {phase}"]
    if ready_replicas is not None:
        parts.append(f"[dim]·[/dim] {ready_replicas} replicas")

    elapsed = _format_elapsed(rev.get("phaseStartedAt"))
    if elapsed:
        parts.append(f"[dim]·[/dim] [dim]{elapsed}[/dim]")

    return " ".join(parts)


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
    desired_deployment_id: Optional[str] = None,
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
    min_agents: Optional[int] = 0
    max_agents: Optional[int] = None
    # @deprecated
    min_instances: Optional[int] = field(default=None, metadata={"deprecated": True})
    # @deprecated
    max_instances: Optional[int] = field(default=None, metadata={"deprecated": True})

    def __attrs_post_init__(self):
        # Handle deprecated fields
        if self.min_instances is not None:
            logger.warning("min_instances is deprecated, use min_agents instead")
            self.min_agents = self.min_instances

        if self.max_instances is not None:
            logger.warning("max_instances is deprecated, use max_agents instead")
            self.max_agents = self.max_instances

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
    audio_filter: Optional[KrispVivaAudioFilter] = None

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


@dataclass
class BuildConfig:
    """Configuration for cloud builds."""

    context_dir: str = "."
    dockerfile: str = "Dockerfile"
    exclude_patterns: List[str] = field(factory=list)

    def to_dict(self):
        return {
            "context_dir": self.context_dir,
            "dockerfile": self.dockerfile,
            "exclude_patterns": self.exclude_patterns,
        }


@dataclass
class DeployConfigParams:
    agent_name: Optional[str] = None
    image: Optional[str] = None
    build_id: Optional[str] = None  # For cloud builds
    image_credentials: Optional[str] = None
    secret_set: Optional[str] = None
    region: Optional[str] = None
    scaling: ScalingParams = ScalingParams()
    enable_krisp: bool = False
    enable_managed_keys: bool = False
    docker_config: dict = field(factory=dict)
    build_config: BuildConfig = field(factory=BuildConfig)  # Cloud build configuration
    agent_profile: Optional[str] = None
    krisp_viva: KrispVivaConfig = field(factory=KrispVivaConfig)

    def __attrs_post_init__(self):
        if self.image is not None and ":" not in self.image:
            raise ValueError("Provided image must include tag e.g. my-image:latest")
        # Cannot specify both image and build_id
        if self.image is not None and self.build_id is not None:
            raise ValueError("Cannot specify both 'image' and 'build_id'")

    def to_dict(self):
        return {
            "agent_name": self.agent_name,
            "image": self.image,
            "build_id": self.build_id,
            "image_credentials": self.image_credentials,
            "secret_set": self.secret_set,
            "region": self.region,
            "scaling": self.scaling.to_dict() if self.scaling else None,
            "enable_krisp": self.enable_krisp,
            "enable_managed_keys": self.enable_managed_keys,
            "docker_config": self.docker_config,
            "build_config": self.build_config.to_dict() if self.build_config else None,
            "agent_profile": self.agent_profile,
            "krisp_viva": self.krisp_viva.to_dict() if self.krisp_viva else None,
        }


def load_deploy_config_file() -> Optional[DeployConfigParams]:
    from pipecatcloud.cli.config import deploy_config_path

    logger.debug(f"Deploy config path: {deploy_config_path}")
    logger.debug(f"Deploy config path exists: {os.path.exists(deploy_config_path)}")

    try:
        with open(deploy_config_path, "r") as f:
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

        # Extract build configuration if present
        build_data = config_data.pop("build", {})
        exclude_data = build_data.pop("exclude", {})
        build_config = BuildConfig(
            context_dir=build_data.get("context_dir", "."),
            dockerfile=build_data.get("dockerfile", "Dockerfile"),
            exclude_patterns=exclude_data.get("patterns", []),
        )

        # Create DeployConfigParams with validated data
        validated_config = DeployConfigParams(
            **config_data,
            scaling=scaling_params,
            docker_config=docker_data,
            build_config=build_config,
            krisp_viva=krisp_viva_config,
        )

        # Check for unexpected keys
        expected_keys = {
            "agent_name",
            "image",
            "build_id",
            "image_credentials",
            "secret_set",
            "region",
            "scaling",
            "enable_krisp",
            "enable_managed_keys",
            "docker",
            "build",
            "agent_profile",
            "krisp_viva",
        }
        unexpected_keys = set(config_data.keys()) - expected_keys
        if unexpected_keys:
            raise ConfigFileError(f"Unexpected keys in config file: {unexpected_keys}")

        return validated_config

    except Exception as e:
        logger.debug(e)
        raise ConfigFileError(str(e))


def with_deploy_config(func: Callable) -> Callable:
    """
    Decorator that loads the deploy config file and injects it into the function.
    If the config file exists, it will be loaded and passed to the function as `deploy_config`.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            deploy_config = load_deploy_config_file()
            kwargs["deploy_config"] = deploy_config
        except Exception as e:
            logger.error(f"Error loading deploy config: {e}")
            raise ConfigFileError(str(e))
        return func(*args, **kwargs)

    return wrapper
