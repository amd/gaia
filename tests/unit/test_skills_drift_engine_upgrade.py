# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A skill attested before the digest rule changed is re-audited, not accused.

The 0.2.0 audit engine hashes compiled modules, archives and ``node_modules``,
which 0.1.0 left out. A skill installed under 0.1.0 that ships one of those no
longer matches its recorded digest, though nobody touched it. Drift checks it
against the rule it was recorded under: unchanged there means "needs
re-auditing" (code off, no tamper alarm); changed there is still tampering.

Cold state throughout: every hub, root and lock lives under ``tmp_path``.
"""

from __future__ import annotations

import argparse

import pytest

from gaia.skills import cli as skills_cli
from gaia.skills.audit.findings import (
    AUDIT_ENGINE,
    LEGACY_DIGEST_ENGINE,
    legacy_content_digest,
)
from gaia.skills.capture import code_is_deferred, deferred_remedy
from gaia.skills.drift import (
    DRIFT_CONTENT,
    DRIFT_REAUDIT,
    check_drift,
    reaudit_required,
    relock,
)
from gaia.skills.errors import SkillDriftError
from gaia.skills.lock import LockEntry, SkillLock
from tests.unit.skills_helpers import make_marketplace

_TOOLS = (
    "from gaia.agents.base.tools import tool\n"
    "\n"
    "\n"
    "@tool\n"
    "def count_words(text: str) -> int:\n"
    '    """Count words."""\n'
    "    return len(text.split())\n"
)


def _write_source(tmp_path, *, name: str, tier: str):
    directory = tmp_path / "src" / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(
        "\n".join(
            [
                "---",
                f"name: {name}",
                "description: Count the words in a piece of text for the user.",
                "version: 1.0.0",
                "license: MIT",
                "metadata:",
                "  gaia:",
                f"    security_tier: {tier}",
                "    tools:",
                "      - name: count_words",
                "        description: Count words.",
                "        parameters:",
                "          text: {type: string, required: true}",
                "---",
                "",
                "# Words",
                "",
                "Call `count_words` and report the result.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (directory / "tools.py").write_text(_TOOLS, encoding="utf-8")
    return directory


@pytest.fixture
def marketplace(tmp_path):
    return make_marketplace(tmp_path)


@pytest.fixture
def attested(marketplace, tmp_path):
    """A ``community`` install whose signature verified."""
    key = marketplace.keygen()
    marketplace.trust(key, role="publisher")
    marketplace.publish(_write_source(tmp_path, name="words", tier="community"))
    result = marketplace.install("words")
    assert result.installed_tier == "community"
    return result


@pytest.fixture
def unattested(marketplace, tmp_path):
    """An ``experimental`` install, published unsigned."""
    source = _write_source(tmp_path, name="scratch", tier="experimental")
    marketplace.publish(source, unsigned=True)
    result = marketplace.install("scratch", allow_experimental=True)
    assert result.installed_tier == "experimental"
    return result


def _as_if_installed_under_0_1_0(root, name, directory):
    """Ship a native module and record the lock the way 0.1.0 would have.

    0.1.0 left ``.so`` files out of the digest and wrote no ``digest_engine``.
    """
    (directory / "native.cpython-312-x86_64-linux-gnu.so").write_bytes(b"\x7fELF")
    with SkillLock.locked(root) as lock:
        entry = lock.get(name)
        entry.content_digest = legacy_content_digest(directory)
        entry.digest_engine = ""
        lock.record(entry)
        lock.save()


def _kinds(report):
    return {d.kind for d in report.drifts}


# --- the lock records which rule computed the digest -------------------------


def test_install_records_the_engine_that_computed_the_digest(marketplace, attested):
    entry = SkillLock.load(marketplace.skills_root).get("words")
    assert entry.digest_engine == AUDIT_ENGINE


def test_a_lock_entry_written_before_the_field_existed_still_loads():
    entry = LockEntry.from_dict(
        {"name": "old", "version": "1.0.0", "content_digest": "sha256:ab"},
        where="test",
    )
    assert entry.digest_engine == ""


# --- old engine, unchanged files: re-audit, not tampering --------------------


def test_an_unchanged_pre_upgrade_install_needs_reaudit_not_reinstall_for_tamper(
    marketplace, attested
):
    _as_if_installed_under_0_1_0(marketplace.skills_root, "words", attested.path)

    report = check_drift(marketplace.skills_root)

    assert _kinds(report) == {DRIFT_REAUDIT}
    (drift,) = report.drifts
    assert drift.fatal is False
    assert LEGACY_DIGEST_ENGINE in drift.summary
    assert "re-audit" in drift.summary
    assert "edited" not in drift.summary
    assert "gaia skill install words --force" in drift.remediation


def test_a_skill_awaiting_reaudit_loads_with_its_code_off(marketplace, attested):
    _as_if_installed_under_0_1_0(marketplace.skills_root, "words", attested.path)
    marketplace.manager.reload()

    skill = marketplace.manager.load("words")

    assert code_is_deferred(skill) is True
    assert deferred_remedy(skill) == "`gaia skill install words --force`"


def test_relock_will_not_stamp_the_new_rule_over_an_attested_skill(
    marketplace, attested
):
    _as_if_installed_under_0_1_0(marketplace.skills_root, "words", attested.path)

    result = relock(marketplace.skills_root)

    assert [d.kind for d in result.refused] == [DRIFT_REAUDIT]
    assert SkillLock.load(marketplace.skills_root).get("words").digest_engine == ""


def test_relock_reaudits_an_unattested_skill_and_turns_its_code_back_on(
    marketplace, unattested
):
    _as_if_installed_under_0_1_0(marketplace.skills_root, "scratch", unattested.path)
    marketplace.manager.reload()
    assert code_is_deferred(marketplace.manager.load("scratch")) is True

    result = relock(marketplace.skills_root)

    assert result.updated == ["scratch"]
    assert SkillLock.load(marketplace.skills_root).get("scratch").digest_engine == (
        AUDIT_ENGINE
    )
    assert check_drift(marketplace.skills_root).clean
    marketplace.manager.reload()
    assert code_is_deferred(marketplace.manager.load("scratch")) is False


def test_cli_lock_check_says_reaudit_rather_than_changed(
    marketplace, attested, monkeypatch, capsys
):
    monkeypatch.setattr(skills_cli, "_manager", lambda: marketplace.manager)
    _as_if_installed_under_0_1_0(marketplace.skills_root, "words", attested.path)

    args = argparse.Namespace(
        skill_action="lock", check=False, relock=False, as_json=False
    )
    assert skills_cli.handle(args) == skills_cli.EXIT_INVALID

    err = capsys.readouterr().err
    assert "need re-auditing" in err
    assert "blocking" not in err


# --- tampering stays fatal ---------------------------------------------------


def test_same_engine_edit_is_still_fatal_tampering(marketplace, attested):
    (attested.path / "tools.py").write_text("import socket\n", encoding="utf-8")
    marketplace.manager.reload()

    report = check_drift(marketplace.skills_root)

    assert _kinds(report) == {DRIFT_CONTENT}
    assert report.fatal
    with pytest.raises(SkillDriftError):
        marketplace.manager.load("words")


def test_an_edit_to_a_pre_upgrade_install_is_still_fatal_tampering(
    marketplace, attested
):
    """The 0.1.0 rule covered tools.py, so an edit there is caught under it."""
    _as_if_installed_under_0_1_0(marketplace.skills_root, "words", attested.path)
    (attested.path / "tools.py").write_text("import socket\n", encoding="utf-8")
    marketplace.manager.reload()

    entry = SkillLock.load(marketplace.skills_root).get("words")
    assert reaudit_required(entry, attested.path) is False
    report = check_drift(marketplace.skills_root)
    assert _kinds(report) == {DRIFT_CONTENT}
    assert report.fatal
    with pytest.raises(SkillDriftError):
        marketplace.manager.load("words")


def test_a_pre_upgrade_install_with_nothing_new_to_hash_is_clean(marketplace, attested):
    """No compiled module, archive or node_modules: both rules agree."""
    with SkillLock.locked(marketplace.skills_root) as lock:
        entry = lock.get("words")
        entry.digest_engine = ""
        lock.record(entry)
        lock.save()
    marketplace.manager.reload()

    assert check_drift(marketplace.skills_root).clean
    assert code_is_deferred(marketplace.manager.load("words")) is False
