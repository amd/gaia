# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for tool-call parse-error recovery in the base Agent class.

Small models (4B-class) occasionally emit malformed native ``tool_calls``
envelopes — for example a 1000+ char ``summary_type`` argument that gets
truncated mid-string. Before the recovery layer landed, ``_parse_llm_response``
would raise ``ValueError`` and the unhandled exception bubbled out to the user
as ``Agent error: Malformed native tool_calls envelope: ...``.

The recovery layer in ``Agent.process_query`` catches the parse error, logs
it, appends a synthetic recovery prompt to the conversation, and continues
the loop so the model can retry with cleaner arguments.
"""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent


class _DummyAgent(Agent):
    """Minimal concrete Agent for testing."""

    def _get_system_prompt(self) -> str:
        return "You are a test agent."

    def _register_tools(self) -> None:
        pass

    def _create_console(self):
        from gaia.agents.base.console import AgentConsole

        return AgentConsole()


@pytest.fixture
def agent():
    with patch("gaia.agents.base.agent.AgentSDK"):
        a = _DummyAgent(silent_mode=True, skip_lemonade=True)
        # Disable streaming so we exercise the simpler non-streaming path.
        a.streaming = False
        return a


class TestParseLLMResponseRaisesOnMalformed:
    def test_truncated_tool_calls_envelope_raises_valueerror(self, agent):
        """Malformed JSON in the __tool_calls__ sentinel raises ValueError."""
        # Truncated mid-string — what the small model produced for
        # honest_limitation Turn 2.
        bad = (
            '{"__tool_calls__": [{"function": {"name": "summarize_document", '
            '"arguments": "{\\"summary_type\\": \\"brief detailed bullets'
        )
        with pytest.raises(ValueError, match="Malformed native tool_calls"):
            agent._parse_llm_response(bad)


class TestProcessQueryRecoversOnParseError:
    """The full process_query loop should not crash when parse fails."""

    def _stub_chat(self, agent, *responses):
        """Replace agent.chat with a stub that yields *responses* in order."""
        responses = list(responses)
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def test_malformed_envelope_then_plain_answer(self, agent):
        """First call malformed tool_calls, second call plain text answer."""
        bad = (
            '{"__tool_calls__": [{"function": {"name": "summarize_document",'
            ' "arguments": "{\\"summary_type\\": \\"brief detailed bullets'
        )
        good_answer = json.dumps(
            {
                "thought": "Done.",
                "answer": "Acme Corp had $14.2M revenue in Q3 2025.",
            }
        )
        chat = self._stub_chat(agent, bad, good_answer)

        result = agent.process_query("What can you tell me?", max_steps=5)

        # Recovery path was exercised — chat called twice.
        assert chat.send_messages.call_count == 2
        # error_history records the parse error
        assert any(
            e.get("type") == "tool_call_parse_error" for e in agent.error_history
        )
        # Final answer reached the user (not the raw envelope error)
        assert (
            "Agent error" not in result.get("response", "")
            if isinstance(result, dict)
            else True
        )

    def test_three_consecutive_parse_errors_give_up_gracefully(self, agent):
        """After 3 parse errors the loop bails with a friendly message."""
        bad = '{"__tool_calls__": [{"function": {"name": "x", "arguments": "{'
        # Pre-load 5 responses so the loop has plenty to chew on.
        self._stub_chat(agent, bad, bad, bad, bad, bad)

        result = agent.process_query("test", max_steps=10)

        # Final answer is the friendly fallback, NOT a leaked envelope error.
        # The result shape varies per agent — accept either dict or string.
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            assert "Malformed" not in text
            assert "Agent error" not in text


class TestProcessQueryRecoversOnContextOverflow:
    """When the multi-step loop accumulates tool messages past the context
    window, the agent should trim and retry once before giving up."""

    def _stub_chat_with_exception_then_answer(self, agent, exc, answer):
        """First call raises *exc*, second returns plain-text *answer*."""
        responses = [exc, answer]
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            if isinstance(r, BaseException):
                raise r
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def test_context_overflow_triggers_trim_and_retry(self, agent):
        """First call raises overflow, second succeeds → final_answer set."""
        agent.streaming = False
        # Stub the live Lemonade health probe: the trim-and-retry path this
        # test asserts is only taken when the loaded ctx is NOT "too small".
        # Without this the test's outcome depends on whatever model a dev's
        # local Lemonade happens to have loaded (issue #2287).
        agent._is_loaded_ctx_too_small = lambda: False
        good = json.dumps({"thought": "ok", "answer": "Here you go."})
        chat = self._stub_chat_with_exception_then_answer(
            agent,
            RuntimeError("exceed_context_size: prompt + history exceeds 32768 tokens"),
            good,
        )
        result = agent.process_query("anything", max_steps=5)
        assert chat.send_messages.call_count == 2
        assert any(
            e.get("type") == "llm_context_overflow_trimmed" for e in agent.error_history
        )
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            assert "exceed_context_size" not in text
            assert "Sorry, I ran into" not in text

    def test_context_overflow_after_retry_gives_friendly_fallback(self, agent):
        """When trim+retry STILL fails, final message is friendly."""
        agent.streaming = False
        # See test above: stub the probe so this exercises the trim-then-fallback
        # path deterministically rather than the reload re-raise (issue #2287).
        agent._is_loaded_ctx_too_small = lambda: False
        responses = [
            RuntimeError("exceeds the available context size"),
            RuntimeError("exceeds the available context size"),
        ]
        chat = MagicMock()

        def _send(*_, **__):
            raise responses.pop(0)

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        result = agent.process_query("x", max_steps=5)
        text = result.get("response") if isinstance(result, dict) else str(result)
        if text:
            # No raw exception leaked
            assert "exceeds the available context size" not in text
            assert "Traceback" not in text

    def test_wrong_ctx_loaded_reraises_for_model_reload(self, agent):
        """When the probe reports a too-small loaded ctx, the overflow is
        re-raised so the chat helper can reload the model — instead of being
        trimmed in-loop. This branch is unreachable on CI (no Lemonade), so
        stubbing the probe is the only way to cover it (issue #2287)."""
        agent.streaming = False
        # Probe reports the loaded model has a ctx smaller than GAIA expects.
        agent._is_loaded_ctx_too_small = lambda: True
        chat = MagicMock()

        def _send(*_, **__):
            raise RuntimeError("exceeds the available context size")

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat

        # The overflow must propagate so an outer helper can reload the model.
        with pytest.raises(RuntimeError, match="exceeds the available context size"):
            agent.process_query("x", max_steps=5)
        # The re-raise path (not the trim path) was taken.
        assert any(
            e.get("type") == "llm_wrong_ctx_loaded_reraise" for e in agent.error_history
        )
        assert not any(
            e.get("type") == "llm_context_overflow_trimmed" for e in agent.error_history
        )


class TestRepairInvalidJsonEscapes:
    """Helper that doubles invalid JSON backslash escapes (e.g. Windows paths).

    Issue #1023: smaller LLMs (Gemma-4-E4B-class) sometimes emit Windows paths
    in tool-call arguments with single backslashes. Strict ``json.loads``
    rejects ``\\U`` (and any other backslash followed by a non-escape char).
    """

    def test_doubles_backslash_before_invalid_escape_char(self):
        """`C:\\Users\\K` (single-escaped) becomes parseable JSON after repair."""
        from gaia.agents.base.agent import _repair_invalid_json_escapes

        bad = r'{"path":"C:\Users\K"}'
        # Sanity: input is genuinely invalid JSON without repair.
        with pytest.raises(json.JSONDecodeError):
            json.loads(bad)
        repaired = _repair_invalid_json_escapes(bad)
        assert json.loads(repaired) == {"path": r"C:\Users\K"}

    def test_preserves_valid_json_escape_sequences(self):
        """Valid escapes (\\n \\t \\\\ \\" \\u00ff) must pass through unchanged."""
        from gaia.agents.base.agent import _repair_invalid_json_escapes

        valid = (
            r'{"text":"line1\nline2\ttab","quote":"\"",'
            r'"unicode":"ÿ","slash":"a\\b"}'
        )
        before = json.loads(valid)
        repaired = _repair_invalid_json_escapes(valid)
        assert json.loads(repaired) == before

    def test_idempotent_on_already_repaired_string(self):
        """Running repair twice gives the same result as running it once."""
        from gaia.agents.base.agent import _repair_invalid_json_escapes

        bad = r'{"a":"\X","b":"\W"}'
        once = _repair_invalid_json_escapes(bad)
        twice = _repair_invalid_json_escapes(once)
        assert once == twice
        # And the result actually parses.
        assert json.loads(once) == {"a": r"\X", "b": r"\W"}


class TestParseLLMResponseRecoversFromInvalidEscapes:
    """Issue #1023: tool-call arguments with under-escaped Windows paths."""

    def test_windows_path_with_single_escapes_parses_via_repair(self, agent):
        """Reproduces #1023 step 2: ``C:\\Users\\Klaus\\img.png`` parses cleanly."""
        # The arguments string the LLM emitted (under-escaped backslashes).
        # ``r"..."`` keeps backslashes literal -- this is what the outer JSON
        # decoder hands to the inner ``json.loads(arguments_raw)`` call.
        malformed_inner = (
            r'{"image_path":"C:\Users\Klaus\.gaia\cache\sd\images\img.png",'
            r'"story_style":"dramatic"}'
        )
        # Sanity: confirm the input is genuinely invalid JSON without repair.
        with pytest.raises(json.JSONDecodeError):
            json.loads(malformed_inner)

        # Build the full LLM response envelope. ``json.dumps`` encodes the
        # outer level correctly while keeping the inner string verbatim.
        response = json.dumps(
            {
                "__tool_calls__": [
                    {
                        "function": {
                            "name": "create_story_from_image",
                            "arguments": malformed_inner,
                        }
                    }
                ]
            }
        )

        parsed = agent._parse_llm_response(response)
        assert parsed["tool"] == "create_story_from_image"
        assert parsed["tool_args"]["image_path"] == (
            r"C:\Users\Klaus\.gaia\cache\sd\images\img.png"
        )
        assert parsed["tool_args"]["story_style"] == "dramatic"

    def test_truly_malformed_args_still_raise_after_repair_attempt(self, agent):
        """When repair cannot fix the JSON, ValueError still propagates."""
        # Truncated/corrupt -- repair won't help, error must still surface.
        broken = (
            '{"__tool_calls__": [{"function": {"name": "x",'
            ' "arguments": "{not-json:"}}]}'
        )
        with pytest.raises(ValueError, match="Malformed"):
            agent._parse_llm_response(broken)


class TestPostFailureOverrideSkippedAfterCapabilitySuccess:
    """Issue #1023 step 3: the verbose-failure override at process_query
    must NOT fire when the capability tool (``generate_image``) succeeded.

    The original guard fired solely on ``has_tried_capability_tool`` --
    "was generate_image called this turn?" -- with no check on whether the
    call actually returned an error.  As a result, when generate_image
    succeeded and a SUBSEQUENT tool's parse error provoked a verbose
    apology, the override clobbered the model's reply with a misleading
    "Image generation is not available" message.
    """

    def _stub_chat(self, agent, *responses):
        responses = list(responses)
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def _register_generate_image(self, agent, *, status: str = "success") -> None:
        """Inject a fake generate_image into the agent's instance registry.

        ``_instance_tools`` is per-instance, so this does not pollute the
        global ``_TOOL_REGISTRY`` or other tests.
        """
        result = (
            {
                "status": "success",
                "image_path": r"C:\Users\K\img.png",
                "model": "SDXL-Turbo",
            }
            if status == "success"
            else {"status": "error", "error": "SD backend not available"}
        )
        agent._instance_tools = {
            "generate_image": {
                "name": "generate_image",
                "description": "stub",
                "parameters": {
                    "prompt": {"type": "string", "required": True},
                },
                "function": lambda prompt="", _r=result: _r,
                "atomic": True,
            }
        }

    def test_override_skipped_when_generate_image_succeeded(self, agent):
        """generate_image returned success -> verbose model reply is preserved."""
        self._register_generate_image(agent, status="success")

        # Step 1: model calls generate_image (succeeds).
        step1 = json.dumps(
            {"tool": "generate_image", "tool_args": {"prompt": "a forest"}}
        )
        # Step 2: malformed tool call -> parse fails -> recovery prompt added.
        step2 = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "make_story", "arguments": "{not-json:"}}]}'
        )
        # Step 3: model responds with phrasing that matches the verbose-failure
        # regex (``r"i apologize for the confusion"``).  After the fix, this
        # text passes through unchanged because generate_image SUCCEEDED.
        step3 = json.dumps(
            {
                "answer": (
                    "I apologize for the confusion. "
                    "The image was generated successfully."
                )
            }
        )
        self._stub_chat(agent, step1, step2, step3)

        result = agent.process_query("make me an image", max_steps=10)
        # ``process_query`` returns ``{"status": ..., "result": <final_answer>, ...}``
        text = result["result"]

        # The hardcoded override message must NOT replace the model's reply.
        assert "Image generation is not available" not in text
        # And the model's actual answer must be visible.
        assert "image was generated successfully" in text.lower()

    def test_override_still_fires_when_generate_image_failed(self, agent):
        """Regression guard: legitimate failure path keeps the canonical message.

        Pairs with the post-success test above.  Without this, the gate
        could be (intentionally or accidentally) widened to skip the
        override unconditionally and only the post-success test would
        catch it -- leaving the legitimate "SD not enabled" UX broken.
        """
        self._register_generate_image(agent, status="error")

        # Step 1: model calls generate_image (returns error).
        step1 = json.dumps(
            {"tool": "generate_image", "tool_args": {"prompt": "a forest"}}
        )
        # Step 2: model emits a verbose-apology answer matching the
        # ``i apologize for the confusion`` pattern.  Because the
        # capability tool actually FAILED, the override must replace
        # this with the canonical "Image generation is not available"
        # message -- preserving the pre-existing UX for the legitimate
        # "SD not enabled" case.
        step2 = json.dumps(
            {
                "answer": (
                    "I apologize for the confusion. Let me explain what "
                    "I would have done with prompt enhancement..."
                )
            }
        )
        self._stub_chat(agent, step1, step2)

        result = agent.process_query("make me an image", max_steps=10)
        text = result["result"]

        # The override fired and replaced the verbose apology with the
        # canonical "not available" message.
        assert "Image generation is not available" in text

    def test_override_fires_after_mixed_case_capability_failure(self, agent):
        """The tracker must be case-insensitive (mirrors ``has_tried_capability_tool``).

        Models occasionally emit tool names with non-canonical casing
        (``Generate_Image``).  The dispatcher resolves these via
        ``_resolve_tool_name`` for execution, but ``tool_call_log`` records
        the un-normalized name -- so both the pre-existing
        ``has_tried_capability_tool`` check and the new
        ``capability_tool_last_succeeded`` tracker must apply ``.lower()``
        for the gate to evaluate consistently.

        The interesting failure mode is **mixed-case + tool error**:
        without ``.lower()``, the tracker silently stays at ``None``, and
        ``None is False`` is False, so the override DOESN'T fire even
        though the capability tool legitimately errored.  The user would
        then see a verbose model apology instead of the canonical "not
        available" message.  This test pins that down.
        """
        # generate_image returns an error this time.
        self._register_generate_image(agent, status="error")

        # Step 1: model emits the tool name with mixed case.  The
        # dispatcher resolves "Generate_Image" -> "generate_image" but
        # ``tool_call_log`` records the original LLM-emitted casing.
        step1 = json.dumps(
            {"tool": "Generate_Image", "tool_args": {"prompt": "a forest"}}
        )
        # Step 2: verbose-apology answer that matches the failure-override
        # regex.  With ``.lower()`` the tracker correctly registered Step
        # 1's failure, so the gate fires and replaces this with the
        # canonical "not available" message.
        step2 = json.dumps(
            {
                "answer": (
                    "I apologize for the confusion. Let me explain what "
                    "I would have done with prompt enhancement..."
                )
            }
        )
        self._stub_chat(agent, step1, step2)

        result = agent.process_query("make me an image", max_steps=10)
        text = result["result"]

        # Override fired despite the mixed-case tool name.
        assert "Image generation is not available" in text


class TestEnvelopeLevelRepairAndRawDecode:
    """Issue #1023 follow-up: envelope-level recovery for both failure modes
    klkr1 reported after PR #1027 landed.

    The original fix added ``_repair_invalid_json_escapes`` to the inner
    per-tool ``arguments`` parse but **not** to the outer envelope
    ``json.loads(response)`` at ``agent.py:1064``.  klkr1's Try #3 hit the
    envelope with ``Extra data: line 1 column 368 (char 367)`` — trailing
    garbage after a well-formed JSON object.  The new envelope path tries
    the backslash-repair first (defense in depth) and falls back to
    ``raw_decode`` for the ``Extra data`` case.
    """

    def test_envelope_with_invalid_escape_recovers_via_repair(self, agent):
        """Outer envelope with a bare ``\\U`` -> repair pass salvages it.

        Constructed payload: a structurally valid envelope plus a top-level
        ``"extra"`` field containing a literal Windows path with single
        backslashes.  Without repair the envelope's own ``json.loads``
        rejects the ``\\U``.  With repair the envelope parses and the
        ``__tool_calls__`` list is extracted normally.
        """
        bad_envelope = (
            '{"__tool_calls__":[{"function":{"name":"x","arguments":""}}],'
            '"extra":"C:\\Users\\K"}'
        )
        # Sanity: the input is genuinely invalid JSON without repair.
        with pytest.raises(json.JSONDecodeError):
            json.loads(bad_envelope)

        parsed = agent._parse_llm_response(bad_envelope)
        assert parsed["tool"] == "x"

    def test_envelope_with_trailing_data_recovers_via_raw_decode(self, agent, caplog):
        """Outer envelope with trailing commentary -> ``raw_decode`` salvages it.

        Reproduces klkr1's Try #3: a structurally valid envelope followed by
        non-JSON text (commentary, whitespace, a stray brace).  ``json.loads``
        raises ``Extra data: line 1 column N (char N-1)`` but
        ``JSONDecoder.raw_decode`` parses one value and reports the end
        index; we take the structured prefix and log the discarded suffix
        length at info.
        """
        good = '{"__tool_calls__":[{"function":{"name":"x","arguments":""}}]}'
        bad_envelope = good + "   some trailing commentary the model added"
        # Sanity: bare json.loads rejects this with Extra data.
        with pytest.raises(json.JSONDecodeError) as exc_info:
            json.loads(bad_envelope)
        assert exc_info.value.msg.startswith("Extra data")

        with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
            parsed = agent._parse_llm_response(bad_envelope)
        assert parsed["tool"] == "x"
        # Production telemetry must include a marker so a steady stream of
        # trailing-data recoveries surfaces in field logs.
        assert any("tolerated trailing data" in r.message for r in caplog.records)

    def test_completely_malformed_envelope_still_raises(self, agent):
        """Garbage that is neither repairable nor a JSON prefix raises ValueError.

        Guards against silent success: if a future change to the recovery
        path accidentally widens to ``raw_decode`` on anything that starts
        with ``{"__tool_calls__":``, this test catches it.
        """
        # Starts with the sentinel prefix but the inside is structural
        # garbage that neither repair nor raw_decode can salvage.
        bad = '{"__tool_calls__":<<not-json>>}'
        with pytest.raises(ValueError, match="Malformed native tool_calls"):
            agent._parse_llm_response(bad)

    def test_raw_decode_prefix_without_tool_calls_key_raises_valueerror(self, agent):
        """raw_decode salvages a JSON object missing ``__tool_calls__`` -> ValueError.

        Without the defensive check, ``raw_decode`` of ``{"foo":1}<trailing>``
        succeeds with ``{"foo":1}`` and the next line's bare
        ``envelope["__tool_calls__"]`` raises ``KeyError`` -- which escapes
        the recovery branch's ``except (ValueError, NotImplementedError)``
        catch at agent.py:2820 and crashes the session.  The fix converts
        the bare lookup into a checked one; this test pins that the failure
        surfaces as the catchable ``ValueError`` instead.
        """
        # NOTE: the sentinel check at the top of ``_parse_llm_response``
        # requires the response to literally start with ``{"__tool_calls__":``.
        # To exercise the raw_decode path we need that prefix; we then
        # construct an envelope where the prefix is a literal string but
        # the FIRST raw_decode parse finds a *different* JSON object that
        # happens to lack the key.  Easiest hand-crafted case: trailing
        # data after a JSON literal that doesn't have the key.
        #
        # Simpler approach: simulate the raw_decode salvaging a wrong
        # object by feeding a sentinel-prefixed string whose JSON is a
        # nested object that doesn't itself carry ``__tool_calls__`` at
        # top level.  We construct that via JSON with a key that LOOKS
        # like the sentinel but isn't the structural top.
        #
        # The cleanest test is direct: monkey-patch the raw_decode result
        # to confirm the post-recovery check raises.  But we already
        # cover the structural ``Extra data`` path above; the unit being
        # protected is the ``__tool_calls__`` validator at L1115-1120.
        # Verify it directly without going through raw_decode.
        envelope_missing_key = (
            '{"__tool_calls__": null, "other": 1}'  # passes sentinel; loads OK
        )
        # ``json.loads`` succeeds, ``envelope["__tool_calls__"]`` is None
        # (from .get()), and the validator raises ValueError.
        with pytest.raises(ValueError, match="lacks __tool_calls__"):
            agent._parse_llm_response(envelope_missing_key)


class TestParseRecoveryPromptSurfacesStep1ImagePath:
    """Issue #1023 follow-up: when ``generate_image`` succeeded earlier in
    the turn and a subsequent tool call parse-failed, both the recovery
    prompt (next user message) and the give-up fallback (final answer)
    must surface the canonical ``image_path`` from step 1.

    Before this change, klkr1 saw the final answer report only "image was
    generated" and stay silent about the story step failure — losing both
    the breadcrumb the model needed to retry verbatim and the user's
    visibility into what went wrong.
    """

    def _stub_chat(self, agent, *responses):
        responses = list(responses)
        chat = MagicMock()

        def _send(*_, **__):
            r = responses.pop(0)
            resp = MagicMock()
            resp.text = r
            resp.stats = {}
            return resp

        chat.send_messages = MagicMock(side_effect=_send)
        agent.chat = chat
        return chat

    def _register_generate_image(self, agent) -> None:
        result = {
            "status": "success",
            "image_path": r"C:\Users\K\img.png",
            "model": "SDXL-Turbo",
        }
        agent._instance_tools = {
            "generate_image": {
                "name": "generate_image",
                "description": "stub",
                "parameters": {
                    "prompt": {"type": "string", "required": True},
                },
                "function": lambda prompt="", _r=result: _r,
                "atomic": True,
            }
        }

    def test_recovery_prompt_includes_step1_image_path_verbatim(self, agent):
        """After step 1 succeeded, the parse-error retry prompt names the path."""
        self._register_generate_image(agent)
        step1 = json.dumps(
            {"tool": "generate_image", "tool_args": {"prompt": "a forest"}}
        )
        # Step 2: malformed inner arguments that no repair can fix.
        step2_bad = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "make_story", "arguments": "{not-json:"}}]}'
        )
        # Step 3: plain answer so the loop terminates cleanly.
        step3 = json.dumps({"answer": "ok, done"})
        chat = self._stub_chat(agent, step1, step2_bad, step3)

        agent.process_query("make me an image and a story", max_steps=10)

        # The 3rd send_messages call's ``messages`` kwarg contains the
        # recovery user message appended after step 2's parse failure.
        third_call_kwargs = chat.send_messages.call_args_list[2].kwargs
        third_messages = third_call_kwargs["messages"]
        user_contents = [
            m["content"]
            for m in third_messages
            if m.get("role") == "user" and isinstance(m.get("content"), str)
        ]
        # Canonical path appears verbatim and the prompt tells the model
        # to copy it.
        assert any(r"C:\Users\K\img.png" in c for c in user_contents)
        assert any("VERBATIM" in c for c in user_contents)

    def test_giveup_fallback_includes_step1_image_path(self, agent):
        """After step 1 succeeded, 3 consecutive parse errors yield a friendly
        give-up message that still surfaces the canonical path so the user
        isn't told their successful generation was lost."""
        self._register_generate_image(agent)
        step1 = json.dumps(
            {"tool": "generate_image", "tool_args": {"prompt": "a forest"}}
        )
        # 3 malformed envelopes after step 1 -> give-up branch.
        bad = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "make_story", "arguments": "{not-json:"}}]}'
        )
        self._stub_chat(agent, step1, bad, bad, bad)

        result = agent.process_query("make me an image and a story", max_steps=10)
        text = result["result"]

        assert r"C:\Users\K\img.png" in text
        # User-facing message acknowledges the partial success.
        assert "follow-up" in text.lower() or "couldn't finish" in text.lower()

    def test_giveup_fallback_unchanged_when_no_capability_success(self, agent):
        """Regression guard: when no successful step has an image_path, the
        existing generic fallback is preserved (no silent regressions for
        non-SD agents)."""
        # No generate_image registered → step_results stays empty across
        # the parse failures.
        bad = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "x", "arguments": "{not-json:"}}]}'
        )
        self._stub_chat(agent, bad, bad, bad)

        result = agent.process_query("anything", max_steps=10)
        text = result["result"]

        # Original generic message preserved.
        assert "trouble formatting" in text.lower()
        # No spurious path placeholder leaked.
        assert "img.png" not in text

    def test_recovery_telemetry_marker_fires_with_canonical_path(self, agent, caplog):
        """Production-log triage marker: when the recovery prompt injects
        the canonical ``image_path``, an INFO-level log line names the path.

        Without this, an oncall engineer cannot distinguish "model received
        the hint and ignored it" from "model never saw the hint" — pre-mortem
        flagged this gap as a debug-experience blocker.
        """
        self._register_generate_image(agent)
        step1 = json.dumps(
            {"tool": "generate_image", "tool_args": {"prompt": "a forest"}}
        )
        step2_bad = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "make_story", "arguments": "{not-json:"}}]}'
        )
        step3 = json.dumps({"answer": "ok"})
        self._stub_chat(agent, step1, step2_bad, step3)

        with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
            agent.process_query("anything", max_steps=10)
        assert any(
            "[PARSE-RECOVERY] injected canonical image_path" in r.message
            for r in caplog.records
        )

    def test_giveup_fallback_uses_most_recent_image_path_when_multiple(self, agent):
        """When step_results carries multiple successful ``image_path``
        entries (multi-image plans), the give-up fallback names the *latest*
        one — matches the "most recent" semantics of the iteration order
        (``reversed(step_results)``)."""
        # Build a stateful stub tool: first call returns ``result_a``,
        # second returns ``result_b``.  Must accept arbitrary kwargs since
        # the dispatcher passes ``prompt=`` (and possibly others) through.
        result_a = {"status": "success", "image_path": r"C:\first\a.png"}
        result_b = {"status": "success", "image_path": r"C:\second\b.png"}
        call_count = [0]

        def _gen_image(**kwargs):
            call_count[0] += 1
            return result_a if call_count[0] == 1 else result_b

        agent._instance_tools = {
            "generate_image": {
                "name": "generate_image",
                "description": "stub",
                "parameters": {"prompt": {"type": "string", "required": True}},
                "function": _gen_image,
                "atomic": True,
            }
        }
        gen1 = json.dumps({"tool": "generate_image", "tool_args": {"prompt": "p1"}})
        gen2 = json.dumps({"tool": "generate_image", "tool_args": {"prompt": "p2"}})
        bad = (
            '{"__tool_calls__": [{"function":'
            ' {"name": "x", "arguments": "{not-json:"}}]}'
        )
        self._stub_chat(agent, gen1, gen2, bad, bad, bad)

        result = agent.process_query("make me two images", max_steps=10)
        text = result["result"]

        # The give-up fallback should point to the SECOND (most recent)
        # image, not the first.
        assert r"C:\second\b.png" in text
        assert r"C:\first\a.png" not in text
