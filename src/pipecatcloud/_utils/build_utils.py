#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""
Build utilities for Pipecat Cloud CLI.

Provides functions for creating deterministic build contexts, uploading to S3,
and polling build status.
"""

import asyncio
import fnmatch
import gzip
import hashlib
import io
import os
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Set, Tuple

import aiohttp
from loguru import logger

# Default patterns to exclude from build context
DEFAULT_EXCLUSIONS: Set[str] = {
    # Version control
    ".git",
    ".gitignore",
    ".gitattributes",
    # Environment and secrets
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    # Python artifacts
    "__pycache__",
    "*.pyc",
    "*.pyo",
    "*.pyd",
    "*.so",
    ".Python",
    # Virtual environments
    ".venv",
    "venv",
    "ENV",
    "env",
    # Testing
    ".pytest_cache",
    ".coverage",
    "htmlcov",
    ".tox",
    ".nox",
    # Type checking / Linting
    ".mypy_cache",
    ".ruff_cache",
    # IDE
    ".vscode",
    ".idea",
    "*.swp",
    "*.swo",
    # Build artifacts
    "dist",
    "build",
    "*.egg-info",
    "*.egg",
    ".eggs",
    # Node (if present)
    "node_modules",
    # CI/CD
    ".github",
    # AI tools
    ".claude",
    ".codex",
    ".cursor",
    # Pipecat config
    "pcc-deploy.toml",
    # Jupyter
    ".ipynb_checkpoints",
    # Caches
    ".cache",
    # Misc
    ".DS_Store",
    "Thumbs.db",
    "*.log",
}


@dataclass
class BuildContext:
    """Represents a packaged build context."""

    tarball: bytes
    context_hash: str
    file_count: int
    total_size: int


class BuildStatus:
    """Build status constants."""

    PENDING = "pending"
    BUILDING = "building"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"

    TERMINAL_STATUSES = {SUCCESS, FAILED, TIMEOUT}

    @classmethod
    def is_terminal(cls, status: str) -> bool:
        return status in cls.TERMINAL_STATUSES


def _should_exclude(path: Path, exclusions: Set[str], base_path: Path) -> bool:
    """
    Check if a path should be excluded from the build context.

    Args:
        path: Path to check
        exclusions: Set of exclusion patterns
        base_path: Base directory for relative path calculation

    Returns:
        True if path should be excluded
    """
    rel_path = path.relative_to(base_path)
    path_parts = rel_path.parts

    for pattern in exclusions:
        # Check each part of the path against the pattern
        for part in path_parts:
            if fnmatch.fnmatch(part, pattern):
                return True
        # Check full relative path
        if fnmatch.fnmatch(str(rel_path), pattern):
            return True

    return False


def _normalize_pattern(pattern: str) -> str:
    """Normalize a single ``.dockerignore`` pattern for ``fnmatch`` matching.

    The matcher in ``_should_exclude`` walks the tree and compares each
    path component against every pattern with ``fnmatch.fnmatch``, which
    treats the pattern as literal text. ``.dockerignore`` (and the
    companion ``.gitignore`` grammar users pattern after) allows a few
    pieces of sugar that ``fnmatch`` does not understand: a trailing
    slash meaning "directory only", and a leading ``./`` anchoring to
    the context root. Both forms are common in the wild and routinely
    silently match nothing under a raw ``fnmatch`` implementation, so
    we strip them here to make the pattern intent actually take effect.
    """
    normalized = pattern.strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.rstrip("/")
    return normalized


def load_dockerignore(context_dir: Path) -> Optional[Set[str]]:
    """
    Load patterns from .dockerignore file if it exists.

    Args:
        context_dir: Directory containing .dockerignore

    Returns:
        Set of patterns or None if file doesn't exist
    """
    dockerignore_path = context_dir / ".dockerignore"
    if not dockerignore_path.exists():
        return None

    patterns = set()
    try:
        with open(dockerignore_path, "r") as f:
            for line in f:
                stripped = line.strip()
                # Skip comments and empty lines
                if not stripped or stripped.startswith("#"):
                    continue
                normalized = _normalize_pattern(stripped)
                if normalized:
                    patterns.add(normalized)
    except Exception as e:
        logger.warning(f"Failed to read .dockerignore: {e}")
        return None

    return patterns


def get_exclusions(context_dir: Path, extra_patterns: Optional[List[str]] = None) -> Set[str]:
    """
    Get the set of exclusion patterns to use.

    Priority:
    1. .dockerignore (if exists, takes precedence)
    2. Default exclusions + extra patterns

    Args:
        context_dir: Build context directory
        extra_patterns: Additional patterns from config

    Returns:
        Set of exclusion patterns
    """
    dockerignore = load_dockerignore(context_dir)
    if dockerignore is not None:
        # .dockerignore takes precedence
        return dockerignore

    # Use defaults + extras
    exclusions = DEFAULT_EXCLUSIONS.copy()
    if extra_patterns:
        for extra in extra_patterns:
            normalized = _normalize_pattern(extra)
            if normalized:
                exclusions.add(normalized)
    return exclusions


def create_deterministic_tarball(
    context_dir: str,
    exclusions: Set[str],
    dockerfile_path: str = "Dockerfile",
) -> BuildContext:
    """
    Create a deterministic tarball from the build context directory.

    Determinism is achieved by:
    - Sorting files alphabetically
    - Setting mtime to Unix epoch (0)
    - Normalizing permissions (uid=0, gid=0)
    - Using gzip with mtime=0

    Args:
        context_dir: Directory containing build context
        exclusions: Set of patterns to exclude
        dockerfile_path: Path to Dockerfile (verified to exist)

    Returns:
        BuildContext with tarball bytes and metadata

    Raises:
        FileNotFoundError: If context_dir or Dockerfile doesn't exist
        ValueError: If context is too large (>500MB)
    """
    base_path = Path(context_dir).resolve()

    if not base_path.exists():
        raise FileNotFoundError(f"Context directory not found: {context_dir}")

    dockerfile_full = base_path / dockerfile_path
    if not dockerfile_full.exists():
        raise FileNotFoundError(f"Dockerfile not found: {dockerfile_full}")

    # Collect all files, sorted alphabetically
    files_to_add: List[Tuple[Path, str]] = []

    for root, dirs, files in os.walk(base_path):
        root_path = Path(root)

        # Filter directories in-place to prevent descending into excluded dirs
        dirs[:] = [
            d for d in sorted(dirs) if not _should_exclude(root_path / d, exclusions, base_path)
        ]

        for filename in sorted(files):
            file_path = root_path / filename
            if not _should_exclude(file_path, exclusions, base_path):
                rel_path = file_path.relative_to(base_path)
                files_to_add.append((file_path, str(rel_path)))

    # Sort by relative path for determinism
    files_to_add.sort(key=lambda x: x[1])

    # Create tarball in memory
    tar_buffer = io.BytesIO()
    total_size = 0

    with tarfile.open(fileobj=tar_buffer, mode="w") as tar:
        for file_path, arc_name in files_to_add:
            # Create TarInfo with normalized attributes
            info = tarfile.TarInfo(name=arc_name)
            info.size = file_path.stat().st_size
            info.mtime = 0  # Unix epoch for determinism
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            # Normalize permissions: 0o644 for files, 0o755 for executables
            if os.access(file_path, os.X_OK):
                info.mode = 0o755
            else:
                info.mode = 0o644

            with open(file_path, "rb") as f:
                tar.addfile(info, f)

            total_size += info.size

    # Check size limit (500MB)
    MAX_CONTEXT_SIZE = 500 * 1024 * 1024
    if total_size > MAX_CONTEXT_SIZE:
        raise ValueError(
            f"Build context too large: {total_size / (1024 * 1024):.1f}MB "
            f"(max {MAX_CONTEXT_SIZE / (1024 * 1024):.0f}MB)"
        )

    # Compress with gzip (mtime=0 for determinism)
    tar_bytes = tar_buffer.getvalue()
    gzip_buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=gzip_buffer, mode="wb", mtime=0) as gz:
        gz.write(tar_bytes)

    tarball = gzip_buffer.getvalue()

    # Compute MD5 hash (first 16 hex chars to match server-side)
    context_hash = hashlib.md5(tarball).hexdigest()[:16]

    logger.debug(
        f"Created tarball: {len(files_to_add)} files, "
        f"{len(tarball)} bytes compressed, hash={context_hash}"
    )

    return BuildContext(
        tarball=tarball,
        context_hash=context_hash,
        file_count=len(files_to_add),
        total_size=total_size,
    )


async def upload_to_s3(
    tarball: bytes,
    upload_url: str,
    upload_fields: dict,
) -> bool:
    """
    Upload tarball to S3 using presigned POST.

    Args:
        tarball: Compressed tarball bytes
        upload_url: Presigned S3 URL
        upload_fields: Fields for multipart form upload

    Returns:
        True on success, False on failure
    """
    # Create multipart form data
    data = aiohttp.FormData()

    # Add all presigned fields first (order matters for S3)
    for key, value in upload_fields.items():
        data.add_field(key, value)

    # S3 presigned POST requires Content-Type as a form field (not just multipart header)
    # Add it if not already in upload_fields
    if "Content-Type" not in upload_fields:
        data.add_field("Content-Type", "application/gzip")

    # Add file last (must be named "file" for S3 presigned POST)
    data.add_field("file", tarball, filename="context.tar.gz", content_type="application/gzip")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(upload_url, data=data) as response:
                if response.status in (200, 201, 204):
                    logger.debug(f"Upload successful: {response.status}")
                    return True
                else:
                    body = await response.text()
                    logger.error(f"Upload failed: {response.status} - {body}")
                    return False
    except Exception as e:
        logger.error(f"Upload error: {e}")
        return False


async def poll_build_status(
    build_id: str,
    org: str,
    api_client,
    poll_interval: float = 3.0,
    max_duration: float = 600.0,  # 10 minutes
    status_callback: Optional[Callable[[dict], None]] = None,
) -> Tuple[bool, dict]:
    """
    Poll build status until completion or timeout.

    Args:
        build_id: Build ID to poll
        org: Organization ID
        api_client: API client instance
        poll_interval: Seconds between polls
        max_duration: Maximum wait time in seconds
        status_callback: Optional callback(build_data) on each poll

    Returns:
        Tuple of (success: bool, final_build_data: dict)
    """
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed > max_duration:
            return False, {"status": "timeout", "error": "Polling timeout exceeded"}

        data, error = await api_client.build_get(org=org, build_id=build_id)

        if error:
            return False, {"status": "error", "error": str(error)}

        if not data:
            return False, {"status": "error", "error": "Build not found"}

        build = data.get("build", data)
        status = build.get("status", "unknown")

        if status_callback:
            status_callback(build)

        if BuildStatus.is_terminal(status):
            return status == BuildStatus.SUCCESS, build

        await asyncio.sleep(poll_interval)


def format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
