# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the single-source version stamper (packaging/stamp_version.py).

Guards the contract that ``AGENT_VERSION`` in ``version.py`` is the one source of
truth: ``--check`` passes on a consistent tree, fails loudly (non-zero) when any
target drifts, the default mode stamps every present target to match, and absent
targets (files/fields that live on other in-flight branches) are skipped with a
warning rather than failing. Fully hermetic — operates on a synthesized temp tree,
no network, no dependence on the real repo's current versions.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# stamp_version.py is a packaging script, not part of the gaia_agent_email
# package — load it by path (mirrors test_gen_binaries_lock.py). Register it in
# sys.modules before exec so its @dataclass definitions can resolve their module.
_STAMP_PATH = Path(__file__).resolve().parents[1] / "packaging" / "stamp_version.py"
_spec = importlib.util.spec_from_file_location("stamp_version", _STAMP_PATH)
stamp = importlib.util.module_from_spec(_spec)
sys.modules["stamp_version"] = stamp
_spec.loader.exec_module(stamp)


def _build_tree(root: Path, version: str, *, include_optional: bool = True) -> None:
    """Synthesize a complete agent-email tree, every target at ``version``."""
    email = root / "hub" / "agents" / "email" / "python"
    npm = root / "hub" / "agents" / "email" / "npm"
    (email / "gaia_agent_email").mkdir(parents=True)
    npm.mkdir(parents=True)

    (email / "gaia_agent_email" / "version.py").write_text(
        f'AGENT_VERSION = "{version}"\nAPI_VERSION = "9.9.9"\n', encoding="utf-8"
    )
    (email / "gaia-agent.yaml").write_text(
        f'id: email\nversion: {version}\nmin_gaia_version: "0.20.0"\n',
        encoding="utf-8",
    )
    (email / "pyproject.toml").write_text(
        f'[project]\nname = "gaia-agent-email"\nversion = "{version}"\n',
        encoding="utf-8",
    )
    (npm / "package.json").write_text(
        json.dumps(
            {"name": "@amd-gaia/agent-email", "version": version, "type": "module"},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (npm / "binaries.lock.json").write_text(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "agentVersion": version,
                "baseUrl": f"https://hub.amd-gaia.ai/agents/email/{version}",
                "binaries": {},
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if include_optional:
        (email / "README.md").write_text(
            f"![play](https://hub.amd-gaia.ai/agents/email/{version}/playground.webp)\n",
            encoding="utf-8",
        )
        (npm / "README.md").write_text(
            f"![arch](https://hub.amd-gaia.ai/agents/email/{version}/architecture.webp)\n",
            encoding="utf-8",
        )
        (npm / "assets").mkdir()
        (npm / "assets" / "architecture.html").write_text(
            f'<span class="badge" id="ver">v{version}</span>\n', encoding="utf-8"
        )
    else:
        # Mirror real `main`: the README files EXIST but carry no versioned image
        # URL yet (field-absent skip), while architecture.html is entirely missing
        # (file-absent skip). Both must skip-with-warning, never fail the gate.
        (email / "README.md").write_text("# Email agent\nno image yet\n", "utf-8")
        (npm / "README.md").write_text("# agent-email\nno image yet\n", "utf-8")


@pytest.fixture
def tree(tmp_path, monkeypatch):
    """A consistent v1.2.3 tree with the module's path constants pointed at it."""
    _build_tree(tmp_path, "1.2.3")
    email = tmp_path / "hub" / "agents" / "email" / "python"
    npm = tmp_path / "hub" / "agents" / "email" / "npm"
    monkeypatch.setattr(stamp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(stamp, "EMAIL_ROOT", email)
    monkeypatch.setattr(stamp, "NPM_ROOT", npm)
    monkeypatch.setattr(stamp, "VERSION_PY", email / "gaia_agent_email" / "version.py")
    return tmp_path


def test_reads_agent_version_as_source(tree):
    assert stamp.read_agent_version() == "1.2.3"


def test_api_version_is_not_touched(tree):
    """API_VERSION (the contract version) must never be stamped as the pkg version."""
    result = stamp.process("1.2.3", check_only=False)
    text = (tree / "hub/agents/email/python/gaia_agent_email/version.py").read_text()
    assert 'API_VERSION = "9.9.9"' in text
    # version.py is the SOURCE, not a stampable target — it shouldn't appear.
    assert not any("version.py" in s for s in result.stamped)


def test_check_passes_on_consistent_tree(tree):
    assert stamp.main(["--check"]) == 0
    result = stamp.process("1.2.3", check_only=True)
    assert result.mismatches == []
    # every required target present + consistent
    assert {"gaia-agent.yaml", "pyproject.toml", "npm package.json"} <= set(
        result.already_ok
    )


@pytest.mark.parametrize(
    "rel,marker,bad",
    [
        ("hub/agents/email/python/gaia-agent.yaml", "version: 1.2.3", "version: 9.9.9"),
        (
            "hub/agents/email/python/pyproject.toml",
            'version = "1.2.3"',
            'version = "9.9.9"',
        ),
        (
            "hub/agents/email/npm/binaries.lock.json",
            "/agents/email/1.2.3",
            "/agents/email/9.9.9",
        ),
        (
            "hub/agents/email/npm/assets/architecture.html",
            'id="ver">v1.2.3',
            'id="ver">v9.9.9',
        ),
    ],
)
def test_check_fails_when_a_target_drifts(tree, rel, marker, bad):
    target = tree / rel
    target.write_text(target.read_text().replace(marker, bad), encoding="utf-8")
    assert stamp.main(["--check"]) == 1
    result = stamp.process("1.2.3", check_only=True)
    assert any("9.9.9" in m for m in result.mismatches)


def test_stamp_syncs_every_target(tree):
    # Start from an all-0.9.0 tree, then bump the source to 2.0.0 and stamp.
    for f in tree.rglob("*"):
        if f.is_file():
            f.write_text(
                f.read_text(encoding="utf-8").replace("1.2.3", "0.9.0"),
                encoding="utf-8",
            )
    # Source of truth now says 2.0.0; every downstream target still says 0.9.0.
    vpy = tree / "hub/agents/email/python/gaia_agent_email/version.py"
    vpy.write_text('AGENT_VERSION = "2.0.0"\nAPI_VERSION = "9.9.9"\n', encoding="utf-8")

    assert stamp.read_agent_version() == "2.0.0"
    assert stamp.main([]) == 0  # stamp mode
    # Every present target now matches; --check is green.
    assert stamp.main(["--check"]) == 0
    lock = json.loads((tree / "hub/agents/email/npm/binaries.lock.json").read_text())
    assert lock["agentVersion"] == "2.0.0"
    assert lock["baseUrl"].endswith("/agents/email/2.0.0")


def test_absent_targets_skipped_with_warning_not_failed(tmp_path, monkeypatch):
    # Tree WITHOUT the optional npm-side targets (README images, architecture.html)
    # — they live on other in-flight branches and aren't on main yet.
    _build_tree(tmp_path, "1.2.3", include_optional=False)
    email = tmp_path / "hub" / "agents" / "email" / "python"
    npm = tmp_path / "hub" / "agents" / "email" / "npm"
    monkeypatch.setattr(stamp, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(stamp, "EMAIL_ROOT", email)
    monkeypatch.setattr(stamp, "NPM_ROOT", npm)
    monkeypatch.setattr(stamp, "VERSION_PY", email / "gaia_agent_email" / "version.py")

    result = stamp.process("1.2.3", check_only=True)
    assert result.mismatches == []  # absent != mismatch
    skipped = "\n".join(result.skipped)
    assert "architecture.html" in skipped  # file-absent skip
    # An absent optional target must NOT fail the gate.
    assert stamp.main(["--check"]) == 0


def test_stamp_is_idempotent_and_minimal(tree):
    lock_path = tree / "hub/agents/email/npm/binaries.lock.json"
    before = lock_path.read_text(encoding="utf-8")
    result = stamp.process("1.2.3", check_only=False)
    assert result.stamped == []  # already consistent — nothing rewritten
    assert lock_path.read_text(encoding="utf-8") == before  # byte-identical
