#
# Copyright (c) 2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Warnings about the base image an agent's Dockerfile builds from.

``dailyco/pipecat-base:latest`` names no Python, so it carries Python 3.12, and
it stops building on ``LATEST_RETIRES``. A Dockerfile that takes it is warned
before its build. Version tags without ``-py3.X``, such as ``0.3.0``, are
deprecated too, but each is fixed to one image and stays published, so a
Dockerfile pinned to one keeps building and is not warned.
"""

import re
from pathlib import Path

from rich.markup import escape

from pipecatcloud._utils.console_utils import console

LATEST_RETIRES = "2027-04-05"
LATEST_PYTHON = "3.12"

# Docker Hub's own names for the repository, which Docker resolves alike.
_BASE_IMAGE = re.compile(
    r"(?:(?:docker\.io|index\.docker\.io|registry-1\.docker\.io)/)?dailyco/pipecat-base"
    r"(?::(?P<tag>[\w][\w.-]*))?(?P<digest>@\S+)?"
)
_FROM = re.compile(r"\s*FROM\s+(?:--\S+\s+)*(?P<image>\S+)", re.IGNORECASE)


def _instructions(text: str):
    """Each instruction with the line it starts on, continuation lines joined.

    Comment lines are dropped, inside a continuation too, as Docker drops them.
    """
    start, parts = None, []
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("#"):
            continue
        if start is None:
            start = number
        if line.rstrip().endswith("\\"):
            parts.append(line.rstrip()[:-1])
            continue
        parts.append(line)
        yield start, " ".join(parts)
        start, parts = None, []
    if parts:
        yield start, " ".join(parts)


def latest_base_images(text: str) -> list[tuple[int, str]]:
    """The FROM instructions that take ``dailyco/pipecat-base:latest``.

    Returns each one's line number and image, as written. A FROM with no tag
    takes ``latest`` too. One pinned by digest is fixed, and one built from a
    variable cannot be read here, so neither is returned.
    """
    found = []
    for number, instruction in _instructions(text):
        match = _FROM.match(instruction)
        if not match:
            continue
        image = match["image"]
        base = _BASE_IMAGE.fullmatch(image)
        if base and not base["digest"] and base["tag"] in (None, "latest"):
            found.append((number, image))
    return found


def warn_latest_base_image(dockerfile: Path) -> None:
    """Warn for each FROM in ``dockerfile`` that takes the base image's ``latest``.

    An unreadable Dockerfile is left to the build to report.
    """
    try:
        text = dockerfile.read_text(errors="replace")
    except OSError:
        return
    for number, image in latest_base_images(text):
        console.print(
            f"[yellow]Warning: {escape(dockerfile.name)} line {number} builds FROM "
            f"{escape(image)}, which stops building on {LATEST_RETIRES}. It names no "
            f"Python, and runs Python {LATEST_PYTHON}: to keep it, use "
            f"dailyco/pipecat-base:latest-py{LATEST_PYTHON}.[/yellow]"
        )
