# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Coverage, exact evidence, turn isolation and deterministic enumeration."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.extraction import (
    PAGE_CHARS,
    Entry,
    ExtractionLedger,
    exhaustive_request,
    extract_pages,
    parse_page,
    read_snapshot,
    reconcile_occurrence,
)
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


def touch(root, *names):
    """Sources are files on disk; create the ones a query names."""
    for name in names:
        (root / name).write_text("source")


def reply(*items):
    return json.dumps(
        {"complete": True, "items": [{"text": x, "quote": x} for x in items]}
    )


def test_page_mapping_visits_every_core_and_preserves_boundary_items():
    import re

    source = (
        " " * (PAGE_CHARS - 5)
        + "Exercise ZEPHYR-73"
        + " " * PAGE_CHARS
        + "Exercise CEDAR-92"
    )
    calls = []

    def ask(system, payload):
        data = json.loads(payload)
        calls.append(data)
        return reply(*re.findall(r"Exercise [A-Z]+-\d+", data["source_page"]))

    items, pages = extract_pages(source, "List every exercise", ask, lambda: None)
    assert pages == 3 and len(calls) == 6
    assert [i.text for i in items] == ["Exercise ZEPHYR-73", "Exercise CEDAR-92"]
    assert all(len(c["source_page"]) <= PAGE_CHARS + 1200 for c in calls)


def test_overlap_aligns_whole_lines_without_gaps():
    source = "".join(f"line {n}: " + "x" * 71 + "\n" for n in range(140))
    pages = []

    def ask(system, payload):
        pages.append(json.loads(payload)["source_page"])
        return reply()

    extract_pages(source, "List every item", ask, lambda: None)
    covered = set()
    for page in pages:
        assert page.startswith("line ") and page.endswith("\n")
        start = source.index(page)
        covered.update(range(start, start + len(page)))
    assert len(covered) == len(source)


def test_overlap_falls_back_to_sentence_boundaries_on_a_single_unbroken_line():
    """Transcripts have no newlines; pages must still open on a sentence."""
    sentence = "This is one sentence about the workshop. "
    source = sentence * (PAGE_CHARS // len(sentence) + 20)
    assert "\n" not in source
    pages = []

    def ask(system, payload):
        pages.append(json.loads(payload)["source_page"])
        return reply()

    extract_pages(source, "List every item", ask, lambda: None)
    for page in pages[1:-1]:
        # Neither edge may land mid-word: every page after the first starts
        # right after ". ", and every page before the last ends right after it.
        start = source.index(page)
        assert source[start - 2 : start] == ". " or start == 0
        assert page.endswith(". ") or source.index(page) + len(page) == len(source)


def test_repeated_names_remain_separate_source_occurrences():
    with pytest.raises(ValueError, match="Ambiguous"):
        parse_page(reply("Lift"), "Lift then Lift", 30)


@pytest.mark.parametrize("value", ["U.S.", "example.com.", ".", "..."])
def test_field_values_preserve_verbatim_punctuation(value):
    quote = "Value: " + value
    raw = json.dumps(
        {"complete": True, "items": [{"quote": quote, "fields": {"value": value}}]}
    )
    assert parse_page(raw, quote, 0, ("value",))[0].text == "value: " + value


def test_same_span_sentence_period_variants_keep_original_values():
    quote = "Cue: look up."

    def entry(value):
        return parse_page(
            json.dumps(
                {
                    "complete": True,
                    "items": [{"quote": quote, "fields": {"cue": value}}],
                }
            ),
            quote,
            0,
            ("cue",),
        )[0]

    first, second = entry("look up."), entry("look up")
    assert reconcile_occurrence(first, second) == first
    assert reconcile_occurrence(second, first) == first
    assert first.text == "cue: look up."
    # With no identity field, a different value is a different item.
    assert reconcile_occurrence(entry("up."), first) is None


def test_broad_quote_does_not_hide_missed_neighbor_on_later_page():
    source = " " * 3900 + "Exercise A then Exercise B" + " " * 5000
    seen = []

    def ask(system, payload):
        page = json.loads(payload)["source_page"]
        seen.append(page)
        if len(seen) <= 2:
            return reply("Exercise A then Exercise B") if len(seen) == 1 else reply()
        return reply("Exercise B")

    # Ambiguous overlapping evidence is explicit failure, never masking B
    # out of the next page and silently reporting the first inventory complete.
    with pytest.raises(ValueError, match="Conflicting"):
        extract_pages(source, "List every exercise", ask, lambda: None)
    assert "Exercise B" in seen[2]


def test_new_field_schema_invalidates_every_cached_source(tmp_path):
    state = ExtractionLedger("List every exercise in a.txt and b.txt", str(tmp_path))
    for path in ("a.txt", "b.txt"):
        assert (
            state.run(path, lambda p: "alpha", lambda *a: reply("alpha"), lambda: None)[
                "status"
            ]
            == "success"
        )
    calls = []

    def ask(*args):
        calls.append(args)
        return json.dumps(
            {
                "complete": True,
                "items": [{"fields": {"name": "alpha"}, "quote": "alpha"}],
            }
        )

    assert (
        state.run("a.txt", lambda p: "alpha", ask, lambda: None, ["name"])["status"]
        == "success"
    )
    assert calls and "name: alpha" in state.render()
    assert any("b.txt" in gap for gap in state.gaps())
    state.activate_skill("Extract every exercise with its coach name.")
    assert not state.results


def test_expired_budget_does_not_retry_provider(monkeypatch):
    clock = [0]
    monkeypatch.setattr("gaia.agents.base.extraction.time.monotonic", lambda: clock[0])
    calls = []

    def ask(*args):
        calls.append(args)
        clock[0] = 10000
        return reply("alpha")

    with pytest.raises(ValueError, match="time budget"):
        extract_pages("alpha", "List every item", ask, lambda: None)
    assert len(calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{}",
        "[]",
        '{"complete":false,"items":[]}',
        reply("invented"),
        '{"complete":true,"items":[{}]}',
    ],
)
def test_malformed_or_ungrounded_output_fails(raw):
    with pytest.raises((ValueError, TypeError)):
        parse_page(raw, "real", 0)


def test_page_failure_never_publishes_partial_inventory(tmp_path):
    state = ExtractionLedger("List every exercise in source.txt", str(tmp_path))
    answers = iter([reply("alpha"), reply(), "", ""])
    result = state.run(
        "source.txt",
        lambda p: "alpha" + " " * PAGE_CHARS,
        lambda *a: next(answers),
        lambda: None,
    )
    assert result["status"] == "error"
    assert not state.results and "page 2" in " ".join(state.gaps())


def test_changed_source_invalidates_prior_inventory(tmp_path):
    state = ExtractionLedger("List every exercise in source.txt", str(tmp_path))
    assert (
        state.run(
            "source.txt", lambda p: "alpha", lambda *a: reply("alpha"), lambda: None
        )["status"]
        == "success"
    )
    assert (
        state.run("source.txt", lambda p: "beta", lambda *a: "", lambda: None)["status"]
        == "error"
    )
    assert not state.results


def test_cancellation_does_not_publish(tmp_path):
    state = ExtractionLedger("List every exercise in source.txt", str(tmp_path))

    def cancel():
        raise ValueError("cancelled")

    assert (
        state.run("source.txt", lambda p: "alpha", lambda *a: reply("alpha"), cancel)[
            "status"
        ]
        == "error"
    )
    assert not state.results


def test_multisource_inventory_does_not_satisfy_unread_source(tmp_path):
    touch(tmp_path, "one.txt", "two.txt")
    state = ExtractionLedger(
        "List every exercise in one.txt and two.txt", str(tmp_path)
    )
    state.run("one.txt", lambda p: "alpha", lambda *a: reply("alpha"), lambda: None)
    assert len(state.gaps()) == 1 and "two.txt" in state.gaps()[0]


def test_safe_snapshot_respects_scope_and_size(tmp_path, monkeypatch):
    p = tmp_path / "source.txt"
    p.write_text("alpha")
    assert read_snapshot(str(p), PathValidator(allowed_paths=[str(p)])) == "alpha"
    with pytest.raises(ValueError, match="Access denied"):
        read_snapshot(str(p), PathValidator(allowed_paths=[]))
    monkeypatch.setattr("gaia.agents.base.extraction.MAX_CHARS", 3)
    with pytest.raises(ValueError, match="exceeds"):
        read_snapshot(str(p), PathValidator(allowed_paths=[str(p)]))


def test_safe_snapshot_rejects_nonregular_file_without_waiting(tmp_path, monkeypatch):
    import os
    import stat
    from types import SimpleNamespace

    path = tmp_path / "pipe"
    if hasattr(os, "mkfifo"):
        os.mkfifo(path)
    else:
        # Windows has no mkfifo; exercise the same descriptor rejection
        # without requiring an OS-specific named-pipe service or a skipped test.
        path.write_text("placeholder")
        monkeypatch.setattr(
            os, "fstat", lambda fd: SimpleNamespace(st_mode=stat.S_IFIFO)
        )
    with pytest.raises(ValueError, match="regular text file"):
        read_snapshot(str(path), PathValidator(allowed_paths=[str(tmp_path)]))


def test_active_skill_enables_extraction_but_general_knowledge_does_not(tmp_path):
    state = ExtractionLedger("List all planets", str(tmp_path))
    assert not state.enabled and not state.gaps()
    state.activate_skill("Extract **every** exercise from the workshop transcript.")
    # Nothing was read, so the answer did not come from a document.
    assert state.enabled and not state.gaps()
    state.observe("read_file", {"file_path": "workshop.txt"}, {"status": "success"})
    assert state.gaps()
    assert exhaustive_request("Enumerate every function in code.py")


@pytest.mark.parametrize(
    "query",
    [
        "Find every file larger than 1GB in my Downloads folder.",
        "List all the log levels in Python's logging module.",
        "List every U.S. state capital.",
        "List every action item in this meeting: ship it, test it.",
    ],
)
def test_questions_without_a_read_document_leave_no_extraction_gap(query, tmp_path):
    state = ExtractionLedger(query, str(tmp_path))
    assert not state.requested and not state.gaps()


def test_loading_the_skill_keeps_code_symbol_queries_off(tmp_path):
    touch(tmp_path, "utils.py")
    state = ExtractionLedger("List every function in utils.py", str(tmp_path))
    state.activate_skill("Extract every item from the source document.")
    assert not state.enabled


class FileAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self):
        return "Use tools for files."

    def _register_tools(self):
        self.path_validator = PathValidator(allowed_paths=[str(Path.cwd())])
        self.register_file_io_tools()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        instance = FileAgent(silent_mode=True, skip_lemonade=True)
    instance.streaming = False
    instance.console = MagicMock()
    instance.console.cancelled = None
    instance._make_extraction_chat = lambda: instance.chat
    yield instance
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def script(agent, *outer):
    turns = iter(outer)

    def send(messages, system_prompt=None, tools=None, **kwargs):
        if system_prompt and system_prompt.startswith("Extract every requested"):
            assert tools == [] and len(messages) == 1
            response_format = kwargs["response_format"]
            assert response_format["type"] == "json_schema"
            assert response_format["json_schema"]["strict"] is True
            payload = json.loads(messages[0]["content"])
            return MagicMock(
                text=(
                    reply("Exercise ALPHA", "Exercise BETA")
                    if not payload.get("already_found")
                    else reply()
                ),
                stats={},
            )
        return MagicMock(text=json.dumps(next(turns)), stats={})

    agent.chat.send_messages.side_effect = send


def test_real_loop_keeps_items_omitted_by_final_synthesis(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    script(
        agent,
        {"tool": "extract_document_items", "tool_args": {"file_path": "source.txt"}},
        {"answer": "Exercise ALPHA only."},
    )
    result = agent.process_query("List every exercise in source.txt", max_steps=5)
    assert result["status"] == "success"
    assert "Exercise BETA" in result["result"]
    assert agent.console.print_final_answer.call_args.args[0] == result["result"]


def test_reading_every_byte_does_not_satisfy_extraction(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    script(
        agent,
        {"tool": "read_file", "tool_args": {"file_path": "source.txt"}},
        {"answer": "Exercise ALPHA only."},
        {"answer": "Exercise ALPHA only."},
    )
    result = agent.process_query("List every exercise in source.txt", max_steps=5)
    assert (
        result["status"] == "incomplete" and "Incomplete extraction" in result["result"]
    )


def test_inventory_does_not_hide_a_missing_save(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    script(
        agent,
        {"tool": "extract_document_items", "tool_args": {"file_path": "source.txt"}},
        {"answer": "Saved report.txt"},
        {"answer": "Saved report.txt"},
    )
    result = agent.process_query(
        "List every exercise in source.txt. Save to report.txt.", max_steps=5
    )
    assert (
        result["status"] == "incomplete" and "No successful write" in result["result"]
    )


def test_tool_not_offered_to_unrelated_turn(agent):
    script(agent, {"answer": "Hello"})
    result = agent.process_query("Hello", max_steps=1)
    assert result["status"] == "success"
    assert "extract_document_items" not in agent._tools_registry


def test_filtered_agent_admits_tools_only_for_current_extraction_turn(agent, tmp_path):
    agent._select_tools_for_turn = lambda query: ["read_file"]
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    for query, expected in [
        ("Hello", False),
        ("List every exercise in source.txt", True),
        ("Hello again", False),
    ]:
        if expected:
            script(
                agent,
                {
                    "tool": "extract_document_items",
                    "tool_args": {"file_path": "source.txt"},
                },
                {"answer": "Done"},
            )
        else:
            script(agent, {"answer": "Hello"})
        result = agent.process_query(query, max_steps=4)
        assert result["status"] == "success", result
        assert ("extract_document_items" in agent._active_tool_filter) == expected
        assert ("extract_document_items" in agent._tools_registry) == expected


def test_superseded_worker_cannot_publish_or_charge_the_next_turn(agent, tmp_path):
    import threading

    (tmp_path / "source.txt").write_text("alpha")
    old = ExtractionLedger("List every item in source.txt", str(tmp_path))
    agent._extraction_ledger = old
    agent._tool_reported_usage = []
    agent._register_extraction_tool()
    started, release = threading.Event(), threading.Event()

    def send(**kwargs):
        started.set()
        assert release.wait(3), "test did not release the simulated backend"
        return MagicMock(text=reply("alpha"), usage={"total_tokens": 10}, stats={})

    agent.chat.send_messages.side_effect = send
    results = []
    function = agent._tools_registry["extract_document_items"]["function"]
    worker = threading.Thread(target=lambda: results.append(function("source.txt")))
    worker.start()
    try:
        assert started.wait(3)
        current = ExtractionLedger("Hello", str(tmp_path))
        agent._extraction_ledger = current
        agent._tool_reported_usage = []
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive()
    assert results[0]["status"] == "error"
    assert "superseded" in results[0]["error"]
    assert not old.results and not current.results
    assert not agent._tool_reported_usage


def test_extraction_sdk_configuration_is_private(agent):
    from types import SimpleNamespace

    agent.chat.config = SimpleNamespace(temperature=0.8, max_tokens=8192)
    with patch("gaia.agents.base.agent.AgentSDK") as constructor:
        Agent._make_extraction_chat(agent)
    config = constructor.call_args.args[0]
    assert config is not agent.chat.config
    assert config.temperature == 0 and config.max_tokens == 8192
    assert agent.chat.config.temperature == 0.8


@pytest.mark.parametrize(
    "query",
    [
        "Do not list every exercise in source.txt; just summarize it",
        "How can I extract every item from source.txt?",
        "List all files in directory /tmp",
    ],
)
def test_advice_negation_and_directory_requests_are_not_extraction(query, tmp_path):
    assert not ExtractionLedger(query, str(tmp_path)).enabled


@pytest.mark.parametrize(
    "query",
    [
        "List all functions in app.py",
        "Find every class in models.py",
        "Enumerate every function in code.py",
    ],
)
def test_code_symbol_queries_do_not_demand_the_source_file_as_a_destination(
    query, tmp_path
):
    """A code file the query names is a search target, not a destination."""
    state = ExtractionLedger(query, str(tmp_path))
    assert state.gaps() == []
    assert not state.enabled


def test_document_queries_still_activate_alongside_a_code_destination(tmp_path):
    """Only an all-code query is a code query; a real document still counts."""
    touch(tmp_path, "notes.md")
    state = ExtractionLedger(
        "Extract every TODO from notes.md and save to out.py", str(tmp_path)
    )
    assert state.enabled
    assert state.requested == {state.key("notes.md")}


def test_combined_source_and_save_request_keeps_all_sources(tmp_path):
    touch(tmp_path, "one.txt", "two.txt")
    state = ExtractionLedger(
        "List every exercise in one.txt and two.txt and save to report.txt",
        str(tmp_path),
    )
    # key(), not str(tmp_path / ...): self.requested holds normalized keys
    # (Windows lowercases the drive/path via ntpath.normcase), which a raw
    # mixed-case tmp_path string never matches there.
    assert state.requested == {state.key("one.txt"), state.key("two.txt")}


def test_item_found_only_from_neighboring_page_is_retained():
    source = " " * 3900 + "Exercise A" + " " * 1100 + "details" + " " * 100

    def ask(system, payload):
        page = json.loads(payload)["source_page"]
        return reply("Exercise A") if "details" in page else reply()

    items, _ = extract_pages(source, "List every exercise", ask, lambda: None)
    assert len(items) == 1 and items[0].start == 3900


def test_overlapping_conflicting_items_fail_instead_of_guessing():
    answers = iter([reply("Squat — 5 reps"), reply("Squat"), reply("Squat")])
    with pytest.raises(ValueError, match="Conflicting"):
        extract_pages(
            "Squat — 5 reps",
            "List every exercise",
            lambda *a: next(answers),
            lambda: None,
        )


def test_required_fields_cannot_be_silently_omitted():
    with pytest.raises(ValueError, match="field"):
        parse_page(reply("Squat"), "Squat", 0, ("name", "reps"))
    raw = json.dumps(
        {
            "complete": True,
            "items": [
                {"quote": "Squat — 5 reps", "fields": {"name": "Squat", "reps": "5"}}
            ],
        }
    )
    assert (
        parse_page(raw, "Squat — 5 reps", 0, ("name", "reps"))[0].text
        == "name: Squat; reps: 5"
    )


def test_short_saved_inventory_is_incomplete(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    agent._tool_requires_confirmation = lambda *a: False
    script(
        agent,
        {"tool": "extract_document_items", "tool_args": {"file_path": "source.txt"}},
        {
            "tool": "write_file",
            "tool_args": {"file_path": "report.txt", "content": "Exercise ALPHA"},
        },
        {"tool": "read_file", "tool_args": {"file_path": "report.txt"}},
        {"answer": "Saved report.txt"},
        {"answer": "Saved report.txt"},
    )
    result = agent.process_query(
        "List every exercise in source.txt. Save to report.txt.", max_steps=8
    )
    assert result["status"] == "incomplete" and "does not preserve" in result["result"]


@pytest.mark.parametrize("suffix", ["txt", "json", "csv"])
def test_deterministic_export_preserves_every_item(agent, tmp_path, suffix):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    agent._tool_requires_confirmation = lambda *a: False
    path = "report." + suffix
    script(
        agent,
        {"tool": "extract_document_items", "tool_args": {"file_path": "source.txt"}},
        {"tool": "save_extracted_items", "tool_args": {"file_path": path}},
        {"tool": "read_file", "tool_args": {"file_path": path}},
        {"answer": "Saved " + path},
    )
    result = agent.process_query(
        f"List every exercise in source.txt. Save to {path}.", max_steps=8
    )
    assert result["status"] == "success", result["result"]
    assert all(
        x in (tmp_path / path).read_text() for x in ["Exercise ALPHA", "Exercise BETA"]
    )


def test_source_freshness_invalidates_old_results(tmp_path):
    state = ExtractionLedger("List every exercise in source.txt", str(tmp_path))
    state.run("source.txt", lambda p: "alpha", lambda *a: reply("alpha"), lambda: None)
    state.validate_sources(lambda p: "changed")
    assert "source changed" in " ".join(state.gaps()) and not state.results


def test_existing_memory_hook_records_source_summary_and_recall(agent, tmp_path):
    from gaia.agents.base.memory import MemoryMixin
    from gaia.agents.base.memory_store import MemoryStore

    # The real mixin wrapper, real SQLite store and real extraction tool: no
    # alternate event database or mocked memory-write/recall path.
    class LoggedAgent(MemoryMixin, FileAgent):
        pass

    with patch("gaia.agents.base.agent.AgentSDK"):
        agent = LoggedAgent(silent_mode=True, skip_lemonade=True)
    agent.console = MagicMock()
    agent.console.cancelled = None
    agent._make_extraction_chat = lambda: agent.chat
    agent._memory_store = MemoryStore(tmp_path / "memory.sqlite")
    agent._memory_session_id = "extraction-test"
    agent._memory_context = "project-test"
    agent._incognito = False
    agent._auto_extract_enabled = False
    agent._original_user_input = "List every exercise in source.txt"
    agent._forget_errors_for_operation = lambda *a: None
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    agent._extraction_ledger = ExtractionLedger(
        agent._original_user_input, str(tmp_path)
    )
    agent._register_extraction_tool()
    script(agent)
    result = agent._execute_tool("extract_document_items", {"file_path": "source.txt"})
    assert result["status"] == "success"
    events = agent._memory_store.get_tool_history("extract_document_items")
    assert len(events) == 1 and events[0]["success"]
    assert (
        "source_sha256" in events[0]["result_summary"]
        and "'items': 2" in events[0]["result_summary"]
    )
    agent._after_process_query(
        agent._original_user_input, agent._extraction_ledger.render()
    )
    recalled = agent._memory_store.search_conversations("BETA", context="project-test")
    assert recalled and "Exercise BETA" in str(recalled)
    # The default 4000-character conversation cap used to silently remove the
    # tail of a long inventory. Search via the real registered tool, and keep
    # the answer as one canonical turn rather than synthetic chunk turns.
    long_answer = "Historical introduction. " * 250 + agent._extraction_ledger.render()
    agent._after_process_query(agent._original_user_input, long_answer)
    agent.register_memory_tools()
    found = agent._execute_tool("search_past_conversations", {"query": "BETA"})
    assert any(
        len(r["content"]) > 4000 and "Exercise BETA" in r["content"]
        for r in found["results"]
    )
    history = agent._memory_store.get_history(session_id="extraction-test")
    assert len(history) == 4
    assert "SHA256" in history[-1]["content"]
    # Private mode must not add another action or summary.
    agent._incognito = True
    agent._execute_tool("extract_document_items", {"file_path": "source.txt"})
    agent._after_process_query(agent._original_user_input, "private inventory")
    assert len(agent._memory_store.get_tool_history("extract_document_items")) == 1
    assert len(agent._memory_store.get_history(session_id="extraction-test")) == 4
    # An inventory over the memory limit stores neither turn, never a lone question.
    agent._incognito = False
    agent._after_process_query(agent._original_user_input, "x" * 256001)
    assert len(agent._memory_store.get_history(session_id="extraction-test")) == 4


def test_full_inventory_memory_is_bounded_and_regular_turns_keep_existing_cap(tmp_path):
    from gaia.agents.base.memory_store import MemoryStore

    store = MemoryStore(tmp_path / "bounded.sqlite")
    store.store_turn("test", "assistant", "a" * 5000)
    assert len(store.get_history()[0]["content"]) == 4000
    with pytest.raises(ValueError, match="memory limit"):
        store.store_turn("test", "assistant", "a" * 256001, preserve_full=True)
    assert len(store.get_history()) == 1


def test_export_cannot_masquerade_as_a_binary_document(tmp_path):
    state = ExtractionLedger("List every exercise in source.txt", str(tmp_path))
    state.run("source.txt", lambda p: "alpha", lambda *a: reply("alpha"), lambda: None)
    for path in ("inventory.pdf", "inventory.xlsx", "inventory.docx"):
        with pytest.raises(ValueError, match="supports"):
            state.export(path)


def test_export_refuses_to_overwrite_its_source(agent, tmp_path):
    path = tmp_path / "source.txt"
    path.write_text("alpha")
    agent._extraction_ledger = ExtractionLedger(
        "List every item in source.txt", str(tmp_path)
    )
    agent._extraction_ledger.run(
        "source.txt", lambda p: "alpha", lambda *a: reply("alpha"), lambda: None
    )
    agent._register_extraction_tool()
    result = agent._tools_registry["save_extracted_items"]["function"]("./source.txt")
    assert result["status"] == "error" and "different" in result["error"]
    assert path.read_text() == "alpha"


@pytest.mark.parametrize(
    "query, sources",
    [
        ("Extract every TODO from app.py and notes.md", {"app.py", "notes.md"}),
        ("Extract every TODO from app.py and save to report.md", {"app.py"}),
        ("Extract every TODO from app.py", {"app.py"}),
        ("Extract every TODO from functions.py", {"functions.py"}),
    ],
)
def test_content_extraction_keeps_code_sources_required(query, sources, tmp_path):
    touch(tmp_path, *sources)
    state = ExtractionLedger(query, str(tmp_path))
    assert state.enabled
    assert state.requested == {state.key(p) for p in sources}
    state.results[state.key("notes.md")] = ([], 1, "digest")
    for source in sources - {"notes.md"}:
        assert any(source in gap for gap in state.gaps())


def test_empty_pages_are_valid_but_unfinished_pages_still_fail():
    assert parse_page(reply(), "Please wait by the door.", 0) == []
    with pytest.raises(ValueError, match="did not confirm"):
        parse_page(
            json.dumps({"complete": False, "items": []}), "Please wait by the door.", 0
        )


@pytest.mark.parametrize("reverse", [False, True])
def test_overlap_enriches_missing_fields_without_losing_provenance(reverse):
    page = "Exercise: Lift. Cue: breathe slowly."

    def entry(quote, cue):
        return parse_page(
            json.dumps(
                {
                    "complete": True,
                    "items": [{"quote": quote, "fields": {"name": "Lift", "cue": cue}}],
                }
            ),
            page,
            20,
            ("name", "cue"),
        )[0]

    partial = entry("Exercise: Lift.", "not stated")
    full = entry(page, "breathe slowly")
    assert (
        reconcile_occurrence(*((full, partial) if reverse else (partial, full))) == full
    )


@pytest.mark.parametrize(
    "old_values,new_values",
    [
        ({"name": "Lift", "cue": "slow"}, {"name": "Lift", "cue": "fast"}),
        ({"name": "not stated", "cue": "not stated"}, {"name": "Lift", "cue": "slow"}),
        (
            {"name": "Lift", "reps": "3", "cue": "not stated"},
            {"name": "Squat", "reps": "3", "cue": "slow"},
        ),
    ],
)
def test_enrichment_rejects_conflicts_and_unidentified_neighbors(
    old_values, new_values
):
    from gaia.agents.base.extraction import Entry

    first = Entry(10, 30, str(old_values), "old", tuple(old_values.items()))
    second = Entry(0, 40, str(new_values), "new", tuple(new_values.items()))
    assert reconcile_occurrence(first, second) is None
    assert reconcile_occurrence(second, first) is None


def test_page_overlap_replaces_partial_item_with_grounded_full_item():
    quote = "Exercise: Lift. Cue: breathe slowly."
    source = "Context. " * 460 + quote + " More context." * 400
    calls = 0

    def ask(system, payload):
        nonlocal calls
        calls += 1
        page = json.loads(payload)["source_page"]
        if quote not in page:
            return reply()
        partial = calls <= 2
        return json.dumps(
            {
                "complete": True,
                "items": [
                    {
                        "quote": "Exercise: Lift." if partial else quote,
                        "fields": {
                            "name": "Lift",
                            "cue": "not stated" if partial else "breathe slowly",
                        },
                    }
                ],
            }
        )

    entries, _ = extract_pages(
        source, "List every exercise", ask, lambda: None, ("name", "cue")
    )
    assert len(entries) == 1
    assert entries[0].quote == quote
    assert dict(entries[0].fields)["cue"] == "breathe slowly"


def test_repeated_items_with_shared_context_are_not_silently_deduplicated():
    page = "Squat reps10. pause. Squat reps10."
    items = [
        {"text": "Squat reps10", "quote": quote}
        for quote in ("Squat reps10. pause.", "pause. Squat reps10.")
    ]
    entries = parse_page(json.dumps({"complete": True, "items": items}), page, 0)
    assert reconcile_occurrence(*entries) is None
    with pytest.raises(ValueError, match="Conflicting"):
        extract_pages(
            page,
            "List every exercise",
            lambda *_: json.dumps({"complete": True, "items": items}),
            lambda: None,
        )


FIELDS = ("name", "target area", "cue")
SENTENCE = (
    "As long as the march is feeling good, I want you to slow down your march "
    "and start picking your feet up higher, which is going to be a little bit "
    "of a balance exercise."
)
VALUES = {
    "name": "slow down your march",
    "target area": "not stated",
    "cue": "start picking your feet up higher",
}


def field_entry(page, quote, values, base=0):
    return parse_page(
        json.dumps({"complete": True, "items": [{"quote": quote, "fields": values}]}),
        page,
        base,
        tuple(values),
    )[0]


def test_differently_bounded_quotes_of_one_sentence_are_one_occurrence():
    early = field_entry(SENTENCE, SENTENCE[:120], VALUES)
    late = field_entry(SENTENCE, SENTENCE[40:], VALUES)
    assert early.start < late.start < early.end < late.end
    merged = reconcile_occurrence(early, late)
    assert merged == reconcile_occurrence(late, early)
    # The merged quote is still verbatim source text, spanning both quotes.
    assert (merged.start, merged.end, merged.quote) == (0, len(SENTENCE), SENTENCE)
    assert dict(merged.fields) == VALUES


@pytest.mark.parametrize(
    "early_values",
    [
        {**VALUES, "cue": "not stated"},
        # The page boundary cut the cue short.
        {**VALUES, "cue": "start picking your feet"},
    ],
)
def test_partial_overlap_keeps_the_fuller_value_of_each_field(early_values):
    early = field_entry(SENTENCE, SENTENCE[:110], early_values)
    late = field_entry(SENTENCE, SENTENCE[40:], {**VALUES, "target area": "feet"})
    for merged in (
        reconcile_occurrence(early, late),
        reconcile_occurrence(late, early),
    ):
        assert dict(merged.fields) == {**VALUES, "target area": "feet"}
        assert merged.quote == SENTENCE


def test_partial_overlap_of_repeated_names_stays_two_occurrences():
    page = "Squat reps10. pause. Squat reps10."
    values = {"name": "Squat", "reps": "reps10"}
    first = field_entry(page, "Squat reps10. pause.", values)
    second = field_entry(page, "pause. Squat reps10.", values)
    assert reconcile_occurrence(first, second) is None


@pytest.mark.parametrize(
    "page, values, early, late",
    [
        # The name also occurs earlier in one quote.
        (SENTENCE, {"name": "march"}, SENTENCE[:120], SENTENCE[40:]),
        # "3" also occurs inside "30".
        (
            "Next: Squat 3 sets, rest 30 seconds between them.",
            {"name": "Squat", "sets": "3"},
            "Next: Squat 3 sets, rest 30",
            "Squat 3 sets, rest 30 seconds between",
        ),
    ],
)
def test_values_repeated_inside_one_quote_still_match_their_occurrence(
    page, values, early, late
):
    first, second = field_entry(page, early, values), field_entry(page, late, values)
    assert dict(reconcile_occurrence(first, second).fields) == values


@pytest.mark.parametrize("fields", [(), ("name", "reps")])
@pytest.mark.parametrize("broad_first", [True, False])
def test_broad_quote_never_absorbs_two_repeated_items(fields, broad_first):
    page = "Squat reps10. a. b. Squat reps10."

    def item(quote):
        if fields:
            return {"quote": quote, "fields": {"name": "Squat", "reps": "reps10"}}
        return {"text": "Squat reps10", "quote": quote}

    broad = [item(page)]
    pair = [item("Squat reps10. a."), item("b. Squat reps10.")]
    replies = [broad, pair] if broad_first else [pair, broad]
    calls = []

    def ask(system, payload):
        calls.append(payload)
        # A retry repeats the model's last answer.
        return json.dumps({"complete": True, "items": replies[min(len(calls), 2) - 1]})

    with pytest.raises(ValueError, match="Conflicting|multiple occurrences"):
        extract_pages(page, "List every exercise", ask, lambda: None, fields)


def test_ambiguous_reply_is_retried_with_the_validation_error():
    page = "Today Bob and Carol, engineers, joined."
    bad = [
        {"quote": "Bob and Carol", "fields": {"name": "Bob"}},
        {"quote": "Carol, engineers", "fields": {"name": "Carol"}},
    ]
    good = [
        {"quote": "Bob and", "fields": {"name": "Bob"}},
        {"quote": "Carol, engineers", "fields": {"name": "Carol"}},
    ]
    payloads = []

    def ask(system, payload):
        payloads.append(json.loads(payload))
        items = bad if len(payloads) == 1 else good if len(payloads) == 2 else []
        return json.dumps({"complete": True, "items": items})

    entries, _ = extract_pages(page, "List every person", ask, lambda: None, ("name",))
    assert "validation_error" in payloads[1]
    assert [dict(e.fields)["name"] for e in entries] == ["Bob", "Carol"]


def test_transcript_sentence_quoted_differently_across_pages_extracts_once():
    filler = "The coach talks about the room and the chairs. "
    source = filler * 80 + SENTENCE + " " + filler * 150
    sentence_at = source.index(SENTENCE)
    assert PAGE_CHARS - 600 < sentence_at < PAGE_CHARS
    seen_pages = 0

    def ask(system, payload):
        nonlocal seen_pages
        data = json.loads(payload)
        page = data["source_page"]
        if SENTENCE not in page or "already_found" in data:
            return json.dumps({"complete": True, "items": []})
        seen_pages += 1
        # Each page chooses its own plausible start and end for the quote.
        quote = SENTENCE[:120] if seen_pages == 1 else SENTENCE[40:]
        return json.dumps(
            {"complete": True, "items": [{"quote": quote, "fields": VALUES}]}
        )

    entries, pages = extract_pages(
        source, "List every exercise", ask, lambda: None, FIELDS
    )
    assert seen_pages == 2 and pages > 2
    assert len(entries) == 1
    assert entries[0].start == sentence_at


def test_contained_quote_may_repeat_the_name_it_identifies():
    page = "The march. Keep the march slow and lift your knees."
    values = {"name": "march", "cue": "not stated"}
    short = field_entry(page, "Keep the march slow", values)
    full = field_entry(page, page, {"name": "march", "cue": "lift your knees"})
    assert reconcile_occurrence(short, full) == full


def test_free_text_quotes_of_one_sentence_merge_but_repeats_do_not():
    early = Entry(0, 120, "slow march", SENTENCE[:120])
    late = Entry(40, len(SENTENCE), "slow march", SENTENCE[40:])
    assert reconcile_occurrence(early, late) == early
    page = "Squat reps10. pause. Squat reps10."
    first = Entry(0, 20, "Squat reps10", page[:20])
    second = Entry(14, 34, "Squat reps10", page[14:])
    assert reconcile_occurrence(first, second) is None


@pytest.mark.parametrize(
    "page, first, second",
    [
        (
            "Warm-up: march, then march in place for a minute.",
            ("Warm-up: march, then march in place", "march"),
            ("march in place for a minute", "march in place"),
        ),
        (
            "Hold a plank and side plank, 30 seconds each.",
            ("plank and side", "plank"),
            ("side plank, 30", "side plank"),
        ),
    ],
)
def test_a_name_inside_another_name_is_a_different_item(page, first, second):
    a = field_entry(page, first[0], {"name": first[1], "duration": "not stated"})
    b = field_entry(page, second[0], {"name": second[1], "duration": "not stated"})
    assert reconcile_occurrence(a, b) is None
    assert reconcile_occurrence(b, a) is None


@pytest.mark.parametrize(
    "query, sources",
    [
        (
            "Extract all action items from meeting.txt into actions.json",
            {"meeting.txt"},
        ),
        ("List every exercise in a.txt and every stretch in b.txt", {"a.txt", "b.txt"}),
        ("List every exercise in the transcript (workshop.txt)", {"workshop.txt"}),
        ("List every exercise in workshop.txt as JSON", {"workshop.txt"}),
    ],
)
def test_sources_are_what_the_request_reads_not_where_it_writes(
    query, sources, tmp_path
):
    touch(tmp_path, *sources)
    state = ExtractionLedger(query, str(tmp_path))
    assert state.requested == {state.key(p) for p in sources}


@pytest.mark.parametrize(
    "query",
    [
        "List all the features of Node.js",
        "List every lifecycle method in Next.js",
        "Enumerate all methods on Array.prototype",
        "Find every ERROR line in app.log",
    ],
)
def test_topics_and_data_files_are_not_document_sources(query, tmp_path):
    touch(tmp_path, "app.log")
    state = ExtractionLedger(query, str(tmp_path))
    assert not state.requested and not state.gaps()


@pytest.mark.parametrize(
    "query",
    [
        "Find all the bugs in main.py",
        "Identify every security issue in auth.py",
        "List all the key dates in contract.pdf",
        "List all U.S. state capitals",
    ],
)
def test_analysis_binary_and_general_questions_stay_off(query, tmp_path):
    assert not ExtractionLedger(query, str(tmp_path)).enabled


def test_agents_without_a_read_boundary_never_extract(tmp_path):
    state = ExtractionLedger(
        "List every exercise in workshop.txt", str(tmp_path), available=False
    )
    state.activate_skill("Extract every item from the source document.")
    assert not state.enabled and not state.gaps()


@pytest.mark.parametrize("field", ["exercise", "exercise_name", "ExerciseName"])
def test_the_identifying_field_must_match_exactly_whatever_its_name(field):
    page = "Warm-up: march, then march in place, 1 minute each."
    a = field_entry(
        page, "Warm-up: march, then", {field: "march", "duration": "not stated"}
    )
    b = field_entry(
        page,
        "march in place, 1 minute",
        {field: "march in place", "duration": "1 minute"},
    )
    assert reconcile_occurrence(a, b) is None


def test_extracting_into_a_file_makes_it_an_output(tmp_path):
    touch(tmp_path, "meeting.txt")
    state = ExtractionLedger(
        "Extract all action items from meeting.txt into actions.json", str(tmp_path)
    )
    assert state.destinations == {state.key("actions.json")}


def test_an_unstated_first_field_still_merges_on_an_equal_field():
    page = "Next: send the deck to the board by Friday."
    values = {"owner": "not stated", "task": "send the deck", "deadline": "Friday"}
    a = field_entry(page, "Next: send the deck to the board by Friday", values)
    b = field_entry(page, "send the deck to the board by Friday.", values)
    merged = reconcile_occurrence(a, b)
    assert merged is not None and dict(merged.fields) == values


def test_running_out_of_steps_keeps_the_extracted_inventory(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    script(
        agent,
        {"tool": "extract_document_items", "tool_args": {"file_path": "source.txt"}},
        {"tool": "save_extracted_items", "tool_args": {"file_path": "report.txt"}},
    )
    agent._tool_requires_confirmation = lambda *a, **kw: False
    result = agent.process_query(
        "List every exercise in source.txt. Save to report.txt.", max_steps=2
    )
    assert result["status"] == "incomplete"
    assert "Exercise ALPHA" in result["result"] and "Exercise BETA" in result["result"]


def test_a_finished_extraction_survives_the_step_limit(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    script(
        agent,
        {"tool": "extract_document_items", "tool_args": {"file_path": "source.txt"}},
    )
    result = agent.process_query("List every exercise in source.txt", max_steps=1)
    assert result["status"] == "incomplete"
    assert "Exercise BETA" in result["result"]
