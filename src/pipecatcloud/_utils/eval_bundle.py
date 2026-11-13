#
# Copyright (c) 2025, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Collect the files of an eval run from a scenario file, or a bundle's from a directory.

A scenario file can pull in other YAML files with ``!include <path>``. A run
carries the scenario file and every file it includes, as UTF-8 text, keyed by
its path relative to a root directory. Nothing else is sent: not the rest of the
directory, and not audio or image files, which hosted runs do not use.

Every include is resolved the way the service resolves it, relative to the file
that names it, and must stay inside the root. A file that cannot be sent is
refused here, before any request, with a message that names it.

The service expands an include each time it is named, and bounds the work: how
many files it loads and how many characters it parses. Those bounds are applied
here too, so a bundle the service would refuse is refused before it is sent.
Each file is read and parsed once.
"""

import fnmatch
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

# What the service accepts: the files' contents as UTF-8, in all.
MAX_BUNDLE_BYTES = 256 * 1024
# The service refuses includes nested deeper than this.
MAX_INCLUDE_DEPTH = 8
MAX_FILES = 200
# The service's bounds on expanding includes: an include is loaded again each
# time it is named, and counts toward both limits each time.
MAX_FILE_PARSES = 200
MAX_PARSED_CHARS = MAX_BUNDLE_BYTES

# Names that suggest credentials: key and certificate files, and env files. A
# dotfile (.env and the like) is refused whatever its name. Names that merely
# contain a word such as "secret" are fine: they are far more often a scenario
# about secrets than a secret.
_SECRET_NAME_PATTERNS = (
    "*.env",
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
)


class BundleError(Exception):
    """The scenario's files can't be sent. The message says why and names the file."""


@dataclass
class Bundle:
    """What a run sends: the files, the scenario file's key, and the scenarios it holds."""

    files: dict[str, str] = field(default_factory=dict)
    scenario_path: str = ""
    scenario_names: list[str] = field(default_factory=list)
    # The scenario file as parsed, its includes expanded the way the service
    # expands them: what describes each scenario before it runs.
    data: Any = None

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


@dataclass
class _Entry:
    """A file that has been read and parsed once."""

    text: str
    includes: list[str] = field(default_factory=list)
    data: Any = None


class _Collector:
    def __init__(self, root: Path, too_large: str | None = None):
        self.root = root
        self.real_root = root.resolve()
        # What to say when the files come to too much, for a bundle rather than a run.
        self.too_large = too_large
        self.bundle = Bundle()
        self._entries: dict[str, _Entry] = {}
        self._loading: list[str] = []
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
        # The same rules for what the name points at: `user.yaml -> .env` is
        # refused whatever the link is called.
        reason = _refused_name(resolved.relative_to(self.real_root).as_posix())
        if reason:
            target_name = resolved.relative_to(self.real_root).as_posix()
            raise BundleError(f"{where} is a link to {target_name!r}, which {reason}.")
        if not resolved.is_file():
            raise BundleError(f"{where} isn't a regular file.")
        try:
            size = resolved.stat().st_size
            if size > MAX_BUNDLE_BYTES - self._bytes:
                raise BundleError(self.too_large or self._too_large())
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

    @staticmethod
    def _too_large() -> str:
        return (
            f"The scenario's files come to more than {MAX_BUNDLE_BYTES // 1024} KiB, "
            "which is the most a run can carry. Trim the files it includes."
        )

    def load(self, key: str, via: str | None = None) -> _Entry:
        """Read and parse a file the first time it is named; later, return what was parsed."""
        if key in self._loading:
            raise BundleError(f"{key!r} includes itself.")
        entry = self._entries.get(key)
        if entry is not None:
            return entry
        if len(self._loading) >= MAX_INCLUDE_DEPTH:
            raise BundleError(f"{key!r}: includes nest deeper than {MAX_INCLUDE_DEPTH}.")
        if len(self._entries) >= MAX_FILES:
            raise BundleError(f"The scenario includes more than {MAX_FILES} files.")
        text = self.read(key, via)
        self._bytes += len(text.encode("utf-8"))
        if self._bytes > MAX_BUNDLE_BYTES:
            raise BundleError(self._too_large())
        entry = _Entry(text)
        self._entries[key] = entry
        self.bundle.files[key] = text
        self._loading.append(key)
        try:
            entry.data = self._parse(key, entry)
        finally:
            self._loading.pop()
        return entry

    def _parse(self, key: str, entry: _Entry) -> Any:
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
            entry.includes.append(resolved)
            return collector.load(resolved, via=key).data

        def other_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> None:
            # The service decides what a tag means. Here only includes matter.
            return None

        _Loader.add_constructor("!include", include)
        _Loader.add_multi_constructor("!", other_tag)
        try:
            return yaml.load(entry.text, _Loader)
        except yaml.YAMLError as e:
            problem = getattr(e, "problem", None)
            detail = f": {problem}" if problem else ""
            raise BundleError(f"{key!r} isn't valid YAML{detail}.") from None

    def check_expansion(self, key: str) -> None:
        """Apply the service's bounds on expanding includes, without parsing anything again.

        The service loads a file once for each time it is named, so a file named
        by many files, or by one many times, costs that many loads.
        """
        parses = 0
        chars = 0

        def visit(name: str, ancestors: tuple[str, ...]) -> None:
            nonlocal parses, chars
            if name in ancestors:
                raise BundleError(f"{name!r} includes itself.")
            if len(ancestors) >= MAX_INCLUDE_DEPTH:
                raise BundleError(f"{name!r}: includes nest deeper than {MAX_INCLUDE_DEPTH}.")
            entry = self._entries[name]
            parses += 1
            chars += len(entry.text)
            if parses > MAX_FILE_PARSES or chars > MAX_PARSED_CHARS:
                raise BundleError(
                    f"{key!r}: includes expand past {MAX_FILE_PARSES} file loads or "
                    f"{MAX_PARSED_CHARS} characters. An include counts each time it is "
                    "named, so a file included from many places adds up. Include fewer "
                    "or smaller files."
                )
            for child in entry.includes:
                visit(child, (*ancestors, name))

        visit(key, ())


def _scenario_names(data: Any, key: str) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), list):
        raise BundleError(f"{key!r} has no 'scenarios:' list, so it isn't a scenario file.")
    return [
        s["name"]
        for s in data["scenarios"]
        if isinstance(s, dict) and isinstance(s.get("name"), str)
    ]


# What a stored bundle holds: YAML files and, for suites' bodies, JSON.
BUNDLE_SUFFIXES = (".yaml", ".yml", ".json")
# The most files a stored bundle may hold, and the longest path, as the service has them.
MAX_STORED_FILES = 2000
MAX_PATH_LENGTH = 1024

# A directory is walked whole, so more is left out than of a file a scenario
# names: YAML and JSON names that credentials are commonly kept under, which
# the patterns above (key files, env files) don't reach.
_DIRECTORY_SECRET_NAME_PATTERNS = (
    "*credential*",
    "*secret*",
    "*service-account*",
    "*service_account*",
    "*serviceaccount*",
    "*kubeconfig*",
    "*token*.json",
    "*.sops.*",
)
# Content no eval file holds: a private key, or a Kubernetes Secret.
_PRIVATE_KEY_MARKER = "PRIVATE KEY-----"
# `kind: Secret` in YAML, its value quoted or not, at any depth or as a list item,
# with or without a comment after it; or `"kind": "Secret"` in JSON.
_KUBERNETES_SECRET = re.compile(
    r"""(^\s*(?:-\s+)?kind:\s*["']?Secret["']?\s*(?:#.*)?$)|(["']kind["']\s*:\s*["']Secret["'])""",
    re.MULTILINE,
)


def _credential_name(name: str) -> bool:
    """Whether a file or folder is named the way credentials are commonly kept."""
    return any(fnmatch.fnmatch(name.lower(), p) for p in _DIRECTORY_SECRET_NAME_PATTERNS)


def _secret_content(text: str) -> str | None:
    """Why a file's text looks like a credential, or None."""
    if _PRIVATE_KEY_MARKER in text:
        return "holds a private key"
    if _KUBERNETES_SECRET.search(text):
        return "is a Kubernetes Secret"
    return None


@dataclass
class DirectoryBundle:
    """A directory's files to publish as a bundle, and the ones left out."""

    files: dict[str, str] = field(default_factory=dict)
    # Paths left out, each with why: hidden, unreadable, or named or holding
    # what looks like a credential.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    # How many files were left out for not being YAML or JSON.
    other_files: int = 0

    @property
    def size(self) -> int:
        return sum(len(text.encode("utf-8")) for text in self.files.values())


def collect_directory(directory: Path) -> DirectoryBundle:
    """Collect every YAML and JSON file under a directory, keyed by its path in it.

    The files are read as a run's are: UTF-8 text, within the size limit, and
    never through a symlink that leaves the directory. Hidden files and
    folders, folders that can't be read, and files named like or holding
    credentials are left out and listed; a linked folder is not followed.
    Nothing left out is sent.

    Raises:
        BundleError: If a file can't be sent, or the files come to too much.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise BundleError(f"{str(directory)!r} isn't a directory.")
    root_abs = Path(posixpath.normpath(directory.absolute().as_posix()))
    too_large = (
        f"The files come to more than {MAX_BUNDLE_BYTES // 1024} KiB, which is the most a "
        "bundle can hold. Publish a smaller directory."
    )
    collector = _Collector(root_abs, too_large)
    found = DirectoryBundle()
    keys: list[str] = []

    def unreadable(error: OSError) -> None:
        where = Path(str(error.filename or root_abs))
        key = where.relative_to(root_abs).as_posix() if where.is_relative_to(root_abs) else "."
        found.skipped.append((f"{key}/", "can't be read"))

    for current, dirs, names in os.walk(root_abs, onerror=unreadable):
        base = Path(current).relative_to(root_abs)
        kept_dirs = []
        for name in sorted(dirs):
            key = (base / name).as_posix()
            if name.startswith("."):
                found.skipped.append((f"{key}/", "is a hidden folder"))
            elif _credential_name(name):
                found.skipped.append((f"{key}/", "is named like it holds credentials"))
            elif (Path(current) / name).is_symlink():
                found.skipped.append((f"{key}/", "is a linked folder, which isn't followed"))
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(names):
            key = (base / name).as_posix()
            if not name.lower().endswith(BUNDLE_SUFFIXES):
                if not name.startswith("."):
                    found.other_files += 1
                continue
            reason = _refused_name(key)
            if reason is None and _credential_name(name):
                reason = "is named like a credential"
            if reason:
                found.skipped.append((key, reason))
                continue
            keys.append(key)
    if not keys:
        raise BundleError(f"{str(directory)!r} holds no .yaml, .yml or .json files.")
    if len(keys) > MAX_STORED_FILES:
        raise BundleError(f"{str(directory)!r} holds more than {MAX_STORED_FILES} files to send.")
    for key in sorted(keys):
        if len(key) > MAX_PATH_LENGTH:
            raise BundleError(
                f"{key[:80]!r}... is a path longer than {MAX_PATH_LENGTH} characters."
            )
        text = collector.read(key)
        reason = _secret_content(text)
        if reason:
            found.skipped.append((key, reason))
            continue
        collector._bytes += len(text.encode("utf-8"))
        if collector._bytes > MAX_BUNDLE_BYTES:
            raise BundleError(too_large)
        found.files[key] = text
    if not found.files:
        raise BundleError(
            f"Every file in {str(directory)!r} was left out, so there's nothing to send."
        )
    return found


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
    try:
        data = collector.load(key).data
        collector.check_expansion(key)
    except RecursionError:
        raise BundleError(f"{key!r} or a file it includes is nested too deeply to read.") from None
    bundle = collector.bundle
    bundle.scenario_path = key
    bundle.scenario_names = _scenario_names(data, key)
    bundle.data = data
    return bundle
