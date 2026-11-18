"""``pipecat cloud`` output works when stdout's encoding can't hold every character."""

import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = str(Path(__file__).parent.parent / "src")

# Mounts the app under a parent the way the Pipecat CLI does, then runs it.
MOUNTED_RUN = """
import sys, typer
from pipecatcloud.cli.entry_point import entrypoint_cli_typer
app = typer.Typer()
app.add_typer(entrypoint_cli_typer, name="cloud")
@app.command()
def other():
    print(sys.stdout.errors)
app(sys.argv[1:])
"""


def run_cp1252(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONPATH": SRC}
    env.pop("PYTHONUTF8", None)
    return subprocess.run(
        [sys.executable, "-X", "utf8=0", "-c", MOUNTED_RUN, *args],
        env=env,
        capture_output=True,
    )


def test_help_on_cp1252_output_does_not_crash():
    result = run_cp1252("cloud", "--help")

    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")
    assert b"Pipecat Cloud CLI" in result.stdout
    # The mascot is dropped rather than printed as "???".
    assert b"??? Pipecat Cloud CLI" not in result.stdout


def test_other_commands_keep_strict_stdout():
    # The Pipecat CLI imports this module for every command; only `cloud` changes stdout.
    result = run_cp1252("other")

    assert result.returncode == 0, result.stderr.decode("cp1252", errors="replace")
    assert result.stdout.strip() == b"strict"


@pytest.mark.parametrize(
    ("encoding", "expected_errors"),
    [("cp1252", "replace"), ("utf-8", "strict"), ("utf_8", "strict"), ("UTF8", "strict")],
)
def test_replace_unencodable_stdout(monkeypatch, encoding, expected_errors):
    from pipecatcloud.cli.entry_point import _replace_unencodable_stdout

    stream = io.TextIOWrapper(io.BytesIO(), encoding=encoding, errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)

    _replace_unencodable_stdout()

    assert stream.errors == expected_errors
