#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

import sys

PIPECAT_CLI_NAME = "pipecat cloud"
PIPECAT_CREDENTIALS_PATH = "~/.config/pipecatcloud/pipecatcloud.toml"
PIPECAT_DEPLOY_CONFIG_PATH = "pcc-deploy.toml"


def _mascot() -> str:
    """Return "ᓚᘏᗢ " for titles, or "" when stdout can't encode it (e.g. cp1252)."""
    mascot = "ᓚᘏᗢ"
    try:
        mascot.encode(getattr(sys.stdout, "encoding", None) or "utf-8")
    except (UnicodeEncodeError, LookupError):
        return ""
    return f"{mascot} "


MASCOT = _mascot()

PANEL_TITLE_ERROR = f"{MASCOT}Error"
PANEL_TITLE_SUCCESS = f"{MASCOT}Success"
PANEL_TITLE_NOT_IMPLEMENTED = f"{MASCOT}Not yet implemented"
