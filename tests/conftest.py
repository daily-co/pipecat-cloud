"""
Global test fixtures.

Sets PIPECAT_CONFIG_PATH to an isolated temp file so tests never
read or corrupt the real user credentials. Provides a fake token
and org so @requires_login passes through.
"""

import os
import tempfile

# Must be set before any pipecatcloud module is imported, since
# config.py reads the credentials file at module load time.
_tmp = tempfile.NamedTemporaryFile(suffix=".toml", delete=False, mode="w")
_tmp.write('token = "test-token"\norg = "test-org"\n')
_tmp.close()
os.environ["PIPECAT_CONFIG_PATH"] = _tmp.name

# Rich decides is_terminal from the environment before ever asking the file:
# TTY_COMPATIBLE and FORCE_COLOR (any non-empty value) both make a console
# writing to a plain buffer claim to be a terminal. Developer shells commonly
# export FORCE_COLOR, which flipped every auto-detection console into rich
# mode locally and failed the plain-mode tests that pass in CI. The console
# singleton holds a live reference to os.environ, so scrubbing here (before
# any pipecatcloud import, like the config path above) makes detection depend
# only on the file handed to the console — for the singleton and for every
# console a test constructs.
os.environ.pop("FORCE_COLOR", None)
os.environ.pop("TTY_COMPATIBLE", None)

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_console_output_mode():
    """The output mode mutates the console singleton (e.g. `spend-limit show
    --json` or the global --output callback); restore it so one test's mode
    can't leak into the next. Save the private `_file` (normally None =
    resolve sys.stdout dynamically), not the `file` property — pinning the
    resolved object back would bypass CliRunner capture in later tests."""
    from pipecatcloud._utils.console_utils import console

    explicit = console._explicit_output_mode
    file = console._file
    yield
    console._explicit_output_mode = explicit
    console._file = file
