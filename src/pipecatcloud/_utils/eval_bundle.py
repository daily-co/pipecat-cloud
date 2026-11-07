#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Collect the files of an eval run from a scenario file.

A scenario file can pull in other YAML files with ``!include <path>``. A run
carries the scenario file and every file it includes, as UTF-8 text, keyed by
its path relative to a root directory. Nothing else is sent: not the rest of the
directory, and not audio or image files, which hosted runs do not use.

Every include is resolved the way the service resolves it, relative to the file
that names it, and must stay inside the root. A file that cannot be sent is
refused here, before any request, with a message that names it.
"""

import fnmatch
import posixpath
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

# What the service accepts: the files' contents as UTF-8, in all.
MAX_BUNDLE_BYTES = 256 * 1024
# The service refuses includes nested deeper than this.
MAX_INCLUDE_DEPTH = 8
MAX_FILES = 200

# Names that suggest credentials. A dotfile (.env and the like) is refused
# whatever its name; these catch the rest.
_SECRET_NAME_PATTERNS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_dsa*",
    "id_ecdsa*",
    "id_ed25519*",
    "*secret*",
    "*credential*",
    "*password*",
    "*private_key*",
    "*private-key*",
)


class BundleError(Exception):
    """The scenario's files can't be sent. The message says why and names the file."""


@dataclass
class Bundle:
    """What a run sends: the files, the scenario file's key, and the scenarios it holds."""

    files: dict[str, str] = field(default_factory=dict)
    scenario_path: str = ""
    scenario_names: list[str] = field(default_factory=list)

    @property
    def size(self) -> int:
        return sum(len(text.encode("utf-8")) for text in self.files.values())


def _refused_name(key: str) -> str | None:
    """Why a path relative to the root can't be sent, judged by its names alone."""
    for part in PurePosixPath(key).parts:
        if part.startswith("."):
            return "is or sits in a hidden file or folder"
    name = PurePosixPath(key).name.lower()
    for pattern in _SECRET_NAME_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return "looks like it holds secrets"
    return None


def _normalize(path: str) -> str | None:
    """A relative POSIX path with `.` and `..` resolved, or None if it leaves the root."""
    parts: list[str] = []
    for part in PurePosixPath(path).parts:
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
        else:
            parts.append(part)
    return "/".join(parts)


class _Collector:
    def __init__(self, root: Path):
        self.root = root
        self.real_root = root.resolve()
        self.bundle = Bundle()
        self._bytes = 0

    def read(self, key: str, via: str | None = None) -> str:
        """Read one file of the bundle by its key, applying every rule."""
        where = f"{key!r}" if via is None else f"{key!r} (included by {via!r})"
        reason = _refused_name(key)
        if reason:
            raise BundleError(f"{where} {reason}, so it isn't sent.")
        target = self.root / key
        try:
            resolved = target.resolve(strict=True)
        except (FileNotFoundError, NotADirectoryError):
            raise BundleError(f"{where} doesn't exist.") from None
        except (OSError, RuntimeError):
            raise BundleError(f"{where} can't be read (a symlink loop?).") from None
        if not resolved.is_relative_to(self.real_root):
            raise BundleError(
                f"{where} is a symlink that leaves the root directory, so it isn't followed."
            )
        if not resolved.is_file():
            raise BundleError(f"{where} isn't a regular file.")
        try:
            data = resolved.read_bytes()
        except OSError as e:
            raise BundleError(f"{where} can't be read: {e.strerror or type(e).__name__}.") from None
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            raise BundleError(
                f"{where} isn't UTF-8 text. Only text files are sent with a run."
            ) from None
        if "\x00" in text:
            raise BundleError(f"{where} contains a NUL character, so it can't be sent.")
        return text

    def add(self, key: str, via: str | None = None, seen: tuple[str, ...] = ()) -> Any:
        """Add a file and, through its includes, the files it names. Returns its parsed YAML."""
        if key in seen:
            raise BundleError(f"{key!r} includes itself.")
        if len(seen) >= MAX_INCLUDE_DEPTH:
            raise BundleError(f"{key!r}: includes nest deeper than {MAX_INCLUDE_DEPTH}.")
        text = self.bundle.files.get(key)
        if text is None:
            text = self.read(key, via)
            if len(self.bundle.files) >= MAX_FILES:
                raise BundleError(f"The scenario includes more than {MAX_FILES} files.")
            self._bytes += len(text.encode("utf-8"))
            if self._bytes > MAX_BUNDLE_BYTES:
                raise BundleError(
                    f"The scenario's files come to more than {MAX_BUNDLE_BYTES // 1024} KiB, "
                    "which is the most a run can carry. Trim the files it includes."
                )
            self.bundle.files[key] = text
        return self._parse(key, text, seen)

    def _parse(self, key: str, text: str, seen: tuple[str, ...]) -> Any:
        collector = self

        class _Loader(yaml.SafeLoader):
            pass

        def include(loader: yaml.SafeLoader, node: yaml.Node) -> Any:
            if not isinstance(node, yaml.ScalarNode):
                raise BundleError(f"{key!r}: !include expects a file path.")
            target = str(loader.construct_scalar(node))
            if PurePosixPath(target).is_absolute() or posixpath.isabs(target):
                raise BundleError(f"{key!r}: !include {target!r} is an absolute path.")
            resolved = _normalize(str(PurePosixPath(key).parent / target))
            if resolved is None or resolved == "":
                raise BundleError(f"{key!r}: !include {target!r} leaves the root directory.")
            return collector.add(resolved, via=key, seen=(*seen, key))

        def other_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> None:
            # The service decides what a tag means. Here only includes matter.
            return None

        _Loader.add_constructor("!include", include)
        _Loader.add_multi_constructor("!", other_tag)
        try:
            return yaml.load(text, _Loader)
        except yaml.YAMLError as e:
            problem = getattr(e, "problem", None)
            detail = f": {problem}" if problem else ""
            raise BundleError(f"{key!r} isn't valid YAML{detail}.") from None


def _scenario_names(data: Any, key: str) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise BundleError(f"{key!r} has no 'scenarios:' list, so it isn't a scenario file.")
    return [
        s["name"]
        for s in data["scenarios"]
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    ]


def build_bundle(scenario_file: Path, root: Path | None = None) -> Bundle:
    """Collect a scenario file and the files it includes.

    Args:
        scenario_file: The scenario file.
        root: The directory the files are keyed relative to and may not leave.
            Defaults to the scenario file's directory.

    Raises:
        BundleError: If a file can't be sent, or the bundle is too large.
    """
    scenario_file = Path(scenario_file)
    if not scenario_file.exists():
        raise BundleError(f"{str(scenario_file)!r} doesn't exist.")
    root = Path(root) if root is not None else scenario_file.parent
    if not root.is_dir():
        raise BundleError(f"The root {str(root)!r} isn't a directory.")

    root_abs = Path(posixpath.normpath(root.absolute().as_posix()))
    file_abs = Path(posixpath.normpath(scenario_file.absolute().as_posix()))
    if not file_abs.is_relative_to(root_abs):
        raise BundleError(
            f"The scenario file {str(scenario_file)!r} isn't inside the root {str(root)!r}."
        )
    key = file_abs.relative_to(root_abs).as_posix()

    collector = _Collector(root_abs)
    data = collector.add(key)
    bundle = collector.bundle
    bundle.scenario_path = key
    bundle.scenario_names = _scenario_names(data, key)
    return bundle
