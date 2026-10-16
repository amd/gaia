# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Finished work reaches the user even when the extraction ledger has doubts.

A redaction task that loaded the extraction skill had its whole answer replaced
by "I can't confirm this is done", because its own CSV log was treated as a
source to extract. These tests pin each link of that chain.
"""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent
from gaia.agents.base.completion import named_outputs, save_obligations
from gaia.agents.base.extraction import ExtractionLedger, extract_pages
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.agents.tools.skill_library_tools import SkillLibraryToolsMixin
from gaia.security import PathValidator

SKILLS = (
    Path(__file__).resolve().parents[3]
    / "hub"
    / "agents"
    / "gaia"
    / "python"
    / "gaia_agent"
    / "skills"
)
SKILL_BODY = "Extract every requested item from the source document."

REDACTION = (
    "Redact all personal information from these agreements; keep business "
    "identifiers. Give me a redaction log.\n\nThe agreements are in {root}/in/. "
    "Save redacted copies with the same file names in {root}/redacted/ - the "
    "personal data must be gone from the files themselves - and write "
    "{root}/redaction_log.csv with one row per redaction (document, location, type)."
)
TRACKER = (
    "Build me an action tracker from the minutes in {root}/minutes/. Save the "
    "tracker as {root}/tracker.md (one row per action) and the email to the "
    "group as {root}/followup.md."
)


# ---------------------------------------------------------------------------
# Every named deliverable is an output
# ---------------------------------------------------------------------------


def test_every_save_target_is_kept_not_only_the_last():
    paths, requested = save_obligations(TRACKER.format(root="C:/w"))
    assert requested
    assert paths == ["C:/w/tracker.md", "C:/w/followup.md"]


def test_a_written_log_is_a_named_output():
    outputs = named_outputs(REDACTION.format(root="C:/w"))
    assert "C:/w/redaction_log.csv" in outputs and "C:/w/redacted/" in outputs
    assert "C:/w/in/" not in outputs


def test_a_place_before_a_target_is_still_a_source():
    assert save_obligations("Save the notes in notes.txt to out.md")[0] == ["out.md"]


def test_a_written_code_file_is_not_a_save_obligation():
    # Only the extraction ledger widens to "write <path>"; saves keep their contract.
    assert save_obligations("Write build.py that prints hi.")[0] == []


# ---------------------------------------------------------------------------
# The ledger: a skill alone offers the tools, outputs are never sources
# ---------------------------------------------------------------------------


def _redaction_ledger(tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "redacted").mkdir()
    query = REDACTION.format(root=tmp_path.as_posix())
    return ExtractionLedger(query, str(tmp_path))


def test_the_skill_on_a_redaction_task_offers_tools_but_finds_no_gaps(tmp_path):
    ledger = _redaction_ledger(tmp_path)
    ledger.activate_skill(SKILL_BODY)
    assert ledger.offered and not ledger.enabled
    log = str(tmp_path / "redaction_log.csv")
    ledger.observe("write_file", {"file_path": log}, {"status": "success"})
    ledger.observe("read_file", {"file_path": log}, {"status": "success"})
    assert ledger.gaps() == []


def test_a_named_csv_log_is_never_a_source_even_with_extraction_on(tmp_path):
    ledger = _redaction_ledger(tmp_path)
    ledger.enabled = True  # e.g. the model ran the extractor on something
    log = str(tmp_path / "redaction_log.csv")
    # Written by a script the ledger never saw; only read back.
    ledger.observe("read_file", {"file_path": log}, {"status": "success"})
    assert ledger.gaps() == []


@pytest.mark.parametrize("by_script", [False, True])
def test_a_file_the_turn_wrote_is_never_a_source(tmp_path, by_script):
    (tmp_path / "notes.txt").write_text("Action: ship it")
    ledger = ExtractionLedger("List every action item in notes.txt", str(tmp_path))
    assert ledger.enabled
    summary = str(tmp_path / "summary.txt")
    if not by_script:
        ledger.observe("write_file", {"file_path": summary}, {"status": "success"})
    ledger.observe("read_file", {"file_path": summary}, {"status": "success"})
    outputs = {ledger.key(summary)} if by_script else set()
    gaps = ledger.gaps(outputs)
    # The named source is still owed; the agent's own file is not.
    assert len(gaps) == 1 and "notes.txt" in gaps[0]


def test_an_unnamed_document_read_on_an_extraction_task_is_still_a_gap(tmp_path):
    ledger = ExtractionLedger("List all planets", str(tmp_path))
    ledger.activate_skill(SKILL_BODY)
    assert ledger.enabled
    ledger.observe("read_file", {"file_path": "notes.txt"}, {"status": "success"})
    assert any("notes.txt" in gap for gap in ledger.gaps())


def test_a_failed_extraction_is_reported_whatever_the_file(tmp_path):
    ledger = _redaction_ledger(tmp_path)
    path = tmp_path / "minutes.html"
    path.write_text("<p>x</p>")

    def ask(*_):
        return json.dumps({"complete": False, "items": []})

    result = ledger.run(str(path), lambda p: "<p>x</p>", ask, lambda: None)
    assert result["status"] == "error"
    assert any("did not confirm" in gap for gap in ledger.gaps())


def test_several_deliverables_are_not_each_checked_as_the_inventory(tmp_path):
    (tmp_path / "notes.txt").write_text("Action: ship it")
    ledger = ExtractionLedger(
        "List every action item in notes.txt. Save the list as a.md and the "
        "email as b.md.",
        str(tmp_path),
    )
    ledger.results[ledger.key("notes.txt")] = ([], 1, "digest")
    checked = []
    ledger.validate_outputs(lambda path, limit: checked.append(path) or "")
    assert checked == []


# ---------------------------------------------------------------------------
# Extractor failures leave the raw reply in the log
# ---------------------------------------------------------------------------


def test_a_rejected_extractor_reply_is_logged_raw(caplog):
    raw = json.dumps({"complete": False, "items": [], "why": "RAW-MARKER"})
    with caplog.at_level(logging.WARNING, logger="gaia.agents.base.extraction"):
        with pytest.raises(ValueError, match="did not confirm"):
            extract_pages(
                "<html>page</html>", "List every item", lambda *a: raw, lambda: None
            )
    assert "RAW-MARKER" in caplog.text and "page 1" in caplog.text


# ---------------------------------------------------------------------------
# Through the real loop
# ---------------------------------------------------------------------------


class SkillFileAgent(Agent, FileIOToolsMixin, SkillLibraryToolsMixin):
    def _get_system_prompt(self):
        return "Use tools for files."

    def _register_tools(self):
        self.path_validator = PathValidator(allowed_paths=[str(Path.cwd())])
        self.register_skill_library_tools()
        self.register_file_io_tools()


@pytest.fixture
def agent(tmp_path, monkeypatch):
    from gaia.skills.manager import SkillManager

    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    with patch("gaia.agents.base.agent.AgentSDK"):
        instance = SkillFileAgent(silent_mode=True, skip_lemonade=True)
    instance._skill_manager = SkillManager(
        agent_skill_dirs=[SKILLS],
        user_skills_root=tmp_path / "user-skills",
        include_claude_roots=False,
    )
    instance.streaming = False
    instance._tool_requires_confirmation = lambda *a, **kw: False
    instance.console = MagicMock()
    instance.console.cancelled = None
    yield instance
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def script(agent, *turns):
    left = list(turns)

    def send(*_a, **_kw):
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat.send_messages.side_effect = send


def _call(tool_name, **args):
    return {"tool": tool_name, "tool_args": args}


def test_load_skill_reports_only_tools_it_really_lacks(agent, tmp_path):
    agent._extraction_ledger = ExtractionLedger("Tidy these files", str(tmp_path))
    payload = agent._execute_tool("load_skill", {"name": "document-extract"})
    assert payload["status"] == "success"
    assert "extract_document_items" in agent._tools_registry
    assert "extract_document_items" not in payload.get("unmet_tools_required", [])
    assert "cannot run" not in payload.get("warning", "")


def test_load_skill_still_warns_when_the_tools_cannot_register(agent, tmp_path):
    agent._extraction_ledger = ExtractionLedger(
        "Tidy these files", str(tmp_path), available=False
    )
    payload = agent._execute_tool("load_skill", {"name": "document-extract"})
    assert "extract_document_items" not in agent._tools_registry
    assert "extract_document_items" in payload["unmet_tools_required"]


def test_a_redaction_answer_survives_loading_the_extraction_skill(agent, tmp_path):
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "lease.txt").write_text("Tenant: Jane Roe")
    log = str(tmp_path / "redaction_log.csv")
    redacted = str(tmp_path / "redacted" / "lease.txt")
    answer = "Redacted 1 name in lease.txt; the log has one row."
    script(
        agent,
        _call("load_skill", name="document-extract"),
        _call("read_file", file_path=str(tmp_path / "in" / "lease.txt")),
        _call("write_file", file_path=redacted, content="Tenant: [REDACTED]\n"),
        _call("read_file", file_path=redacted),
        _call("write_file", file_path=log, content="document,location,type\n"),
        _call("read_file", file_path=log),
        {"answer": answer},
    )
    result = agent.process_query(REDACTION.format(root=tmp_path.as_posix()))
    assert result["result"].startswith(answer)
    assert "can't confirm" not in result["result"]
    assert not any("extraction" in gap for gap in result["completion_gaps"])


def test_a_real_extraction_gap_keeps_the_answer_and_adds_a_caveat(agent, tmp_path):
    (tmp_path / "source.txt").write_text("Exercise ALPHA\nExercise BETA")
    answer = "The exercises are ALPHA and BETA."
    script(
        agent,
        _call("read_file", file_path="source.txt"),
        {"answer": answer},
        {"answer": answer},
    )
    result = agent.process_query("List every exercise in source.txt", max_steps=5)
    assert result["status"] == "incomplete"
    text = result["result"]
    assert text.startswith(answer)
    assert "I couldn't confirm all of this" in text
    assert "Incomplete extraction of" in text and "source.txt" in text
    assert "Treat it as unfinished" not in text


# ---------------------------------------------------------------------------
# No gate replaces a non-empty answer
# ---------------------------------------------------------------------------


def test_an_unbacked_save_claim_is_kept_with_its_caveat(agent):
    claim = "Here is the summary: alpha, beta. I saved it to `summary.md`."
    script(agent, {"answer": claim}, {"answer": claim}, {"answer": claim})
    result = agent.process_query("Summarize alpha and beta and save it to `summary.md`")
    assert result["status"] == "incomplete"
    assert result["result"].startswith(claim)
    assert "No successful write to `summary.md`" in result["result"]


def test_a_test_claim_with_its_correction_spent_is_kept_with_its_caveat(agent):
    answer = "Fixed the parser in parse.py. All 12 tests passed."
    script(agent, *[{"answer": answer}] * 4)
    result = agent.process_query("Fix the parser in parse.py", max_steps=10)
    assert result["status"] == "incomplete"
    assert result["result"].startswith(answer)
    assert "No tests were run this turn." in result["result"]


def test_an_unsealed_answer_without_an_error_is_kept(agent, tmp_path):
    """An early exit with no error used to drop the reply for the bare report."""
    (tmp_path / "source.txt").write_text("Exercise ALPHA")
    script(agent, {"answer": "warm-up"})
    agent.process_query("hello", max_steps=2)
    agent._extraction_ledger = ExtractionLedger(
        "List every exercise in source.txt", str(tmp_path)
    )
    findings = "So far: the source lists Exercise ALPHA."
    answer, gaps = agent._gate_unsealed_answer(findings)
    assert gaps and answer.startswith(findings)
    assert "source.txt" in answer
