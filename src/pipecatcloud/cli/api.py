#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from pipecatcloud.api import _API
from pipecatcloud.cli.config import config

API = _API(config.get("token"), is_cli=True)
