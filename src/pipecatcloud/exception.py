from typing import Optional

from pipecatcloud.errors import ERROR_CODES


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
            message: str = "Unauthorized / token expired. Please run `pcc auth login` to login again."):
        self.message = message
        super().__init__(self.message)


class InvalidError(Error):
    """Raised when user does something invalid."""


class ConfigError(Error):
    """Raised when config is unable to be stored or updated"""


class AgentNotHealthyError(Error):
    """Raised when agent is not healthy and cannot be started."""

    def __init__(
            self,
            message: str = "Agent deployment is not in a ready state and cannot be started.",
            error_code: Optional[str] = None):
        self.message = f"{message} (Error code: {error_code})"
        self.error_code = error_code
        super().__init__(self.message)


class AgentStartError(Error):
    """Raised when agent start request fails."""

    def __init__(
            self,
            message: str = "Agent start request failed.",
            error_code: Optional[str] = None):

        error_message = message if not error_code else ERROR_CODES.get(
            error_code, message)

        self.message = f"{error_message} (Error code: {error_code})"
        self.error_code = error_code
        super().__init__(self.message)
