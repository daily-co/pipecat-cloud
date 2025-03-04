from typing import Optional, Union


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
            error_code: Optional[Union[str, dict]] = None):

        if isinstance(error_code, dict):
            error_message = error_code.get("error", "Unknown error. Please contact support.")
            code = error_code.get("code")
        else:
            error_message = str(
                error_code) if error_code else "Unknown error. Please contact support."
            code = None

        self.message = f"{error_message} (Error code: {code})"
        self.error_code = code
        super().__init__(self.message)
