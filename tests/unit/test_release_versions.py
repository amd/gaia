# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/release_versions.py: every version field follows its source of truth.

Each test runs against a copy of the real files, so a format change in any
target (a moved field, a reformatted lockfile) fails here before it fails a
release.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_tag  # noqa: E402
import release_versions as rv  # noqa: E402

CORE_TARGETS = [t for t in rv.TARGETS if t.path != rv.CORE_PY]
ENFORCED = [t for t in CORE_TARGETS if not t.held]
HELD = [t for t in rv.TARGETS if t.held]


def _all_paths() -> list[str]:
    return sorted({t.path for t in rv.TARGETS} | set(rv.SOURCE_FILES.values()))


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of every version source and target, byte for byte."""
    for rel in _all_paths():
        dst = tmp_path / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO_ROOT / rel, dst)
    return tmp_path


def _raw(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _put(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _set(root: Path, target: rv.Target, value: str, count: int = 1) -> None:
    """Rewrite ``count`` occurrences of ``target``'s version to ``value``."""
    path = root / target.path
    _put(
        path,
        target.pattern.sub(
            lambda m: f"{m.group(1)}{value}{m.group(3)}", _raw(path), count=count
        ),
    )


def _set_source(root: Path, rel: str, pattern: re.Pattern[str], value: str) -> None:
    path = root / rel
    new, n = pattern.subn(lambda m: f"{m.group(1)}{value}{m.group(3)}", _raw(path))
    assert n == 1
    _put(path, new)


def _target(path: str, what: str) -> rv.Target:
    (t,) = [t for t in rv.TARGETS if t.path == path and t.what == what]
    return t


def _snapshot(root: Path) -> dict[str, bytes]:
    return {rel: (root / rel).read_bytes() for rel in _all_paths()}


# -- The real tree ----------------------------------------------------------


def test_main_tree_passes():
    report = rv.run(REPO_ROOT, stamp=False)
    assert report.errors == []
    assert report.drift == []


def test_only_the_declared_targets_are_held():
    """Held drift is reported, never failed; the list must not quietly grow."""
    assert {(t.path, t.what) for t in HELD} == {
        ("hub/agents/gaia/npm/package-lock.json", "root version"),
        ("hub/agents/chat/python/gaia_agent_chat/__init__.py", "__version__"),
    }


def test_held_drift_is_reported_and_left_alone(tree):
    for t in HELD:
        _set(tree, t, "0.0.9", count=t.count)
    held_bytes = {t.path: (tree / t.path).read_bytes() for t in HELD}

    report = rv.run(tree, stamp=True)

    assert report.errors == [] and report.drift == []
    assert len(report.held) == len(HELD)
    assert all("0.0.9" in line for line in report.held)
    for t in HELD:
        assert (tree / t.path).read_bytes() == held_bytes[t.path]


# -- Drift: every target, one at a time -------------------------------------


@pytest.mark.parametrize("target", ENFORCED, ids=lambda t: f"{t.path}:{t.what}")
def test_each_drifted_target_fails_naming_the_file_and_stamp_restores_it(
    tree, target, capsys
):
    original = _snapshot(tree)
    _set(tree, target, "9.9.9")

    report = rv.run(tree, stamp=False)
    assert report.errors == []
    assert len(report.drift) == 1
    assert report.drift[0].startswith(
        f"{target.path} ({target.what}): 9.9.9, expected "
    )
    assert rv.print_report(report, stamp=False) == 1
    out = capsys.readouterr().out
    assert target.path in out and rv.FIX_CMD in out

    rv.run(tree, stamp=True)
    assert _snapshot(tree) == original


def test_one_of_two_lockfile_entries_drifting_fails(tree):
    lock = _target("src/gaia/apps/webui/package-lock.json", "root version")
    _set(tree, lock, "0.0.1", count=1)
    report = rv.run(tree, stamp=False)
    assert [line.split(":")[0] for line in report.drift] == [
        "src/gaia/apps/webui/package-lock.json (root version)"
    ]


# -- Floors ------------------------------------------------------------------


@pytest.mark.parametrize("floor", ["0.25.0", "0.0.1", "99.0.0"])
def test_a_floor_that_is_not_core_fails(tree, floor):
    core = rv.read_sources(tree).core_base
    target = _target("hub/agents/chat/python/pyproject.toml", "amd-gaia floor")
    _set(tree, target, floor)

    report = rv.run(tree, stamp=False)

    assert len(report.drift) == 1
    assert f"{floor}, expected {core}" in report.drift[0]
    rv.run(tree, stamp=True)
    assert rv.run(tree, stamp=False).drift == []


def test_a_core_bump_moves_every_floor_and_component_but_no_agent_version(tree):
    _set_source(tree, rv.CORE_PY, rv._PY_VERSION, "0.26.0")
    report = rv.run(tree, stamp=False)
    drifted = {line.split(":")[0] for line in report.drift}
    core_fields = {
        f"{t.path} ({t.what})"
        for t in CORE_TARGETS
        if t.source in ("core", "core-floor")
    }
    assert drifted == core_fields

    rv.run(tree, stamp=True)
    assert rv.run(tree, stamp=False).drift == []
    flagship = rv.read_sources(tree).flagship
    assert flagship == rv.read_sources(REPO_ROOT).flagship
    assert '"amd-gaia>=0.26.0"' in _raw(tree / "hub/agents/gaia/python/pyproject.toml")


# -- Agent versions ------------------------------------------------------------


def test_a_flagship_target_behind_its_agent_fails(tree):
    flagship = rv.read_sources(tree).flagship
    target = _target("hub/agents/gaia/npm/package.json", "version")
    _set(tree, target, "0.0.7")
    report = rv.run(tree, stamp=False)
    assert report.drift == [
        f"hub/agents/gaia/npm/package.json (version): 0.0.7, expected {flagship} "
        f"(follows {rv.FLAGSHIP_INIT})"
    ]


def test_a_flagship_bump_drifts_only_flagship_fields(tree):
    _set_source(tree, rv.FLAGSHIP_INIT, rv._PY_VERSION, "0.3.9")
    report = rv.run(tree, stamp=False)
    assert {line.split(":")[0] for line in report.drift} == {
        f"{t.path} ({t.what})"
        for t in rv.TARGETS
        if t.source == "flagship" and not t.held
    }
    rv.run(tree, stamp=True)
    lock = _raw(tree / "hub/agents/gaia/npm/binaries.lock.json")
    assert '"agentVersion": "0.3.9"' in lock
    assert "/agents/gaia/0.3.9" in lock
    # The terminal UI lane in the same lock follows terminal-hub, not the agent.
    assert "/agents/terminal-hub/0.3.9" not in lock


def test_the_chat_manifest_follows_chat_not_core_or_flagship(tree):
    _set_source(tree, rv.CHAT_PYPROJECT, rv._TOML_VERSION, "0.2.5")
    report = rv.run(tree, stamp=False)
    assert [line.split(":")[0] for line in report.drift] == [
        "hub/agents/chat/python/gaia-agent.yaml (version)"
    ]


# -- Unreadable sources and targets ------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "hub/components/agent-ui/gaia-agent.yaml",
        "src/gaia/apps/webui/package-lock.json",
        "docs/docs.json",
    ],
)
def test_a_missing_target_file_is_an_error_and_stamp_writes_nothing(tree, rel):
    floor = _target("hub/agents/chat/python/pyproject.toml", "amd-gaia floor")
    _set(tree, floor, "0.1.0")
    drifted = (tree / floor.path).read_bytes()
    (tree / rel).unlink()

    report = rv.run(tree, stamp=True)

    assert any(rel in e and "file not found" in e for e in report.errors)
    assert report.stamped == []
    assert (tree / floor.path).read_bytes() == drifted
    assert rv.print_report(report, stamp=True) == 1


@pytest.mark.parametrize("rel", [rv.CORE_PY, rv.FLAGSHIP_INIT, rv.CHAT_PYPROJECT])
def test_a_missing_source_is_an_error(tree, rel):
    (tree / rel).unlink()
    report = rv.run(tree, stamp=False)
    assert len(report.errors) == 1 and "file not found" in report.errors[0]


@pytest.mark.parametrize(
    "version", ["0.25", "0.25.1.1", "0.25.1rc0", "0.25.1-rc.2", "0.25.1.dev0", ""]
)
def test_an_unparseable_core_version_is_an_error(tree, version):
    _set_source(tree, rv.CORE_PY, rv._PY_VERSION, version)
    report = rv.run(tree, stamp=False)
    assert report.errors == [
        f"{rv.CORE_PY}: __version__ '{version}' is not X.Y.Z or X.Y.ZrcN (N >= 1)"
    ]


@pytest.mark.parametrize("version", ["0.3", "0.3.1rc1", "latest"])
def test_an_unparseable_agent_version_is_an_error(tree, version):
    _set_source(tree, rv.FLAGSHIP_INIT, rv._PY_VERSION, version)
    report = rv.run(tree, stamp=False)
    assert report.errors == [f"{rv.FLAGSHIP_INIT}: version '{version}' is not X.Y.Z"]


def test_a_field_that_appears_twice_is_an_error_not_a_guess(tree):
    path = tree / "hub/components/terminal-hub/gaia-agent.yaml"
    _put(path, _raw(path) + "version: 0.25.1\n")
    report = rv.run(tree, stamp=False)
    assert len(report.errors) == 1
    assert "terminal-hub/gaia-agent.yaml (version): expected 1 match(es), found 2" in (
        report.errors[0]
    )


def test_a_field_that_vanished_is_an_error(tree):
    path = tree / "docs/docs.json"
    _put(path, _raw(path).replace("Lemonade", "Lemon"))
    report = rv.run(tree, stamp=False)
    assert len(report.errors) == 2
    assert all("docs/docs.json (navbar" in e for e in report.errors)


# -- Pre-release trees ---------------------------------------------------------


def _rc_tree(root: Path, rc: int) -> str:
    base = rv.read_sources(root).core_base
    rv.stamp_rc(root, f"v{base}-rc{rc}")
    return base


def test_a_tree_stamped_for_an_rc_passes(tree):
    base = _rc_tree(tree, 2)
    src = rv.read_sources(tree)
    assert (src.core_pep440, src.core_npm) == (f"{base}rc2", f"{base}-rc.2")
    report = rv.run(tree, stamp=False)
    assert report.errors == [] and report.drift == []


def test_an_rc_tree_with_a_stale_npm_version_fails(tree):
    base = _rc_tree(tree, 2)
    pkg = _target("src/gaia/apps/webui/package.json", "version")
    _set(tree, pkg, f"{base}-rc.3")
    report = rv.run(tree, stamp=False)
    assert report.drift == [
        f"src/gaia/apps/webui/package.json (version): {base}-rc.3, expected "
        f"{base}-rc.2 (follows {rv.CORE_PY})"
    ]


@pytest.mark.parametrize(
    "path, what",
    [
        ("hub/components/agent-ui/gaia-agent.yaml", "version"),
        ("hub/agents/gaia/python/gaia-agent.yaml", "min_gaia_version"),
        ("docs/docs.json", "navbar GAIA version"),
    ],
)
def test_an_rc_suffix_never_leaks_into_a_final_only_field(tree, path, what):
    base = _rc_tree(tree, 2)
    _set(tree, _target(path, what), f"{base}rc2")
    report = rv.run(tree, stamp=False)
    assert report.drift == [
        f"{path} ({what}): {base}rc2, expected {base} (follows {rv.CORE_PY})"
    ]


def test_rc_stamp_matches_release_tag_stamp_byte_for_byte(tmp_path):
    """The plan's acceptance check, on v0.26.0-rc1 after a 0.26.0 bump."""
    trees = []
    for name in ("release_tag", "release_versions"):
        root = tmp_path / name
        for rel in _all_paths():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / rel, root / rel)
        _set_source(root, rv.CORE_PY, rv._PY_VERSION, "0.26.0")
        assert rv.print_report(rv.run(root, stamp=True), stamp=True) == 0
        trees.append(root)
    a, b = trees
    assert _snapshot(a) == _snapshot(b)

    release_tag.stamp("v0.26.0-rc1", a)
    assert rv.stamp_rc(b, "v0.26.0-rc1") == "0.26.0rc1"

    assert _snapshot(a) == _snapshot(b)
    assert 'version__ = "0.26.0rc1"' in _raw(b / rv.CORE_PY)
    assert _raw(b / "src/gaia/apps/webui/package-lock.json").count("0.26.0-rc.1") == 2


def test_rc_stamp_is_idempotent(tree):
    base = _rc_tree(tree, 1)
    once = _snapshot(tree)
    rv.stamp_rc(tree, f"v{base}-rc1")
    assert _snapshot(tree) == once


def test_rc_stamp_refuses_a_final_tag(tree):
    base = rv.read_sources(tree).core_base
    with pytest.raises(rv.VersionError, match="final release"):
        rv.stamp_rc(tree, f"v{base}")


def test_rc_stamp_refuses_a_tree_without_the_release_bump(tree):
    before = _snapshot(tree)
    with pytest.raises(rv.VersionError, match="Land the 9.0.0 release bump"):
        rv.stamp_rc(tree, "v9.0.0-rc1")
    assert _snapshot(tree) == before


@pytest.mark.parametrize("tag", ["v0.25.1-rc0", "v0.25.1rc1", "0.25.1-rc1"])
def test_rc_stamp_refuses_a_malformed_tag(tree, tag):
    with pytest.raises(rv.VersionError, match="vX.Y.Z-rcN"):
        rv.stamp_rc(tree, tag)


# -- Line endings --------------------------------------------------------------


@pytest.mark.parametrize("newline", ["\r\n", "\n"])
def test_line_endings_are_read_and_preserved(tree, newline):
    for rel in _all_paths():
        path = tree / rel
        _put(path, _raw(path).replace("\r\n", "\n").replace("\n", newline))
    assert rv.run(tree, stamp=False).drift == []
    original = _snapshot(tree)

    for t in ENFORCED:
        _set(tree, t, "8.8.8", count=t.count)
    assert len(rv.run(tree, stamp=False).drift) == len(ENFORCED)

    rv.run(tree, stamp=True)
    assert _snapshot(tree) == original


# -- docs.json -----------------------------------------------------------------


def test_navbar_stamp_keeps_the_escaped_separator(tree):
    path = tree / "docs/docs.json"
    assert "\\u00b7 Lemonade" in _raw(path)
    _set(tree, _target("docs/docs.json", "navbar Lemonade version"), "1.0.0")
    rv.run(tree, stamp=True)
    src = rv.read_sources(tree)
    assert f'"v{src.core_base} \\u00b7 Lemonade {src.lemonade}"' in _raw(path)


def test_navbar_with_a_literal_separator_is_read_too(tree):
    path = tree / "docs/docs.json"
    _put(path, _raw(path).replace("\\u00b7", "·"))
    assert rv.run(tree, stamp=False).errors == []


# -- CLI and wiring --------------------------------------------------------------


def test_cli_check_fails_with_the_fix_command(tree, capsys):
    _set(tree, _target("hub/components/agent-ui/gaia-agent.yaml", "version"), "0.1.0")
    assert rv.main(["--check", "--root", str(tree)]) == 1
    out = capsys.readouterr().out
    assert "DRIFT  hub/components/agent-ui/gaia-agent.yaml (version): 0.1.0" in out
    assert f"Fix: {rv.FIX_CMD}" in out


def test_cli_stamp_then_check(tree):
    _set(tree, _target("hub/components/agent-ui/gaia-agent.yaml", "version"), "0.1.0")
    assert rv.main(["--stamp", "--root", str(tree)]) == 0
    assert rv.main(["--check", "--root", str(tree)]) == 0


def test_cli_rc_stamp_error_is_reported(tree, capsys):
    assert rv.main(["--stamp", "v9.0.0-rc1", "--root", str(tree)]) == 1
    assert "ERROR:" in capsys.readouterr().err


def test_lint_runs_the_check_and_the_workflows_trigger_on_its_inputs():
    lint = (REPO_ROOT / "util/lint.py").read_text(encoding="utf-8")
    assert "from release_versions import run_check" in lint
    assert "if args.release_versions or run_all:" in lint
    workflow = (REPO_ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    assert "python util/lint.py --all" in workflow
    for path in ("util/release_versions.py", "util/release_tag.py", "docs/docs.json"):
        assert workflow.count(f"- '{path}'") == 2, path
