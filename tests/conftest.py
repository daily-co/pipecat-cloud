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
