# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``skill-lock.json`` is read back: drift detection and relock (#2467 follow-up).

The lock was written by ``gaia skill install`` and never re-read, so a skill
could be swapped, downgraded, or hand-edited after install and nothing noticed.
These tests are the counter-evidence, and each one is written to fail if the
check it covers is removed.

Every lock entry here is produced by the **real** publish → install path against
the stand-in hub, so the digests, signature, and enforced tier are the ones a
genuine install writes — not a hand-built fixture that would keep passing if
install stopped recording them.
"""

from __future__ import annotations

import argparse

import pytest

from gaia.skills import cli as skills_cli
from gaia.skills.drift import (
    DRIFT_CONTENT,
    DRIFT_MISSING,
    DRIFT_TIER,
    DRIFT_UNRECORDED,
    DRIFT_UNTRACKED,
    DRIFT_VERSION,
    check_drift,
    relock,
)
from gaia.skills.errors import SkillDriftError
from gaia.skills.lock import SOURCE_HUB, SOURCE_LOCAL, SkillLock
from tests.unit.skills_helpers import make_marketplace

SKILL_BODY = """# Web Research

1. Call `web-research/search_web` with the user's question.
2. Summarize the top results.
"""


def _write_source(
    tmp_path,
    *,
    name="web-research",
    version="1.0.0",
    tier="community",
    permissions=("network:read:*.brave.com",),
):
    """Author a publishable skill source directory."""
    directory = tmp_path / "src" / name
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"name: {name}",
        "description: Search the web for current information and summarize it.",
        f"version: {version}",
        "license: MIT",
        "metadata:",
        "  gaia:",
        f"    security_tier: {tier}",
    ]
    if permissions:
        lines.append("    permissions:")
        lines.extend(f"      - {p}" for p in permissions)
    lines += ["---", "", SKILL_BODY]
    (directory / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return directory


@pytest.fixture
def marketplace(tmp_path):
    return make_marketplace(tmp_path)


@pytest.fixture
def attested(marketplace, tmp_path):
    """A ``community`` install: publisher-signed, signature verified at install."""
    key = marketplace.keygen()
    marketplace.trust(key, role="publisher")
    marketplace.publish(_write_source(tmp_path), publisher="acme")
    result = marketplace.install("web-research")
    assert result.installed_tier == "community"
    return result


@pytest.fixture
def unattested(marketplace, tmp_path):
    """An ``experimental`` install: published unsigned, opted into explicitly."""
    source = _write_source(tmp_path, name="scratch-skill", tier="experimental")
    marketplace.publish(source, unsigned=True)
    result = marketplace.install("scratch-skill", allow_experimental=True)
    assert result.installed_tier == "experimental"
    return result


def _retier(skill_dir, tier):
    """Rewrite the installed manifest's declared security tier."""
    path = skill_dir / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    original, _, rest = text.partition("security_tier: ")
    current, _, tail = rest.partition("\n")
    path.write_text(f"{original}security_tier: {tier}\n{tail}", encoding="utf-8")
    assert current != tier, "the test must actually change the tier"


def _reversion(skill_dir, version):
    path = skill_dir / "SKILL.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace("version: 1.0.0", f"version: {version}"), "utf-8")


def _kinds(report):
    return {d.kind for d in report.drifts}


# ----------------------------------------------------------------------
# Install records the digest the whole feature rests on
# ----------------------------------------------------------------------


def test_install_records_a_content_digest_of_the_bytes_that_landed(
    marketplace, attested
):
    """Without this field there is nothing to compare against later."""
    from gaia.skills.audit.findings import content_digest

    entry = SkillLock.load(marketplace.skills_root).get("web-research")

    assert entry is not None
    assert entry.source == SOURCE_HUB
    assert entry.content_digest.startswith("sha256:")
    # The digest must cover the INSTALLED directory, not the downloaded archive.
    assert entry.content_digest == content_digest(marketplace.skills_root / entry.name)
    assert entry.content_digest != entry.artifact_sha256


# ----------------------------------------------------------------------
# No false positives
# ----------------------------------------------------------------------


def test_a_clean_install_reports_no_drift(marketplace, attested):
    report = check_drift(marketplace.skills_root)

    assert report.clean, report.render()
    assert report.fatal == ()


def test_two_clean_installs_report_no_drift(marketplace, tmp_path, attested):
    """A second skill must not make the first look drifted."""
    marketplace.publish(
        _write_source(tmp_path, name="note-taker", tier="experimental"), unsigned=True
    )
    marketplace.install("note-taker", allow_experimental=True)

    assert check_drift(marketplace.skills_root).clean


def test_a_clean_install_still_loads(marketplace, attested):
    """The load gate must not refuse a skill that has not changed."""
    marketplace.manager.reload()

    assert marketplace.manager.load("web-research").name == "web-research"


# ----------------------------------------------------------------------
# One test per drift mode
# ----------------------------------------------------------------------


def test_content_drift_is_detected_when_an_installed_file_is_edited(
    marketplace, attested
):
    (attested.path / "SKILL.md").write_text(
        (attested.path / "SKILL.md").read_text(encoding="utf-8")
        + "\n<!-- injected after install -->\n",
        encoding="utf-8",
    )

    report = check_drift(marketplace.skills_root)

    assert DRIFT_CONTENT in _kinds(report)
    assert not report.clean


def test_content_drift_is_detected_when_a_file_is_added(marketplace, attested):
    """A dropped-in module is content the signature never covered."""
    (attested.path / "helper.py").write_text("import os\n", encoding="utf-8")

    assert DRIFT_CONTENT in _kinds(check_drift(marketplace.skills_root))


def test_version_drift_is_detected_when_the_manifest_version_changes(
    marketplace, attested
):
    _reversion(attested.path, "9.9.9")

    report = check_drift(marketplace.skills_root)
    version = next(d for d in report.drifts if d.kind == DRIFT_VERSION)

    assert version.expected == "1.0.0"
    assert version.actual == "9.9.9"


def test_missing_drift_is_detected_when_the_directory_is_deleted(marketplace, attested):
    import shutil

    shutil.rmtree(attested.path)

    report = check_drift(marketplace.skills_root)
    missing = next(d for d in report.drifts if d.kind == DRIFT_MISSING)

    assert missing.skill == "web-research"
    # Nothing can load, so this is a report-level finding, not a load blocker.
    assert missing.fatal is False


def test_untracked_drift_is_detected_when_an_extra_skill_appears(
    marketplace, tmp_path, attested
):
    """A directory nobody installed is exactly what a lock exists to surface."""
    import shutil

    planted = _write_source(tmp_path, name="planted", tier="experimental")
    shutil.copytree(planted, marketplace.skills_root / "planted")

    report = check_drift(marketplace.skills_root)
    untracked = next(d for d in report.drifts if d.kind == DRIFT_UNTRACKED)

    assert untracked.skill == "planted"


def test_a_lock_entry_without_a_digest_reports_as_unverifiable(marketplace, attested):
    """A pre-digest lock must not read as 'checked and clean'."""
    lock = SkillLock.load(marketplace.skills_root)
    lock.get("web-research").content_digest = ""
    lock.save()

    report = check_drift(marketplace.skills_root)

    assert DRIFT_UNRECORDED in _kinds(report)
    assert not report.clean


# ----------------------------------------------------------------------
# Fatal vs. warning
# ----------------------------------------------------------------------


def test_an_attested_skill_refuses_to_load_after_content_drift(marketplace, attested):
    """Drift under a signature-backed tier defeats the signature — hard stop."""
    (attested.path / "tools.py").write_text("import socket\n", encoding="utf-8")
    marketplace.manager.reload()

    with pytest.raises(SkillDriftError) as caught:
        marketplace.manager.load("web-research")

    message = str(caught.value)
    assert "web-research" in message
    assert "gaia skill install web-research --force" in message


def test_an_unattested_skill_warns_but_still_loads_after_content_drift(
    marketplace, unattested, caplog
):
    """Editing an experimental skill is the normal workflow, not an attack."""
    (unattested.path / "notes.md").write_text("scratch\n", encoding="utf-8")
    marketplace.manager.reload()

    with caplog.at_level("WARNING", logger="gaia.skills.drift"):
        skill = marketplace.manager.load("scratch-skill")

    assert skill.name == "scratch-skill"
    assert any("drifted from the lock" in r.message for r in caplog.records)


def test_tier_escalation_is_fatal_even_from_the_experimental_floor(
    marketplace, unattested
):
    """Claiming 'verified' after install is a claim install examined and refused."""
    _retier(unattested.path, "verified")
    marketplace.manager.reload()

    report = check_drift(marketplace.skills_root)
    tier = next(d for d in report.drifts if d.kind == DRIFT_TIER)
    assert tier.expected == "experimental"
    assert tier.actual == "verified"
    assert tier.fatal is True

    with pytest.raises(SkillDriftError):
        marketplace.manager.load("scratch-skill")


def test_a_skill_outside_the_user_root_is_not_gated(marketplace, tmp_path, attested):
    """The lock only describes the user root; a bundled skill has no entry there."""
    import shutil

    from tests.unit.skills_helpers import isolated_manager

    bundled = tmp_path / "agent-skills"
    shutil.copytree(attested.path, bundled / "web-research")
    (bundled / "web-research" / "SKILL.md").write_text(
        (bundled / "web-research" / "SKILL.md").read_text(encoding="utf-8")
        + "\nedit\n",
        encoding="utf-8",
    )
    manager = isolated_manager(
        tmp_path,
        user_skills_root=tmp_path / "empty-user-root",
        agent_skill_dirs=[bundled],
    )

    assert manager.load("web-research").name == "web-research"


# ----------------------------------------------------------------------
# Relock
# ----------------------------------------------------------------------


def test_relock_records_an_intentional_edit_and_then_validates_clean(
    marketplace, unattested
):
    (unattested.path / "notes.md").write_text("scratch\n", encoding="utf-8")
    assert not check_drift(marketplace.skills_root).clean

    result = relock(marketplace.skills_root)

    assert result.updated == ["scratch-skill"]
    assert result.refused == []
    assert check_drift(marketplace.skills_root).clean


def test_relock_tracks_an_untracked_skill_as_local_and_then_validates_clean(
    marketplace, tmp_path
):
    import shutil

    shutil.copytree(
        _write_source(tmp_path, name="hand-written", tier="experimental"),
        marketplace.skills_root / "hand-written",
    )

    result = relock(marketplace.skills_root)

    assert result.added == ["hand-written"]
    entry = SkillLock.load(marketplace.skills_root).get("hand-written")
    # Never claim hub provenance for a skill that was never installed from one.
    assert entry.source == SOURCE_LOCAL
    assert entry.content_digest.startswith("sha256:")
    assert check_drift(marketplace.skills_root).clean


def test_relock_drops_the_entry_for_a_skill_that_is_gone(marketplace, unattested):
    import shutil

    shutil.rmtree(unattested.path)

    result = relock(marketplace.skills_root)

    assert result.removed == ["scratch-skill"]
    assert "scratch-skill" not in SkillLock.load(marketplace.skills_root).entries
    assert check_drift(marketplace.skills_root).clean


def test_relock_refuses_to_re_record_an_attested_skill_that_drifted(
    marketplace, attested
):
    """Rewriting the digest would leave a signature asserting bytes it never signed."""
    before = SkillLock.load(marketplace.skills_root).get("web-research").content_digest
    (attested.path / "tools.py").write_text("import socket\n", encoding="utf-8")

    result = relock(marketplace.skills_root)

    assert [d.skill for d in result.refused] == ["web-research"]
    assert "web-research" not in result.updated
    after = SkillLock.load(marketplace.skills_root).get("web-research")
    assert after.content_digest == before
    # And the skill still refuses to load — relock did not launder it.
    marketplace.manager.reload()
    with pytest.raises(SkillDriftError):
        marketplace.manager.load("web-research")


def test_relock_refuses_a_hub_entry_whose_manifest_changed_tier(
    marketplace, unattested
):
    """Changing the enforced tier by editing the file is never re-recordable."""
    _retier(unattested.path, "community")

    result = relock(marketplace.skills_root)

    assert [d.skill for d in result.refused] == ["scratch-skill"]
    entry = SkillLock.load(marketplace.skills_root).get("scratch-skill")
    assert entry.installed_tier == "experimental"


def test_relock_never_writes_a_hub_entrys_tier_from_the_manifest_it_governs(
    marketplace, unattested
):
    """A legacy hub entry with no recorded tier must not adopt the file's claim.

    ``installed_tier`` is what install *enforced* against the signature. Taking
    it from ``SKILL.md`` would let anyone promote a hub skill by editing it and
    running relock — the reachable case is an entry written before the tier was
    recorded, where the drift check has no enforced tier to compare against.
    """
    lock = SkillLock.load(marketplace.skills_root)
    lock.get("scratch-skill").installed_tier = ""
    lock.save()
    _retier(unattested.path, "verified")

    relock(marketplace.skills_root)

    entry = SkillLock.load(marketplace.skills_root).get("scratch-skill")
    assert entry.source == SOURCE_HUB
    assert entry.installed_tier == ""


# ----------------------------------------------------------------------
# Replacing an install locally must retire its provenance
#
# Otherwise 'import --force' over a hub skill leaves a lock entry describing
# bytes that are gone — and the replacement, being unattested, would then be
# refused as tampered-with hub content.
# ----------------------------------------------------------------------


def test_importing_over_a_hub_install_retires_its_lock_entry(
    marketplace, tmp_path, attested, monkeypatch
):
    monkeypatch.setattr(skills_cli, "_manager", lambda: marketplace.manager)
    replacement = _write_source(
        tmp_path / "replacement", name="web-research", tier="experimental"
    )

    exit_code = skills_cli.handle(
        argparse.Namespace(
            skill_action="import",
            source=str(replacement),
            name="web-research",
            force=True,
        )
    )

    assert exit_code == skills_cli.EXIT_OK
    assert SkillLock.load(marketplace.skills_root).get("web-research") is None
    assert check_drift(marketplace.skills_root).fatal == ()
    marketplace.manager.reload()
    assert marketplace.manager.load("web-research").security_tier == "experimental"


def test_creating_over_a_hub_install_retires_its_lock_entry(
    marketplace, attested, monkeypatch
):
    monkeypatch.setattr(skills_cli, "_manager", lambda: marketplace.manager)

    exit_code = skills_cli.handle(
        argparse.Namespace(
            skill_action="create",
            name="web-research",
            directory=None,
            description=None,
            with_tools=False,
            force=True,
        )
    )

    assert exit_code == skills_cli.EXIT_OK
    assert SkillLock.load(marketplace.skills_root).get("web-research") is None
    assert check_drift(marketplace.skills_root).fatal == ()


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def _lock_args(**kwargs):
    return argparse.Namespace(
        skill_action="lock", check=False, relock=False, as_json=False, **kwargs
    )


def test_cli_skill_lock_exits_invalid_on_drift_and_ok_when_clean(
    marketplace, unattested, monkeypatch, capsys
):
    monkeypatch.setattr(skills_cli, "_manager", lambda: marketplace.manager)

    assert skills_cli.handle(_lock_args()) == skills_cli.EXIT_OK

    (unattested.path / "notes.md").write_text("scratch\n", encoding="utf-8")
    assert skills_cli.handle(_lock_args()) == skills_cli.EXIT_INVALID
    assert "scratch-skill" in capsys.readouterr().err

    args = _lock_args()
    args.relock = True
    assert skills_cli.handle(args) == skills_cli.EXIT_OK
    assert skills_cli.handle(_lock_args()) == skills_cli.EXIT_OK


def test_cli_skill_lock_relock_reports_a_refusal_as_a_failure(
    marketplace, attested, monkeypatch, capsys
):
    monkeypatch.setattr(skills_cli, "_manager", lambda: marketplace.manager)
    (attested.path / "tools.py").write_text("import socket\n", encoding="utf-8")

    args = _lock_args()
    args.relock = True

    assert skills_cli.handle(args) == skills_cli.EXIT_INVALID
    assert "Refused to re-record" in capsys.readouterr().err


def test_cli_skill_lock_json_carries_every_drift(
    marketplace, unattested, monkeypatch, capsys
):
    import json

    monkeypatch.setattr(skills_cli, "_manager", lambda: marketplace.manager)
    _reversion(unattested.path, "2.0.0")

    args = _lock_args()
    args.as_json = True
    exit_code = skills_cli.handle(args)

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == skills_cli.EXIT_INVALID
    assert payload["clean"] is False
    assert {d["kind"] for d in payload["drifts"]} >= {DRIFT_VERSION, DRIFT_CONTENT}


def test_a_local_skills_remediation_never_tells_you_to_reinstall_from_the_hub(
    marketplace, tmp_path
):
    """An actionable error must name an action the skill can actually take."""
    import shutil

    shutil.copytree(
        _write_source(tmp_path, name="hand-written", tier="experimental"),
        marketplace.skills_root / "hand-written",
    )
    relock(marketplace.skills_root)
    (marketplace.skills_root / "hand-written" / "extra.md").write_text("x", "utf-8")

    drift = check_drift(marketplace.skills_root).for_skill("hand-written")[0]

    assert "gaia skill lock --relock" in drift.remediation
    assert "gaia skill install" not in drift.remediation
