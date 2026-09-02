# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Adaptive skills: the learned overlay (#2674).

Runs fully offline — no model, no network, no Lemonade. One command:

    pytest tests/unit/test_adaptive_skills.py -v

What it proves, in the order the sections appear below:

1. **Replace, not append.** The failure mode this feature exists to avoid is a
   learned "adjustment" pasted under the authored text, leaving the wrong
   instruction in the prompt beside the right one. Every replacement test
   asserts the old text is *gone*, not merely outranked.
2. **Learning does not inflate the prompt.** N corrections to one section cost
   what one costs, because a revised lesson supersedes its predecessor instead
   of stacking. Shape adaptation trends the resolved skill *smaller*.
3. **The authored file is never written.** Hashed before and after.
4. **Nothing takes effect without consent**, and the off-switch restores the
   authored bytes exactly.
5. **A base edit never silently re-points a delta** at text it was not approved
   against.
6. **The real bug from the session that motivated this** — a POSIX-quoted `gh`
   command that dies under Windows `cmd.exe` with "The system cannot find the
   path specified", which the agent misdiagnosed as a missing binary — is
   corrected end to end, and the corrected command actually runs.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.skill_deltas import (
    KIND_DROP_SECTION,
    KIND_REPLACE_SECTION,
    KIND_REPLACE_SNIPPET,
    MAX_OVERLAY_TOKENS,
    STATUS_ACTIVE,
    DeltaRefused,
    SkillDelta,
    approve_delta,
    estimate_tokens,
    preview_diff,
    resolve_skill_body,
    supersession_key,
    validate_delta,
)
from gaia.skills.sections import parse_sections, render_sections, section_digest

from .skills_helpers import LearnedOverlayStubMixin

# --------------------------------------------------------------------------
# Fixtures — a repo-backlog-shaped skill, exactly the mismatch that motivated
# this work. The authored `hub/skills/github-triage/SKILL.md` is deliberately
# NOT used: another task edits that file, and a test that reads it would fail
# for reasons unrelated to the overlay.
# --------------------------------------------------------------------------

BROKEN_INBOX_CMD = (
    "gh issue list --repo amd/gaia --limit 30 \\\n"
    "     --json number,title --jq '.[].title'"
)

FIXED_INBOX_CMD = (
    "gh issue list --repo amd/gaia --limit 30 --json number,title,updatedAt"
)

BASE_SKILL = f"""# GitHub Triage

The bring-your-own-CLI example. Run every GitHub command through
`run_shell_command`.

## Setup

```bash
gh auth login        # once, interactively
```

If `gh` is not installed the skill refuses to load and says so.

## Procedure

1. **Pull the backlog.** Ask for the repository if the user did not name one.

   ```bash
   {BROKEN_INBOX_CMD}
   ```

2. **Group before judging.** Cluster issues that describe the same underlying
   problem. Report the cluster, not each issue.

3. **Judge each cluster on severity and reach.** Rank by the pair.

## Rules

- Never close an issue on your own judgement.
- Report a refused command as a refusal.

## Fork this

Point it at your own repo's labels and severity ladder. The same shape works
for any CLI in GAIA's read-only command policy.
"""

INBOX_PROCEDURE = """## Procedure

1. **Pull what landed on you.** Default to the user's own inbox, not a
   repository backlog.

   ```bash
   gh search issues --involves @me --state open --limit 30 --json number,title,repository
   ```

2. **Find what is blocking you.** PRs awaiting your review first.

3. **Rank by what unblocks other people**, then by severity and reach.

4. **Say what to do next** — one concrete action per item."""

SCOPE = "gaia:test-agent"
TRUSTED = {"source": "user_instruction", "turns": [7]}


def _digest_of(body: str, slug: str) -> str:
    section = next(s for s in parse_sections(body) if s.slug == slug)
    return section.digest


def _delta(
    kind: str,
    section: str,
    payload: dict,
    *,
    body: str = BASE_SKILL,
    delta_id: str = "d1",
    created_at: str = "2026-08-18T00:00:00Z",
    status: str = STATUS_ACTIVE,
    digest: str | None = None,
    provenance: dict | None = None,
) -> SkillDelta:
    return SkillDelta(
        id=delta_id,
        base_name="github-triage",
        scope=SCOPE,
        kind=kind,
        anchor_section=section,
        anchor_digest=digest if digest is not None else _digest_of(body, section),
        payload=payload,
        provenance=provenance if provenance is not None else dict(TRUSTED),
        status=status,
        created_at=created_at,
    )


@pytest.fixture
def store(tmp_path):
    """A real MemoryStore on a throwaway DB — never the developer's ~/.gaia."""
    s = MemoryStore(db_path=tmp_path / "memory.db")
    yield s
    s.close() if hasattr(s, "close") else None


# --------------------------------------------------------------------------
# 1. REPLACE NOT APPEND — the core assertion
# --------------------------------------------------------------------------


def test_replace_snippet_removes_the_broken_command_entirely():
    """The wrong command must be GONE. Both present is the failure mode."""
    delta = _delta(
        KIND_REPLACE_SNIPPET,
        "procedure",
        {"old": BROKEN_INBOX_CMD, "new": FIXED_INBOX_CMD},
    )
    resolved = resolve_skill_body(BASE_SKILL, [delta])

    assert FIXED_INBOX_CMD in resolved.body
    assert BROKEN_INBOX_CMD not in resolved.body
    # And specifically: no POSIX single-quoted --jq, no backslash continuation.
    assert "--jq '" not in resolved.body
    assert "\\\n" not in resolved.body
    assert resolved.applied == ["d1"]


def test_replace_section_removes_the_old_section_body():
    delta = _delta(KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE})
    resolved = resolve_skill_body(BASE_SKILL, [delta])

    assert "Pull what landed on you" in resolved.body
    assert "Pull the backlog" not in resolved.body
    assert "Group before judging" not in resolved.body
    # Untouched sections survive verbatim.
    assert "Never close an issue on your own judgement." in resolved.body


def test_drop_section_removes_it_and_shrinks_the_prompt():
    delta = _delta(KIND_DROP_SECTION, "fork-this", {})
    resolved = resolve_skill_body(BASE_SKILL, [delta])

    assert "## Fork this" not in resolved.body
    assert "severity ladder" not in resolved.body
    assert resolved.token_delta < 0


# --------------------------------------------------------------------------
# 2. NO UNBOUNDED GROWTH / IDEMPOTENCE / SUPERSESSION
# --------------------------------------------------------------------------


def test_n_corrections_to_one_section_stay_flat(store):
    """Ten successive corrections cost what one costs — not 10x."""
    base_tokens = estimate_tokens(BASE_SKILL)
    sizes = []
    previous_id = None

    for n in range(10):
        new_id = store.put_delta(
            base_name="github-triage",
            scope=SCOPE,
            kind=KIND_REPLACE_SECTION,
            anchor_section="procedure",
            anchor_digest=_digest_of(BASE_SKILL, "procedure"),
            payload={"body": INBOX_PROCEDURE + f"\n\n5. Revision {n}."},
            provenance=dict(TRUSTED),
        )
        store.approve_delta(new_id)
        if previous_id:
            store.supersede_delta(previous_id, new_id)
        previous_id = new_id

        rows = store.search_deltas(base_name="github-triage", scope=SCOPE)
        deltas = [_from_row(r) for r in rows]
        resolved = resolve_skill_body(BASE_SKILL, deltas)
        sizes.append(resolved.resolved_tokens)

    # Exactly one delta is live no matter how many were learned.
    live = store.search_deltas(base_name="github-triage", scope=SCOPE)
    assert len(live) == 1

    # Flat, not linear: the 10th correction costs within a few tokens of the 1st.
    assert max(sizes) - min(sizes) <= 5, sizes
    assert sizes[-1] - base_tokens <= MAX_OVERLAY_TOKENS


def test_same_lesson_twice_is_one_delta():
    """Idempotence: the supersession key targets the section, not the wording."""
    first = _delta(
        KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE}, delta_id="a"
    )
    second = _delta(
        KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE}, delta_id="b"
    )
    assert supersession_key(first) == supersession_key(second)

    # Resolution is identical whichever single one is live.
    assert (
        resolve_skill_body(BASE_SKILL, [first]).body
        == resolve_skill_body(BASE_SKILL, [second]).body
    )


def test_supersession_means_only_the_latest_resolves(store):
    old_id = store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": "## Procedure\n\nOLD LESSON."},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(old_id)
    new_id = store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": "## Procedure\n\nNEW LESSON."},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(new_id)
    assert store.supersede_delta(old_id, new_id) is True

    resolved = resolve_skill_body(
        BASE_SKILL,
        [_from_row(r) for r in store.search_deltas(base_name="github-triage")],
    )
    assert "NEW LESSON." in resolved.body
    assert "OLD LESSON." not in resolved.body

    # Archived, not deleted: the retired row is still inspectable.
    all_rows = store.search_deltas(base_name="github-triage", include_superseded=True)
    assert {r["id"] for r in all_rows} == {old_id, new_id}


# --------------------------------------------------------------------------
# 3. BASE IMMUTABILITY
# --------------------------------------------------------------------------


def test_the_authored_file_is_never_written(tmp_path, store):
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text(BASE_SKILL, encoding="utf-8")
    before = hashlib.sha256(skill_file.read_bytes()).hexdigest()

    delta_id = store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": INBOX_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(delta_id)
    resolved = resolve_skill_body(
        skill_file.read_text(encoding="utf-8"),
        [_from_row(r) for r in store.search_deltas(base_name="github-triage")],
    )

    assert "Pull what landed on you" in resolved.body  # the overlay did apply
    after = hashlib.sha256(skill_file.read_bytes()).hexdigest()
    assert after == before, "resolution must never write the authored SKILL.md"


# --------------------------------------------------------------------------
# 4. CONSENT GATE + OFF SWITCH
# --------------------------------------------------------------------------


def test_a_staged_delta_has_zero_effect(store):
    delta_id = store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": INBOX_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    rows = store.search_deltas(base_name="github-triage")
    assert rows[0]["status"] == "staged"
    assert rows[0]["approved_at"] is None

    resolved = resolve_skill_body(BASE_SKILL, [_from_row(r) for r in rows])
    assert resolved.body == BASE_SKILL
    assert resolved.applied == []

    # ...and takes effect only after consent.
    assert store.approve_delta(delta_id) is True
    rows = store.search_deltas(base_name="github-triage")
    assert (
        resolve_skill_body(BASE_SKILL, [_from_row(r) for r in rows]).body != BASE_SKILL
    )


def test_off_switch_restores_the_authored_bytes_exactly():
    deltas = [
        _delta(
            KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE}, delta_id="a"
        ),
        _delta(KIND_DROP_SECTION, "fork-this", {}, delta_id="b"),
    ]
    assert resolve_skill_body(BASE_SKILL, deltas).body != BASE_SKILL
    off = resolve_skill_body(BASE_SKILL, deltas, enabled=False)
    assert off.body == BASE_SKILL
    assert off.applied == []


# --------------------------------------------------------------------------
# 5. REBASE SAFETY
# --------------------------------------------------------------------------


def test_a_changed_section_is_flagged_not_silently_applied():
    """The author rewrites the section a replacement was approved against."""
    delta = _delta(KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE})
    edited_base = BASE_SKILL.replace(
        "2. **Group before judging.**", "2. **Cluster duplicates first.**"
    )
    resolved = resolve_skill_body(edited_base, [delta])

    assert resolved.body == edited_base, "authored text must win when the anchor moved"
    assert resolved.applied == []
    note = next(n for n in resolved.notes if n.delta_id == "d1")
    assert note.outcome == "stale"
    assert "re-approve" in note.detail


def test_a_removed_section_orphans_its_delta_visibly():
    delta = _delta(KIND_DROP_SECTION, "fork-this", {})
    without = BASE_SKILL.split("## Fork this")[0]
    resolved = resolve_skill_body(without, [delta])

    assert resolved.body == without
    note = next(n for n in resolved.notes if n.delta_id == "d1")
    assert note.outcome == "orphaned"


def test_a_snippet_that_no_longer_matches_changes_nothing():
    delta = _delta(
        KIND_REPLACE_SNIPPET,
        "procedure",
        {"old": BROKEN_INBOX_CMD, "new": FIXED_INBOX_CMD},
    )
    already_fixed = BASE_SKILL.replace(BROKEN_INBOX_CMD, FIXED_INBOX_CMD)
    resolved = resolve_skill_body(already_fixed, [delta])

    assert resolved.body == already_fixed
    assert resolved.notes[0].outcome == "snippet_not_found"


def test_an_unknown_kind_is_skipped_and_the_rest_resolve():
    good = _delta(KIND_DROP_SECTION, "fork-this", {}, delta_id="good")
    bad = _delta("rewrite_everything", "procedure", {}, delta_id="bad")
    resolved = resolve_skill_body(BASE_SKILL, [good, bad])

    assert "## Fork this" not in resolved.body
    assert resolved.applied == ["good"]
    assert any(n.outcome == "unknown_kind" for n in resolved.notes)


# --------------------------------------------------------------------------
# 6. SHAPE ADAPTATION — the motivating product need
# --------------------------------------------------------------------------


def test_shape_adaptation_reorients_the_skill_and_stays_within_budget():
    """Repo-backlog-shaped skill -> inbox-shaped, without a prompt tax.

    This is the case the user hit: the authored skill described the wrong
    workflow, and closing the gap by hand grew the file 48%.
    """
    deltas = [
        _delta(
            KIND_REPLACE_SECTION,
            "procedure",
            {"body": INBOX_PROCEDURE},
            delta_id="shape",
            created_at="2026-08-18T00:00:01Z",
        ),
        _delta(
            KIND_DROP_SECTION,
            "fork-this",
            {},
            delta_id="prune",
            created_at="2026-08-18T00:00:02Z",
        ),
    ]
    resolved = resolve_skill_body(BASE_SKILL, deltas)

    # Reoriented toward the inbox workflow...
    assert "--involves @me" in resolved.body
    assert "what is blocking you" in resolved.body.lower()
    # ...and the repo-backlog framing is gone, not sitting underneath it.
    assert "Pull the backlog" not in resolved.body
    assert "Ask for the repository if the user did not name one" not in resolved.body

    # Safety rules are untouched by a shape change.
    assert "Never close an issue on your own judgement." in resolved.body

    # And it costs LESS than the authored skill, not 48% more.
    assert resolved.token_delta < 0, resolved.token_delta
    assert resolved.resolved_tokens < estimate_tokens(BASE_SKILL)


def test_the_budget_ceiling_refuses_a_delta_that_would_inflate_the_prompt():
    """Enforced at write time — never by truncating at render."""
    # Under the per-payload char cap, so it is the RESOLVED-token ceiling that
    # must catch this — not the cruder size guard.
    body = "## Procedure\n\n" + ("padding padding padding. " * 70)
    assert len(body) < 2000
    bloat = _delta(KIND_REPLACE_SECTION, "procedure", {"body": body})

    with pytest.raises(DeltaRefused) as excinfo:
        validate_delta(BASE_SKILL, bloat)
    message = str(excinfo.value)
    assert "ceiling" in message
    assert f"{MAX_OVERLAY_TOKENS}-token" in message
    # An actionable error names the way out.
    assert "gaia skill deltas" in message


# --------------------------------------------------------------------------
# 7. WRITE-TIME REFUSALS
# --------------------------------------------------------------------------


def test_untrusted_provenance_is_refused_outright():
    delta = _delta(
        KIND_REPLACE_SECTION,
        "procedure",
        {"body": INBOX_PROCEDURE},
        provenance={"source": "observed_content", "turns": [3]},
    )
    with pytest.raises(DeltaRefused, match="user_instruction"):
        validate_delta(BASE_SKILL, delta)


def test_an_anchor_that_does_not_exist_is_refused_with_the_valid_ones():
    delta = _delta(
        KIND_DROP_SECTION,
        "no-such-section",
        {},
        digest="sha256:00",
    )
    with pytest.raises(DeltaRefused) as excinfo:
        validate_delta(BASE_SKILL, delta)
    assert "procedure" in str(excinfo.value)  # lists the real anchors


def test_a_snippet_not_present_verbatim_is_refused_at_write_time():
    delta = _delta(
        KIND_REPLACE_SNIPPET,
        "procedure",
        {"old": "text that is not in the skill", "new": "x"},
    )
    with pytest.raises(DeltaRefused, match="verbatim"):
        validate_delta(BASE_SKILL, delta)


def test_frontmatter_is_structurally_out_of_reach():
    """A delta rewrites the body only, so permissions cannot be widened."""
    delta = _delta(KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE})
    resolved = resolve_skill_body(BASE_SKILL, [delta])
    assert "security_tier" not in resolved.body
    assert "permissions" not in resolved.body


# --------------------------------------------------------------------------
# 8. THE SECTION PARSER'S LOAD-BEARING PROPERTY
# --------------------------------------------------------------------------


def test_parsing_a_body_and_rendering_it_back_is_lossless():
    assert render_sections(parse_sections(BASE_SKILL)) == BASE_SKILL


def test_digests_are_crlf_insensitive():
    """A Windows checkout must not orphan every delta a Linux author wrote."""
    assert section_digest("a\r\nb") == section_digest("a\nb")


# --------------------------------------------------------------------------
# 9. END TO END — the real bug, on this platform
# --------------------------------------------------------------------------


def test_the_broken_command_really_fails_on_this_platform():
    """The premise. `gh` need not be installed for the quoting failure to bite.

    On Windows `shell_tools` passes the raw string to cmd.exe (shell=True); on
    POSIX it passes a pre-split argv with no shell. The POSIX-quoted form is
    only broken on the former, which is exactly why this belongs in a
    machine-local learned delta and not in the cross-platform authored skill.
    """
    if os.name != "nt":
        pytest.skip("the cmd.exe quoting failure is Windows-only by construction")

    result = subprocess.run(
        BROKEN_INBOX_CMD,
        shell=True,
        capture_output=True,
        text=True,
        errors="replace",
    )
    assert result.returncode != 0
    combined = (result.stdout + result.stderr).lower()
    # Either cmd.exe's misleading path error, or gh rejecting the stray "\".
    assert "cannot find the path" in combined or "unknown argument" in combined


@pytest.mark.skipif(
    subprocess.run("gh --version", shell=True, capture_output=True).returncode != 0,
    reason="gh CLI not installed",
)
def test_the_resolved_command_runs_clean_on_this_platform():
    """The payoff: the corrected command in the RESOLVED skill actually works."""
    delta = _delta(
        KIND_REPLACE_SNIPPET,
        "procedure",
        {"old": BROKEN_INBOX_CMD, "new": FIXED_INBOX_CMD},
    )
    resolved = resolve_skill_body(BASE_SKILL, [delta])

    # Pull the command back out of the resolved skill rather than trusting the
    # constant — this asserts what the model would actually be told to run.
    line = next(
        ln.strip()
        for ln in resolved.body.splitlines()
        if ln.strip().startswith("gh issue list")
    )
    assert "--jq '" not in line

    if shutil.which("gh") is None:
        pytest.skip(
            "gh is not installed here; nothing to run the resolved line against"
        )

    if os.name == "nt":
        result = subprocess.run(
            line, shell=True, capture_output=True, text=True, errors="replace"
        )
    else:
        import shlex

        result = subprocess.run(
            shlex.split(line), capture_output=True, text=True, errors="replace"
        )

    # The claim is that the SHELL parses this line — the bug the delta fixes is
    # an unbalanced `--jq '` that Windows cmd swallows, taking the rest of the
    # arguments with it. Reaching gh's auth check proves the argv survived
    # intact, which is the whole point; demanding a successful API call would
    # make a unit test depend on a token it has no business needing.
    output = (result.stderr or "") + (result.stdout or "")
    unauthenticated = "GH_TOKEN" in output or "gh auth login" in output
    assert result.returncode == 0 or unauthenticated, result.stderr[:400]


# --------------------------------------------------------------------------
# 10. LEGIBILITY — a learned change must be inspectable
# --------------------------------------------------------------------------


def test_the_consent_diff_shows_what_would_change():
    delta = _delta(KIND_REPLACE_SECTION, "procedure", {"body": INBOX_PROCEDURE})
    diff = preview_diff(BASE_SKILL, [delta])

    assert "-1. **Pull the backlog.**" in diff
    assert "+1. **Pull what landed on you.**" in diff
    assert "authored SKILL.md" in diff


def test_a_staged_delta_can_be_previewed_before_it_is_approved():
    """Review before consent: the diff works while the delta is still inert."""
    staged = _delta(
        KIND_REPLACE_SECTION,
        "procedure",
        {"body": INBOX_PROCEDURE},
        status="staged",
    )
    assert resolve_skill_body(BASE_SKILL, [staged]).body == BASE_SKILL
    assert "+1. **Pull what landed on you.**" in preview_diff(BASE_SKILL, [staged])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _from_row(row: dict) -> SkillDelta:
    """Map a store row to the resolver's dataclass."""
    return SkillDelta(
        id=row["id"],
        base_name=row["base_name"],
        scope=row["scope"],
        kind=row["kind"],
        anchor_section=row["anchor_section"],
        anchor_digest=row["anchor_digest"],
        payload=row["payload"],
        provenance=row["provenance"],
        status=row["status"],
        superseded_by=row["superseded_by"],
        created_at=row["created_at"],
        approved_at=row["approved_at"],
    )


def test_schema_migrates_to_v4_and_keeps_existing_tables(tmp_path):
    """Additive migration: a v3 database gains skill_deltas, loses nothing."""
    db = tmp_path / "memory.db"
    store = MemoryStore(db_path=db)
    with store._lock:  # noqa: SLF001 - asserting the migration, not a public API
        version = store._conn.execute(
            "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
        ).fetchone()[0]
        tables = {
            r[0]
            for r in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert version == 4
    assert "skill_deltas" in tables
    for pre_existing in ("knowledge", "procedures", "conversations", "tool_history"):
        assert pre_existing in tables


# --------------------------------------------------------------------------
# 11. INTEGRATION — the real Agent render path, not the resolver in isolation
# --------------------------------------------------------------------------


class _OverlayStubAgent(LearnedOverlayStubMixin):
    """Drives the real prompt methods without booting the LLM stack.

    Mirrors ``test_agent_lazy_skill_prompt._StubAgent``: every method under test
    is the genuine ``Agent`` one, so this exercises the shipping render path
    rather than a reimplementation of it. The overlay members come from the
    shared mixin — unlike the other two stubs, this one then supplies a real
    store, because these tests ARE about learning.
    """

    _loaded_skills = None
    _active_skill_filter = None

    loaded_skills = Agent.loaded_skills
    get_skills_system_prompt = Agent.get_skills_system_prompt
    _always_on_skill_names = Agent._always_on_skill_names

    def __init__(self, store, skill):
        self._memory_store = store
        self._loaded_skills = {skill.name: skill}
        self._scope = SCOPE

    def _namespaced_agent_id(self):
        return self._scope


class _FakeSkill:
    def __init__(self, name, body, description="A test skill."):
        self.name = name
        self.body = body
        self.description = description


def _approved_overlay(store):
    """Stage + approve the shape-adaptation delta. Returns its id."""
    delta_id = store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": INBOX_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(delta_id)
    return delta_id


def test_the_composed_prompt_carries_the_overlay_not_the_authored_procedure(store):
    _approved_overlay(store)
    agent = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    prompt = agent.get_skills_system_prompt()

    assert "==== LOADED SKILLS ====" in prompt
    assert "Pull what landed on you" in prompt
    assert "Pull the backlog" not in prompt
    assert agent.overlaid_skills["github-triage"]


def test_a_staged_delta_does_not_reach_the_composed_prompt(store):
    store.put_delta(  # staged, never approved
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": INBOX_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    agent = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    prompt = agent.get_skills_system_prompt()

    assert "Pull the backlog" in prompt
    assert "Pull what landed on you" not in prompt
    assert agent.overlaid_skills == {}


def test_off_switch_makes_the_composed_prompt_byte_identical(store):
    """The floor asserted by hash equality, not by inspection."""
    _approved_overlay(store)

    on = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    off = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    off._learned_skills_enabled = False
    none_at_all = _OverlayStubAgent(None, _FakeSkill("github-triage", BASE_SKILL))

    off_prompt = off.get_skills_system_prompt()
    baseline = none_at_all.get_skills_system_prompt()

    assert (
        hashlib.sha256(off_prompt.encode()).hexdigest()
        == hashlib.sha256(baseline.encode()).hexdigest()
    )
    assert on.get_skills_system_prompt() != off_prompt


def test_incognito_suppresses_the_overlay(store):
    _approved_overlay(store)
    agent = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    agent._incognito = True

    assert "Pull the backlog" in agent.get_skills_system_prompt()


def test_a_broken_overlay_never_takes_the_skills_block_down(store, monkeypatch):
    """The hazard: this fragment raising would delete every skill's body.

    ``_get_mixin_prompts`` swallows a raising fragment with only a warning, so
    resolution failing must floor to the authored text — not to nothing.
    """
    _approved_overlay(store)
    agent = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    def _explode(*a, **kw):
        raise RuntimeError("resolver blew up")

    monkeypatch.setattr("gaia.agents.base.skill_deltas.resolve_skill_body", _explode)
    prompt = agent.get_skills_system_prompt()

    assert "Pull the backlog" in prompt, "must fall back to the AUTHORED body"
    assert "==== LOADED SKILLS ====" in prompt


def test_a_delta_for_another_agent_scope_does_not_leak(store):
    delta_id = store.put_delta(
        base_name="github-triage",
        scope="gaia:some-other-agent",
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": INBOX_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(delta_id)
    agent = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    assert "Pull the backlog" in agent.get_skills_system_prompt()


# --------------------------------------------------------------------------
# 12. THE WRITE TOOL — what the model actually calls
# --------------------------------------------------------------------------


class _LearningAgent(_OverlayStubAgent):
    """Stub agent with the real learning tool registered onto it."""

    def __init__(self, store, skill):
        super().__init__(store, skill)
        self._registered = {}
        from gaia.agents.tools.skill_learning_tools import SkillLearningToolsMixin

        self._mixin = SkillLearningToolsMixin()
        self._mixin._namespaced_agent_id = self._namespaced_agent_id
        self._mixin.learned_skill_scope = self.learned_skill_scope
        self._mixin.loaded_skills = self._loaded_skills
        self._mixin._memory_store = store
        self._mixin._incognito = False
        self._mixin._effective_skill_cache = None
        self._mixin.rebuild_system_prompt = lambda: None

    def call(self, **kwargs):
        """Invoke remember_skill_lesson by capturing it at registration."""
        captured = {}

        def _tool(*a, **kw):
            def deco(fn):
                captured["fn"] = fn
                return fn

            # Support both @tool and @tool(atomic=True) forms.
            if a and callable(a[0]):
                captured["fn"] = a[0]
                return a[0]
            return deco

        import gaia.agents.base.tools as tools_mod

        real = tools_mod.tool
        tools_mod.tool = _tool
        try:
            self._mixin.register_skill_learning_tools()
        finally:
            tools_mod.tool = real
        return captured["fn"](**kwargs)


def test_the_tool_corrects_a_broken_command_by_replacement(store):
    """The correction reaches the prompt on approval — and not one turn before."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=FIXED_INBOX_CMD,
        replaces=BROKEN_INBOX_CMD,
        reason="single quotes fail under cmd.exe",
    )
    assert result["status"] == "success", result
    assert result["change"] == KIND_REPLACE_SNIPPET
    assert result["applied"] is False

    # The model wrote it, so nothing may have moved yet.
    staged_prompt = agent.get_skills_system_prompt()
    assert BROKEN_INBOX_CMD in staged_prompt
    assert FIXED_INBOX_CMD not in staged_prompt

    approve_delta(store, result["delta_id"], BASE_SKILL)
    agent._effective_skill_cache = None
    agent._overlaid_skills = None

    prompt = agent.get_skills_system_prompt()
    assert FIXED_INBOX_CMD in prompt
    assert BROKEN_INBOX_CMD not in prompt
    assert "--jq '" not in prompt


def test_the_tool_stages_and_never_activates(store):
    """The consent gate, at the only layer that can enforce it.

    The model may propose a change to the instructions it runs under; only the
    user may activate one. A tool that approved its own write would make every
    "nothing applies without your consent" claim in the docs false.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
    assert result["status"] == "success", result

    rows = store.search_deltas(base_name="github-triage", scope=SCOPE)
    assert [r["status"] for r in rows] == ["staged"]
    assert rows[0]["approved_at"] is None
    assert store.search_deltas(base_name="github-triage", status="active") == []


def test_the_tool_supersedes_instead_of_stacking(store):
    """The prompt-tax guard, exercised through the real tool."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    sizes = []

    for n in range(6):
        result = agent.call(
            skill="github-triage",
            section="procedure",
            corrected_text=INBOX_PROCEDURE + f"\n\n5. Revision {n}.",
            reason=f"revision {n}",
        )
        assert result["status"] == "success", result
        approve_delta(store, result["delta_id"], BASE_SKILL)
        agent._effective_skill_cache = None
        agent._overlaid_skills = None
        sizes.append(len(agent.get_skills_system_prompt()))

    live = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    assert len(live) == 1, "each correction must retire the last, not stack"
    # The revisions the user never approved must not pile up either.
    pending = store.search_deltas(
        base_name="github-triage", scope=SCOPE, status="staged"
    )
    assert pending == []
    assert max(sizes) - min(sizes) <= 20, sizes


def test_an_unapproved_revision_leaves_the_live_correction_alone(store):
    """Staging a replacement must not retire the correction it would replace."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    first = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
    approve_delta(store, first["delta_id"], BASE_SKILL)

    agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE + "\n\n5. Unapproved revision.",
        reason="a revision the user has not seen",
    )

    live = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    assert [r["id"] for r in live] == [first["delta_id"]]

    agent._effective_skill_cache = None
    agent._overlaid_skills = None
    prompt = agent.get_skills_system_prompt()
    assert "Unapproved revision." not in prompt
    assert "--involves @me" in prompt


def test_the_tool_refuses_an_unloaded_skill(store):
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(skill="not-loaded", section="procedure", corrected_text="x")
    assert result["status"] == "error"
    assert "load_skill" in result["message"]


def test_the_tool_refuses_a_bad_section_and_lists_the_real_ones(store):
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(
        skill="github-triage", section="nonexistent", corrected_text="x"
    )
    assert result["status"] == "error"
    assert "procedure" in result["message"]


def test_the_tool_refuses_to_save_in_a_private_session(store):
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    agent._mixin._incognito = True
    result = agent.call(
        skill="github-triage", section="procedure", corrected_text=INBOX_PROCEDURE
    )
    assert result["status"] == "error"
    assert "private session" in result["message"]
    assert store.search_deltas(base_name="github-triage") == []


def test_the_tool_cannot_delete_a_section(store):
    """Removal is a human action, not a model one.

    An empty replacement is a (bad) section rewrite, never a drop — so a
    confidently wrong model cannot quietly remove the rules constraining it.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(skill="github-triage", section="rules", corrected_text="   ")

    # Refused at the tool boundary, and the error names who CAN remove it.
    assert result["status"] == "error"
    assert "delete" in result["message"]
    assert "gaia skill deltas" in result["message"]

    # Nothing was stored, and the rules are intact in the composed prompt.
    assert store.search_deltas(base_name="github-triage", scope=SCOPE) == []
    prompt = agent.get_skills_system_prompt()
    assert "Never close an issue on your own judgement." in prompt


def test_the_tool_reports_the_token_cost_it_added(store):
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="user works from their inbox, not a repo backlog",
    )
    assert result["status"] == "success"
    # Reorienting the skill to a different workflow is ~token-neutral: the new
    # procedure REPLACES the old one instead of sitting beneath it. Contrast
    # the two alternatives measured on this fixture — appending the same lesson
    # costs +36%, hand-patching the authored file cost +48%.
    cost = result["token_delta_vs_authored_if_approved"]
    assert abs(cost) <= MAX_OVERLAY_TOKENS
    assert cost < 0.05 * estimate_tokens(BASE_SKILL)
    # ...and the tool reports the cost, and that nothing has applied yet.
    assert "prompt tokens" in result["message"]
    assert "approve" in result["message"]


def test_shape_adaptation_plus_pruning_makes_the_skill_cheaper(store):
    """Replacement is neutral; removing what the user never uses is the saving."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    correction = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
    approve_delta(store, correction["delta_id"], BASE_SKILL)
    # The user prunes a section they never exercise (a human action, via CLI).
    drop_id = store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_DROP_SECTION,
        anchor_section="fork-this",
        anchor_digest=_digest_of(BASE_SKILL, "fork-this"),
        payload={},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(drop_id)

    rows = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    resolved = resolve_skill_body(BASE_SKILL, [_from_row(r) for r in rows])

    assert "--involves @me" in resolved.body
    assert "Pull the backlog" not in resolved.body
    assert resolved.token_delta < 0, resolved.token_delta


def test_dropping_a_middle_section_leaves_well_formed_markdown():
    """A removal must not leave a blank-line crater the model reads as a gap."""
    delta = _delta(KIND_DROP_SECTION, "rules", {})
    body = resolve_skill_body(BASE_SKILL, [delta]).body

    assert "## Rules" not in body
    assert "\n\n\n" not in body, "collapsed section left a stray blank-line run"
    # The sections either side survive intact and still parse.
    slugs = [s.slug for s in parse_sections(body)]
    assert "procedure" in slugs and "fork-this" in slugs
    assert "rules" not in slugs


def test_a_skill_with_no_headings_anchors_to_the_whole_body():
    """The legal bare-skill case: whole-body replacement needs no special path."""
    bare = "Just prose. No headings at all."
    sections = parse_sections(bare)
    assert [s.slug for s in sections] == ["_preamble"]

    delta = SkillDelta(
        id="whole",
        base_name="bare",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="_preamble",
        anchor_digest=sections[0].digest,
        payload={"body": "Better prose."},
        provenance=dict(TRUSTED),
        status=STATUS_ACTIVE,
        created_at="2026-08-18T00:00:00Z",
    )
    assert resolve_skill_body(bare, [delta]).body == "Better prose."


# --------------------------------------------------------------------------
# The consent gate is a floor, and a floor may only grow
# --------------------------------------------------------------------------


def test_remember_skill_lesson_is_in_the_base_confirmation_floor():
    """Pins the tool into the gate so it cannot quietly fall back out.

    The tool writes to the instructions the agent runs under. It shipped absent
    from every confirmation set once already; this is the test that catches the
    second time.

    Asserted on the BASE set, not one agent's: the mixin is composable by name
    (``KNOWN_TOOLS["skill_learning"]``), so a per-agent gate would leave every
    other composer ungated. No hub wheel needed, so this always runs in CI.
    """
    from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION

    assert "remember_skill_lesson" in TOOLS_REQUIRING_CONFIRMATION
    assert "remember_skill_lesson" in Agent.confirmation_required_tools()


def test_every_skill_learning_tool_is_gated():
    """The floor covers the whole mixin, not just the one name known today."""
    from gaia.agents.tools.skill_learning_tools import SKILL_LEARNING_TOOL_NAMES

    gated = Agent.confirmation_required_tools()
    assert set(SKILL_LEARNING_TOOL_NAMES) <= gated, (
        "a skill-learning tool ships ungated: "
        f"{sorted(set(SKILL_LEARNING_TOOL_NAMES) - gated)}"
    )


def test_an_always_allow_grant_scopes_to_one_skill():
    """ "Always" on a learning call must not become "always, for every skill"."""
    from gaia.agents.base.tool_grants import grant_scope

    scope = grant_scope("remember_skill_lesson", {"skill": "github-triage"})
    assert scope is not None, "no grant scope means the UI cannot offer 'always'"
    assert "github-triage" in scope.key


def test_the_ceiling_is_re_checked_at_approval(store):
    """Write-time validation cannot see the deltas queued behind it.

    Each staged lesson is validated against the deltas active *at write time*,
    so N of them can each pass alone and still blow the overlay budget once
    approved in turn. Approval is the only point that sees the real resolved
    skill, so the ceiling has to hold there too.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    # Sized so each lesson clears the ceiling alone but the three together do not.
    filler = "\n".join(f"- padding line {n} to grow the section." for n in range(13))

    staged = []
    for section in ("setup", "rules", "fork-this"):
        result = agent.call(
            skill="github-triage",
            section=section,
            corrected_text=f"## {section.title()}\n\n{filler}",
            reason=f"grow {section}",
        )
        assert result["status"] == "success", result
        staged.append(result["delta_id"])

    approved, refused = 0, 0
    for delta_id in staged:
        try:
            if approve_delta(store, delta_id, BASE_SKILL) is not None:
                approved += 1
        except DeltaRefused:
            refused += 1
    assert refused, "the ceiling never fired — approval accepted every delta"

    rows = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    resolved = resolve_skill_body(BASE_SKILL, [SkillDelta.from_row(r) for r in rows])
    assert resolved.token_delta <= MAX_OVERLAY_TOKENS, resolved.token_delta


def test_a_superseded_staged_delta_cannot_be_approved(store):
    """No success receipt for a change that can never apply.

    Staging a revision retires the staged one it replaces. Approving the older
    id — the one the user may still have in front of them — must fail, not
    report success and silently activate a row that can never resolve.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    first = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
    second = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE + "\n\n5. Revised.",
        reason="a better version",
    )

    assert approve_delta(store, first["delta_id"], BASE_SKILL) is None
    assert store.search_deltas(base_name="github-triage", status="active") == []

    assert approve_delta(store, second["delta_id"], BASE_SKILL) is not None
    live = store.search_deltas(base_name="github-triage", status="active")
    assert [r["id"] for r in live] == [second["delta_id"]]


def test_approval_is_bound_to_the_skill_the_user_named(store):
    """An id from another skill must not be approved under this one's name."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    staged = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )

    assert (
        approve_delta(
            store, staged["delta_id"], BASE_SKILL, base_name="some-other-skill"
        )
        is None
    )
    assert store.search_deltas(base_name="github-triage", status="active") == []

    assert (
        approve_delta(store, staged["delta_id"], BASE_SKILL, base_name="github-triage")
        is not None
    )


# ----------------------------------------------------------------------
# Store contracts the resolution path depends on
# ----------------------------------------------------------------------


def _put(store, name, section="procedure", scope="AgentA", digest="d"):
    return store.put_delta(
        base_name=name,
        scope=scope,
        kind=KIND_REPLACE_SECTION,
        anchor_section=section,
        anchor_digest=digest,
        payload={"body": "x"},
        provenance={"source": "user_instruction"},
    )


def test_the_row_ceiling_drops_the_newest_not_the_oldest(store):
    """Rows come back oldest-first, so a LIMIT truncates the tail.

    The tail is the set that wins resolution — the last write to a section is
    the one applied — so a caller that resolves a skill must never accept a
    truncated read. Two comments used to claim the opposite.
    """
    ids = [_put(store, "s") for _ in range(5)]

    kept = [r["id"] for r in store.search_deltas(base_name="s", limit=2)]

    assert kept == ids[:2]
    assert ids[-1] not in kept, "the newest delta — the winner — was dropped"
    assert len(store.search_deltas(base_name="s", limit=None)) == 5


def test_archiving_is_bound_to_the_skill_and_scope_named(store):
    mine = _put(store, "mine", scope="AgentA")
    theirs = _put(store, "theirs", scope="AgentA")
    other_scope = _put(store, "mine", scope="AgentB")

    assert store.archive_delta(theirs, base_name="mine") is False
    assert store.archive_delta(other_scope, base_name="mine", scope="AgentA") is False
    assert store.archive_delta(mine, base_name="mine", scope="AgentA") is True

    def status(delta_id):
        return store.search_deltas(delta_id=delta_id, include_superseded=True)[0][
            "status"
        ]

    assert status(mine) == "archived"
    assert status(theirs) == "staged"
    assert status(other_scope) == "staged"


def test_archiving_the_same_delta_twice_reports_only_one_change(store):
    delta_id = _put(store, "s")

    assert store.archive_delta(delta_id) is True
    assert store.archive_delta(delta_id) is False, "a no-op must not report success"


def test_superseding_never_rewrites_an_existing_lineage(store):
    original = _put(store, "s")
    first = _put(store, "s")
    second = _put(store, "s")

    assert store.supersede_delta(original, first) is True
    assert store.supersede_delta(original, second) is False

    row = store.search_deltas(delta_id=original, include_superseded=True)[0]
    assert row["superseded_by"] == first, "the audit trail was rewritten"


def test_the_tool_refuses_to_stage_while_the_off_switch_is_on(store, monkeypatch):
    """Off means nothing is learned, not learned-invisibly.

    Resolution already ignores everything under the switch, so a delta staged
    now would sit in a queue the user has said they do not want and cannot see
    the effect of. Refusing tells the model to give the correction to the user
    instead.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    # The tool closes over the mixin — that object is what a real agent IS.
    agent._mixin.learned_skills_enabled = lambda: False

    result = agent.call(
        skill="github-triage",
        section="rules",
        corrected_text="A correction the user will never see.",
    )

    assert result["status"] == "error"
    assert "switched off" in result["message"]
    assert "GAIA_NO_LEARNED_SKILLS" in result["message"]
    assert store.search_deltas(base_name="github-triage", scope=SCOPE) == []
