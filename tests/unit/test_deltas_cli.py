# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Tests for ``gaia skill deltas`` — the legibility surface for adaptive skills.

Everything here runs through the shipped CLI: the real ``gaia.skills.cli``
argparse tree, the real ``handle()`` dispatch, and therefore the real skill
loader — not ``handle_deltas`` called with a hand-built Namespace. A flag that
is never registered, or a dispatch entry that is never wired, fails here.

The command's job is consent, so the assertions are about what a user is
allowed to be surprised by:

* an unknown id never silently succeeds (``EXIT_USAGE``, not a cheerful "done"),
* ``--reset`` archives and never deletes — every row is still inspectable after,
* ``--drop-section`` refuses without ``--scope`` rather than attaching a removal
  to no agent at all,
* ``--diff`` and ``--json`` describe the skill this agent actually runs.

Every run starts cold: ``GAIA_CONFIG_DIR``, ``HOME``, the CWD, and the memory
database all point inside ``tmp_path``, so no test reads or writes the
developer's real ``~/.gaia/skills`` or ``~/.gaia/memory.db``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from gaia.agents.base.memory_store import MemoryStore
from gaia.agents.base.skill_deltas import (
    KIND_DROP_SECTION,
    KIND_REPLACE_SECTION,
    KIND_REPLACE_SNIPPET,
)
from gaia.skills import cli as skills_cli
from gaia.skills.sections import parse_sections
from tests.unit.skills_helpers import write_skill_dir

SKILL_NAME = "learned-demo"
SCOPE = "gaia:test-agent"
OTHER_SCOPE = "gaia:other-agent"
TRUSTED = {"source": "user_instruction", "reason": "the user said so", "turns": [4]}

SKILL_MD = """---
name: learned-demo
description: A skill that exists to exercise `gaia skill deltas`. Test-only.
license: MIT
version: 1.0.0
---

# Learned Demo

The shipped instructions, before anything was learned about them.

## Setup

Authenticate once, interactively.

## Procedure

1. **Pull the backlog.** Ask for the repository if the user did not name one.
2. **Group before judging.** Report the cluster, not each issue.

## Fork this

Point it at your own repo's labels and severity ladder.
"""

NEW_PROCEDURE = """## Procedure

1. **Pull what landed on you.** Default to the user's own inbox.
2. **Say what to do next** — one concrete action per item."""


@pytest.fixture
def db(tmp_path) -> Path:
    """Path to a throwaway memory database — never the developer's ~/.gaia."""
    return tmp_path / "memory.db"


@pytest.fixture
def store(db) -> MemoryStore:
    return MemoryStore(db_path=db)


@pytest.fixture
def skill_body(tmp_path, monkeypatch) -> str:
    """Install the demo skill into a cold ``~/.gaia/skills`` and return its body."""
    home = tmp_path / "gaia-home"
    skills = home / "skills"
    skills.mkdir(parents=True)
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir()

    monkeypatch.setenv("GAIA_CONFIG_DIR", str(home))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.chdir(workdir)
    # Path.home() is consulted directly for ~/.claude/skills discovery.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fake_home))

    write_skill_dir(skills, SKILL_NAME, SKILL_MD)

    from gaia.skills.format import parse_skill

    return parse_skill(SKILL_MD, source="<test>").body


@pytest.fixture
def run(skill_body, db, capsys):
    """Parse and dispatch ``gaia skill deltas …`` in-process; return (rc, out, err)."""

    def _run(*args: str):
        parser = argparse.ArgumentParser(prog="gaia")
        subparsers = parser.add_subparsers(dest="action")
        skills_cli.add_subparser(subparsers)
        parsed = parser.parse_args(
            ["skill", "deltas", SKILL_NAME, "--db", str(db), *args]
        )
        rc = skills_cli.handle(parsed)
        captured = capsys.readouterr()
        return rc, captured.out, captured.err

    return _run


def _digest(body: str, slug: str) -> str:
    return next(s for s in parse_sections(body) if s.slug == slug).digest


def _seed(
    store: MemoryStore,
    body: str,
    *,
    kind: str = KIND_REPLACE_SECTION,
    section: str = "procedure",
    payload: dict | None = None,
    scope: str = SCOPE,
    approve: bool = False,
    digest: str | None = None,
) -> str:
    """Stage one delta (optionally approving it) and return its id."""
    delta_id = store.put_delta(
        base_name=SKILL_NAME,
        scope=scope,
        kind=kind,
        anchor_section=section,
        anchor_digest=digest if digest is not None else _digest(body, section),
        payload=payload if payload is not None else {"body": NEW_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    if approve:
        assert store.approve_delta(delta_id) is True
    return delta_id


def _row(store: MemoryStore, delta_id: str) -> dict:
    rows = store.search_deltas(delta_id=delta_id, include_superseded=True)
    assert rows, f"delta {delta_id} vanished from the store"
    return rows[0]


# ----------------------------------------------------------------------
# --approve
# ----------------------------------------------------------------------


def test_approve_activates_a_staged_delta(run, store, skill_body):
    delta_id = _seed(store, skill_body)
    assert _row(store, delta_id)["status"] == "staged"

    rc, out, _ = run("--approve", delta_id)

    assert rc == 0
    assert delta_id in out and "Approved" in out
    row = _row(store, delta_id)
    assert row["status"] == "active"
    assert row["approved_at"] is not None


def test_approve_an_unknown_id_is_a_usage_error(run, store, skill_body):
    _seed(store, skill_body)

    rc, out, err = run("--approve", "delta_does_not_exist")

    assert rc == 2
    assert "delta_does_not_exist" in err
    assert "--pending" in err, "the error must name how to find the real ids"
    assert out == ""


def test_approving_twice_is_refused_rather_than_reported_as_done(
    run, store, skill_body
):
    """Consent is recorded once; a second approval is not a silent no-op success."""
    delta_id = _seed(store, skill_body, approve=True)
    approved_at = _row(store, delta_id)["approved_at"]

    rc, _, err = run("--approve", delta_id)

    assert rc == 2
    assert delta_id in err
    assert _row(store, delta_id)["approved_at"] == approved_at


# ----------------------------------------------------------------------
# --revert
# ----------------------------------------------------------------------


def test_revert_archives_the_delta_without_deleting_it(run, store, skill_body):
    delta_id = _seed(store, skill_body, approve=True)

    rc, out, _ = run("--revert", delta_id)

    assert rc == 0
    assert "archived, not deleted" in out
    assert _row(store, delta_id)["status"] == "archived"


def test_a_reverted_delta_stops_affecting_the_skill(run, store, skill_body):
    delta_id = _seed(store, skill_body, approve=True)
    assert "Pull what landed on you" in run("--diff", "--scope", SCOPE)[1]

    run("--revert", delta_id)

    rc, out, _ = run("--diff", "--scope", SCOPE)
    assert rc == 0
    assert "no difference" in out


def test_revert_of_an_unknown_id_is_a_usage_error(run, store, skill_body):
    _seed(store, skill_body, approve=True)

    rc, out, err = run("--revert", "delta_nope")

    assert rc == 2
    assert "delta_nope" in err
    assert SKILL_NAME in err
    assert out == ""


# ----------------------------------------------------------------------
# --reset — the destructive one
# ----------------------------------------------------------------------


def test_reset_archives_every_delta_and_deletes_nothing(run, store, skill_body):
    """The blast radius: all scopes, all statuses, and every row survives."""
    ids = [
        _seed(store, skill_body, approve=True),
        _seed(store, skill_body, section="setup", approve=True),
        _seed(
            store,
            skill_body,
            section="fork-this",
            kind=KIND_DROP_SECTION,
            payload={},
            scope=OTHER_SCOPE,
            approve=True,
        ),
        _seed(store, skill_body, section="learned-demo"),  # still staged
    ]

    rc, out, _ = run("--reset")

    assert rc == 0
    assert f"archived {len(ids)} learned change(s)" in out
    assert "Nothing was deleted" in out

    surviving = store.search_deltas(base_name=SKILL_NAME, include_superseded=True)
    assert {r["id"] for r in surviving} == set(ids), "a row was hard-deleted"
    assert {r["status"] for r in surviving} == {"archived"}


def test_reset_leaves_the_agent_running_the_shipped_skill(run, store, skill_body):
    _seed(store, skill_body, approve=True)
    assert "Pull what landed on you" in run("--diff", "--scope", SCOPE)[1]

    run("--reset")

    rc, out, _ = run("--diff", "--scope", SCOPE)
    assert rc == 0
    assert "no difference" in out
    assert store.search_deltas(base_name=SKILL_NAME, status="active") == []


def test_reset_with_nothing_learned_reports_zero(run, store, skill_body):
    rc, out, _ = run("--reset")

    assert rc == 0
    assert "archived 0 learned change(s)" in out


def test_reset_counts_only_what_it_actually_archived(run, store, skill_body):
    """A second --reset has nothing left to retire, and must say so.

    The count is the only feedback that anything happened, so re-reporting rows
    an earlier run already archived is a confident wrong answer.
    """
    _seed(store, skill_body, approve=True)
    _seed(store, skill_body, section="setup", approve=True)

    assert "archived 2 learned change(s)" in run("--reset")[1]
    assert "archived 0 learned change(s)" in run("--reset")[1]


def test_reset_is_scoped_to_the_named_skill(run, store, skill_body):
    """Another skill's learning is not collateral damage."""
    mine = _seed(store, skill_body, approve=True)
    theirs = store.put_delta(
        base_name="some-other-skill",
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest(skill_body, "procedure"),
        payload={"body": NEW_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    store.approve_delta(theirs)

    run("--reset")

    assert _row(store, mine)["status"] == "archived"
    assert _row(store, theirs)["status"] == "active"


# ----------------------------------------------------------------------
# --drop-section
# ----------------------------------------------------------------------


def test_drop_section_removes_it_for_one_scope(run, store, skill_body):
    rc, out, _ = run("--drop-section", "fork-this", "--scope", SCOPE)

    assert rc == 0
    assert "fork-this" in out
    assert "shipped skill file is unchanged" in out

    rows = store.search_deltas(base_name=SKILL_NAME, scope=SCOPE, status="active")
    assert len(rows) == 1
    assert rows[0]["kind"] == KIND_DROP_SECTION
    assert rows[0]["anchor_section"] == "fork-this"
    # Applied without a second consent step — a human removal IS the consent.
    assert rows[0]["approved_at"] is not None

    diff = run("--diff", "--scope", SCOPE)[1]
    assert "-## Fork this" in diff


def test_drop_section_only_affects_the_scope_it_was_written_for(run, store, skill_body):
    run("--drop-section", "fork-this", "--scope", SCOPE)

    assert "no difference" in run("--diff", "--scope", OTHER_SCOPE)[1]


def test_drop_section_refuses_an_unknown_slug_and_lists_the_real_ones(
    run, store, skill_body
):
    rc, out, err = run("--drop-section", "no-such-section", "--scope", SCOPE)

    assert rc == 2
    assert "no-such-section" in err
    for slug in ("setup", "procedure", "fork-this"):
        assert slug in err
    assert out == ""
    assert store.search_deltas(base_name=SKILL_NAME) == []


def test_drop_section_refuses_without_a_scope(run, store, skill_body):
    """A removal attached to no agent would be a change nobody consented to."""
    rc, out, err = run("--drop-section", "fork-this")

    assert rc == 2
    assert "--scope" in err
    assert "agent id" in err
    assert out == ""
    assert store.search_deltas(base_name=SKILL_NAME) == []


# ----------------------------------------------------------------------
# --diff
# ----------------------------------------------------------------------


def test_diff_shows_what_this_agent_actually_runs(run, store, skill_body):
    _seed(store, skill_body, approve=True)

    rc, out, _ = run("--diff", "--scope", SCOPE)

    assert rc == 0
    assert "authored SKILL.md" in out
    assert "effective (with learned changes)" in out
    assert "-1. **Pull the backlog.**" in out
    assert "+1. **Pull what landed on you.**" in out


def test_diff_with_nothing_learned_says_so(run, store, skill_body):
    rc, out, _ = run("--diff", "--scope", SCOPE)

    assert rc == 0
    assert "no difference" in out
    assert "exactly as shipped" in out


def test_diff_does_not_show_another_agents_learning(run, store, skill_body):
    """A diff must describe one runnable skill, not a merge of several agents'."""
    _seed(store, skill_body, scope=OTHER_SCOPE, approve=True)

    rc, out, _ = run("--diff", "--scope", SCOPE)

    assert rc == 0
    assert "no difference" in out


def test_diff_previews_a_staged_change_before_it_is_approved(run, store, skill_body):
    _seed(store, skill_body)  # staged, never approved

    assert "no difference" in run("--diff", "--scope", SCOPE)[1]

    rc, out, _ = run("--diff", "--pending", "--scope", SCOPE)
    assert rc == 0
    assert "+1. **Pull what landed on you.**" in out


# ----------------------------------------------------------------------
# --json
# ----------------------------------------------------------------------


def test_json_emits_the_documented_keys(run, store, skill_body):
    delta_id = _seed(store, skill_body, approve=True)

    rc, out, _ = run("--json", "--scope", SCOPE)

    assert rc == 0
    payload = json.loads(out)
    assert set(payload) == {
        "skill",
        "authored_tokens",
        "effective_tokens",
        "token_delta",
        "deltas",
        "notes",
    }
    assert payload["skill"] == SKILL_NAME
    assert payload["token_delta"] == (
        payload["effective_tokens"] - payload["authored_tokens"]
    )
    assert [d["id"] for d in payload["deltas"]] == [delta_id]
    assert payload["deltas"][0]["kind"] == KIND_REPLACE_SECTION
    assert payload["notes"] == [
        {
            "delta": delta_id,
            "section": "procedure",
            "outcome": "applied",
            "detail": "section replaced",
        }
    ]


def test_json_reports_a_delta_that_no_longer_applies(run, store, skill_body):
    """A stale anchor is surfaced in ``notes``, not silently dropped."""
    delta_id = _seed(store, skill_body, approve=True, digest="sha256:stale")

    rc, out, _ = run("--json", "--scope", SCOPE)

    assert rc == 0
    payload = json.loads(out)
    assert payload["token_delta"] == 0, "the authored text must stand"
    note = next(n for n in payload["notes"] if n["delta"] == delta_id)
    assert note["outcome"] == "stale"
    assert "re-approve" in note["detail"]


def test_json_with_nothing_learned_is_still_valid_json(run, store, skill_body):
    rc, out, _ = run("--json", "--scope", SCOPE)

    assert rc == 0
    payload = json.loads(out)
    assert payload["deltas"] == []
    assert payload["token_delta"] == 0
    assert payload["authored_tokens"] > 0


# ----------------------------------------------------------------------
# the default text view
# ----------------------------------------------------------------------


def test_the_default_view_explains_each_learned_change(run, store, skill_body):
    delta_id = _seed(
        store,
        skill_body,
        kind=KIND_REPLACE_SNIPPET,
        payload={"old": "Group before judging", "new": "Cluster duplicates first"},
        approve=True,
    )

    rc, out, _ = run("--scope", SCOPE)

    assert rc == 0
    assert delta_id in out
    assert KIND_REPLACE_SNIPPET in out
    assert SCOPE in out
    assert "the user said so" in out, "a learned change must say WHY"
    assert "effective skill:" in out
    # ...and every listed change names the way to undo it.
    assert "--revert" in out and "--reset" in out


def test_the_default_view_from_a_cold_state(run, store, skill_body):
    rc, out, _ = run("--scope", SCOPE)

    assert rc == 0
    assert "none — this agent runs the skill exactly as shipped." in out


def test_pending_shows_staged_changes_the_default_view_hides(run, store, skill_body):
    delta_id = _seed(store, skill_body)

    assert delta_id not in run("--scope", SCOPE)[1]

    rc, out, _ = run("--pending", "--scope", SCOPE)
    assert rc == 0
    assert delta_id in out
    assert "awaiting approval" in out


# ----------------------------------------------------------------------
# Consent-surface regressions
#
# Every test below reproduced a real defect before its fix. They are grouped
# because they share one failure mode: the command printed a success receipt
# for something it had not done, which is the one thing a consent surface may
# never do.
# ----------------------------------------------------------------------

OTHER_SKILL = "someone-elses-skill"


def _seed_foreign(store: MemoryStore, body: str) -> str:
    """One active delta belonging to a DIFFERENT skill."""
    delta_id = store.put_delta(
        base_name=OTHER_SKILL,
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest(body, "procedure"),
        payload={"body": NEW_PROCEDURE},
        provenance=dict(TRUSTED),
    )
    assert store.approve_delta(delta_id) is True
    return delta_id


def test_revert_cannot_reach_another_skills_change(run, store, skill_body):
    """`--revert <id>` must be bound to the skill the user named."""
    foreign = _seed_foreign(store, skill_body)

    rc, out, err = run("--revert", foreign)

    assert rc == 2, "reverting another skill's id must fail, not succeed"
    assert out == ""
    assert "another skill" in err
    assert _row(store, foreign)["status"] == "active", "the other skill was touched"


def test_revert_cannot_reach_another_scopes_change(run, store, skill_body):
    """--scope narrows --revert too, or one agent can undo another's learning."""
    theirs = _seed(store, skill_body, scope=OTHER_SCOPE, approve=True)

    rc, _, err = run("--scope", SCOPE, "--revert", theirs)

    assert rc == 2
    assert _row(store, theirs)["status"] == "active"
    assert SCOPE in err


def test_a_reverted_change_is_listed_by_archived(run, store, skill_body):
    """ "Kept for inspection" has to mean some command actually lists it."""
    delta_id = _seed(store, skill_body, approve=True)
    rc, out, _ = run("--scope", SCOPE, "--revert", delta_id)
    assert rc == 0
    assert "--archived" in out, "the receipt must name where the row went"

    rc, out, _ = run("--scope", SCOPE, "--archived")

    assert rc == 0
    assert delta_id in out
    assert "archived" in out
    # ...and it is gone from the view of what the agent runs.
    assert delta_id not in run("--scope", SCOPE)[1]


def test_reverting_twice_does_not_report_a_second_success(run, store, skill_body):
    delta_id = _seed(store, skill_body, approve=True)
    assert run("--scope", SCOPE, "--revert", delta_id)[0] == 0

    rc, out, err = run("--scope", SCOPE, "--revert", delta_id)

    assert rc == 2, "an already-reverted change must not report a fresh revert"
    assert out == ""
    assert "already be reverted" in err


def test_two_actions_at_once_is_refused_not_silently_dropped(run, store, skill_body):
    """`--approve X --revert Y` used to run the first and exit 0."""
    staged = _seed(store, skill_body)
    active = _seed(
        store, skill_body, section="setup", payload={"body": "## Setup\n\nx"}
    )
    store.approve_delta(active)

    with pytest.raises(SystemExit) as exc:
        run("--scope", SCOPE, "--approve", staged, "--revert", active)

    assert exc.value.code == 2
    assert _row(store, staged)["status"] == "staged", "nothing may have happened"
    assert _row(store, active)["status"] == "active"


def test_pending_diff_shows_what_approving_would_actually_give(run, store, skill_body):
    """The consent diff must include the deltas already live, not just the staged one."""
    _seed(
        store,
        skill_body,
        kind=KIND_REPLACE_SNIPPET,
        section="setup",
        payload={"old": "Authenticate once", "new": "Authenticate with a token"},
        approve=True,
    )
    _seed(store, skill_body)  # staged replacement of `procedure`

    rc, out, _ = run("--scope", SCOPE, "--pending", "--diff")

    assert rc == 0
    assert (
        "Authenticate with a token" in out
    ), "the live change is missing from the diff"
    assert "Pull what landed on you" in out, "the staged change is missing"


def test_a_reason_cannot_repaint_the_terminal(run, store, skill_body):
    """The `why` line is model-authored and is read to make the approval call."""
    delta_id = store.put_delta(
        base_name=SKILL_NAME,
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest(skill_body, "procedure"),
        payload={"body": NEW_PROCEDURE},
        provenance={
            "source": "user_instruction",
            "reason": f"harmless{chr(27)}[2J{chr(27)}[H  APPROVED BY AMD",
        },
    )
    assert store.approve_delta(delta_id) is True

    rc, out, _ = run("--scope", SCOPE)

    assert rc == 0
    assert chr(27) not in out, "an escape sequence reached the terminal"
    assert r"\x1b" in out, "the escape should be shown, not silently dropped"
    assert "APPROVED BY AMD" in out, "the text itself must still be readable"


def test_reset_is_confined_to_the_named_skill(run, store, skill_body):
    foreign = _seed_foreign(store, skill_body)
    mine = _seed(store, skill_body, approve=True)

    rc, _, _ = run("--reset")

    assert rc == 0
    assert _row(store, mine)["status"] == "archived"
    assert _row(store, foreign)["status"] == "active", "--reset crossed skills"


def test_the_resolver_survives_a_corrupted_payload(store, skill_body):
    """A hand-edited row must not take the skills block down with a traceback."""
    from gaia.agents.base.skill_deltas import SkillDelta, resolve_skill_body

    broken = SkillDelta(
        id="d-broken",
        base_name=SKILL_NAME,
        scope=SCOPE,
        kind=KIND_REPLACE_SECTION,
        anchor_section="procedure",
        anchor_digest=_digest(skill_body, "procedure"),
        payload=["not", "an", "object"],
        provenance=dict(TRUSTED),
        status="active",
    )

    resolved = resolve_skill_body(skill_body, [broken])

    assert resolved.body == skill_body, "the authored text must stand"
    assert [n.outcome for n in resolved.notes] == ["malformed_payload"]


def test_approving_a_delta_anchored_to_changed_text_is_refused(run, store, skill_body):
    """A stale anchor resolves to "authored text kept" — approving is a no-op.

    This is what actually goes wrong when the command resolves a different copy
    of the skill than the agent runs, and it is detectable directly, without
    guessing from which root each side loaded.
    """
    delta_id = _seed(store, skill_body, digest="sha256:not-the-current-section")

    rc, out, err = run("--scope", SCOPE, "--approve", delta_id)

    assert rc == 2
    assert out == ""
    assert "would change nothing" in err.replace("\n", " ").replace("  ", " ")
    assert "procedure" in err
    assert _row(store, delta_id)["status"] == "staged"


def test_a_current_anchor_still_approves(run, store, skill_body):
    """The stale-anchor gate must not block the ordinary case."""
    delta_id = _seed(store, skill_body)

    rc, _, err = run("--scope", SCOPE, "--approve", delta_id)

    assert rc == 0, err
    assert _row(store, delta_id)["status"] == "active"


def test_an_inert_staged_delta_cannot_veto_drop_section(run, store, skill_body):
    """A staged proposal is the model's; it must not block a human action."""
    _seed(store, skill_body, digest="sha256:stale-and-never-approved")

    rc, out, err = run("--scope", SCOPE, "--drop-section", "fork-this")

    assert rc == 0, err
    assert "Removed section" in out


def test_the_pending_diff_shows_the_staged_text_winning(run, store, skill_body):
    """Approving supersedes the active delta, so the preview must too."""
    _seed(
        store,
        skill_body,
        payload={"body": "## Procedure\n\nACTIVE-OLD"},
        approve=True,
    )
    _seed(store, skill_body, payload={"body": "## Procedure\n\nSTAGED-WINS"})

    rc, out, _ = run("--scope", SCOPE, "--pending", "--diff")

    assert rc == 0
    assert "STAGED-WINS" in out
    assert "ACTIVE-OLD" not in out, "the delta approval would retire won the preview"


def test_reverting_a_superseded_delta_is_refused(run, store, skill_body):
    """--revert promises --archived lists it, and --archived hides superseded rows."""
    old = _seed(store, skill_body, approve=True)
    new = _seed(store, skill_body)
    store.supersede_delta(old, new)

    rc, out, err = run("--scope", SCOPE, "--revert", old)

    assert rc == 2, "a row that already cannot apply must not report a fresh revert"
    assert out == ""
    assert _row(store, old)["status"] == "active"


def test_the_cli_survives_a_corrupted_payload_on_every_path(run, store, skill_body):
    """A hand-edited row must not hand the user a traceback."""
    import json as _json
    import sqlite3

    delta_id = _seed(store, skill_body, approve=True)
    conn = sqlite3.connect(store._db_path)
    conn.execute(
        "UPDATE skill_deltas SET payload = ? WHERE id = ?",
        (_json.dumps(["not", "an", "object"]), delta_id),
    )
    conn.commit()
    conn.close()

    for argv in (
        ("--scope", SCOPE),
        ("--scope", SCOPE, "--diff"),
        ("--scope", SCOPE, "--json"),
    ):
        rc, out, _ = run(*argv)
        assert rc == 0, f"{argv} raised instead of reporting"
        assert out, f"{argv} printed nothing"
