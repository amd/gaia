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
    ExtractionLedger,
    exhaustive_request,
    extract_pages,
    parse_page,
    read_snapshot,
    same_occurrence_fields,
)
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator


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
    assert same_occurrence_fields(first, second)
    assert first.text == "cue: look up."
    assert not same_occurrence_fields(first, entry("up."))


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


def test_safe_snapshot_rejects_fifo_without_waiting(tmp_path):
    import os

    path = tmp_path / "pipe"
    os.mkfifo(path)
    with pytest.raises(ValueError, match="regular text file"):
        read_snapshot(str(path), PathValidator(allowed_paths=[str(tmp_path)]))


def test_active_skill_enables_extraction_but_general_knowledge_does_not(tmp_path):
    state = ExtractionLedger("List all planets", str(tmp_path))
    assert not state.enabled and not state.gaps()
    state.activate_skill("Extract **every** exercise from the workshop transcript.")
    assert state.enabled and state.gaps()
    assert exhaustive_request("Enumerate every function in code.py")


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


def test_combined_source_and_save_request_keeps_all_sources(tmp_path):
    state = ExtractionLedger(
        "List every exercise in one.txt and two.txt and save to report.txt",
        str(tmp_path),
    )
    assert state.requested == {str(tmp_path / "one.txt"), str(tmp_path / "two.txt")}


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
