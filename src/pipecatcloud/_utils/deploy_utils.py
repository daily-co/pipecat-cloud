#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
import functools
from typing import Optional, Callable, Any

import toml
from attr import dataclass, field
from loguru import logger

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
class DeployConfigParams:
    agent_name: Optional[str] = None
    image: Optional[str] = None
    image_credentials: Optional[str] = None
    secret_set: Optional[str] = None
    scaling: ScalingParams = ScalingParams()
    enable_krisp: bool = False

    def __attrs_post_init__(self):
        if self.image is not None and ":" not in self.image:
            raise ValueError("Provided image must include tag e.g. my-image:latest")

    def to_dict(self):
        return {
            "agent_name": self.agent_name,
            "image": self.image,
            "image_credentials": self.image_credentials,
            "secret_set": self.secret_set,
            "scaling": self.scaling.to_dict() if self.scaling else None,
            "enable_krisp": self.enable_krisp,
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

        # Create DeployConfigParams with validated data
        validated_config = DeployConfigParams(
            **config_data,
            scaling=scaling_params,
        )

        # Check for unexpected keys
        expected_keys = {
            "agent_name",
            "image",
            "image_credentials",
            "secret_set",
            "scaling",
            "enable_krisp",
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
            kwargs['deploy_config'] = deploy_config
        except Exception as e:
            logger.error(f"Error loading deploy config: {e}")
            raise ConfigFileError(str(e))
        return func(*args, **kwargs)
    return wrapper
