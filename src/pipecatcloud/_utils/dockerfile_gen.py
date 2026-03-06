#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Dockerfile generation utilities for Pipecat Cloud CLI.

Generates Dockerfiles optimized for Pipecat Cloud deployment.
"""

from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger


class ProjectType(str, Enum):
    """Supported Python project types."""

    UV = "uv"
    PIP = "pip"
    POETRY = "poetry"
    UNKNOWN = "unknown"


def detect_project_type(project_dir: str = ".") -> ProjectType:
    """
    Detect the Python project type based on files present.

    Detection priority:
    1. uv: pyproject.toml + uv.lock
    2. poetry: pyproject.toml with [tool.poetry] or poetry.lock
    3. pip: requirements.txt
    4. unknown: none of the above

    Args:
        project_dir: Directory to analyze

    Returns:
        Detected ProjectType
    """
    path = Path(project_dir)

    has_pyproject = (path / "pyproject.toml").exists()
    has_uv_lock = (path / "uv.lock").exists()
    has_poetry_lock = (path / "poetry.lock").exists()
    has_requirements = (path / "requirements.txt").exists()

    # Check for uv first (most specific)
    if has_uv_lock:
        logger.debug("Detected uv project (uv.lock found)")
        return ProjectType.UV

    # Check pyproject.toml content for poetry markers
    if has_pyproject:
        try:
            content = (path / "pyproject.toml").read_text()
            if "[tool.poetry]" in content:
                logger.debug("Detected poetry project ([tool.poetry] in pyproject.toml)")
                return ProjectType.POETRY
            if "[tool.uv]" in content:
                logger.debug("Detected uv project ([tool.uv] in pyproject.toml)")
                return ProjectType.UV
        except Exception as e:
            logger.warning(f"Failed to read pyproject.toml: {e}")

    # Check for poetry.lock
    if has_poetry_lock:
        logger.debug("Detected poetry project (poetry.lock found)")
        return ProjectType.POETRY

    # Check for requirements.txt
    if has_requirements:
        logger.debug("Detected pip project (requirements.txt found)")
        return ProjectType.PIP

    logger.debug("Could not detect project type")
    return ProjectType.UNKNOWN


def detect_entrypoint(project_dir: str = ".") -> Optional[str]:
    """
    Detect the likely entrypoint for the project.

    Checks common entrypoint filenames in order of preference.

    Args:
        project_dir: Directory to analyze

    Returns:
        Detected entrypoint filename or None
    """
    path = Path(project_dir)

    # Common entrypoint files in order of preference
    common_entrypoints = ["bot.py", "main.py", "app.py", "server.py", "run.py", "agent.py"]

    # Check root directory
    for entrypoint in common_entrypoints:
        if (path / entrypoint).exists():
            logger.debug(f"Detected entrypoint: {entrypoint}")
            return entrypoint

    # Check src/ directory structure
    src_path = path / "src"
    if src_path.exists() and src_path.is_dir():
        for entrypoint in common_entrypoints:
            if (src_path / entrypoint).exists():
                result = f"src/{entrypoint}"
                logger.debug(f"Detected entrypoint in src/: {result}")
                return result

    logger.debug("Could not detect entrypoint")
    return None


# Dockerfile templates

UV_DOCKERFILE_TEMPLATE = '''FROM dailyco/pipecat-base:latest

# Enable bytecode compilation
ENV UV_COMPILE_BYTECODE=1

# Copy from the cache instead of linking since it's a mounted volume
ENV UV_LINK_MODE=copy

# Install the project's dependencies using the lockfile and settings
RUN --mount=type=cache,target=/root/.cache/uv \\
    --mount=type=bind,source=uv.lock,target=uv.lock \\
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \\
    uv sync --locked --no-install-project --no-dev

# Copy the application code
COPY ./{entrypoint} {entrypoint}
'''

PIP_DOCKERFILE_TEMPLATE = '''FROM dailyco/pipecat-base:latest

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN --mount=type=cache,target=/root/.cache/pip \\
    pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY ./{entrypoint} {entrypoint}
'''

POETRY_DOCKERFILE_TEMPLATE = '''FROM dailyco/pipecat-base:latest

# Install poetry
RUN pip install poetry

# Configure poetry to not create a virtualenv (use system Python)
ENV POETRY_VIRTUALENVS_CREATE=false

# Copy dependency files first for better caching
COPY pyproject.toml poetry.lock* ./

# Install dependencies (no dev, no interaction)
RUN poetry install --no-dev --no-interaction --no-ansi

# Copy the application code
COPY ./{entrypoint} {entrypoint}
'''


def generate_dockerfile(project_type: ProjectType, entrypoint: str) -> str:
    """
    Generate a Dockerfile for the given project type.

    Args:
        project_type: Type of Python project
        entrypoint: Python file to run (e.g., "bot.py")

    Returns:
        Dockerfile content as string

    Raises:
        ValueError: If project type is unsupported
    """
    templates = {
        ProjectType.UV: UV_DOCKERFILE_TEMPLATE,
        ProjectType.PIP: PIP_DOCKERFILE_TEMPLATE,
        ProjectType.POETRY: POETRY_DOCKERFILE_TEMPLATE,
    }

    if project_type not in templates:
        raise ValueError(
            f"Cannot generate Dockerfile for project type: {project_type}. "
            f"Supported types: {', '.join(t.value for t in templates.keys())}"
        )

    return templates[project_type].format(entrypoint=entrypoint)


def write_dockerfile(
    content: str,
    output_path: str = "Dockerfile",
    overwrite: bool = False,
) -> bool:
    """
    Write Dockerfile content to file.

    Args:
        content: Dockerfile content
        output_path: Path to write to
        overwrite: Whether to overwrite existing file

    Returns:
        True if written successfully

    Raises:
        FileExistsError: If file exists and overwrite is False
    """
    path = Path(output_path)

    if path.exists() and not overwrite:
        raise FileExistsError(f"Dockerfile already exists at {output_path}")

    path.write_text(content)
    logger.debug(f"Wrote Dockerfile to {output_path}")
    return True
