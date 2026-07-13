# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Unit tests for the adaptive bootstrap conversation (issue #1955).

Covers the day-zero onboarding core in ``gaia.agents.base.bootstrap``: role
branching, timezone capture, entity linking, work-context scoping, context
suggestion, the show-before-store review gate, cancellation, and the fail-loud
paths — plus the ``MemoryMixin.run_bootstrap_conversation`` wrapper.

Every storage assertion runs against a real ``MemoryStore`` on a temp sqlite
file, so they assert the shape of the rows that were actually written, not that
a mock was called.

No Lemonade and no mocked LLM: the conversation core takes an injected store and
injected IO by design, which is exactly what keeps ``gaia memory bootstrap
--chat-only`` working with no embedding backend. The filename must keep its
``test_memory_`` prefix — tests/unit/conftest.py keys on it to clear
``GAIA_MEMORY_DISABLED``.
"""

import sqlite3

import numpy as np
import pytest

from gaia.agents.base.bootstrap import (
    BOOTSTRAP_GRAPH,
    BootstrapCancelled,
    BootstrapQuestion,
    ProposedEntry,
    run_bootstrap_conversation,
)
from gaia.agents.base.memory_store import MemoryStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REVIEW_PREFIX = "Store this?"

#: A complete engineer run: name, role, engineering follow-up, use cases,
#: communication style, timezone, tools, interests, extra.
_ENGINEER_ANSWERS = [
    "Alex",
    "senior software engineer",
    "Python monorepo, pytest, GitHub Actions",
    "Work coding, personal task management, and learning",
    "concise and casual",
    "America/Los_Angeles",
    "Python, TypeScript, VS Code, git",
    "hiking and chess",
    "I prefer metric units",
]

#: Entries the answers above propose: one per question (9), plus the context
#: suggestion, plus the tools answer's global mirror and its IDE fact.
_ENGINEER_ENTRY_COUNT = 12


class ScriptedIO:
    """Scripted ``prompt_fn``/``output_fn`` pair standing in for a human.

    Answers questions from *answers* in order (an exhausted queue means "skip"),
    and review prompts from *reviews* (an exhausted queue means "approve", the
    default). Raises ``BootstrapCancelled`` when the configured cancel point is
    reached, mimicking the CLI adapter's EOF / Ctrl-C translation.
    """

    def __init__(
        self, answers, reviews=None, cancel_at_question=None, cancel_at_review=None
    ):
        self._answers = list(answers)
        self._reviews = list(reviews or [])
        self._cancel_at_question = cancel_at_question
        self._cancel_at_review = cancel_at_review
        self.questions_asked: list[str] = []
        self.reviews_shown: int = 0
        self.output: list[str] = []

    def prompt(self, text: str) -> str:
        if text.startswith(_REVIEW_PREFIX):
            self.reviews_shown += 1
            if self.reviews_shown == self._cancel_at_review:
                raise BootstrapCancelled("cancelled at review")
            return self._reviews.pop(0) if self._reviews else ""

        self.questions_asked.append(text)
        if len(self.questions_asked) == self._cancel_at_question:
            raise BootstrapCancelled("cancelled at question")
        return self._answers.pop(0) if self._answers else ""

    def out(self, line: str) -> None:
        self.output.append(line)


def _rows(store: MemoryStore) -> list[dict]:
    """Every active knowledge row, newest first."""
    return store.get_all_knowledge(limit=100)["items"]


def _contents(store: MemoryStore) -> list[str]:
    return [row["content"] for row in _rows(store)]


def _prompt_of(node_id: str) -> str:
    return BOOTSTRAP_GRAPH[node_id].prompt


@pytest.fixture
def store(tmp_path):
    """A real MemoryStore on a temp sqlite file — no Lemonade needed."""
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


# ---------------------------------------------------------------------------
# 1. Happy path — stored row shape (contract-shape assert)
# ---------------------------------------------------------------------------


def test_full_flow_stores_rows_with_the_documented_shape(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    rows = _rows(store)
    assert result.stored == len(rows) > 0
    assert result.skipped == 0
    assert result.rejected == 0
    assert result.cancelled is False
    assert result.answers["name"] == "Alex"

    # Every onboarding row is user-sourced (reset-safety) and high-confidence.
    for row in rows:
        assert row["source"] == "user"
        assert row["confidence"] == pytest.approx(0.8)

    by_content = {row["content"]: row for row in rows}

    name_row = by_content["User's name is Alex"]
    assert (name_row["category"], name_row["context"]) == ("profile", "global")

    style_row = by_content["Preferred communication style: concise and casual"]
    assert (style_row["category"], style_row["context"]) == ("preference", "global")

    stack_row = by_content["Primary stack: Python, TypeScript, VS Code, git"]
    assert (stack_row["category"], stack_row["context"]) == ("fact", "work")
    assert stack_row["entity"] == "app:vscode"


# ---------------------------------------------------------------------------
# 2. Role branching (AC #1)
# ---------------------------------------------------------------------------


def test_student_role_branches_to_coursework_not_engineering(store):
    io = ScriptedIO(["Ana", "I'm a student"])

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert _prompt_of("coursework") in io.questions_asked
    assert _prompt_of("eng_workflow") not in io.questions_asked


def test_engineer_role_branches_to_engineering_workflow(store):
    io = ScriptedIO(["Alex", "senior software engineer"])

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert _prompt_of("eng_workflow") in io.questions_asked
    assert _prompt_of("coursework") not in io.questions_asked


def test_unmatched_role_branches_to_the_generic_follow_up(store):
    io = ScriptedIO(["Sam", "I run a bakery"])

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert _prompt_of("generic_focus") in io.questions_asked


def test_role_keywords_match_on_word_boundaries(store):
    """'I build things' must not route to the designer branch via 'ui' in 'build'."""
    io = ScriptedIO(["Sam", "I build things"])

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert _prompt_of("design_tools") not in io.questions_asked
    assert _prompt_of("generic_focus") in io.questions_asked


# ---------------------------------------------------------------------------
# 3. Timezone
# ---------------------------------------------------------------------------


def test_timezone_is_asked_and_stored(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert _prompt_of("timezone") in io.questions_asked
    tz_rows = [
        r for r in _rows(store) if r["content"] == "Timezone: America/Los_Angeles"
    ]
    assert len(tz_rows) == 1
    assert (tz_rows[0]["category"], tz_rows[0]["context"]) == ("profile", "global")


# ---------------------------------------------------------------------------
# 4. Entity linking — the tools answer emits two facts
# ---------------------------------------------------------------------------


def test_tools_answer_emits_stack_fact_and_ide_fact_with_entity(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    ide_rows = [r for r in _rows(store) if r["entity"] == "app:vscode"]
    assert len(ide_rows) == 2
    assert {r["content"] for r in ide_rows} == {
        "Primary stack: Python, TypeScript, VS Code, git",
        "Uses VS Code as primary IDE",
    }
    for row in ide_rows:
        assert (row["category"], row["context"]) == ("fact", "work")


def test_tools_answer_also_mirrors_the_stack_into_the_global_profile(store):
    """A work-only stack would never reach the system prompt (global context)."""
    io = ScriptedIO(_ENGINEER_ANSWERS)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    # This is exactly what the prompt builder reads for a default chat session.
    profile = store.get_by_category_contexts("profile", "global", limit=20)
    assert "User's primary tools and languages: Python, TypeScript, VS Code, git" in [
        row["content"] for row in profile
    ]


def test_tools_answer_without_an_ide_emits_no_entity_and_no_ide_fact(store):
    answers = list(_ENGINEER_ANSWERS)
    answers[6] = "Python, TypeScript, git"
    io = ScriptedIO(answers)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert [r for r in _rows(store) if r["entity"]] == []
    assert "Primary stack: Python, TypeScript, git" in _contents(store)
    assert "Uses VS Code as primary IDE" not in _contents(store)


# ---------------------------------------------------------------------------
# 5. Context scoping (AC #2)
# ---------------------------------------------------------------------------


def test_work_facts_are_scoped_to_work_and_identity_stays_global(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    contexts = {row["content"]: row["context"] for row in _rows(store)}
    assert contexts["Primary stack: Python, TypeScript, VS Code, git"] == "work"
    assert (
        contexts["User's engineering workflow: Python monorepo, pytest, GitHub Actions"]
        == "work"
    )
    assert contexts["User's name is Alex"] == "global"
    assert contexts["Timezone: America/Los_Angeles"] == "global"


# ---------------------------------------------------------------------------
# 6. Context suggestion
# ---------------------------------------------------------------------------


def test_multi_context_use_cases_propose_a_context_suggestion(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    suggestions = [
        r for r in _rows(store) if r["content"].startswith("Uses GAIA across")
    ]
    assert len(suggestions) == 1
    assert suggestions[0]["content"] == (
        "Uses GAIA across these contexts: work, personal, learning"
    )
    assert (suggestions[0]["category"], suggestions[0]["context"]) == (
        "profile",
        "global",
    )


def test_single_context_use_cases_propose_no_suggestion(store):
    answers = list(_ENGINEER_ANSWERS)
    answers[3] = "work coding"
    io = ScriptedIO(answers)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert not [r for r in _rows(store) if r["content"].startswith("Uses GAIA across")]


# ---------------------------------------------------------------------------
# 7. Review gate — nothing is stored without approval
# ---------------------------------------------------------------------------


def test_rejected_entry_is_not_stored(store):
    # First proposed entry is the name; reject it, approve the rest.
    io = ScriptedIO(_ENGINEER_ANSWERS, reviews=["n"])

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.rejected == 1
    assert "User's name is Alex" not in _contents(store)
    assert "Timezone: America/Los_Angeles" in _contents(store)


def test_quit_stops_the_review_but_keeps_earlier_approvals(store):
    # Approve the first entry, then quit.
    io = ScriptedIO(_ENGINEER_ANSWERS, reviews=["y", "q"])

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.stored == 1
    assert _contents(store) == ["User's name is Alex"]
    assert result.cancelled is False
    # The entries that were never shown are reported, not silently dropped.
    assert result.unreviewed == _ENGINEER_ENTRY_COUNT - 1
    assert result.stored + result.rejected + result.unreviewed == _ENGINEER_ENTRY_COUNT


def test_spelled_out_no_declines_and_never_stores(store):
    """'no' must not be read as approval — only Y / Enter stores."""
    io = ScriptedIO(_ENGINEER_ANSWERS, reviews=["no"])

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.rejected == 1
    assert "User's name is Alex" not in _contents(store)


def test_unrecognised_review_answer_is_re_asked_then_declined(store):
    """A typo is never taken as a yes; after re-asking, the entry is left out."""
    io = ScriptedIO(["Alex"], reviews=["huh", "wat", "zzz"])

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.stored == 0
    assert result.rejected == 1
    assert _rows(store) == []
    assert any("isn't Y, n, or q" in line for line in io.output)


# ---------------------------------------------------------------------------
# 8. Empty answers
# ---------------------------------------------------------------------------


def test_all_questions_skipped_stores_nothing(store):
    io = ScriptedIO([])

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.stored == 0
    assert result.skipped == len(io.questions_asked) > 0
    assert result.knowledge_ids == []
    assert _rows(store) == []
    assert io.reviews_shown == 0


# ---------------------------------------------------------------------------
# 9. Cancellation
# ---------------------------------------------------------------------------


def test_cancel_during_questions_stores_nothing(store):
    io = ScriptedIO(_ENGINEER_ANSWERS, cancel_at_question=3)

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.cancelled is True
    assert result.stored == 0
    assert result.knowledge_ids == []
    assert _rows(store) == []
    assert io.reviews_shown == 0


def test_cancel_during_review_keeps_earlier_approvals(store):
    io = ScriptedIO(_ENGINEER_ANSWERS, cancel_at_review=2)

    result = run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert result.cancelled is True
    assert result.stored == 1
    assert _contents(store) == ["User's name is Alex"]


# ---------------------------------------------------------------------------
# 10. Cold / no-embedder contract — the CLI path
# ---------------------------------------------------------------------------


def test_without_an_on_stored_hook_rows_are_stored_but_never_embedded(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    result = run_bootstrap_conversation(
        store, prompt_fn=io.prompt, output_fn=io.out, on_stored=None
    )

    coverage = store.get_embedding_coverage()
    assert result.stored > 0
    assert coverage["total_items"] == result.stored
    assert coverage["with_embedding"] == 0


# ---------------------------------------------------------------------------
# 11. Fail loudly
# ---------------------------------------------------------------------------


def test_invalid_graph_category_raises_before_asking_anything(store):
    graph = {
        "only": BootstrapQuestion(
            id="only", prompt="Anything?", category="not-a-category"
        )
    }
    io = ScriptedIO(["something"])

    with pytest.raises(ValueError, match="not a MemoryStore category"):
        run_bootstrap_conversation(
            store,
            prompt_fn=io.prompt,
            output_fn=io.out,
            questions=graph,
            start="only",
        )

    assert io.questions_asked == []


def test_dangling_graph_edge_raises(store):
    graph = {
        "only": BootstrapQuestion(id="only", prompt="Anything?", next_id="nowhere")
    }
    io = ScriptedIO(["something"])

    with pytest.raises(ValueError, match="unknown node 'nowhere'"):
        run_bootstrap_conversation(
            store,
            prompt_fn=io.prompt,
            output_fn=io.out,
            questions=graph,
            start="only",
        )


def test_builder_emitting_an_invalid_category_raises(store):
    def _bad_builder(question, answer):  # pylint: disable=unused-argument
        return [ProposedEntry(content="bad", category="not-a-category")]

    graph = {
        "only": BootstrapQuestion(id="only", prompt="Anything?", builder=_bad_builder)
    }
    io = ScriptedIO(["something"])

    with pytest.raises(ValueError, match="not a MemoryStore category"):
        run_bootstrap_conversation(
            store,
            prompt_fn=io.prompt,
            output_fn=io.out,
            questions=graph,
            start="only",
        )


def test_cyclic_graph_raises_instead_of_looping_forever(store):
    graph = {
        "a": BootstrapQuestion(id="a", prompt="A?", next_id="b"),
        "b": BootstrapQuestion(id="b", prompt="B?", next_id="a"),
    }
    io = ScriptedIO(["one", "two", "three", "four"])

    with pytest.raises(ValueError, match="loops back to 'a'"):
        run_bootstrap_conversation(
            store, prompt_fn=io.prompt, output_fn=io.out, questions=graph, start="a"
        )


def test_store_failure_raises_runtime_error_with_context():
    class _BrokenStore:
        _db_path = "/tmp/broken.db"

        def store(self, **kwargs):
            raise sqlite3.OperationalError("database is locked")

    io = ScriptedIO(["Alex"])

    with pytest.raises(RuntimeError, match="Onboarding could not store") as excinfo:
        run_bootstrap_conversation(
            _BrokenStore(), prompt_fn=io.prompt, output_fn=io.out
        )

    assert isinstance(excinfo.value.__cause__, sqlite3.OperationalError)
    assert "/tmp/broken.db" in str(excinfo.value)


def test_embedding_failure_raises_runtime_error_naming_the_stored_row(store):
    def _broken_hook(knowledge_id, content):  # pylint: disable=unused-argument
        raise ConnectionError("lemonade-server not reachable")

    io = ScriptedIO(["Alex"])

    with pytest.raises(RuntimeError, match="could not embed") as excinfo:
        run_bootstrap_conversation(
            store, prompt_fn=io.prompt, output_fn=io.out, on_stored=_broken_hook
        )

    assert isinstance(excinfo.value.__cause__, ConnectionError)
    # The row itself is safe — the error says so, and the DB agrees.
    assert "User's name is Alex" in _contents(store)


# ---------------------------------------------------------------------------
# 12. The MemoryMixin wrapper
# ---------------------------------------------------------------------------


def _memory_host():
    """A bare MemoryMixin host — no Agent, no LLM."""
    from gaia.agents.base.memory import MemoryMixin

    class _Host(MemoryMixin):
        pass

    return _Host()


def test_mixin_wrapper_fails_loudly_when_memory_is_disabled(monkeypatch):
    monkeypatch.setenv("GAIA_MEMORY_DISABLED", "1")
    host = _memory_host()
    host.init_memory()
    assert host.memory_store is None

    io = ScriptedIO(_ENGINEER_ANSWERS)

    with pytest.raises(RuntimeError, match="memory is disabled") as excinfo:
        host.run_bootstrap_conversation(prompt_fn=io.prompt, output_fn=io.out)

    message = str(excinfo.value)
    assert "GAIA_MEMORY_DISABLED" in message
    assert "gaia memory bootstrap --chat-only" in message


def test_mixin_wrapper_embeds_every_stored_entry(store, monkeypatch):
    host = _memory_host()
    host._memory_store = store
    monkeypatch.setattr(
        type(host),
        "_embed_text",
        lambda self, text: np.zeros(768, dtype=np.float32),
    )
    monkeypatch.setattr(type(host), "_faiss_add", lambda self, kid, vec: None)

    io = ScriptedIO(_ENGINEER_ANSWERS)
    result = host.run_bootstrap_conversation(prompt_fn=io.prompt, output_fn=io.out)

    coverage = store.get_embedding_coverage()
    assert result.stored > 0
    assert coverage["with_embedding"] == result.stored
    assert coverage["without_embedding"] == 0


# ---------------------------------------------------------------------------
# 13. First-boot invariant — cli.py detects onboarding via profile/global
# ---------------------------------------------------------------------------


def test_onboarding_writes_a_profile_row_in_the_global_context(store):
    io = ScriptedIO(_ENGINEER_ANSWERS)

    run_bootstrap_conversation(store, prompt_fn=io.prompt, output_fn=io.out)

    assert store.get_by_category("profile", context="global", limit=1)


# ---------------------------------------------------------------------------
# 14. The CLI adapter — stdio in, BootstrapCancelled out
# ---------------------------------------------------------------------------


def _raise(exc):
    def _boom(*_args, **_kwargs):
        raise exc

    return _boom


@pytest.mark.parametrize("interrupt", [EOFError, KeyboardInterrupt])
def test_cli_prompt_translates_an_interrupt_into_bootstrap_cancelled(
    monkeypatch, interrupt
):
    from gaia.cli import _bootstrap_prompt

    monkeypatch.setattr("builtins.input", _raise(interrupt()))

    with pytest.raises(BootstrapCancelled):
        _bootstrap_prompt("What's your name?")


def test_cli_bootstrap_chat_reports_cancellation_and_stores_nothing(
    monkeypatch, tmp_path, capsys
):
    import gaia.agents.base.memory_store as memory_store_module
    from gaia import cli

    db_path = tmp_path / "memory.db"
    monkeypatch.setattr(
        memory_store_module, "MemoryStore", lambda *a, **k: MemoryStore(db_path=db_path)
    )
    monkeypatch.setattr("builtins.input", _raise(EOFError()))

    result = cli._bootstrap_chat()

    out = capsys.readouterr().out
    assert result.cancelled is True
    assert "Bootstrap cancelled" in out
    assert "✅" not in out  # no success line on an abort

    after = MemoryStore(db_path=db_path)
    try:
        assert after.get_all_knowledge(limit=10)["items"] == []
    finally:
        after.close()


def test_cli_marks_onboarding_completed_even_when_the_user_stores_nothing(
    monkeypatch, tmp_path
):
    """Declining every entry must not re-offer the intro on every `gaia chat`.

    First-boot keys on a profile row OR this marker; the review gate means a
    user can answer everything and approve nothing.
    """
    import gaia.agents.base.memory as memory_module
    import gaia.agents.base.memory_store as memory_store_module
    from gaia import cli

    monkeypatch.setattr(
        memory_store_module,
        "MemoryStore",
        lambda *a, **k: MemoryStore(db_path=tmp_path / "memory.db"),
    )
    monkeypatch.setattr(
        memory_module, "_MEMORY_SETTINGS_PATH", tmp_path / "memory_settings.json"
    )
    answers = iter(_ENGINEER_ANSWERS)
    # Answer every question, then decline every proposed entry.
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: "n" if "Store this?" in prompt else next(answers, ""),
    )

    assert cli._onboarding_completed() is False

    result = cli._bootstrap_chat()

    assert result.stored == 0
    assert result.rejected == _ENGINEER_ENTRY_COUNT
    assert cli._onboarding_completed() is True


def test_cli_does_not_mark_onboarding_completed_when_cancelled(monkeypatch, tmp_path):
    import gaia.agents.base.memory as memory_module
    import gaia.agents.base.memory_store as memory_store_module
    from gaia import cli

    monkeypatch.setattr(
        memory_store_module,
        "MemoryStore",
        lambda *a, **k: MemoryStore(db_path=tmp_path / "memory.db"),
    )
    monkeypatch.setattr(
        memory_module, "_MEMORY_SETTINGS_PATH", tmp_path / "memory_settings.json"
    )
    monkeypatch.setattr("builtins.input", _raise(EOFError()))

    cli._bootstrap_chat()

    # Cancelling is not completing — the intro should still be offered.
    assert cli._onboarding_completed() is False


def test_cli_default_bootstrap_skips_discovery_when_the_chat_is_cancelled(monkeypatch):
    from types import SimpleNamespace

    from gaia import cli
    from gaia.agents.base.bootstrap import BootstrapResult

    discovered: list[str] = []
    cancelled_result = BootstrapResult(
        stored=0,
        skipped=0,
        rejected=0,
        unreviewed=0,
        cancelled=True,
        answers={},
        knowledge_ids=[],
    )
    monkeypatch.setattr(cli, "_bootstrap_chat", lambda: cancelled_result)
    monkeypatch.setattr(cli, "_bootstrap_discover", lambda: discovered.append("ran"))

    cli._handle_memory_bootstrap(
        SimpleNamespace(
            reset=False,
            chat_only=False,
            discover=False,
            infer=False,
            system=False,
            reset_system=False,
        )
    )

    assert discovered == []
