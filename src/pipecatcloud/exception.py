class Error(Exception):
    """Base class for exceptions in this module."""

    pass


class AuthError(Error):
    """Exception raised for authentication errors."""

    def __init__(
            self,
            message: str = "Unauthorized / token expired. Please run `pipecat auth login` to login again."):
        self.message = message
        super().__init__(self.message)


class InvalidError(Error):
    """Raised when user does something invalid."""


class ConfigError(Error):
    """Raised when user does something invalid."""


class _CliUserExecutionError(Exception):
    """mdmd:hidden
    Private wrapper for exceptions during when importing or running stubs from the CLI.

    This intentionally does not inherit from `modal.exception.Error` because it
    is a private type that should never bubble up to users. Exceptions raised in
    the CLI at this stage will have tracebacks printed.
    """

    def __init__(self, user_source: str):
        # `user_source` should be the filepath for the user code that is the source of the exception.
        # This is used by our exception handler to show the traceback starting from that point.
        self.user_source = user_source
