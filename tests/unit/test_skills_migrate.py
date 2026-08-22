# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia skill migrate`` — OpenClaw / Hermes → GAIA (issue #692).

Every test is ``tmp_path``-scoped and points ``GAIA_CONFIG_DIR`` at that tmp
dir, so no test reads or writes the developer's real ``~/.gaia/skills``.

The corpus under ``tests/fixtures/openclaw_skills/`` is 26 **real** published
ClawHub skills (see its ``PROVENANCE.md`` for each source URL, license, and
digest). Those tests assert on properties that hold for any real corpus rather
than on one skill's contents, so re-pinning a fixture cannot silently rot them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gaia.skills.errors import SkillValidationError
from gaia.skills.format import (
    DEFAULT_SECURITY_TIER,
    SKILL_FILENAME,
    parse_skill_file,
    validate_skill,
)
from gaia.skills.migrate import (
    VENDOR_HERMES,
    VENDOR_OPENCLAW,
    detect_vendor,
    find_source_skills,
    install_migrated,
    migrate_skill_dir,
    migrate_text,
)

REAL_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "openclaw_skills"


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_gaia_home(tmp_path, monkeypatch):
    """Point every skills root inside tmp_path (cold state, no real ~/.gaia)."""
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path / "gaia-home"))
    from gaia.skills.manager import reset_default_manager

    reset_default_manager()
    yield
    reset_default_manager()


def write_source(root: Path, name: str, text: str) -> Path:
    """Write a foreign skill directory and return it."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    (directory / SKILL_FILENAME).write_text(text, encoding="utf-8")
    return directory


# The worked OpenClaw example from docs/plans/skill-format.mdx.
OPENCLAW_GIT_STATUS = """---
name: git-status
description: Summarize the working tree status.
version: 0.3.0
metadata:
  openclaw:
    requires:
      bins: [git]
---

# Git Status
Summarize it.
"""

# The worked Hermes example from docs/plans/skill-format.mdx.
HERMES_PDF_EXTRACT = """---
name: pdf-extract
description: Extract structured data from a PDF.
version: 1.0.0
metadata:
  hermes:
    category: documents
    requires:
      tools: [read_file]
---

# Extract from PDF
Steps here.
"""

OPENCLAW_INSTRUCTION_ONLY = """---
name: release-notes
description: Draft release notes from a changelog.
version: 1.2.0
metadata:
  openclaw:
    emoji: "\U0001f4dd"
    homepage: https://example.invalid/notes
    requires:
      env: [CHANGELOG_PATH]
---

# Release Notes
Draft them.
"""


# ----------------------------------------------------------------------
# Round-trip through GAIA's own parser
# ----------------------------------------------------------------------


def test_openclaw_migration_round_trips_through_parse_skill_file(tmp_path):
    """The migrated skill parses back as an equal Skill from disk."""
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)
    assert outcome.migrated, outcome.blockers

    target = install_migrated(outcome, tmp_path / "dest")
    reparsed = parse_skill_file(target)

    assert reparsed == outcome.skill
    assert reparsed.name == "release-notes"
    assert reparsed.description == "Draft release notes from a changelog."
    assert reparsed.body.startswith("# Release Notes")


def test_every_migrated_output_validates(tmp_path):
    """A migration that emits an invalid skill is a bug, not a partial success."""
    sources = tmp_path / "src"
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    write_source(sources, "pdf-extract", HERMES_PDF_EXTRACT)

    for directory in find_source_skills(sources):
        outcome = migrate_skill_dir(directory)
        assert outcome.migrated, outcome.blockers
        # Raises if the emitted skill violates the schema.
        validate_skill(outcome.skill, source=str(directory))


# ----------------------------------------------------------------------
# Vendor fields survive under metadata.<vendor>
# ----------------------------------------------------------------------


def test_unmodeled_vendor_fields_survive_under_metadata_openclaw(tmp_path):
    """Fields GAIA cannot express stay under metadata.openclaw, not dropped."""
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    preserved = outcome.skill.other_metadata["openclaw"]
    assert preserved["emoji"] == "\U0001f4dd"
    assert preserved["homepage"] == "https://example.invalid/notes"

    # ...and they survive a trip to disk and back.
    target = install_migrated(outcome, tmp_path / "dest")
    assert parse_skill_file(target).other_metadata["openclaw"] == preserved


def test_modeled_fields_are_consumed_into_metadata_gaia(tmp_path):
    """A field GAIA models moves into metadata.gaia and leaves the vendor block."""
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.skill.gaia.requirements.env_vars == ["CHANGELOG_PATH"]
    # requires.env was fully expressed, so it no longer sits in the vendor block.
    assert "requires" not in outcome.skill.other_metadata["openclaw"]


def test_hermes_requires_tools_becomes_tools_required(tmp_path):
    """The Hermes path works through the same command, per the plan doc."""
    source = write_source(tmp_path / "src", "pdf-extract", HERMES_PDF_EXTRACT)
    outcome = migrate_skill_dir(source, vendor=VENDOR_HERMES)

    assert outcome.migrated, outcome.blockers
    assert outcome.vendor == VENDOR_HERMES
    assert outcome.skill.gaia.tools_required == ["read_file"]
    # `category` has no GAIA equivalent, so it is preserved rather than dropped.
    assert outcome.skill.other_metadata["hermes"]["category"] == "documents"


def test_hermes_and_openclaw_share_one_command(tmp_path):
    """--from auto routes each vendor to its own mapper."""
    sources = tmp_path / "src"
    write_source(sources, "pdf-extract", HERMES_PDF_EXTRACT)
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    detected = {
        migrate_skill_dir(d).vendor: d.name for d in find_source_skills(sources)
    }
    assert detected == {VENDOR_HERMES: "pdf-extract", VENDOR_OPENCLAW: "release-notes"}


# ----------------------------------------------------------------------
# Trust reset
# ----------------------------------------------------------------------


@pytest.mark.parametrize("claimed", ["verified", "community"])
def test_migrated_skill_is_experimental_even_when_source_claims_otherwise(
    tmp_path, claimed
):
    """Migrated skills re-earn trust — the claimed tier is revoked, not honored."""
    text = f"""---
name: trusted-thing
description: Claims to be trusted already.
version: 1.0.0
metadata:
  gaia:
    security_tier: {claimed}
  openclaw:
    emoji: "x"
---

# Trusted Thing
"""
    source = write_source(tmp_path / "src", "trusted-thing", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.security_tier == DEFAULT_SECURITY_TIER == "experimental"
    assert outcome.claimed_tier == claimed

    # The reset survives the write — reading it back does not resurrect the claim.
    target = install_migrated(outcome, tmp_path / "dest")
    assert parse_skill_file(target).security_tier == "experimental"


def test_migrated_skill_is_experimental_with_no_claim(tmp_path):
    """The default is experimental too, with nothing reported as reset."""
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.skill.security_tier == "experimental"
    assert outcome.claimed_tier is None


# ----------------------------------------------------------------------
# Local capabilities are refused, never downgraded
# ----------------------------------------------------------------------


def test_requires_bins_is_reported_unmigratable_not_downgraded(tmp_path):
    """A skill that shells out to an unpoliced binary is refused, not downgraded."""
    source = write_source(tmp_path / "src", "git-status", OPENCLAW_GIT_STATUS)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert not outcome.migrated
    assert outcome.skill is None, "a refused skill must not produce output"
    assert len(outcome.blockers) == 1
    blocker = outcome.blockers[0]

    # The mapping still happened and is reported...
    assert any("shell:execute:git" in line for line in outcome.mapped)
    # ...and the refusal names the binary and why it cannot be granted: GAIA
    # ships no read-only command policy for 'git', so nothing could gate it.
    assert "shell:execute:git" in blocker
    assert "no read-only command policy" in blocker
    assert "BINARY_POLICIES" in blocker


def test_refused_skill_cannot_be_installed(tmp_path):
    """The refusal is enforced at the install boundary too, not just reported."""
    source = write_source(tmp_path / "src", "git-status", OPENCLAW_GIT_STATUS)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    with pytest.raises(SkillValidationError, match="was not migrated"):
        install_migrated(outcome, tmp_path / "dest")
    assert not (tmp_path / "dest" / "git-status").exists()


@pytest.mark.parametrize(
    "requires, expected",
    [
        ("bins: [git]", "shell:execute:git"),
        ("anyBins: [rg, grep]", "shell:execute:rg"),
        ("config: ['~/.netrc']", "filesystem:read:~/.netrc"),
    ],
)
def test_every_local_capability_domain_is_refused(tmp_path, requires, expected):
    """shell and filesystem both refuse — no domain slips through."""
    text = f"""---
name: local-thing
description: Needs a local capability.
version: 1.0.0
metadata:
  openclaw:
    requires:
      {requires}
---

# Local Thing
"""
    source = write_source(tmp_path / "src", "local-thing", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert not outcome.migrated
    assert expected in outcome.blockers[0]


def test_requires_env_is_a_requirement_not_a_refused_permission(tmp_path):
    """`requires.env` declares what must exist; it grants nothing, so it migrates.

    Guards the boundary of the refusal rule: mapping it to the `env` domain would
    make nearly every real OpenClaw skill unmigratable for a field GAIA already
    models as an advisory requirement.
    """
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.gaia.requirements.env_vars == ["CHANGELOG_PATH"]
    assert outcome.skill.gaia.permissions == []


# ----------------------------------------------------------------------
# The shapes real published skills actually use
# ----------------------------------------------------------------------


def test_un_namespaced_openclaw_fields_are_still_migrated(tmp_path):
    """Fields inlined under `metadata` are read, not silently ignored.

    Regression: an un-namespaced skill shelling out to `op` once migrated with
    zero permissions while the namespaced copy of the *same* skill was refused.
    """
    inline = """---
name: op-read
description: Read a secret from 1Password.
metadata:
  emoji: "k"
  requires:
    bins: [op]
---

# Op Read
"""
    namespaced = """---
name: op-read
description: Read a secret from 1Password.
metadata:
  openclaw:
    emoji: "k"
    requires:
      bins: [op]
---

# Op Read
"""
    verdicts = []
    for index, text in enumerate((inline, namespaced)):
        source = write_source(tmp_path / f"src{index}", "op-read", text)
        verdicts.append(migrate_skill_dir(source, vendor=VENDOR_OPENCLAW))

    assert [v.migrated for v in verdicts] == [False, False]
    for verdict in verdicts:
        assert "shell:execute:op" in verdict.blockers[0]


def test_top_level_openclaw_fields_are_still_migrated(tmp_path):
    """Some real skills skip the `metadata` wrapper entirely."""
    text = """---
name: hoisted
description: Puts its requires at the top level.
requires:
  env: [SOME_TOKEN]
emoji: "z"
---

# Hoisted
"""
    source = write_source(tmp_path / "src", "hoisted", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.gaia.requirements.env_vars == ["SOME_TOKEN"]
    assert any("not the documented" in note for note in outcome.notes)


def test_duplicate_aliases_are_first_wins_not_merged(tmp_path):
    """Merging two aliases of one payload would double-count requirements."""
    text = """---
name: dual
description: Ships the same payload under two aliases.
metadata:
  openclaw:
    requires:
      env: [TOKEN]
  clawdbot:
    requires:
      env: [TOKEN]
---

# Dual
"""
    source = write_source(tmp_path / "src", "dual", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.gaia.requirements.env_vars == ["TOKEN"]
    assert any("preserved unread" in note for note in outcome.notes)
    # The unread alias is kept verbatim rather than dropped.
    assert "clawdbot" in outcome.skill.other_metadata


def test_missing_frontmatter_is_a_blocker_not_a_crash(tmp_path):
    """~5% of published skills have no frontmatter; one must not abort a batch."""
    sources = tmp_path / "src"
    write_source(sources, "bare", "# Just a heading, no frontmatter\n")
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    outcomes = [
        migrate_skill_dir(d, vendor=VENDOR_OPENCLAW)
        for d in find_source_skills(sources)
    ]

    assert sorted(o.migrated for o in outcomes) == [False, True]
    refused = next(o for o in outcomes if not o.migrated)
    assert "no YAML frontmatter" in refused.blockers[0]


def test_non_mapping_vendor_block_is_preserved_not_dropped(tmp_path):
    """`metadata.openclaw: "a string"` has no fields, but must not vanish."""
    text = """---
name: weird
description: Vendor block is a string, not a mapping.
metadata:
  openclaw: "just-a-string"
---

# Weird
"""
    source = write_source(tmp_path / "src", "weird", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.other_metadata == {"openclaw": "just-a-string"}
    assert any("not a mapping" in note for note in outcome.notes)

    target = install_migrated(outcome, tmp_path / "dest")
    assert parse_skill_file(target).other_metadata == {"openclaw": "just-a-string"}


def test_find_source_skills_uses_the_file_it_was_given(tmp_path):
    """Pointing at ./OTHER.md must not silently migrate a sibling SKILL.md."""
    directory = write_source(
        tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY
    )
    other = directory / "OTHER.md"
    other.write_text(HERMES_PDF_EXTRACT, encoding="utf-8")

    assert find_source_skills(other) == [other]
    assert migrate_skill_dir(other).vendor == VENDOR_HERMES


def test_name_and_version_are_coerced_with_a_note(tmp_path):
    """Foreign names and loose versions are normalized, and the change is reported."""
    text = """---
name: My_Skill
description: Has a name GAIA would reject.
version: v2.1
---

# My Skill
"""
    source = write_source(tmp_path / "src", "My_Skill", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.name == "my-skill"
    assert outcome.skill.version == "2.1.0"
    assert any("my-skill" in note for note in outcome.notes)
    assert any("2.1.0" in note for note in outcome.notes)


def test_missing_description_is_a_blocker(tmp_path):
    """The trigger signal cannot be invented, so migration stops."""
    source = write_source(
        tmp_path / "src", "nodesc", "---\nname: nodesc\n---\n\n# No description\n"
    )
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert not outcome.migrated
    assert "description" in outcome.blockers[0]


def test_auto_detect_refuses_a_plain_agent_skills_document(tmp_path):
    """A skill with no vendor namespace needs `import`, not `migrate`."""
    source = write_source(
        tmp_path / "src",
        "plain",
        "---\nname: plain\ndescription: A plain standard skill.\n---\n\n# Plain\n",
    )
    with pytest.raises(SkillValidationError, match="nothing to migrate"):
        migrate_skill_dir(source, vendor="auto")


def test_unknown_vendor_fails_loudly(tmp_path):
    with pytest.raises(SkillValidationError, match="Unknown migration source"):
        migrate_text("---\nname: x\ndescription: y\n---\n", vendor="nope")


def test_support_files_are_carried_across(tmp_path):
    """Scripts the instructions reference must still resolve after migration."""
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    (source / "template.md").write_text("# template\n", encoding="utf-8")
    (source / "scripts").mkdir()
    (source / "scripts" / "run.sh").write_text("echo hi\n", encoding="utf-8")

    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)
    target = install_migrated(outcome, tmp_path / "dest")

    assert (target / "template.md").read_text() == "# template\n"
    assert (target / "scripts" / "run.sh").is_file()


def test_install_refuses_to_clobber_without_force(tmp_path):
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    destination = tmp_path / "dest"

    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)
    install_migrated(outcome, destination)

    with pytest.raises(SkillValidationError, match="already installed"):
        install_migrated(migrate_skill_dir(source, vendor=VENDOR_OPENCLAW), destination)

    install_migrated(
        migrate_skill_dir(source, vendor=VENDOR_OPENCLAW), destination, force=True
    )


def test_detect_vendor_probes_namespace_then_shape():
    assert detect_vendor({"metadata": {"openclaw": {}}}) == VENDOR_OPENCLAW
    assert detect_vendor({"metadata": {"clawdis": {}}}) == VENDOR_OPENCLAW
    assert detect_vendor({"metadata": {"hermes": {}}}) == VENDOR_HERMES
    assert detect_vendor({"clawdbot": {}}) == VENDOR_OPENCLAW
    assert detect_vendor({"requires": {"bins": ["git"]}}) == VENDOR_OPENCLAW
    assert detect_vendor({"name": "plain", "description": "d"}) is None


# ----------------------------------------------------------------------
# The real published corpus
# ----------------------------------------------------------------------


def _real_fixture_dirs() -> list[Path]:
    if not REAL_FIXTURES.is_dir():  # pragma: no cover - guards a bad checkout
        return []
    return sorted(p for p in REAL_FIXTURES.iterdir() if p.is_dir())


def test_the_real_corpus_is_actually_present():
    """Guards the acceptance criterion: ≥10 real skills, with provenance."""
    assert len(_real_fixture_dirs()) >= 10
    assert (REAL_FIXTURES / "PROVENANCE.md").is_file()


@pytest.mark.parametrize("skill_dir", _real_fixture_dirs(), ids=lambda p: p.name)
def test_real_openclaw_skill_migrates_or_reports_why(tmp_path, skill_dir):
    """Every real skill reaches a defined verdict — never a crash, never both.

    A migrated one validates and round-trips; a refused one produces no output
    and says why. There is no third outcome, and in particular no skill that
    "succeeds" by quietly losing a capability it declared.
    """
    outcome = migrate_skill_dir(skill_dir, vendor=VENDOR_OPENCLAW)

    if outcome.migrated:
        validate_skill(outcome.skill, source=str(skill_dir))
        target = install_migrated(outcome, tmp_path / "dest")
        assert parse_skill_file(target) == outcome.skill
        assert outcome.skill.security_tier == "experimental"
        # v1 only ever emits instruction-only or connector-bridged skills.
        for permission in outcome.skill.parsed_permissions():
            assert not permission.is_local_capability
    else:
        assert outcome.skill is None
        assert outcome.blockers
        assert all(blocker.strip() for blocker in outcome.blockers)


# ----------------------------------------------------------------------
# The shipped CLI verb
# ----------------------------------------------------------------------


@pytest.fixture
def run_cli(tmp_path, monkeypatch, capsys):
    """Parse and dispatch a real ``gaia skill migrate …`` in-process."""
    workdir = tmp_path / "workdir"
    workdir.mkdir(exist_ok=True)
    monkeypatch.chdir(workdir)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "fake-home"))
    (tmp_path / "fake-home").mkdir(exist_ok=True)

    def _run(*args: str):
        import argparse

        from gaia.skills import cli as skills_cli

        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="action")
        skills_cli.add_subparser(subparsers)
        parsed = parser.parse_args(["skill", "migrate", *args])
        rc = skills_cli.handle(parsed)
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    return _run


def test_cli_migrate_installs_and_reports(run_cli, tmp_path):
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    rc, out, _ = run_cli(str(source), "--from", "openclaw")
    assert rc == 0, out
    assert "Migrated 1/1" in out
    assert "experimental" in out

    installed = tmp_path / "gaia-home" / "skills" / "release-notes"
    assert parse_skill_file(installed).name == "release-notes"


def test_cli_migrate_exits_4_when_a_skill_is_refused(run_cli, tmp_path):
    """A refused skill is a non-zero exit so scripts can branch on it."""
    sources = tmp_path / "src"
    write_source(sources, "git-status", OPENCLAW_GIT_STATUS)
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    rc, out, err = run_cli(str(sources), "--from", "openclaw")

    assert rc == 4
    assert "Migrated 1/2" in out
    assert "no read-only command policy" in out
    assert "refused rather than silently stripped" in err
    # The good one still installed; one refusal does not block the batch.
    assert (tmp_path / "gaia-home" / "skills" / "release-notes").is_dir()
    assert not (tmp_path / "gaia-home" / "skills" / "git-status").exists()


def test_cli_migrate_dry_run_writes_nothing(run_cli, tmp_path):
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    rc, out, _ = run_cli(str(source), "--from", "openclaw", "--dry-run")

    assert rc == 0
    assert "Would migrate 1/1" in out
    assert not (tmp_path / "gaia-home" / "skills" / "release-notes").exists()


def test_cli_migrate_json_report(run_cli, tmp_path):
    import json

    sources = tmp_path / "src"
    write_source(sources, "git-status", OPENCLAW_GIT_STATUS)
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    rc, out, _ = run_cli(str(sources), "--from", "openclaw", "--json")

    assert rc == 4
    payload = json.loads(out)
    assert payload["total"] == 2
    assert payload["migrated"] == 1
    assert payload["unmigratable"] == 1
    by_name = {entry["name"]: entry for entry in payload["skills"]}
    assert by_name["release-notes"]["security_tier"] == "experimental"
    assert by_name["git-status"]["migrated"] is False
    assert by_name["git-status"]["blockers"]


def test_cli_migrate_reports_a_collision_without_hiding_the_batch(run_cli, tmp_path):
    """An install collision on one skill must not suppress the others' report."""
    sources = tmp_path / "src"
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    write_source(sources, "pdf-extract", HERMES_PDF_EXTRACT)

    # Pre-install one so the second pass collides on it.
    rc, _, _ = run_cli(str(sources / "release-notes"), "--from", "openclaw")
    assert rc == 0

    rc, out, err = run_cli(str(sources))

    assert rc == 4
    # The collision is named, and distinguished from being unmigratable.
    assert "could not be written" in err
    assert "release-notes" in err
    assert "unmigratable" not in out
    # The other skill still migrated and installed.
    assert (tmp_path / "gaia-home" / "skills" / "pdf-extract").is_dir()


def test_cli_migrate_auto_detect_miss_does_not_abort_the_batch(run_cli, tmp_path):
    """A real collection mixes plain Agent-Skills docs in; one must not abort it."""
    sources = tmp_path / "src"
    write_source(
        sources,
        "plain",
        "---\nname: plain\ndescription: A plain standard skill.\n---\n\n# Plain\n",
    )
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)

    # Default --from auto, which is what the docs' whole-collection example uses.
    rc, out, _ = run_cli(str(sources))

    assert rc == 4
    # The undetectable one is a per-skill blocker, named in the report...
    assert "plain" in out
    assert "nothing to migrate" in out
    # ...and the rest of the collection still migrated.
    assert "1/2" in out
    assert (tmp_path / "gaia-home" / "skills" / "release-notes").is_dir()


def test_cli_migrate_out_directory(run_cli, tmp_path):
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    out_dir = tmp_path / "elsewhere"

    rc, _, _ = run_cli(str(source), "--from", "openclaw", "--out", str(out_dir))

    assert rc == 0
    assert (out_dir / "release-notes" / SKILL_FILENAME).is_file()
    assert not (tmp_path / "gaia-home" / "skills" / "release-notes").exists()


def test_cli_migrate_rejects_name_for_a_batch(run_cli, tmp_path):
    sources = tmp_path / "src"
    write_source(sources, "a-skill", OPENCLAW_INSTRUCTION_ONLY)
    write_source(sources, "b-skill", HERMES_PDF_EXTRACT)

    rc, _, err = run_cli(str(sources), "--name", "renamed")

    assert rc == 2
    assert "--name applies to a single skill" in err


def _subprocess_env(tmp_path: Path) -> dict[str, str]:
    """A minimal env for the real CLI, with a home directory on every platform.

    Windows resolves ``Path.home()`` from ``USERPROFILE``, not ``HOME``; without
    it ``import gaia`` dies before the CLI ever parses an argument.
    """
    import os

    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin"),
        "HOME": str(tmp_path / "fake-home"),
        "USERPROFILE": str(tmp_path / "fake-home"),
        "GAIA_CONFIG_DIR": str(tmp_path / "gaia-home"),
        "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
        "GAIA_MEMORY_DISABLED": "1",
    }
    for passthrough in ("SYSTEMROOT", "TEMP", "TMP", "PATHEXT"):
        if passthrough in os.environ:
            env[passthrough] = os.environ[passthrough]
    return env


def test_real_cli_migrate_end_to_end(tmp_path):
    """`gaia skill migrate` through the real entry point a user types."""
    import subprocess
    import sys

    (tmp_path / "fake-home").mkdir()
    (tmp_path / "gaia-home" / "skills").mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    env = _subprocess_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "gaia.cli", "skill", "migrate", str(source)],
        capture_output=True,
        text=True,
        cwd=workdir,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Migrated 1/1" in result.stdout
    installed = tmp_path / "gaia-home" / "skills" / "release-notes" / SKILL_FILENAME
    assert installed.is_file()

    info = subprocess.run(
        [sys.executable, "-m", "gaia.cli", "skill", "info", "release-notes"],
        capture_output=True,
        text=True,
        cwd=workdir,
        env=env,
        check=False,
    )
    assert info.returncode == 0, info.stderr
    assert "security tier: experimental" in info.stdout


def test_real_corpus_covers_both_verdicts():
    """The corpus exercises the refusal path, not just the happy one.

    If every real skill migrated cleanly, the unenforceable-permission refusal
    would be untested against real data and could rot unnoticed.
    """
    outcomes = [
        migrate_skill_dir(d, vendor=VENDOR_OPENCLAW) for d in _real_fixture_dirs()
    ]
    migrated = [o for o in outcomes if o.migrated]
    refused = [o for o in outcomes if not o.migrated]

    assert len(migrated) >= 5, "expected several real skills to migrate cleanly"
    assert any(
        "no read-only command policy" in b for o in refused for b in o.blockers
    ), "expected at least one real skill refused for an unpoliced binary"


# ----------------------------------------------------------------------
# Malformed input — every shape is a blocker, never a crash
# ----------------------------------------------------------------------


MALFORMED_SOURCES = [
    pytest.param("", "no YAML frontmatter", id="empty-file"),
    pytest.param("   \n\n\n", "no YAML frontmatter", id="whitespace-only"),
    pytest.param("# Heading only\n", "no YAML frontmatter", id="no-frontmatter"),
    pytest.param(
        "---\nname: x\ndescription: y\n\n# Never closed\n",
        "no YAML frontmatter",
        id="unterminated-frontmatter",
    ),
    pytest.param("---\n---\n\n# Nothing\n", "no YAML frontmatter", id="empty-fences"),
    pytest.param(
        "---\nname: x\n\tdescription: tab-indented\n---\n\n# Tab\n",
        "YAML frontmatter is invalid",
        id="broken-yaml-tab",
    ),
    pytest.param(
        "---\nname: x\ndescription: a: b: c\n---\n\n# Colons\n",
        "YAML frontmatter is invalid",
        id="broken-yaml-unquoted-colon",
    ),
    pytest.param(
        "---\n- one\n- two\n---\n\n# List\n",
        "frontmatter must be a YAML mapping",
        id="frontmatter-is-a-list",
    ),
    pytest.param(
        "---\njust a bare string\n---\n\n# Scalar\n",
        "frontmatter must be a YAML mapping",
        id="frontmatter-is-a-scalar",
    ),
]


@pytest.mark.parametrize("text, expected", MALFORMED_SOURCES)
@pytest.mark.parametrize("vendor", [VENDOR_OPENCLAW, "auto"])
def test_malformed_source_is_a_blocker_never_a_crash(tmp_path, text, expected, vendor):
    """Broken YAML never crashes and never half-migrates — it blocks, with a reason.

    Both ``--from openclaw`` and ``--from auto`` must reach the same verdict: an
    unparseable document has no vendor to detect either, so the auto path must
    not fall through to its "nothing to migrate" raise.
    """
    source = write_source(tmp_path / "src", "bad", text)
    outcome = migrate_skill_dir(source, vendor=vendor)

    assert not outcome.migrated
    assert outcome.skill is None, "a malformed source must not produce a half-skill"
    assert outcome.blockers, "a refusal with no stated reason is a silent failure"
    assert expected in outcome.blockers[0]
    # Actionable, per the fail-loudly rule: says where to look next.
    assert "skill-format" in outcome.blockers[0]
    # Nothing was written anywhere.
    assert not (tmp_path / "dest").exists()


def test_non_utf8_source_fails_loudly_naming_the_file(tmp_path):
    """A latin-1 SKILL.md is unreadable, not silently decoded with replacements."""
    directory = tmp_path / "src" / "latin1"
    directory.mkdir(parents=True)
    (directory / SKILL_FILENAME).write_bytes(
        "---\nname: latin\ndescription: caf\xe9.\nmetadata:\n"
        "  openclaw: {emoji: x}\n---\n\n# Body\n".encode("latin-1")
    )

    with pytest.raises(SkillValidationError) as excinfo:
        migrate_skill_dir(directory, vendor=VENDOR_OPENCLAW)

    message = str(excinfo.value)
    assert SKILL_FILENAME in message
    assert "UTF-8" in message


@pytest.mark.parametrize(
    "frontmatter_line",
    [
        pytest.param("on: push", id="bareword-key-parses-as-bool"),
        pytest.param("1: one", id="numeric-key"),
        pytest.param("2024-01-01: released", id="date-key"),
    ],
)
def test_non_string_frontmatter_keys_do_not_crash_the_migrator(
    tmp_path, frontmatter_line
):
    """YAML 1.1 turns a bare ``on:`` key into a bool; joining that set raised.

    Regression: sorting/joining the leftover key set assumed every key was a
    string, so one such key aborted the whole batch with a TypeError that no
    handler caught — not a per-skill blocker, a traceback.
    """
    text = f"""---
name: odd-keys
description: Carries a frontmatter key YAML does not parse as a string.
{frontmatter_line}
metadata:
  openclaw:
    emoji: "x"
---

# Odd Keys
"""
    source = write_source(tmp_path / "src", "odd-keys", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert any("not part of the Agent Skills base" in n for n in outcome.notes)
    # The preserved key survives the round-trip to disk.
    target = install_migrated(outcome, tmp_path / "dest")
    assert parse_skill_file(target) == outcome.skill


def test_non_string_keys_inside_a_vendor_requires_block_do_not_crash(tmp_path):
    """Same hazard one level down, in the leftover-``requires``-keys note."""
    text = """---
name: odd-requires
description: A requires block with a bareword bool key.
metadata:
  openclaw:
    requires:
      env: [TOKEN]
      on: push
---

# Odd Requires
"""
    source = write_source(tmp_path / "src", "odd-requires", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    assert outcome.migrated, outcome.blockers
    assert outcome.skill.gaia.requirements.env_vars == ["TOKEN"]
    assert any("have no GAIA equivalent" in n for n in outcome.notes)


# ----------------------------------------------------------------------
# Non-mapping vendor blocks
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        pytest.param("metadata:\n  openclaw:", id="vendor-block-is-null"),
        pytest.param("metadata:\n  openclaw: [a, b]", id="vendor-block-is-a-list"),
        pytest.param("metadata:\n  openclaw: []", id="vendor-block-is-an-empty-list"),
        pytest.param("metadata:\n  openclaw: 42", id="vendor-block-is-an-int"),
        pytest.param("metadata: nonsense", id="metadata-is-a-string"),
        pytest.param("metadata: [a, b]", id="metadata-is-a-list"),
        pytest.param(
            "metadata:\n  gaia: nope\n  openclaw: {emoji: x}", id="gaia-is-a-string"
        ),
        pytest.param("openclaw: nonsense", id="top-level-namespace-is-a-string"),
        pytest.param(
            "metadata:\n  openclaw:\n    requires: everything",
            id="requires-is-a-string",
        ),
        pytest.param(
            "metadata:\n  openclaw:\n    envVars: TOKEN", id="envVars-is-a-string"
        ),
        pytest.param(
            "metadata:\n  openclaw:\n    install: brew install thing",
            id="install-is-a-string",
        ),
        pytest.param(
            "metadata:\n  openclaw:\n    install: [brew, go]",
            id="install-entries-are-strings",
        ),
    ],
)
def test_non_mapping_vendor_shapes_reach_a_verdict_without_raising(tmp_path, block):
    """A vendor block of the wrong YAML type must not raise TypeError/AttributeError.

    The mapper indexes these blocks as dicts everywhere; a published skill that
    ships a string, list, or null where a mapping belongs must still reach a
    defined verdict rather than a traceback.
    """
    text = f"""---
name: odd-shape
description: A vendor block whose YAML type is not a mapping.
{block}
---

# Odd Shape
"""
    source = write_source(tmp_path / "src", "odd-shape", text)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    # Either verdict is acceptable; a crash and a half-migration are not.
    assert outcome.migrated or outcome.blockers
    if outcome.migrated:
        validate_skill(outcome.skill, source=str(source))
        target = install_migrated(outcome, tmp_path / "dest")
        assert parse_skill_file(target) == outcome.skill
    else:
        assert outcome.skill is None


# ----------------------------------------------------------------------
# Collisions never clobber
# ----------------------------------------------------------------------


def test_a_refused_collision_leaves_the_installed_skill_byte_identical(tmp_path):
    """The defined behavior is refuse-with-an-error; prove nothing was touched."""
    destination = tmp_path / "dest"
    existing = destination / "release-notes"
    existing.mkdir(parents=True)
    (existing / SKILL_FILENAME).write_text("PRECIOUS ORIGINAL\n", encoding="utf-8")
    (existing / "keep-me.txt").write_text("hand-edited\n", encoding="utf-8")

    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)

    with pytest.raises(SkillValidationError, match="already installed"):
        install_migrated(outcome, destination)

    # Not a partial overwrite, not an empty directory — untouched.
    assert (existing / SKILL_FILENAME).read_text() == "PRECIOUS ORIGINAL\n"
    assert (existing / "keep-me.txt").read_text() == "hand-edited\n"
    assert sorted(p.name for p in existing.iterdir()) == [SKILL_FILENAME, "keep-me.txt"]

    # ...and --force is the documented escape hatch, which does replace it.
    install_migrated(outcome, destination, force=True)
    assert parse_skill_file(existing).name == "release-notes"
    assert not (existing / "keep-me.txt").exists()


def test_force_install_over_the_source_is_refused_not_destructive(tmp_path):
    """--force replaces a directory; over the source that would delete the source.

    Reachable as `gaia skill migrate <dir-inside-the-destination> --force`, where
    the target resolves to the source's own directory.
    """
    sources = tmp_path / "src"
    source = write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    (source / "helper.py").write_text("print('important')\n", encoding="utf-8")

    outcome = migrate_skill_dir(source, vendor=VENDOR_OPENCLAW)
    with pytest.raises(SkillValidationError, match="on top of its own source"):
        install_migrated(outcome, sources, force=True)

    assert (source / "helper.py").read_text(encoding="utf-8") == "print('important')\n"
    assert (source / SKILL_FILENAME).read_text(
        encoding="utf-8"
    ) == OPENCLAW_INSTRUCTION_ONLY


def test_cli_migrate_collision_does_not_rewrite_the_installed_skill(run_cli, tmp_path):
    """End of the same guarantee, through the verb a user actually types."""
    source = write_source(tmp_path / "src", "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    installed = tmp_path / "gaia-home" / "skills" / "release-notes"

    assert run_cli(str(source), "--from", "openclaw")[0] == 0
    (installed / "hand-edit.txt").write_text("mine\n", encoding="utf-8")
    before = (installed / SKILL_FILENAME).read_bytes()

    rc, _, err = run_cli(str(source), "--from", "openclaw")

    assert rc == 4
    assert "could not be written" in err
    assert (installed / SKILL_FILENAME).read_bytes() == before
    assert (installed / "hand-edit.txt").read_text() == "mine\n"


# ----------------------------------------------------------------------
# Batch partial failure — one bad skill never takes the batch down
# ----------------------------------------------------------------------


def test_cli_migrate_batch_survives_every_kind_of_bad_skill(run_cli, tmp_path):
    """The headline batch guarantee, against one source of each failure mode.

    A collection cloned off ClawHub really does mix all of these together. Each
    bad one must be reported against its own name and the good ones must still
    install — never an abort that reports nothing for anybody.
    """
    sources = tmp_path / "src"
    # Good.
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    write_source(sources, "pdf-extract", HERMES_PDF_EXTRACT)
    write_source(
        sources,
        "odd-keys",
        "---\nname: odd-keys\ndescription: Bareword key parses as a bool.\n"
        'on: push\nmetadata:\n  openclaw:\n    emoji: "x"\n---\n\n# Odd Keys\n',
    )
    # Bad, one of each kind.
    write_source(sources, "git-status", OPENCLAW_GIT_STATUS)  # refused permission
    write_source(sources, "bare", "# No frontmatter at all\n")
    write_source(sources, "broken", "---\nname: x\n\tdescription: tab\n---\n\n# B\n")
    write_source(
        sources,
        "plain",
        "---\nname: plain\ndescription: A plain standard skill.\n---\n\n# Plain\n",
    )
    latin1 = sources / "latin1"
    latin1.mkdir(parents=True)
    (latin1 / SKILL_FILENAME).write_bytes(
        "---\nname: latin\ndescription: caf\xe9.\nmetadata:\n"
        "  openclaw: {emoji: x}\n---\n\n# Body\n".encode("latin-1")
    )

    rc, out, err = run_cli(str(sources))

    assert rc == 4
    # Every source reached a verdict — none was skipped by an abort.
    assert "Migrated 3/8" in out
    # The good ones are on disk.
    skills_root = tmp_path / "gaia-home" / "skills"
    assert (skills_root / "release-notes").is_dir()
    assert (skills_root / "pdf-extract").is_dir()
    assert (skills_root / "odd-keys").is_dir()
    # The bad ones are not, and each is named in the report.
    for name in ("git-status", "bare", "broken", "plain", "latin1"):
        assert not (skills_root / name).exists()
        assert name in out, f"{name} was not reported"
    assert "could not be migrated" in err


def test_cli_migrate_json_attributes_each_failure_to_its_own_skill(run_cli, tmp_path):
    """Per-skill reporting, not one aggregate error for the batch."""
    import json

    sources = tmp_path / "src"
    write_source(sources, "release-notes", OPENCLAW_INSTRUCTION_ONLY)
    write_source(sources, "bare", "# No frontmatter\n")
    write_source(sources, "broken", "---\nname: x\n\tdescription: tab\n---\n\n# B\n")

    rc, out, _ = run_cli(str(sources), "--json")

    assert rc == 4
    payload = json.loads(out)
    assert (payload["total"], payload["migrated"], payload["unmigratable"]) == (3, 1, 2)

    by_name = {entry["name"]: entry for entry in payload["skills"]}
    assert by_name["release-notes"]["migrated"] is True
    # Each failure carries its own distinct reason, keyed to its own source.
    assert "no YAML frontmatter" in by_name["bare"]["blockers"][0]
    assert "YAML frontmatter is invalid" in by_name["broken"]["blockers"][0]
    assert by_name["bare"]["source"] != by_name["broken"]["source"]


def test_cli_migrate_the_whole_real_corpus_in_one_batch(run_cli):
    """The documented whole-collection command, over all 26 real published skills.

    This is the only test that runs auto-detection across the real corpus in a
    single batch — the exact shape of the primary use case, and the one that
    would catch a regression where one undetectable source aborts the rest.
    """
    import json

    rc, out, _ = run_cli(str(REAL_FIXTURES), "--dry-run", "--json")

    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert payload["total"] == len(_real_fixture_dirs()) >= 10
    assert rc in (0, 4)

    # Every real skill reached a verdict; none was lost to an abort.
    for entry in payload["skills"]:
        assert entry["migrated"] or entry["blockers"], entry["name"]
        assert all(b.strip() for b in entry["blockers"])
    assert payload["migrated"] >= 1, "auto-detection migrated nothing from real data"
    # The corpus really does contain sources auto-detection cannot place; they
    # must be per-skill blockers, not an exception that hides the batch.
    assert any(
        "nothing to migrate" in b for e in payload["skills"] for b in e["blockers"]
    ), "corpus no longer exercises the auto-detect miss — re-pin a fixture"
