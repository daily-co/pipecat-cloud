"""Tests for collecting an eval run's files from a scenario file."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pipecatcloud._utils import eval_bundle
from pipecatcloud._utils.eval_bundle import MAX_BUNDLE_BYTES, BundleError, build_bundle

SCENARIO = """\
name: calls
judge: !include judge.yaml
scenarios:
  - name: book
    persona: A caller.
    goal: Book a table.
    success: A table was booked.
  - name: hours
    persona: Another caller.
    goal: Ask the hours.
    success: Hours were given.
"""


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def tree(tmp_path):
    write(tmp_path / "calls.yaml", SCENARIO)
    write(tmp_path / "judge.yaml", "text: judge\n")
    return tmp_path


class TestWhatIsBundled:
    def test_scenario_file_and_its_includes_only(self, tree):
        write(tree / "unrelated.yaml", "x: 1\n")
        write(tree / "notes.txt", "not referenced")
        bundle = build_bundle(tree / "calls.yaml")
        assert sorted(bundle.files) == ["calls.yaml", "judge.yaml"]
        assert bundle.scenario_path == "calls.yaml"
        assert bundle.scenario_names == ["book", "hours"]
        assert bundle.files["judge.yaml"] == "text: judge\n"

    def test_nested_includes_resolve_from_the_including_file(self, tree):
        write(tree / "calls.yaml", "name: c\njudge: !include parts/a.yaml\nscenarios: []\n")
        write(tree / "parts" / "a.yaml", "inner: !include b.yaml\n")
        write(tree / "parts" / "b.yaml", "v: 1\n")
        # A file next to the scenario with the same name must not be taken.
        write(tree / "b.yaml", "v: 2\n")
        bundle = build_bundle(tree / "calls.yaml")
        assert sorted(bundle.files) == ["calls.yaml", "parts/a.yaml", "parts/b.yaml"]

    def test_the_parsed_scenario_file_has_its_includes_expanded(self, tree):
        bundle = build_bundle(tree / "calls.yaml")
        assert bundle.data["judge"] == {"text": "judge"}
        assert [s["name"] for s in bundle.data["scenarios"]] == ["book", "hours"]

    def test_include_going_up_stays_inside_the_root(self, tree):
        write(tree / "shared" / "judge.yaml", "text: shared\n")
        write(
            tree / "scenarios" / "calls.yaml",
            "name: c\njudge: !include ../shared/judge.yaml\nscenarios: []\n",
        )
        bundle = build_bundle(tree / "scenarios" / "calls.yaml", root=tree)
        assert sorted(bundle.files) == ["scenarios/calls.yaml", "shared/judge.yaml"]
        assert bundle.scenario_path == "scenarios/calls.yaml"

    def test_the_default_root_is_the_scenario_files_directory(self, tree):
        write(tree / "sub" / "judge.yaml", "text: sub\n")
        write(tree / "sub" / "calls.yaml", "name: c\njudge: !include judge.yaml\nscenarios: []\n")
        bundle = build_bundle(tree / "sub" / "calls.yaml")
        assert bundle.scenario_path == "calls.yaml"
        assert bundle.files["judge.yaml"] == "text: sub\n"

    def test_a_file_included_twice_is_sent_once(self, tree):
        write(
            tree / "calls.yaml",
            "name: c\na: !include judge.yaml\nb: !include ./judge.yaml\nscenarios: []\n",
        )
        assert sorted(build_bundle(tree / "calls.yaml").files) == ["calls.yaml", "judge.yaml"]

    def test_unknown_tags_do_not_stop_the_scan(self, tree):
        write(tree / "calls.yaml", "name: c\nx: !other 1\nscenarios: []\n")
        assert list(build_bundle(tree / "calls.yaml").files) == ["calls.yaml"]


class TestRefusals:
    def test_include_that_leaves_the_root(self, tree):
        write(tree / "outside.yaml", "x: 1\n")
        write(tree / "root" / "calls.yaml", "name: c\nx: !include ../outside.yaml\nscenarios: []\n")
        with pytest.raises(BundleError, match="leaves the root"):
            build_bundle(tree / "root" / "calls.yaml")

    def test_absolute_include(self, tree):
        write(tree / "calls.yaml", f"name: c\nx: !include {tree / 'judge.yaml'}\nscenarios: []\n")
        with pytest.raises(BundleError, match="absolute"):
            build_bundle(tree / "calls.yaml")

    def test_missing_include_names_the_file_and_its_includer(self, tree):
        write(tree / "calls.yaml", "name: c\nx: !include nope.yaml\nscenarios: []\n")
        with pytest.raises(BundleError, match=r"'nope.yaml' \(included by 'calls.yaml'\) doesn't"):
            build_bundle(tree / "calls.yaml")

    def test_symlink_out_of_the_root_is_not_followed(self, tree):
        outside = write(tree / "outside" / "secret_judge.yaml", "text: leaked\n")
        root = tree / "root"
        write(root / "calls.yaml", "name: c\nj: !include judge.yaml\nscenarios: []\n")
        os.symlink(outside, root / "judge.yaml")
        with pytest.raises(BundleError, match="symlink that leaves the root"):
            build_bundle(root / "calls.yaml")

    def test_symlinked_directory_out_of_the_root_is_not_followed(self, tree):
        write(tree / "outside" / "judge.yaml", "text: leaked\n")
        root = tree / "root"
        write(root / "calls.yaml", "name: c\nj: !include link/judge.yaml\nscenarios: []\n")
        os.symlink(tree / "outside", root / "link")
        with pytest.raises(BundleError, match="symlink that leaves the root"):
            build_bundle(root / "calls.yaml")

    def test_scenario_file_that_is_a_symlink_out_of_the_root(self, tree):
        root = tree / "root"
        root.mkdir()
        os.symlink(tree / "calls.yaml", root / "calls.yaml")
        with pytest.raises(BundleError, match="symlink that leaves the root"):
            build_bundle(root / "calls.yaml")

    def test_symlink_that_stays_inside_the_root_is_read(self, tree):
        os.symlink(tree / "judge.yaml", tree / "alias.yaml")
        write(tree / "calls.yaml", "name: c\nj: !include alias.yaml\nscenarios: []\n")
        assert build_bundle(tree / "calls.yaml").files["alias.yaml"] == "text: judge\n"

    def test_scenario_file_outside_the_root(self, tree):
        write(tree / "root" / "x.yaml", "")
        with pytest.raises(BundleError, match="isn't inside the root"):
            build_bundle(tree / "calls.yaml", root=tree / "root")

    def test_non_utf8_include(self, tree):
        (tree / "judge.yaml").write_bytes(b"text: caf\xe9\n")
        with pytest.raises(BundleError, match=r"'judge.yaml'.*isn't UTF-8"):
            build_bundle(tree / "calls.yaml")

    def test_non_utf8_scenario_file(self, tree):
        (tree / "calls.yaml").write_bytes(b"\xff\xfe\x00name")
        with pytest.raises(BundleError, match="isn't UTF-8"):
            build_bundle(tree / "calls.yaml")

    def test_nul_character(self, tree):
        (tree / "judge.yaml").write_bytes(b"text: a\x00b\n")
        with pytest.raises(BundleError, match="NUL"):
            build_bundle(tree / "calls.yaml")

    @pytest.mark.parametrize(
        "name",
        [
            ".env",
            ".env.local",
            ".hidden.yaml",
            ".git/config",
            ".private/judge.yaml",
            "keys/server.pem",
            "server.key",
            "id_rsa",
            "prod.env",
            "PROD.ENV",
            "certs/client.p12",
        ],
    )
    def test_dotfiles_and_secret_looking_files_are_refused_even_when_included(self, tree, name):
        write(tree / name, "text: x\n")
        write(tree / "calls.yaml", f"name: c\nj: !include {name}\nscenarios: []\n")
        with pytest.raises(BundleError, match="hidden|secrets"):
            build_bundle(tree / "calls.yaml")

    @pytest.mark.parametrize(
        "name", ["password_reset.yaml", "secret_santa.yaml", "credentials.yaml"]
    )
    def test_ordinary_names_that_mention_a_secret_word_are_sent(self, tree, name):
        write(tree / name, "text: x\n")
        write(tree / "calls.yaml", f"name: c\nj: !include {name}\nscenarios: []\n")
        assert name in build_bundle(tree / "calls.yaml").files

    @pytest.mark.parametrize("target", [".env", "prod.env", ".ssh/id_rsa", "server.pem"])
    def test_a_link_to_a_refused_file_is_refused_whatever_the_link_is_called(self, tree, target):
        write(tree / target, "TOKEN=abc\n")
        os.symlink(tree / target, tree / "user.yaml")
        write(tree / "calls.yaml", "name: c\nj: !include user.yaml\nscenarios: []\n")
        with pytest.raises(BundleError, match=r"link to .* which"):
            build_bundle(tree / "calls.yaml")

    def test_a_dotfile_that_is_not_included_is_simply_not_sent(self, tree):
        write(tree / ".env", "TOKEN=abc\n")
        assert ".env" not in build_bundle(tree / "calls.yaml").files

    def test_a_dot_directory_above_the_root_is_fine(self, tmp_path):
        base = tmp_path / ".config" / "evals"
        write(base / "calls.yaml", SCENARIO)
        write(base / "judge.yaml", "text: j\n")
        assert sorted(build_bundle(base / "calls.yaml").files) == ["calls.yaml", "judge.yaml"]

    def test_include_of_a_directory(self, tree):
        (tree / "dir").mkdir()
        write(tree / "calls.yaml", "name: c\nj: !include dir\nscenarios: []\n")
        with pytest.raises(BundleError, match="regular file"):
            build_bundle(tree / "calls.yaml")

    def test_include_cycle(self, tree):
        write(tree / "calls.yaml", "name: c\na: !include a.yaml\nscenarios: []\n")
        write(tree / "a.yaml", "b: !include calls.yaml\n")
        with pytest.raises(BundleError, match="includes itself"):
            build_bundle(tree / "calls.yaml")

    def test_not_a_scenario_file(self, tree):
        write(tree / "calls.yaml", "name: c\njust: data\n")
        with pytest.raises(BundleError, match="no 'scenarios:' list"):
            build_bundle(tree / "calls.yaml")

    def test_invalid_yaml(self, tree):
        write(tree / "calls.yaml", "name: [unclosed\n")
        with pytest.raises(BundleError, match="isn't valid YAML"):
            build_bundle(tree / "calls.yaml")

    def test_missing_scenario_file(self, tmp_path):
        with pytest.raises(BundleError, match="doesn't exist"):
            build_bundle(tmp_path / "nope.yaml")

    def test_root_that_is_not_a_directory(self, tree):
        with pytest.raises(BundleError, match="isn't a directory"):
            build_bundle(tree / "calls.yaml", root=tree / "judge.yaml")


class TestSize:
    def test_exactly_the_limit_is_accepted(self, tree):
        head = "name: c\nscenarios: []\nfiller: "
        filler = "x" * (MAX_BUNDLE_BYTES - len(head) - 1)
        write(tree / "calls.yaml", head + filler + "\n")
        bundle = build_bundle(tree / "calls.yaml")
        assert bundle.size == MAX_BUNDLE_BYTES

    def test_one_byte_over_is_refused(self, tree):
        head = "name: c\nscenarios: []\nfiller: "
        filler = "x" * (MAX_BUNDLE_BYTES - len(head))
        write(tree / "calls.yaml", head + filler + "\n")
        with pytest.raises(BundleError, match="256 KiB"):
            build_bundle(tree / "calls.yaml")

    def test_size_is_counted_in_utf8_bytes_not_characters(self, tree):
        # 3 bytes a character: under the limit as characters, over it as bytes.
        head = "name: c\nscenarios: []\nfiller: "
        filler = "€" * (MAX_BUNDLE_BYTES // 3)
        write(tree / "calls.yaml", head + filler + "\n")
        with pytest.raises(BundleError, match="256 KiB"):
            build_bundle(tree / "calls.yaml")

    def test_size_counts_the_includes_together(self, tree):
        half = "y: " + "x" * (MAX_BUNDLE_BYTES // 2) + "\n"
        write(tree / "judge.yaml", half)
        write(tree / "calls.yaml", "name: c\nj: !include judge.yaml\nscenarios: []\nz: " + half[3:])
        with pytest.raises(BundleError, match="256 KiB"):
            build_bundle(tree / "calls.yaml")

    def test_file_count_is_bounded(self, tree, monkeypatch):
        monkeypatch.setattr(eval_bundle, "MAX_FILES", 1)
        with pytest.raises(BundleError, match="more than 1 files"):
            build_bundle(tree / "calls.yaml")


def write_fanout(tree: Path, fan: int, levels: int) -> None:
    """`calls.yaml` names `n0_*` files `fan` times; each level names the next `fan` times."""
    names = ", ".join(["!include l1.yaml"] * fan)
    write(tree / "calls.yaml", f"name: c\nscenarios: []\nx: [{names}]\n")
    for level in range(1, levels + 1):
        if level == levels:
            write(tree / f"l{level}.yaml", "leaf: 1\n")
        else:
            inner = ", ".join(f"!include l{level + 1}.yaml" for _ in range(fan))
            write(tree / f"l{level}.yaml", f"x: [{inner}]\n")


class TestExpansionBudget:
    def test_an_include_is_charged_each_time_it_is_named(self, tree):
        # 1 + 6 + 36 + 216 loads: under the byte limit, over the 200 loads the service allows.
        write_fanout(tree, fan=6, levels=3)
        with pytest.raises(BundleError, match="expand past 200 file loads"):
            build_bundle(tree / "calls.yaml")

    def test_a_fan_out_within_the_budget_is_accepted(self, tree):
        # 1 + 5 + 25 + 125 = 156 loads.
        write_fanout(tree, fan=5, levels=3)
        bundle = build_bundle(tree / "calls.yaml")
        assert sorted(bundle.files) == ["calls.yaml", "l1.yaml", "l2.yaml", "l3.yaml"]

    def test_a_huge_fan_out_is_refused_at_once_not_after_expanding_it(self, tree):
        import time

        write_fanout(tree, fan=30, levels=6)
        started = time.monotonic()
        with pytest.raises(BundleError, match="expand past"):
            build_bundle(tree / "calls.yaml")
        assert time.monotonic() - started < 2

    def test_characters_are_charged_each_time_too(self, tree):
        big = "y: " + "x" * (100 * 1024) + "\n"
        write(tree / "big.yaml", big)
        write(
            tree / "calls.yaml",
            "name: c\nscenarios: []\nx: [!include big.yaml, !include big.yaml, !include big.yaml]\n",
        )
        # 100 KiB on disk, 300 KiB once expanded.
        with pytest.raises(BundleError, match="expand past"):
            build_bundle(tree / "calls.yaml")

    def test_each_file_is_parsed_once_however_often_it_is_named(self, tree, monkeypatch):
        calls = []
        real_load = eval_bundle.yaml.load

        def counting_load(text, loader):
            calls.append(text[:20])
            return real_load(text, loader)

        monkeypatch.setattr(eval_bundle.yaml, "load", counting_load)
        write(tree / "leaf.yaml", "v: 1\n")
        names = ", ".join("!include leaf.yaml" for _ in range(20))
        write(tree / "calls.yaml", f"name: c\nscenarios: []\nx: [{names}]\n")
        build_bundle(tree / "calls.yaml")
        assert len(calls) == 2


class TestUnreadableInput:
    def test_nesting_too_deep_for_the_parser_is_a_bundle_error(self, tree):
        write(tree / "calls.yaml", "name: c\nscenarios: []\nx: " + "[" * 5000 + "]" * 5000 + "\n")
        with pytest.raises(BundleError, match="nested too deeply"):
            build_bundle(tree / "calls.yaml")

    def test_a_file_over_the_limit_is_refused_before_it_is_read(self, tree, monkeypatch):
        with open(tree / "judge.yaml", "wb") as f:
            f.truncate(MAX_BUNDLE_BYTES + 1)

        real_read_bytes = Path.read_bytes

        def refuse_read(self):
            if self.name == "judge.yaml":
                raise AssertionError("the file was read")
            return real_read_bytes(self)

        monkeypatch.setattr(Path, "read_bytes", refuse_read)
        with pytest.raises(BundleError, match="256 KiB"):
            build_bundle(tree / "calls.yaml")
