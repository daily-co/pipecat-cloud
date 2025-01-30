#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import os
from typing import Optional

import toml
from attr import dataclass
from loguru import logger

from pipecatcloud.exception import ConfigFileError

DEPLOY_STATUS_MAP = {
    "Unknown": "[dim]Waiting[/dim]",
    "True": "[green]Ready[/green]",
    "False": "[yellow]Creating[/yellow]",
}


@dataclass
class ScalingParams:
    min_instances: Optional[int] = 0
    max_instances: Optional[int] = 10

    def __attrs_post_init__(self):
        if self.min_instances is not None:
            if self.min_instances < 0:
                raise ValueError("min_instances must be greater than or equal to 0")

        if self.max_instances is not None:
            if self.max_instances < 1:
                raise ValueError("max_instances must be greater than 0")

            if self.min_instances is not None and self.max_instances < self.min_instances:
                raise ValueError("max_instances must be greater than or equal to min_instances")

    def to_dict(self):
        return {"min_instances": self.min_instances, "max_instances": self.max_instances}


@dataclass
class DeployConfigParams:
    agent_name: Optional[str] = None
    image: Optional[str] = None
    image_credentials: Optional[str] = None
    secret_set: Optional[str] = None
    scaling: ScalingParams = ScalingParams()

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
        expected_keys = {"agent_name", "image", "image_credentials", "secret_set", "scaling"}
        unexpected_keys = set(config_data.keys()) - expected_keys
        if unexpected_keys:
            raise ConfigFileError(f"Unexpected keys in config file: {unexpected_keys}")

        return validated_config

    except Exception as e:
        logger.debug(e)
        raise ConfigFileError(str(e))
