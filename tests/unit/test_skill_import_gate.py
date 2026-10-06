# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``gaia skill import`` runs the install gate, and every skill stays at its ceiling.

Cold state throughout: every skills root and lock lives under ``tmp_path``.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaia.skills import cli as skills_cli
from gaia.skills.capture import (
    SOURCE_IMPORTED,
    capture_skill,
    code_is_deferred,
    import_bundle,
    promote_skill,
)
from gaia.skills.errors import SkillPermissionError
from gaia.skills.install import SkillInstallError
from gaia.skills.loader import register_skill_tools
from gaia.skills.lock import SkillLock
from gaia.skills.tiers import enforce_skill_tier_ceiling, held_tier
from tests.unit.skills_helpers import isolated_manager

_TOOLS = (
    "from gaia.agents.base.tools import tool\n"
    "\n"
    "raise RuntimeError('tools.py must not run during import')\n"
    "\n"
    "\n"
    "@tool\n"
    "def count_words(text: str) -> int:\n"
    '    """Count words."""\n'
    "    return len(text.split())\n"
)


def _skill_dir(
    root: Path,
    name: str = "notes",
    *,
    tier: str | None = None,
    permissions: tuple[str, ...] = (),
    with_tools: bool = False,
) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    gaia = []
    if tier:
        gaia.append(f"    security_tier: {tier}")
    if permissions:
        gaia.append("    permissions:")
        gaia += [f"      - {p}" for p in permissions]
    if with_tools:
        gaia += [
            "    tools:",
            "      - name: count_words",
            "        description: Count words.",
            "        parameters:",
            "          text: {type: string, required: true}",
        ]
        (directory / "tools.py").write_text(_TOOLS, encoding="utf-8")
    lines = ["---", f"name: {name}", "description: An imported test skill."]
    lines.append('version: "1.0.0"')
    if gaia:
        lines += ["metadata:", "  gaia:", *gaia]
    lines += ["---", "", "# Notes", "", "Summarize the notes.", ""]
    (directory / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return directory


@pytest.fixture
def manager(tmp_path):
    return isolated_manager(tmp_path)


@pytest.fixture
def run(manager, monkeypatch, capsys):
    """Dispatch ``gaia skill import …`` in-process through the real parser."""
    monkeypatch.setattr(skills_cli, "_manager", lambda: manager)

    def _run(*args: str):
        parser = argparse.ArgumentParser(prog="gaia")
        skills_cli.add_subparser(parser.add_subparsers(dest="action"))
        rc = skills_cli.handle(parser.parse_args(["skill", "import", *args]))
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    return _run


# ---------------------------------------------------------------------------
# Transport
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://example.com/s.zip", "HTTP://example.com/s"])
def test_plain_http_url_is_refused_before_any_fetch(run, monkeypatch, url):
    import requests

    def _no_fetch(*_a, **_k):
        raise AssertionError("an http URL must be refused before fetching")

    monkeypatch.setattr(requests, "get", _no_fetch)
    rc, _, err = run(url)
    assert rc == skills_cli.EXIT_INVALID
    assert "only https URLs are accepted" in err


def test_https_redirect_to_http_is_refused(run, monkeypatch, manager):
    import requests

    response = SimpleNamespace(
        url="http://mirror.example.com/s.zip",
        raise_for_status=lambda: None,
        close=lambda: None,
        iter_content=lambda chunk_size: iter([b"PK"]),
    )
    monkeypatch.setattr(requests, "get", lambda *_a, **_k: response)
    rc, _, err = run("https://example.com/s.zip")
    assert rc == skills_cli.EXIT_INVALID
    assert "not https" in err
    assert not (manager.user_root / "notes").exists()


def test_folder_with_a_symlink_is_refused(run, tmp_path, manager):
    source = _skill_dir(tmp_path / "src")
    secret = tmp_path / "outside.txt"
    secret.write_text("not part of the skill", encoding="utf-8")
    try:
        os.symlink(secret, source / "linked.txt")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable here: {exc}")

    rc, _, err = run(str(source))
    assert rc == skills_cli.EXIT_INVALID
    assert "symlink" in err
    assert not (manager.user_root / "notes").exists()


# ---------------------------------------------------------------------------
# Code consent
# ---------------------------------------------------------------------------


def test_code_needs_allow_experimental_and_nothing_lands(run, tmp_path, manager):
    source = _skill_dir(tmp_path / "src", with_tools=True)
    rc, _, err = run(str(source))
    assert rc == skills_cli.EXIT_INVALID
    assert "--allow-experimental" in err
    assert not (manager.user_root / "notes").exists()
    assert SkillLock.load(manager.user_root).get("notes") is None


def test_import_with_consent_never_executes_tools_py(run, tmp_path, manager):
    """tools.py raises at import time, so landing it proves it was not run."""
    source = _skill_dir(tmp_path / "src", with_tools=True)
    rc, out, err = run(str(source), "--allow-experimental")
    assert rc == skills_cli.EXIT_OK, err
    assert "Imported skill 'notes'" in out

    entry = SkillLock.load(manager.user_root).get("notes")
    assert entry.source == SOURCE_IMPORTED
    assert entry.captured is True
    assert entry.installed_tier == "experimental"


def test_code_added_after_an_instruction_only_import_stays_deferred(tmp_path, manager):
    source = _skill_dir(tmp_path / "src")
    import_bundle(source, origin=str(source), manager=manager)
    assert SkillLock.load(manager.user_root).get("notes").code_trusted is False

    # Code dropped in later never got the opt-in, so it must not register.
    _skill_dir(manager.user_root, with_tools=True)
    manager.reload()
    assert code_is_deferred(manager.load("notes"))


def test_import_without_consent_through_the_api_is_refused(tmp_path, manager):
    source = _skill_dir(tmp_path / "src", with_tools=True)
    with pytest.raises(SkillInstallError, match="--allow-experimental"):
        import_bundle(source, origin=str(source), manager=manager)


def test_instruction_only_import_needs_no_opt_in(run, tmp_path, manager):
    source = _skill_dir(tmp_path / "src", permissions=("network:read",))
    rc, _, err = run(str(source))
    assert rc == skills_cli.EXIT_OK, err
    assert (manager.user_root / "notes" / "SKILL.md").is_file()


# ---------------------------------------------------------------------------
# Tier ceiling: import, capture, promote, load
# ---------------------------------------------------------------------------

_ABOVE_EXPERIMENTAL = ("network:write", "mcp:connect", "shell:execute:gh")


@pytest.mark.parametrize("permission", _ABOVE_EXPERIMENTAL)
def test_import_cannot_exceed_the_experimental_ceiling(
    run, tmp_path, manager, permission
):
    source = _skill_dir(tmp_path / "src", tier="verified", permissions=(permission,))
    rc, _, err = run(str(source), "--allow-experimental")
    assert rc == skills_cli.EXIT_INVALID
    assert "ceiling" in err
    assert not (manager.user_root / "notes").exists()


@pytest.mark.parametrize("permission", _ABOVE_EXPERIMENTAL)
def test_capture_cannot_exceed_the_experimental_ceiling(tmp_path, manager, permission):
    source = _skill_dir(tmp_path / "src", tier="community", permissions=(permission,))
    with pytest.raises(SkillPermissionError, match="ceiling"):
        capture_skill(str(source), manager=manager)
    assert not (manager.user_root / "notes").exists()


def _raise_in_place(manager, *, with_tools: bool = False) -> Path:
    """Edit a landed skill to claim a higher tier and a grant above its own."""
    _skill_dir(
        manager.user_root,
        tier="community",
        permissions=("shell:execute:gh",),
        with_tools=with_tools,
    )
    manager.reload()
    return manager.user_root / "notes"


def _raise_after_landing(manager, tmp_path) -> Path:
    source = _skill_dir(tmp_path / "src")
    capture_skill(str(source), manager=manager)
    return _raise_in_place(manager)


def test_promote_refuses_and_revokes_a_skill_edited_above_its_ceiling(
    tmp_path, manager
):
    source = _skill_dir(tmp_path / "src", with_tools=True)
    capture_skill(str(source), manager=manager)
    assert promote_skill("notes", manager=manager).promoted is True

    _raise_in_place(manager, with_tools=True)
    with pytest.raises(SkillPermissionError, match="'experimental' ceiling"):
        promote_skill("notes", manager=manager)
    assert SkillLock.load(manager.user_root).get("notes").code_trusted is False


def test_load_holds_an_edited_skill_to_its_landed_tier(tmp_path, manager):
    _raise_after_landing(manager, tmp_path)
    skill = manager.load("notes")
    assert skill.security_tier == "community"
    assert held_tier(skill) == "experimental"

    with pytest.raises(SkillPermissionError, match="'experimental' ceiling"):
        enforce_skill_tier_ceiling(skill)
    with pytest.raises(SkillPermissionError, match="'experimental' ceiling"):
        register_skill_tools(skill)


def test_agent_load_skill_refuses_before_granting_anything(tmp_path, manager):
    from tests.unit.test_skills_manager import _StubAgent

    _raise_after_landing(manager, tmp_path)
    agent = _StubAgent()
    with pytest.raises(SkillPermissionError, match="'experimental' ceiling"):
        agent.load_skill("notes", manager=manager)
    assert "notes" not in agent.loaded_skills


def test_an_authored_skill_without_a_lock_entry_is_not_capped(tmp_path):
    """No landing decision (authored, agent-bundled): only the author's claim."""
    from gaia.skills.format import parse_skill_file

    directory = _skill_dir(tmp_path / "bundled", permissions=("shell:execute:gh",))
    skill = parse_skill_file(directory)
    assert held_tier(skill) is None
    assert enforce_skill_tier_ceiling(skill) is None
