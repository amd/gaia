# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""util/release_versions.py: every version field follows its source of truth.

Each test runs against a copy of the real files, so a format change in any
target (a moved field, a reformatted lockfile) fails here before it fails a
release.
"""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "util"))

import release_tag  # noqa: E402
import release_versions as rv  # noqa: E402

CORE_TARGETS = [t for t in rv.TARGETS if t.path != rv.CORE_PY]
ENFORCED = [t for t in CORE_TARGETS if not t.held and t.count is not None]
OPTIONAL = [t for t in rv.TARGETS if t.count is None]
HELD = [t for t in rv.TARGETS if t.held]
FLAGSHIP = rv.select("flagship")


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


def test_a_core_bump_moves_every_core_field_but_not_the_navbar_or_chat(tree):
    _set_source(tree, rv.CORE_PY, rv._PY_VERSION, "0.27.0")
    report = rv.run(tree, stamp=False)
    drifted = {line.split(":")[0] for line in report.drift}
    core_fields = {
        f"{t.path} ({t.what})"
        for t in CORE_TARGETS
        if t.source in ("core", "core-floor") and t.count is not None
    }
    assert drifted == core_fields

    rv.run(tree, stamp=True)
    assert rv.run(tree, stamp=False).drift == []
    src = rv.read_sources(tree)
    assert src.released == rv.read_sources(REPO_ROOT).released
    assert src.chat == rv.read_sources(REPO_ROOT).chat
    assert '"amd-gaia>=0.27.0"' in _raw(tree / "hub/agents/gaia/python/pyproject.toml")


# -- The flagship follows core -------------------------------------------------


def test_every_flagship_field_follows_core():
    assert FLAGSHIP
    assert {t.source for t in FLAGSHIP} <= {"core", "core-floor"}
    assert rv.FLAGSHIP_INIT in {t.path for t in FLAGSHIP}
    assert "flagship" not in rv.SOURCE_FILES


@pytest.mark.parametrize(
    "path, what",
    [
        (rv.FLAGSHIP_INIT, "__version__"),
        ("hub/agents/gaia/python/pyproject.toml", "version"),
        ("hub/agents/gaia/python/gaia-agent.yaml", "version"),
        ("hub/agents/gaia/python/gaia-agent.yaml", "min_gaia_version"),
        ("hub/agents/gaia/npm/package.json", "version"),
        ("hub/agents/gaia/npm/package-lock.json", "root version"),
    ],
)
def test_a_drifted_flagship_field_fails_naming_the_file(tree, path, what):
    core = rv.read_sources(tree).core_base
    target = _target(path, what)
    _set(tree, target, "0.3.2", count=target.count)
    report = rv.run(tree, stamp=False, targets=FLAGSHIP)
    assert report.drift == [
        f"{path} ({what}): 0.3.2, expected {core} (follows {rv.CORE_PY})"
    ]


def test_a_core_bump_moves_the_lock_but_not_its_tui_lane(tree):
    _set_source(tree, rv.CORE_PY, rv._PY_VERSION, "0.27.0")
    rv.run(tree, stamp=True)
    lock = _raw(tree / "hub/agents/gaia/npm/binaries.lock.json")
    assert '"agentVersion": "0.27.0"' in lock
    assert "/agents/gaia/0.27.0" in lock
    # The terminal UI lane in the same lock follows terminal-hub's release.
    assert "/agents/terminal-hub/0.27.0" not in lock


def test_the_chat_manifest_follows_chat_not_core(tree):
    _set_source(tree, rv.CHAT_PYPROJECT, rv._TOML_VERSION, "0.2.5")
    report = rv.run(tree, stamp=False)
    assert [line.split(":")[0] for line in report.drift] == [
        "hub/agents/chat/python/gaia-agent.yaml (version)"
    ]


# -- Optional doc URLs ---------------------------------------------------------


@pytest.mark.parametrize(
    "snippet, what",
    [
        ("https://img.shields.io/badge/version-0.3.2-blue", "version badge"),
        (
            "https://github.com/amd/gaia/blob/agent-pkg-gaia-v0.3.2/README.md",
            "pinned doc links",
        ),
        ("https://github.com/amd/gaia/blob/v0.3.2/SPEC.md", "pinned doc links"),
    ],
)
def test_a_stale_doc_url_fails_and_stamp_moves_it(tree, snippet, what):
    core = rv.read_sources(tree).core_base
    path = tree / "hub/agents/gaia/npm/README.md"
    _put(path, _raw(path) + f"\n<{snippet}>\n")

    report = rv.run(tree, stamp=False)
    assert report.drift == [
        f"hub/agents/gaia/npm/README.md ({what}): 0.3.2, expected {core} "
        f"(follows {rv.CORE_PY})"
    ]
    rv.run(tree, stamp=True)
    assert snippet.replace("0.3.2", core) in _raw(path)


def test_doc_urls_are_optional(tree):
    assert OPTIONAL
    for t in OPTIONAL:
        path = tree / t.path
        _put(path, t.pattern.sub("", _raw(path)))
    report = rv.run(tree, stamp=False)
    assert report.errors == [] and report.drift == []


def test_the_changelog_keeps_its_pinned_links(tree):
    path = tree / "hub/agents/gaia/npm/CHANGELOG.md"
    shutil.copyfile(REPO_ROOT / "hub/agents/gaia/npm/CHANGELOG.md", path)
    _put(path, _raw(path) + "\n</amd/gaia/blob/agent-pkg-gaia-v0.3.1/README.md>\n")
    before = path.read_bytes()
    assert rv.run(tree, stamp=False).drift == []
    rv.run(tree, stamp=True)
    assert path.read_bytes() == before


# -- The navbar follows the newest listed release --------------------------------


def _list_release(root: Path, version: str) -> None:
    path = root / rv.DOCS_JSON
    _put(
        path,
        _raw(path).replace(
            '"releases/index",',
            f'"releases/index",\n              "releases/v{version}",',
        ),
    )


def test_main_ahead_of_the_listed_release_passes(tree):
    released = rv.read_sources(tree).released
    _set_source(tree, rv.CORE_PY, rv._PY_VERSION, "9.0.0")
    rv.run(tree, stamp=True)
    report = rv.run(tree, stamp=False)
    assert report.errors == [] and report.drift == []
    assert f'"v{released} \\u00b7 Lemonade' in _raw(tree / rv.DOCS_JSON)


def test_listing_the_release_moves_the_navbar_to_it(tree):
    _set_source(tree, rv.CORE_PY, rv._PY_VERSION, "9.0.0")
    rv.run(tree, stamp=True)
    _list_release(tree, "9.0.0")
    report = rv.run(tree, stamp=False)
    assert report.drift == [
        f"docs/docs.json (navbar GAIA version): {rv.read_sources(REPO_ROOT).released}, "
        f"expected 9.0.0 (follows {rv.DOCS_JSON})"
    ]
    rv.run(tree, stamp=True)
    assert '"v9.0.0 \\u00b7 Lemonade' in _raw(tree / rv.DOCS_JSON)


def test_a_listed_release_ahead_of_core_is_an_error(tree):
    core = rv.read_sources(tree).core_base
    _list_release(tree, "99.0.0")
    report = rv.run(tree, stamp=False)
    assert report.errors == [
        f"{rv.DOCS_JSON} lists releases/v99.0.0, newer than the {core} "
        f"{rv.CORE_PY} declares. Set __version__ to the release being cut, or take "
        "that page out of the Releases tab."
    ]


def test_released_is_the_newest_page_not_the_first(tree):
    newest = rv.released_version(tree)
    _list_release(tree, "0.1.0")
    assert rv.released_version(tree) == newest


def test_no_listed_release_is_an_error(tree):
    path = tree / rv.DOCS_JSON
    docs = json.loads(_raw(path))
    for tab in docs["navigation"]["tabs"]:
        for group in tab.get("groups", []):
            if group.get("group") == "Release Notes":
                group["pages"] = ["releases/index"]
    _put(path, json.dumps(docs))
    with pytest.raises(rv.VersionError, match="lists no releases/vX.Y.Z page"):
        rv.released_version(tree)


# -- Groups --------------------------------------------------------------------


def test_groups_select_by_path():
    assert {t.path for t in rv.select("agent-ui")} == {
        "src/gaia/apps/webui/package.json",
        "src/gaia/apps/webui/package-lock.json",
    }
    assert all(t.path.startswith("hub/agents/gaia/") for t in FLAGSHIP)
    with pytest.raises(rv.VersionError, match="unknown group"):
        rv.select("chat")


def test_only_checks_just_that_group(tree):
    _set(
        tree, _target("hub/components/terminal-hub/gaia-agent.yaml", "version"), "0.1.0"
    )
    assert rv.main(["--check", "--only", "flagship", "--root", str(tree)]) == 0
    assert rv.main(["--check", "--root", str(tree)]) == 1


def test_cli_released_prints_the_newest_listed_release(tree, capsys):
    assert rv.main(["--released", "--root", str(tree)]) == 0
    assert capsys.readouterr().out.strip() == rv.released_version(REPO_ROOT)


# -- Unreadable sources and targets ------------------------------------------


@pytest.mark.parametrize(
    "rel",
    [
        "hub/components/terminal-hub/gaia-agent.yaml",
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


@pytest.mark.parametrize("rel", [rv.CORE_PY, rv.DOCS_JSON, rv.CHAT_PYPROJECT])
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
def test_an_unparseable_chat_version_is_an_error(tree, version):
    _set_source(tree, rv.CHAT_PYPROJECT, rv._TOML_VERSION, version)
    report = rv.run(tree, stamp=False)
    assert report.errors == [f"{rv.CHAT_PYPROJECT}: version '{version}' is not X.Y.Z"]


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
        ("hub/components/terminal-hub/gaia-agent.yaml", "version"),
        ("hub/agents/gaia/python/gaia-agent.yaml", "min_gaia_version"),
        ("hub/agents/gaia/python/gaia-agent.yaml", "version"),
        ("hub/agents/gaia/npm/binaries.lock.json", "agentVersion"),
    ],
)
def test_an_rc_suffix_never_leaks_into_a_final_only_field(tree, path, what):
    base = _rc_tree(tree, 2)
    _set(tree, _target(path, what), f"{base}rc2")
    report = rv.run(tree, stamp=False)
    assert report.drift == [
        f"{path} ({what}): {base}rc2, expected {base} (follows {rv.CORE_PY})"
    ]


def test_an_rc_stamp_writes_the_flagship_rc_forms(tree):
    base = _rc_tree(tree, 3)
    assert f'__version__ = "{base}rc3"' in _raw(tree / rv.FLAGSHIP_INIT)
    pyproject = _raw(tree / "hub/agents/gaia/python/pyproject.toml")
    assert f'version = "{base}rc3"' in pyproject
    pkg = _raw(tree / "hub/agents/gaia/npm/package.json")
    assert f'"version": "{base}-rc.3"' in pkg
    lock = _raw(tree / "hub/agents/gaia/npm/package-lock.json")
    assert lock.count(f'"version": "{base}-rc.3"') == 2
    # Hub paths have no pre-release channel yet.
    manifest = _raw(tree / "hub/agents/gaia/python/gaia-agent.yaml")
    assert re.search(rf"(?m)^version: {re.escape(base)}\s*$", manifest)


def test_an_rc_stamp_refuses_a_flagship_left_behind(tree):
    base = rv.read_sources(tree).core_base
    _set(tree, _target(rv.FLAGSHIP_INIT, "__version__"), "0.3.2")
    with pytest.raises(
        rv.VersionError, match=r"__init__.py \(__version__\) says 0.3.2"
    ):
        rv.stamp_rc(tree, f"v{base}-rc1")


def test_rc_stamp_matches_release_tag_stamp_byte_for_byte(tmp_path):
    """The plan's acceptance check, for the files both write."""
    trees = []
    for name in ("release_tag", "release_versions"):
        root = tmp_path / name
        for rel in _all_paths():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO_ROOT / rel, root / rel)
        _set_source(root, rv.CORE_PY, rv._PY_VERSION, "0.27.0")
        assert rv.print_report(rv.run(root, stamp=True), stamp=True) == 0
        trees.append(root)
    a, b = trees
    assert _snapshot(a) == _snapshot(b)

    release_tag.stamp("v0.27.0-rc1", a)
    assert rv.stamp_rc(b, "v0.27.0-rc1") == "0.27.0rc1"

    shared = [
        p.as_posix()
        for p in (
            release_tag.VERSION_PY,
            release_tag.WEBUI_PACKAGE,
            release_tag.WEBUI_LOCK,
        )
    ]
    assert {r: (a / r).read_bytes() for r in shared} == {
        r: (b / r).read_bytes() for r in shared
    }
    assert 'version__ = "0.27.0rc1"' in _raw(b / rv.CORE_PY)
    assert _raw(b / "src/gaia/apps/webui/package-lock.json").count("0.27.0-rc.1") == 2


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
    assert f'"v{src.released} \\u00b7 Lemonade {src.lemonade}"' in _raw(path)


def test_navbar_with_a_literal_separator_is_read_too(tree):
    path = tree / "docs/docs.json"
    _put(path, _raw(path).replace("\\u00b7", "·"))
    assert rv.run(tree, stamp=False).errors == []


# -- CLI and wiring --------------------------------------------------------------


def test_cli_check_fails_with_the_fix_command(tree, capsys):
    _set(
        tree, _target("hub/components/terminal-hub/gaia-agent.yaml", "version"), "0.1.0"
    )
    assert rv.main(["--check", "--root", str(tree)]) == 1
    out = capsys.readouterr().out
    assert "DRIFT  hub/components/terminal-hub/gaia-agent.yaml (version): 0.1.0" in out
    assert f"Fix: {rv.FIX_CMD}" in out


def test_cli_stamp_then_check(tree):
    _set(
        tree, _target("hub/components/terminal-hub/gaia-agent.yaml", "version"), "0.1.0"
    )
    assert rv.main(["--stamp", "--root", str(tree)]) == 0
    assert rv.main(["--check", "--root", str(tree)]) == 0


def test_cli_rc_stamp_error_is_reported(tree, capsys):
    assert rv.main(["--stamp", "v9.0.0-rc1", "--root", str(tree)]) == 1
    assert "ERROR:" in capsys.readouterr().err


def test_lint_runs_the_check_and_the_workflows_trigger_on_its_inputs():
    lint = (REPO_ROOT / "util/lint.py").read_text(encoding="utf-8")
    assert "from release_versions import run_check" in lint
    assert "if args.release_versions or run_all:" in lint
    text = (REPO_ROOT / ".github/workflows/lint.yml").read_text(encoding="utf-8")
    assert "python util/lint.py --all" in text
    on = yaml.safe_load(text)[True]  # PyYAML reads the bare `on:` key as True.
    inputs = ["util/release_versions.py", "util/release_tag.py"]
    inputs += [t.path for t in rv.TARGETS] + list(rv.SOURCE_FILES.values())
    for event in ("push", "pull_request"):
        patterns = on[event]["paths"]
        for path in inputs:
            assert any(fnmatch.fnmatch(path, p) for p in patterns), (event, path)
