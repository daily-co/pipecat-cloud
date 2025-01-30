#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import functools

from pipecatcloud._utils.console_utils import console
from pipecatcloud.cli import PIPECAT_CLI_NAME
from pipecatcloud.cli.config import config


def requires_login(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        org = config.get("org")
        token = config.get("token")
        if org is None or token is None:
            console.error(
                f"You are not logged in. Please run `{PIPECAT_CLI_NAME} auth login` first.",
            )
            return
        return func(*args, **kwargs)

    return wrapper
