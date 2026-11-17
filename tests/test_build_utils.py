"""Unit tests for build context exclusion logic.

The build-context matcher walks the project tree with ``os.walk`` and
calls ``fnmatch.fnmatch`` against each pattern for every path
component. ``.dockerignore`` users routinely write patterns like
``.venv/`` or ``./node_modules`` that real Docker BuildKit accepts but
that ``fnmatch`` treats as literal text, which caused silent no-op
exclusions and over-sized build contexts. These tests pin the
normalization behavior so those forms keep working.
"""

import io
import sys
import tarfile
from pathlib import Path

import pytest

# Import from source, not installed package.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils.build_utils import (
    DEFAULT_EXCLUSIONS,
    _normalize_pattern,
    _should_exclude,
    create_deterministic_tarball,
    get_exclusions,
    load_dockerignore,
)


class TestNormalizePattern:
    def test_strips_trailing_slash(self):
        assert _normalize_pattern(".venv/") == ".venv"

    def test_strips_multiple_trailing_slashes(self):
        assert _normalize_pattern("build///") == "build"

    def test_strips_leading_dot_slash(self):
        assert _normalize_pattern("./node_modules") == "node_modules"

    def test_strips_both_prefix_and_suffix(self):
        assert _normalize_pattern("./.venv/") == ".venv"

    def test_plain_pattern_unchanged(self):
        assert _normalize_pattern(".venv") == ".venv"

    def test_glob_preserved(self):
        assert _normalize_pattern("*.pyc") == "*.pyc"

    def test_nested_path_trailing_slash(self):
        assert _normalize_pattern("foo/bar/") == "foo/bar"


class TestShouldExclude:
    """Directory entries with trailing slashes must prune the subtree."""

    def test_trailing_slash_dir_pattern_excludes_dir(self, tmp_path: Path):
        patterns = load_dockerignore_from_text(tmp_path, ".venv/\n")
        assert patterns == {".venv"}
        target = tmp_path / ".venv"
        target.mkdir()
        assert _should_exclude(target, patterns, tmp_path) is True

    def test_trailing_slash_dir_pattern_excludes_nested_file(self, tmp_path: Path):
        patterns = load_dockerignore_from_text(tmp_path, ".venv/\n")
        nested = tmp_path / ".venv" / "lib" / "foo.py"
        nested.parent.mkdir(parents=True)
        nested.touch()
        assert _should_exclude(nested, patterns, tmp_path) is True

    def test_leading_dot_slash_pattern_excludes(self, tmp_path: Path):
        patterns = load_dockerignore_from_text(tmp_path, "./node_modules\n")
        target = tmp_path / "node_modules"
        target.mkdir()
        assert _should_exclude(target, patterns, tmp_path) is True

    def test_glob_pattern_still_works(self, tmp_path: Path):
        patterns = load_dockerignore_from_text(tmp_path, "*.pyc\n")
        target = tmp_path / "foo.pyc"
        target.touch()
        assert _should_exclude(target, patterns, tmp_path) is True

    def test_unrelated_path_not_excluded(self, tmp_path: Path):
        patterns = load_dockerignore_from_text(tmp_path, ".venv/\n")
        keeper = tmp_path / "src" / "main.py"
        keeper.parent.mkdir()
        keeper.touch()
        assert _should_exclude(keeper, patterns, tmp_path) is False


class TestLoadDockerignore:
    def test_returns_none_when_missing(self, tmp_path: Path):
        assert load_dockerignore(tmp_path) is None

    def test_skips_comments_and_blanks(self, tmp_path: Path):
        (tmp_path / ".dockerignore").write_text("# header\n\n.venv/\n   \n*.log   \n# trailing\n")
        assert load_dockerignore(tmp_path) == {".venv", "*.log"}

    def test_all_slash_pattern_is_dropped(self, tmp_path: Path):
        # A line that normalizes to the empty string must not become a
        # wildcard match (otherwise everything gets excluded).
        (tmp_path / ".dockerignore").write_text("/\n./\n")
        assert load_dockerignore(tmp_path) == set()


class TestGetExclusions:
    def test_dockerignore_takes_precedence(self, tmp_path: Path):
        (tmp_path / ".dockerignore").write_text(".venv/\n")
        assert get_exclusions(tmp_path) == {".venv"}

    def test_defaults_when_no_dockerignore(self, tmp_path: Path):
        assert get_exclusions(tmp_path) == DEFAULT_EXCLUSIONS

    def test_extra_patterns_are_normalized(self, tmp_path: Path):
        result = get_exclusions(tmp_path, extra_patterns=["./dist/", "secrets"])
        assert "dist" in result
        assert "secrets" in result


class TestTarballIntegration:
    """End-to-end: a ``.dockerignore`` with trailing slashes actually prunes."""

    def test_venv_slash_pattern_keeps_tarball_lean(self, tmp_path: Path):
        # Realistic layout: a tiny source tree plus a fake .venv with
        # several "large" files that would blow the size budget if
        # included.
        (tmp_path / "Dockerfile").write_text("FROM scratch\nCOPY main.py /\n")
        (tmp_path / "main.py").write_text("print('hi')\n")
        venv = tmp_path / ".venv" / "lib"
        venv.mkdir(parents=True)
        for i in range(3):
            (venv / f"fake{i}.so").write_bytes(b"\0" * 1024)
        (tmp_path / ".dockerignore").write_text(".venv/\n")

        exclusions = get_exclusions(tmp_path)
        ctx = create_deterministic_tarball(str(tmp_path), exclusions)

        # Dockerfile, main.py, and the .dockerignore itself survive.
        # Without the fix, the three fake .so files inside .venv would
        # be packed as well, pushing file_count to 6.
        assert ctx.file_count == 3


def load_dockerignore_from_text(root: Path, text: str) -> set[str]:
    (root / ".dockerignore").write_text(text)
    result = load_dockerignore(root)
    assert result is not None
    return result


class TestTarballMemberNames:
    """Member names use "/" whatever the local path separator is."""

    def _member_names(self, ctx) -> list[str]:
        with tarfile.open(fileobj=io.BytesIO(ctx.tarball), mode="r:gz") as tar:
            return tar.getnames()

    def test_nested_file_uses_forward_slash(self, tmp_path: Path):
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "bot.py").write_text("print('hi')\n")

        ctx = create_deterministic_tarball(str(tmp_path), set())

        assert self._member_names(ctx) == ["Dockerfile", "src/bot.py"]

    def test_dockerignore_is_read_as_utf8(self, tmp_path: Path):
        # Windows' default code page would misread a non-ASCII pattern.
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
        (tmp_path / "café.txt").write_text("x\n")
        (tmp_path / ".dockerignore").write_text("café.txt\n", encoding="utf-8")

        ctx = create_deterministic_tarball(str(tmp_path), get_exclusions(tmp_path))

        assert "café.txt" not in self._member_names(ctx)

    def test_non_utf8_dockerignore_keeps_its_other_patterns(self, tmp_path: Path):
        # A cp1252 comment must not cost the file's other patterns.
        (tmp_path / "Dockerfile").write_text("FROM scratch\n")
        (tmp_path / "data").mkdir()
        (tmp_path / "data" / "big.bin").write_bytes(b"\0")
        (tmp_path / ".dockerignore").write_bytes("# Café data\ndata/\n".encode("cp1252"))

        ctx = create_deterministic_tarball(str(tmp_path), get_exclusions(tmp_path))

        assert "data/big.bin" not in self._member_names(ctx)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
