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
4. **A lesson applies at once, and only the user can teach one.** The agent
   activates what it learned in the same turn it learned it — and a turn in
   which anything else has spoken (a fetched page, a command's output) cannot
   teach it at all. The off-switch restores the authored bytes exactly.
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
from gaia.agents.tools.skill_learning_tools import SkillLearningToolsMixin
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
# 4. ONLY AN ACTIVE DELTA RESOLVES + OFF SWITCH
# --------------------------------------------------------------------------


def test_a_delta_that_was_never_activated_has_zero_effect(store):
    """Resolution reads active rows only — the invariant activation relies on.

    The write path activates its own delta now, so nothing routinely stages.
    This pins the resolver rule anyway: a row that is not active is not in the
    prompt, which is what makes ``--revert`` (archive) an actual undo.
    """
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

    # ...and takes effect once it is activated.
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


def test_a_delta_that_was_never_activated_stays_out_of_the_prompt(store):
    store.put_delta(  # staged, never activated
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


class _RecordingConsole:
    """The user-visible surface, captured. ``print_info`` is what the real
    AgentConsole / SSEOutputHandler both implement."""

    def __init__(self):
        self.info = []

    def print_info(self, message):
        self.info.append(message)


class _LearningAgent(_OverlayStubAgent, SkillLearningToolsMixin):
    """The real learning mixin composed onto the real render path.

    The mixin is a *base*, not a side object, so the ``agent`` the tool closes
    over is the same object ``get_skills_system_prompt`` renders — which is what
    lets these tests assert that a correction reaches the prompt with no cache
    poking in between.

    ``turn_content_provenance`` is the genuine ``Agent`` method reading the
    genuine ``_turn_saw_external_content`` flag, so a test that taints the turn
    exercises the shipping guard rather than a stand-in for it.
    """

    turn_content_provenance = Agent.turn_content_provenance

    def __init__(self, store, skill):
        super().__init__(store, skill)
        self._turn_saw_external_content = False
        self.console = _RecordingConsole()
        self.rebuilds = 0

    def rebuild_system_prompt(self):
        self.rebuilds += 1

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
            self.register_skill_learning_tools()
        finally:
            tools_mod.tool = real
        return captured["fn"](**kwargs)


def test_the_tool_corrects_a_broken_command_by_replacement(store):
    """The correction reaches the prompt in the same session it was learned.

    The prompt is read back with no cache poking in between: an agent that
    learns a fix and then keeps running the broken command until it restarts
    has not learned anything the user can see.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    assert BROKEN_INBOX_CMD in agent.get_skills_system_prompt()

    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=FIXED_INBOX_CMD,
        replaces=BROKEN_INBOX_CMD,
        reason="single quotes fail under cmd.exe",
    )
    assert result["status"] == "success", result
    assert result["change"] == KIND_REPLACE_SNIPPET
    assert result["applied"] is True

    prompt = agent.get_skills_system_prompt()
    assert FIXED_INBOX_CMD in prompt
    assert BROKEN_INBOX_CMD not in prompt
    assert "--jq '" not in prompt
    assert agent.rebuilds == 1, "the cached system prompt must be rebuilt"


def test_the_tool_activates_what_it_learned(store):
    """The reversal of the staged model, pinned at the store layer.

    The agent applies the correction it wrote — that is the point of an
    adaptive agent. Nothing is left waiting for a human to press approve, so
    no queue can silently accumulate changes the user never sees.
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
    assert [r["status"] for r in rows] == ["active"]
    assert rows[0]["approved_at"] is not None
    assert store.search_deltas(base_name="github-triage", status="staged") == []


def test_the_user_is_told_what_changed_and_how_to_undo_it(store):
    """Transparency is what replaced the approval prompt, so it is not optional.

    The announcement must name the skill, the section, the reason, and a revert
    command carrying the *real* id — a notice the user cannot act on is the
    same as no notice.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
    delta_id = result["delta_id"]
    undo = f"gaia skill deltas github-triage --revert {delta_id}"

    assert agent.console.info, "the write must announce itself on the console"
    notice = agent.console.info[-1]
    for expected in ("github-triage", "procedure", "inbox, not backlog", undo):
        assert expected in notice, f"{expected!r} missing from: {notice}"

    # The same sentence rides the tool result, which is the surface that stays
    # in the transcript after the status line is overwritten.
    assert result["message"] == notice
    assert result["undo_command"] == undo


def test_the_undo_command_the_notice_prints_is_one_the_cli_accepts(store):
    """A notice naming a command that does not parse is worse than no notice.

    The string is built by hand in the tool and consumed by a parser defined in
    another module, so nothing else would catch a renamed flag or a reordered
    argument. Parsed by the real ``gaia skill`` argparse tree, then the archive
    it names is performed and the correction is gone from the prompt.
    """
    import argparse

    from gaia.skills import cli as skills_cli

    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
    assert "Pull what landed on you" in agent.get_skills_system_prompt()

    parser = argparse.ArgumentParser(prog="gaia")
    sub = parser.add_subparsers(dest="action")
    skills_cli.add_subparser(sub)
    argv = result["undo_command"].split()
    assert argv[0] == "gaia"
    parsed = parser.parse_args(argv[1:])

    assert (parsed.action, parsed.skill_action) == ("skill", "deltas")
    assert parsed.name == "github-triage"
    assert parsed.revert == result["delta_id"]

    # What `--revert` then does, and what the user gets for it.
    assert store.archive_delta(parsed.revert, base_name=parsed.name) is True
    reverted = _OverlayStubAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    prompt = reverted.get_skills_system_prompt()
    assert "Pull the backlog" in prompt
    assert "Pull what landed on you" not in prompt
    assert [r["id"] for r in store.search_deltas(status="archived")] == [parsed.revert]


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
        sizes.append(len(agent.get_skills_system_prompt()))

    live = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    assert len(live) == 1, "each correction must retire the last, not stack"
    assert max(sizes) - min(sizes) <= 20, sizes


def test_a_revision_replaces_the_live_correction_in_the_same_session(store):
    """A second correction to the same section supersedes the first at once.

    Both surviving would put the superseded instruction in the prompt beside
    the one that replaced it — the exact failure this design exists to avoid.
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
        corrected_text=INBOX_PROCEDURE + "\n\n5. Also skim the mentions tab.",
        reason="mentions matter too",
    )
    assert second["superseded"] == [first["delta_id"]]

    live = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    assert [r["id"] for r in live] == [second["delta_id"]]

    prompt = agent.get_skills_system_prompt()
    assert "Also skim the mentions tab." in prompt
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
    agent._incognito = True
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


@pytest.mark.parametrize(
    "kwargs",
    [
        # A whole-section rewrite that forgot the heading...
        {"corrected_text": "1. Pull what landed on you."},
        # ...and a snippet whose 'old' happens to span it. The tool's docstring
        # tells the model to prefer this form, so guarding only the first would
        # leave the recommended path open.
        {"corrected_text": "1. Pull what landed on you.", "replaces": "## Procedure"},
    ],
    ids=["whole-section", "snippet-swallows-the-heading"],
)
def test_an_edit_that_deletes_the_heading_is_refused(store, kwargs):
    """Losing the heading merges the section into the one above it.

    A section's span carries its own heading line, so the rendered prompt ends
    up with two procedures read as one — the same "wrong instruction beside the
    right one" this whole design exists to avoid. A human reviewing the diff
    would have caught it; nothing does now, so it is refused at write time.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    result = agent.call(skill="github-triage", section="procedure", **kwargs)

    assert result["status"] == "error", result
    assert "## Procedure" in result["message"], "the error must quote the line to keep"
    assert store.search_deltas(base_name="github-triage") == []


def test_the_same_lesson_with_the_heading_kept_is_accepted(store):
    """The guard must not block the ordinary case it exists to shape."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    ok = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text="## Procedure\n\n1. Pull what landed on you.\n",
        reason="inbox, not backlog",
    )

    assert ok["status"] == "success", ok
    rows = store.search_deltas(base_name="github-triage", scope=SCOPE, status="active")
    resolved = resolve_skill_body(BASE_SKILL, [_from_row(r) for r in rows])
    assert "procedure" in [s.slug for s in parse_sections(resolved.body)]


def test_renaming_a_heading_is_still_allowed(store):
    """The rule is "keep a heading", not "keep this heading".

    Retitling a section is legitimate shape adaptation, and resolution anchors
    on the authored base, so the delta still finds its section next time.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))

    ok = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text="## Inbox triage",
        replaces="## Procedure",
        reason="it is an inbox, not a backlog",
    )

    assert ok["status"] == "success", ok
    prompt = agent.get_skills_system_prompt()
    assert "## Inbox triage" in prompt
    assert "## Procedure" not in prompt


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
    cost = result["token_delta_vs_authored"]
    assert abs(cost) <= MAX_OVERLAY_TOKENS
    assert cost < 0.05 * estimate_tokens(BASE_SKILL)
    assert result["learned_changes_on_this_skill"] == 1


def test_shape_adaptation_plus_pruning_makes_the_skill_cheaper(store):
    """Replacement is neutral; removing what the user never uses is the saving."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="inbox, not backlog",
    )
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
# Learning raises no modal — transparency and undo are what stand in its place
# --------------------------------------------------------------------------


def test_learning_raises_no_confirmation_modal():
    """A prompt on every learned lesson would destroy the feature.

    Deliberate, not an oversight: the tool writes only to this agent's own
    memory, announces itself, and undoes in one command. Pinned so it is not
    "fixed" back into the gate by pattern-matching on "it writes something".
    """
    from gaia.agents.base.agent import TOOLS_REQUIRING_CONFIRMATION
    from gaia.agents.tools.skill_learning_tools import SKILL_LEARNING_TOOL_NAMES

    gated = Agent.confirmation_required_tools()
    assert not set(SKILL_LEARNING_TOOL_NAMES) & set(TOOLS_REQUIRING_CONFIRMATION)
    assert not set(SKILL_LEARNING_TOOL_NAMES) & gated


def test_no_always_allow_grant_is_offered_for_learning():
    """A grant scope with no modal behind it is a promise nothing keeps."""
    from gaia.agents.base.tool_grants import grant_scope

    assert grant_scope("remember_skill_lesson", {"skill": "github-triage"}) is None
    # The tools that DO raise a modal keep theirs.
    assert grant_scope("install_skill", {"skill": "github-triage"}) is not None


# --------------------------------------------------------------------------
# Provenance — the guard that replaced the human in the loop
# --------------------------------------------------------------------------


class _TaintProbeAgent:
    """Drives the real ``_execute_tool`` so the taint is observed, not simulated.

    Every method here is the shipping ``Agent`` one. Only the registry is
    fabricated, because the question is which *names* taint a turn, not what
    the bodies behind them do.
    """

    _execute_tool = Agent._execute_tool
    _resolve_tool_name = Agent._resolve_tool_name
    _policy_refusal = Agent._policy_refusal
    _tool_requires_confirmation = Agent._tool_requires_confirmation
    _call_is_pre_authorized = Agent._call_is_pre_authorized
    _on_tool_invoked = Agent._on_tool_invoked
    _coerce_tool_args = Agent._coerce_tool_args
    _call_tool_bounded = Agent._call_tool_bounded
    _fold_tool_usage = Agent._fold_tool_usage
    _resolve_tool_timeout = Agent._resolve_tool_timeout
    _begin_turn_provenance = Agent._begin_turn_provenance
    mark_external_content = Agent.mark_external_content
    turn_content_provenance = Agent.turn_content_provenance
    confirmation_required_tools = Agent.confirmation_required_tools

    current_plan = None
    current_step = 0

    def __init__(self):
        self._tool_reported_usage = []
        self._tools_registry = {
            name: {"function": lambda: {"status": "success"}}
            for name in ("list_skills", "fetch_webpage")
        }


def test_a_lesson_from_tool_returned_content_is_refused(store):
    """Prompt injection into long-term memory, refused at the write.

    The agent reads a page, an email, or an issue containing text shaped like
    an instruction. Without this it "learns" that, activates it immediately,
    and carries it into every later session. The turn is tainted the moment any
    tool returns content the user did not type, and the write dies there.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    agent._turn_saw_external_content = True  # a web fetch ran earlier this turn

    result = agent.call(
        skill="github-triage",
        section="procedure",
        corrected_text=INBOX_PROCEDURE,
        reason="a web page said so",
    )

    assert result["status"] == "error"
    assert "tool_content" in result["message"]
    assert store.search_deltas(base_name="github-triage") == [], "nothing stored"
    assert "Pull the backlog" in agent.get_skills_system_prompt()
    assert agent.console.info == [], "a refusal must not announce a change"


def test_an_agent_that_cannot_report_provenance_is_refused(store):
    """Fail closed: no answer is not the same as "the user said it"."""
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    del agent.__class__.turn_content_provenance
    try:
        result = agent.call(
            skill="github-triage",
            section="procedure",
            corrected_text=INBOX_PROCEDURE,
        )
    finally:
        agent.__class__.turn_content_provenance = Agent.turn_content_provenance

    assert result["status"] == "error"
    assert "unknown" in result["message"]
    assert store.search_deltas(base_name="github-triage") == []


def test_the_turn_is_clean_until_any_tool_returns_content():
    """The taint is derived from the live turn, not asserted by the caller.

    Driven through the real ``_execute_tool``, because the allowlist and the
    flag only mean anything if the shipping dispatch path sets them.
    """
    agent = _TaintProbeAgent()
    agent._turn_saw_external_content = False
    assert agent.turn_content_provenance() == "user_instruction"

    # The learning tool's own receipt tells the agent nothing new, so two
    # lessons in one turn both go through.
    agent._execute_tool("remember_skill_lesson", {})
    assert agent.turn_content_provenance() == "user_instruction"

    # Everything else taints — a name this build has never heard of included,
    # so a tool nobody classified fails closed.
    agent._execute_tool("fetch_webpage", {})
    assert agent.turn_content_provenance() == "tool_content"


def test_even_a_skill_listing_taints_the_turn():
    """A skill's own text is third-party, and could name a DIFFERENT skill.

    Skill B's body asking for a rewrite of skill A would outlive uninstalling
    B, so "it is in the prompt anyway" does not make it safe to learn from.
    """
    from gaia.agents.base.agent import TOOLS_WITHOUT_EXTERNAL_CONTENT

    assert TOOLS_WITHOUT_EXTERNAL_CONTENT == {"remember_skill_lesson"}

    agent = _TaintProbeAgent()
    agent._turn_saw_external_content = False
    agent._execute_tool("list_skills", {})
    assert agent.turn_content_provenance() == "tool_content"


def test_pushed_context_taints_the_turn_it_arrives_in():
    """Content the caller injects is not content the user typed.

    The flagship's ``/query`` accepts pushed ``context``, which an orchestrator
    can fill with a page it fetched. That content arrives in *this* turn, so
    the per-turn taint has to see it — otherwise the whole guard is one HTTP
    field away from being bypassed.
    """
    agent = _TaintProbeAgent()
    agent.mark_external_content()
    agent._begin_turn_provenance()  # what _process_query_impl does per turn

    assert agent.turn_content_provenance() == "tool_content"

    # Consumed by that turn only — the next one starts clean again.
    agent._begin_turn_provenance()
    assert agent.turn_content_provenance() == "user_instruction"


def test_provenance_defaults_to_tainted_outside_a_turn():
    """No turn boundary (MCP server, a direct driver) must not read as trusted."""
    assert _TaintProbeAgent().turn_content_provenance() == "tool_content"


def test_the_ceiling_is_re_checked_at_approval(store):
    """Write-time validation cannot see the deltas queued behind it.

    ``approve_delta`` still runs for every ``--approve`` and every
    ``--drop-section``, and a row staged by hand was validated against whatever
    was active when it was written. N of them can each pass alone and still blow
    the overlay budget once activated in turn, so the ceiling has to hold here
    too.
    """
    # Sized so each delta clears the ceiling alone but the three together do not.
    filler = "\n".join(f"- padding line {n} to grow the section." for n in range(13))

    staged = [
        store.put_delta(
            base_name="github-triage",
            scope=SCOPE,
            kind=KIND_REPLACE_SECTION,
            anchor_section=section,
            anchor_digest=_digest_of(BASE_SKILL, section),
            payload={"body": f"## {section.title()}\n\n{filler}"},
            provenance=dict(TRUSTED),
        )
        for section in ("setup", "rules", "fork-this")
    ]

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


def _stage(store, body):
    """A staged row, the way ``--drop-section`` and a hand edit make one."""
    return store.put_delta(
        base_name="github-triage",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest_of(BASE_SKILL, "procedure"),
        payload={"body": body},
        provenance=dict(TRUSTED),
    )


def test_a_superseded_staged_delta_cannot_be_approved(store):
    """No success receipt for a change that can never apply.

    A staged revision retires the staged row it replaces. Approving the older
    id — the one the user may still have in front of them — must fail, not
    report success and silently activate a row that can never resolve.
    """
    first = _stage(store, INBOX_PROCEDURE)
    second = _stage(store, INBOX_PROCEDURE + "\n\n5. Revised.")
    store.supersede_delta(first, second)

    assert approve_delta(store, first, BASE_SKILL) is None
    assert store.search_deltas(base_name="github-triage", status="active") == []

    assert approve_delta(store, second, BASE_SKILL) is not None
    live = store.search_deltas(base_name="github-triage", status="active")
    assert [r["id"] for r in live] == [second]


def test_approval_is_bound_to_the_skill_the_user_named(store):
    """An id from another skill must not be approved under this one's name."""
    staged = _stage(store, INBOX_PROCEDURE)

    assert approve_delta(store, staged, BASE_SKILL, base_name="other-skill") is None
    assert store.search_deltas(base_name="github-triage", status="active") == []

    assert (
        approve_delta(store, staged, BASE_SKILL, base_name="github-triage") is not None
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


def test_the_tool_refuses_to_write_while_the_off_switch_is_on(store, monkeypatch):
    """Off means nothing is learned, not learned-invisibly.

    Resolution ignores everything under the switch, so a row written now would
    sit in a store the user has said they do not want and cannot see the effect
    of. Refusing tells the model to give the correction to the user instead.
    """
    agent = _LearningAgent(store, _FakeSkill("github-triage", BASE_SKILL))
    agent.learned_skills_enabled = lambda: False

    result = agent.call(
        skill="github-triage",
        section="rules",
        corrected_text="A correction the user will never see.",
    )

    assert result["status"] == "error"
    assert "switched off" in result["message"]
    assert "GAIA_NO_LEARNED_SKILLS" in result["message"]
    assert store.search_deltas(base_name="github-triage", scope=SCOPE) == []
