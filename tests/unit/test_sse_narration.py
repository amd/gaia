# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
User-facing progress events on the canonical ``/query`` stream (#2804).

A user watching a two-minute turn used to see only ``Step 1/50`` /
``Processing with Gemma-4-E4B-it-GGUF...`` / ``Thinking`` — three lines about
the harness and none about the work. These tests pin the three halves of the
fix: ``tool_call.narration`` says what the agent is doing, ``tool_result.preview``
says how it went, and the harness bookkeeping only reaches a debug channel.
"""

import pytest

from gaia.agents.base.agent import Agent
from gaia.ui.event_narration import derive_narration, derive_preview, format_count
from gaia.ui.sse_handler import SSEOutputHandler
from gaia.ui.sse_translation import CanonicalTranslator

RUN_ID = "run-narration"


def _tr(**kwargs) -> CanonicalTranslator:
    return CanonicalTranslator(RUN_ID, agent_id="gaia", **kwargs)


@pytest.fixture(autouse=True)
def _no_ambient_debug(monkeypatch):
    """A developer's exported GAIA_SSE_DEBUG_EVENTS must not flip these tests."""
    monkeypatch.delenv("GAIA_SSE_DEBUG_EVENTS", raising=False)


# ===========================================================================
# derive_narration
# ===========================================================================


class TestDeriveNarration:
    """tool_call.narration — present tense, user's terms, never empty."""

    @pytest.mark.parametrize(
        "tool, args, expected",
        [
            # Curated override for a phrase the generic deriver reads badly.
            ("list_skills", {}, "Checking your installed skills"),
            # Generic verb_object conjugation + salient argument.
            ("load_skill", {"name": "github-triage"}, "Loading skill: github-triage"),
            ("read_file", {"file_path": "src/app.py"}, "Reading file: src/app.py"),
            ("write_file", {"file_path": "out.md"}, "Writing file: out.md"),
            ("index_document", {"path": "spec.pdf"}, "Indexing document: spec.pdf"),
            # No arguments -> the verb phrase alone still reads as an action.
            ("fetch_page", {}, "Fetching page"),
        ],
    )
    def test_known_shapes(self, tool, args, expected):
        assert derive_narration(tool, args) == expected

    def test_shell_narration_includes_the_actual_command(self):
        """A shell narration without the command hides the only thing that matters."""
        narration = derive_narration(
            "run_shell_command", {"command": "git log --oneline -5"}
        )
        assert narration == "Running command: git log --oneline -5"

    @pytest.mark.parametrize(
        "tool", ["run_shell_command", "bash_exec", "terminal_run", "subprocess_call"]
    )
    def test_every_shell_flavoured_tool_surfaces_its_command(self, tool):
        assert "rm -rf /tmp/x" in derive_narration(tool, {"command": "rm -rf /tmp/x"})

    def test_shell_without_a_command_says_so_rather_than_faking_one(self):
        assert derive_narration("run_shell_command", {}) == "Running a shell command"

    def test_multiline_command_is_flattened_to_one_line(self):
        narration = derive_narration(
            "run_shell_command", {"command": "cd /tmp\nls -la"}
        )
        assert "\n" not in narration
        assert narration == "Running command: cd /tmp ls -la"

    def test_long_argument_is_clipped(self):
        narration = derive_narration("run_shell_command", {"command": "x" * 500})
        assert len(narration) < 150
        assert narration.endswith("…")

    # -- the fallback: a tool nobody taught this module ---------------------

    def test_unknown_tool_with_known_verb_still_reads_as_an_action(self):
        assert derive_narration("frobnicate_widget", {}) == "Running frobnicate widget"

    def test_unknown_tool_keeps_its_salient_argument(self):
        assert (
            derive_narration("zork_thing", {"name": "alpha"})
            == "Running zork thing: alpha"
        )

    @pytest.mark.parametrize("tool", ["", "   ", None])
    def test_unnamed_tool_never_yields_an_empty_narration(self, tool):
        """No-silent-fallback: an honest generic phrase, never ``''``."""
        assert derive_narration(tool, {}) == "Running a tool"

    def test_narration_is_never_empty_for_any_arg_shape(self):
        for args in (
            {},
            None,
            {"a": None},
            {"a": [1, 2]},
            {"x": 1, "y": 2},
            "not-a-map",
        ):
            assert derive_narration("some_tool", args).strip()

    def test_boolean_flag_does_not_become_the_narration_detail(self):
        """``recursive=True`` is never the point of a call."""
        assert derive_narration("scan_tree", {"recursive": True}) == "Scanning tree"

    def test_several_unrecognized_args_are_dropped_rather_than_dumped(self):
        assert (
            derive_narration("zork_thing", {"aa": 1, "bb": 2}) == "Running zork thing"
        )


# ===========================================================================
# derive_preview
# ===========================================================================


class TestDerivePreview:
    """tool_result.preview — one clipped line: outcome, size, latency."""

    def test_count_and_latency(self):
        payload = {"summary": "18 skills", "success": True, "latency_ms": 20.7}
        assert derive_preview("list_skills", payload) == "18 skills · 21ms"

    def test_bare_success_is_upgraded_to_a_real_count_when_available(self):
        payload = {"summary": "success", "success": True, "skills": [1, 2, 3]}
        assert derive_preview("list_skills", payload) == "3 skills"

    def test_bare_success_survives_when_there_is_nothing_to_count(self):
        assert derive_preview("ping", {"summary": "success"}) == "success"

    def test_a_single_item_reads_as_singular(self):
        """Live capture showed '1 loaded skills'."""
        payload = {"summary": "success", "loaded_skills": ["github-triage"]}
        assert derive_preview("load_skill", payload) == "1 loaded skill"

    @pytest.mark.parametrize(
        "count, noun, expected",
        [
            (1, "loaded_skills", "1 loaded skill"),
            (4, "loaded_skills", "4 loaded skills"),
            (0, "skills", "0 skills"),
            (1, "files", "1 file"),
            (1, "entries", "1 entry"),
            (1, "matches", "1 match"),
            (1, "addresses", "1 address"),
            # Words that merely END in 's' must not lose a letter.
            (1, "status", "1 status"),
            (1, "analysis", "1 analysis"),
        ],
    )
    def test_format_count_pluralization(self, count, noun, expected):
        assert format_count(count, noun) == expected

    def test_failure_leads_with_the_error(self):
        payload = {"success": False, "summary": "GITHUB_TOKEN not set"}
        assert derive_preview("list_issues", payload) == "error: GITHUB_TOKEN not set"

    def test_error_status_is_treated_as_failure(self):
        payload = {"status": "error", "error": "connection refused", "latency_ms": 5}
        assert (
            derive_preview("fetch_page", payload) == "error: connection refused · 5ms"
        )

    def test_shell_success_reports_exit_code_and_line_count(self):
        payload = {"command_output": {"return_code": 0, "stdout": "a\nb\nc"}}
        assert derive_preview("run_shell_command", payload) == "exit 0 · 3 lines"

    def test_shell_no_output_is_stated_not_guessed(self):
        payload = {"command_output": {"return_code": 0, "stdout": ""}}
        assert derive_preview("run_shell_command", payload) == "exit 0 · no output"

    def test_shell_failure_reports_exit_code_and_stderr(self):
        payload = {
            "command_output": {
                "return_code": 128,
                "stdout": "",
                "stderr": "fatal: not a git repository",
            }
        }
        preview = derive_preview("run_shell_command", payload)
        assert preview == "exit 128: fatal: not a git repository"

    def test_missing_payload_is_honest_rather_than_blank(self):
        assert derive_preview("list_skills", None) == "list_skills returned no result"
        assert derive_preview("list_skills", {}) == "list_skills returned no result"

    # -- truncation ---------------------------------------------------------

    def test_long_summary_is_clipped_and_annotated_with_its_size(self):
        payload = {"summary": "x" * 5000, "latency_ms": 42}
        preview = derive_preview("read_file", payload)
        assert len(preview) <= 120
        assert preview.endswith("42ms")
        assert "truncated" in preview
        assert "KB" in preview

    def test_preview_is_always_one_line(self):
        payload = {"summary": "line one\nline two\nline three"}
        preview = derive_preview("read_file", payload)
        assert "\n" not in preview
        assert preview == "line one line two line three"

    def test_preview_never_exceeds_the_cap_for_any_payload(self):
        for summary in ("y" * 10_000, "z\n" * 5000, "short"):
            preview = derive_preview("t", {"summary": summary, "latency_ms": 1234})
            assert 0 < len(preview) <= 120

    def test_raw_json_blob_is_not_dumped_into_the_line(self):
        payload = {"summary": {"nested": {"deep": list(range(200))}}}
        preview = derive_preview("t", payload)
        assert len(preview) <= 120


# ===========================================================================
# CanonicalTranslator wiring
# ===========================================================================


class TestTranslatorNarration:
    """The additive fields reach the wire without disturbing the old ones."""

    def test_tool_call_carries_narration_alongside_the_frozen_fields(self):
        translator = _tr()
        out = translator.translate({"type": "tool_start", "tool": "load_skill"})
        out += translator.translate(
            {"type": "tool_args", "args": {"name": "github-triage"}}
        )
        (call,) = [e for e in out if e["type"] == "tool_call"]
        # The live Go TUI reads these three — they must not move.
        assert call["tool"] == "load_skill"
        assert call["args"] == {"name": "github-triage"}
        assert call["narration"] == "Loading skill: github-triage"

    def test_argument_less_tool_call_still_narrates(self):
        translator = _tr()
        translator.translate({"type": "tool_start", "tool": "list_skills"})
        (call,) = translator.flush()
        assert call["args"] == {}
        assert call["narration"] == "Checking your installed skills"

    def test_tool_result_carries_preview_alongside_data(self):
        translator = _tr()
        translator.translate({"type": "tool_start", "tool": "list_skills"})
        translator.translate({"type": "tool_args", "args": {}})
        (result,) = translator.translate(
            {
                "type": "tool_result",
                "summary": "18 skills",
                "success": True,
                "latency_ms": 20.7,
            }
        )
        assert result["tool"] == "list_skills"
        assert result["data"]["summary"] == "18 skills"
        assert result["preview"] == "18 skills · 21ms"

    def test_tool_result_preserves_truncation_marker(self):
        translator = _tr()
        translator.translate({"type": "tool_start", "tool": "archive_message_batch"})
        translator.translate({"type": "tool_args", "args": {}})
        (result,) = translator.translate(
            {
                "type": "tool_result",
                "summary": '{"succeeded":["message-1"],"failed":[{"error":"already archived"',
                "success": True,
                "summary_truncated": True,
            }
        )
        assert result["data"]["summary_truncated"] is True

    def test_render_map_result_still_gets_a_preview(self):
        """``data`` is the card payload for a render tool; preview comes from the source."""
        translator = CanonicalTranslator(
            RUN_ID, agent_id="gaia", render_tool_map={"draw_chart": "chart"}
        )
        translator.translate({"type": "tool_start", "tool": "draw_chart"})
        translator.translate({"type": "tool_args", "args": {}})
        (result,) = translator.translate(
            {
                "type": "tool_result",
                "result_data": {"type": "chart", "points": [1, 2]},
                "summary": "chart rendered",
                "latency_ms": 8,
            }
        )
        assert result["render"] == "chart"
        assert result["data"] == {"type": "chart", "points": [1, 2]}
        assert result["preview"] == "chart rendered · 8ms"

    def test_synthesized_result_from_bare_tool_end_has_a_preview(self):
        translator = _tr()
        translator.translate({"type": "tool_start", "tool": "list_skills"})
        translator.translate({"type": "tool_args", "args": {}})
        (result,) = translator.translate({"type": "tool_end", "success": True})
        assert result["type"] == "tool_result"
        assert result["preview"]

    def test_every_tool_call_and_result_is_narrated(self):
        """No event reaches the wire with an empty narration/preview."""
        translator = _tr()
        emitted = []
        for event in (
            {"type": "tool_start", "tool": "mystery_tool"},
            {"type": "tool_args", "args": {"blob": {"a": 1}}},
            {"type": "tool_result", "summary": ""},
            {"type": "tool_end"},
        ):
            emitted.extend(translator.translate(event))
        emitted.extend(translator.flush())
        for event in emitted:
            if event["type"] == "tool_call":
                assert event["narration"].strip()
            if event["type"] == "tool_result":
                assert event["preview"].strip()


class TestHarnessDebugChannel:
    """Step counters and model banners are demoted, not deleted."""

    def test_step_events_are_dropped_by_default(self):
        assert _tr().translate({"type": "step", "step": 1, "total": 50}) == []

    def test_processing_banner_is_dropped_by_default(self):
        event = {
            "type": "status",
            "status": "working",
            "message": "Processing with Gemma-4-E4B-it-GGUF...",
            "channel": "debug",
        }
        assert _tr().translate(event) == []

    def test_thinking_progress_label_is_dropped_by_default(self):
        event = {
            "type": "status",
            "status": "working",
            "message": "Thinking",
            "channel": "debug",
        }
        assert _tr().translate(event) == []

    def test_debug_mode_keeps_them_on_a_marked_channel(self):
        translator = _tr(debug=True)
        (step,) = translator.translate({"type": "step", "step": 3, "total": 50})
        assert step == {"type": "status", "message": "Step 3/50", "channel": "debug"}

    def test_env_var_enables_the_debug_channel(self, monkeypatch):
        monkeypatch.setenv("GAIA_SSE_DEBUG_EVENTS", "1")
        (step,) = CanonicalTranslator(RUN_ID, agent_id="gaia").translate(
            {"type": "step", "step": 2, "total": 50}
        )
        assert step["channel"] == "debug"

    def test_explicit_false_beats_the_env_var(self, monkeypatch):
        monkeypatch.setenv("GAIA_SSE_DEBUG_EVENTS", "1")
        translator = CanonicalTranslator(RUN_ID, agent_id="gaia", debug=False)
        assert translator.translate({"type": "step", "step": 2, "total": 50}) == []

    def test_untagged_status_still_reaches_the_user(self):
        """Only harness bookkeeping is demoted — a real update is not."""
        (status,) = _tr().translate(
            {"type": "status", "message": "Reviewing the last 5 commits"}
        )
        assert status == {"type": "status", "message": "Reviewing the last 5 commits"}

    def test_llm_reasoning_still_reaches_the_user(self):
        """The user explicitly asked to see WHY a tool is being called."""
        (status,) = _tr().translate(
            {"type": "thinking", "content": "I need the skill list first"}
        )
        assert status["message"] == "I need the skill list first"
        assert "channel" not in status


class TestNonStreamingProgressLabel:
    """The non-streaming wait names the phase instead of going silent.

    A local model blocks here for up to a minute with nothing else on the wire,
    so demoting the old "Thinking" label without replacing it would trade
    harness noise for dead air (#2804).
    """

    @staticmethod
    def _agent(state: str) -> Agent:
        """A bare Agent — the label depends only on ``execution_state``."""

        class _LabelOnlyAgent(Agent):
            def _register_tools(self):  # pragma: no cover - never invoked
                pass

        agent = _LabelOnlyAgent.__new__(_LabelOnlyAgent)
        agent.execution_state = state
        return agent

    @pytest.mark.parametrize(
        "state, expected",
        [
            ("PLANNING", "Working out how to answer"),
            ("EXECUTING_PLAN", "Working through the plan"),
            ("DIRECT_EXECUTION", "Figuring out what to do"),
            ("ERROR_RECOVERY", "Recovering from a failed step"),
            ("COMPLETION", "Putting the answer together"),
        ],
    )
    def test_each_state_has_a_user_facing_label(self, state, expected):
        assert self._agent(state)._progress_label() == expected

    def test_unmapped_state_names_the_wait_rather_than_the_loop(self):
        assert self._agent("SOME_NEW_STATE")._progress_label() == (
            "Working on your request"
        )

    def test_back_to_back_duplicate_status_emits_once(self):
        translator = _tr()
        first = translator.translate({"type": "status", "message": "Planning"})
        second = translator.translate({"type": "status", "message": "Planning"})
        assert len(first) == 1
        assert second == []

    def test_the_same_phase_re_emits_after_a_tool_runs(self):
        """The repeat is what fills the silence while the model composes."""
        translator = _tr()
        assert translator.translate({"type": "status", "message": "Planning"})
        translator.translate({"type": "tool_start", "tool": "list_skills"})
        translator.translate({"type": "tool_args", "args": {}})
        translator.translate({"type": "tool_result", "summary": "ok"})
        assert translator.translate({"type": "status", "message": "Planning"})

    def test_a_changed_phase_always_emits(self):
        translator = _tr()
        translator.translate({"type": "status", "message": "Planning"})
        assert translator.translate(
            {"type": "status", "message": "Putting the answer together"}
        )

    def test_no_phase_label_is_demoted_to_the_debug_channel(self):
        """A phase label must survive the harness filter and reach the user."""
        labels = list(Agent._STATE_PROGRESS_LABELS.values()) + [
            "Working on your request"
        ]
        for label in labels:
            handler = SSEOutputHandler()
            handler.start_progress(label)
            event = handler.event_queue.get_nowait()
            assert "channel" not in event, f"{label!r} was demoted to debug"
            assert _tr().translate(event), f"{label!r} never reached the user"
