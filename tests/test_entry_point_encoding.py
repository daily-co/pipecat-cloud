"""``pipecat cloud --help`` works when the output encoding can't hold "ᓚᘏᗢ"."""

import os
import subprocess
import sys
from pathlib import Path

SRC = str(Path(__file__).parent.parent / "src")


def test_help_on_cp1252_output_does_not_crash():
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": SRC}
    env.pop("PYTHONUTF8", None)
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8=0",
            "-c",
            "from pipecatcloud.cli.entry_point import entrypoint_cli_typer as app; app(['--help'])",
        ],
        env=env,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")
    assert b"Pipecat Cloud CLI" in result.stdout
