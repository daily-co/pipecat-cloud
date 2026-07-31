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
