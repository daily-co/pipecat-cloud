import os
import sys

from loguru import logger

PIPECAT_CLI_NAME = "pcc"
PIPECAT_CREDENTIALS_PATH = "~/.pipecatcloud.toml"
PIPECAT_DEPLOY_CONFIG_PATH = ".pcc-deploy.toml"


logger.remove()
logger.add(sys.stderr, level=str(os.getenv("PCC_LOG_LEVEL", "INFO")))
