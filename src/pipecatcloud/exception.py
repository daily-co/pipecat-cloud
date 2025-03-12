#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

from typing import Optional, Union

from pipecatcloud.cli import PIPECAT_CLI_NAME


class Error(Exception):
    """Base class for exceptions in this module."""

    pass


class ConfigFileError(Exception):
    """Error when config file is malformed"""

    pass


class AuthError(Error):
    """Exception raised for authentication errors."""

    def __init__(
        self,
        message: str = f"Unauthorized / token expired. Please run `{PIPECAT_CLI_NAME} auth login` to login again.",
    ):
        self.message = message
        super().__init__(self.message)


class InvalidError(Error):
    """Raised when user does something invalid."""


class ConfigError(Error):
    """Raised when config is unable to be stored or updated"""

    def __init__(self, message: str = "Failed to update configuration"):
        self.message = message
        super().__init__(self.message)


class AgentNotHealthyError(Error):
    """Raised when agent is not healthy and cannot be started."""

    def __init__(
        self,
        message: str = "Agent deployment is not in a ready state and cannot be started.",
        error_code: Optional[str] = None,
    ):
        self.message = f"{message} (Error code: {error_code})"
        self.error_code = error_code
        super().__init__(self.message)


class AgentStartError(Error):
    """Raised when agent start request fails."""

    def __init__(self, error: Optional[Union[str, dict]] = None):
        if isinstance(error, dict):
            error_message = error.get("error", "Unknown error. Please contact support.")
            code = error.get("code")
        else:
            error_message = str(error) if error else "Unknown error. Please contact support."
            code = None

        self.message = f"{code} - {error_message}"
        self.error_code = code
        super().__init__(self.message)
