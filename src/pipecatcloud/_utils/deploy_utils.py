import os
from typing import Optional

import toml
from attr import dataclass
from loguru import logger

from pipecatcloud.exception import ConfigFileError


@dataclass
class ScalingParams:
    min_instances: Optional[int]
    max_instances: Optional[int]

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
        return {
            "min_instances": self.min_instances,
            "max_instances": self.max_instances
        }


@dataclass
class DeployConfigParams():
    agent_name: str
    image_url: Optional[str]
    secret_set: Optional[str]
    scaling: Optional[ScalingParams]
    secrets: Optional[dict]

    def to_dict(self):
        return {
            "agent_name": self.agent_name,
            "image_url": self.image_url,
            "secret_set": self.secret_set,
            "scaling": self.scaling.to_dict() if self.scaling else None,
            "secrets": self.secrets
        }


def load_deploy_config_file() -> Optional[DeployConfigParams]:
    from pipecatcloud.cli.config import deploy_config_path

    logger.debug(f"Deploy config path: {deploy_config_path}")
    logger.debug(f"Deploy config path exists: {os.path.exists(deploy_config_path)}")

    try:
        with open(deploy_config_path, "r") as f:
            config_data = toml.load(f)

        # Extract scaling parameters if present
        scaling_data = config_data.pop('scaling', None)
        scaling_params = ScalingParams(**scaling_data) if scaling_data else None

        # Create DeployConfigParams with validated data
        validated_config = DeployConfigParams(
            agent_name=config_data['agent_name'],
            image_url=config_data.get('image_url'),
            secret_set=config_data.get('secret_set'),
            scaling=scaling_params,
            secrets=config_data.get('secrets')
        )

        # Check for unexpected keys
        expected_keys = {'agent_name', 'image_url', 'secret_set', 'scaling', 'secrets'}
        unexpected_keys = set(config_data.keys()) - expected_keys
        if unexpected_keys:
            raise ConfigFileError(f"Unexpected keys in config file: {unexpected_keys}")

        return validated_config

    except Exception as e:
        logger.debug(e)
        raise ConfigFileError(str(e))
