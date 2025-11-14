#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import functools
import os
from typing import Callable, Optional

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
class DeployConfigParams:
    agent_name: Optional[str] = None
    image: Optional[str] = None
    image_credentials: Optional[str] = None
    secret_set: Optional[str] = None
    region: Optional[str] = None
    scaling: ScalingParams = ScalingParams()
    enable_krisp: bool = False
    enable_managed_keys: bool = False
    docker_config: dict = field(factory=dict)
    agent_profile: Optional[str] = None
    krisp_viva: KrispVivaConfig = field(factory=KrispVivaConfig)

    def __attrs_post_init__(self):
        if self.image is not None and ":" not in self.image:
            raise ValueError("Provided image must include tag e.g. my-image:latest")

    def to_dict(self):
        return {
            "agent_name": self.agent_name,
            "image": self.image,
            "image_credentials": self.image_credentials,
            "secret_set": self.secret_set,
            "region": self.region,
            "scaling": self.scaling.to_dict() if self.scaling else None,
            "enable_krisp": self.enable_krisp,
            "enable_managed_keys": self.enable_managed_keys,
            "docker_config": self.docker_config,
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

        # Create DeployConfigParams with validated data
        validated_config = DeployConfigParams(
            **config_data,
            scaling=scaling_params,
            docker_config=docker_data,
            krisp_viva=krisp_viva_config,
        )

        # Check for unexpected keys
        expected_keys = {
            "agent_name",
            "image",
            "image_credentials",
            "secret_set",
            "region",
            "scaling",
            "enable_krisp",
            "enable_managed_keys",
            "docker",
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
