# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/research <topic>``: the agent loads the research skill and frames the turn."""

from __future__ import annotations

import contextlib

import pytest
from gaia_agent import research
from gaia_agent.agent import GaiaAgent, GaiaAgentConfig

from gaia.agents.base.agent import Agent
from gaia.agents.base.model_features import TIER_ENV
from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME, LARGE_DEFAULT_MODEL_NAME


@pytest.mark.parametrize(
    "text,topic",
    [
        ("/research RISC-V history", "RISC-V history"),
        ("  /research   the EU AI Act  ", "the EU AI Act"),
        ("/RESEARCH jwst", "jwst"),
        ("/research\nmulti-line topic", "multi-line topic"),
        ("/research", ""),
        ("/research   ", ""),
    ],
)
def test_a_leading_research_command_names_its_topic(text, topic):
    assert research.parse_research_command(text) == topic


@pytest.mark.parametrize(
    "text",
    [
        "research RISC-V",
        "/researcher notes",
        "please /research this",
        "/model x",
        "",
        None,
        {"query": "/research x"},
    ],
)
def test_anything_else_is_an_ordinary_turn(text):
    assert research.parse_research_command(text) is None


def test_the_brief_carries_the_citation_rules_and_the_message_keeps_the_topic():
    for rule in ("research-report", "fetch_page", "Sources list", "cited inline"):
        assert rule in research.research_brief("RISC-V")
    assert research.research_brief("") == research.BARE_RESEARCH_BRIEF
    assert research.research_message("", "RISC-V") == "RISC-V"
    assert research.research_message("[note]", "RISC-V") == "[note]\n\nRISC-V"
    assert research.research_message("", "") == "/research"


@contextlib.contextmanager
def _agent(model_id=LARGE_DEFAULT_MODEL_NAME, **config):
    """A flagship whose model call is replaced by a recorder.

    The recorder sits at the base Agent, below the memory layer, so it sees the
    message exactly as the model would: the turn's context block, then the
    user's words.
    """
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    try:
        with pytest.MonkeyPatch.context() as mp:
            # Unroutable: building an agent must not load a model on a real server.
            mp.setenv("LEMONADE_BASE_URL", "http://127.0.0.1:9/api/v1")
            mp.setenv("GAIA_MEMORY_DISABLED", "1")
            mp.setenv("GAIA_DYNAMIC_TOOLS", "0")
            mp.delenv("GAIA_FAST", raising=False)
            mp.delenv(TIER_ENV, raising=False)
            seen = []
            mp.setattr(
                Agent,
                "process_query",
                lambda self, user_input, *a, **k: seen.append(user_input) or {},
            )
            agent = GaiaAgent(
                config=GaiaAgentConfig(silent_mode=True, model_id=model_id, **config)
            )
            yield agent, seen
    finally:
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(saved)


@pytest.mark.parametrize("model_id", [LARGE_DEFAULT_MODEL_NAME, DEFAULT_MODEL_NAME])
def test_research_loads_the_skill_and_briefs_the_model(model_id):
    with _agent(model_id) as (agent, seen):
        assert research.RESEARCH_SKILL not in agent.loaded_skills
        agent.process_query("/research the RISC-V ISA")
        (sent,) = seen
        assert research.RESEARCH_BRIEF in sent
        assert sent.endswith("the RISC-V ISA")
        assert research.RESEARCH_SKILL in agent.loaded_skills
        # Its instructions are in the prompt the model gets this turn.
        assert "# Research Report" in agent.system_prompt


def test_memory_and_history_keep_the_topic_not_the_brief():
    with _agent() as (agent, _seen):
        agent.process_query("/research the RISC-V ISA")
        assert agent._original_user_input == "the RISC-V ISA"


def test_the_brief_is_for_that_turn_only():
    with _agent() as (agent, seen):
        agent.process_query("/research one")
        agent.process_query("and a plain follow-up")
        assert research.RESEARCH_BRIEF in seen[0]
        assert research.RESEARCH_BRIEF not in seen[1]
        assert agent._research_brief is None


def test_a_second_research_turn_reuses_the_loaded_skill():
    with _agent() as (agent, seen):
        agent.process_query("/research one")
        agent.process_query("/research two")
        assert list(agent.loaded_skills).count(research.RESEARCH_SKILL) == 1
        assert seen[-1].endswith("two") and research.RESEARCH_BRIEF in seen[-1]


def test_an_ordinary_turn_is_passed_through_untouched():
    with _agent() as (agent, seen):
        agent.process_query("what's the weather like")
        assert seen[0].endswith("what's the weather like")
        assert research.RESEARCH_BRIEF not in seen[0]
        assert research.RESEARCH_SKILL not in agent.loaded_skills


def test_a_bare_research_asks_for_a_topic():
    with _agent() as (agent, seen):
        agent.process_query("/research")
        assert research.BARE_RESEARCH_BRIEF in seen[0]


def test_an_agent_without_web_tools_refuses_research_by_name():
    with _agent(prompt_profile="chat") as (agent, seen):
        with pytest.raises(RuntimeError, match="search_web, fetch_page"):
            agent.process_query("/research anything")
        assert seen == []


def test_a_note_the_app_puts_first_does_not_hide_the_command():
    from gaia.ui.session_environment import with_folder_note

    sent = with_folder_note("/research the RISC-V ISA", ["C:/work/project"])
    notes, topic = research.split_research_command(sent)
    assert topic == "the RISC-V ISA"
    assert notes.startswith("[This chat is working in the folder C:/work/project.")


def test_a_folder_name_with_brackets_keeps_the_command():
    from gaia.ui.session_environment import with_folder_note

    sent = with_folder_note("/research jwst", ["C:/work/[draft] notes"])
    assert research.parse_research_command(sent) == "jwst"


def test_a_note_alone_is_not_a_command():
    assert research.split_research_command("[note]\nplain question") is None
    assert research.split_research_command("[note] /research inline") is None
    assert research.split_research_command("plain") is None


def test_research_in_a_chat_with_a_folder_still_researches():
    from gaia.ui.session_environment import with_folder_note

    with _agent() as (agent, seen):
        agent.process_query(with_folder_note("/research jwst", ["C:/w"]))
        assert seen[0].endswith("jwst")
        assert "[This chat is working in the folder C:/w." in seen[0]
        assert research.RESEARCH_SKILL in agent.loaded_skills


# ── Run-time citation check ──────────────────────────────────────────────

_WSGR = "https://www.wsgr.com/en/insights/federal-circuit-revives-crispr-cas9-patent-priority-dispute.html"
_NIH = "https://pmc.ncbi.nlm.nih.gov/articles/PMC9377665/"
_SEARCH = (
    "search_web",
    {"query": "crispr patent dispute"},
    {"status": "success", "results": [{"url": _WSGR}, {"url": _NIH}]},
)
_FETCH_NIH = (
    "fetch_page",
    {"url": _NIH},
    {"status": "success", "content": "Doudna and Charpentier shared the 2020 Nobel."},
)


def test_a_report_citing_only_fetched_pages_passes():
    report = f"They shared the 2020 Nobel [source]({_NIH}).\n\n## Sources\n- {_NIH}"
    assert research.citation_problems(report, [_SEARCH, _FETCH_NIH]) == []


def test_a_page_seen_only_in_search_results_is_never_opened():
    report = f"The court revived the dispute [source]({_WSGR})."
    (problem,) = research.citation_problems(report, [_SEARCH, _FETCH_NIH])
    # Shown as the report spells it, so the model can copy it exactly.
    assert "never opened" in problem and _WSGR in problem


def test_an_altered_link_is_caught():
    """Gemma rewrote ``crispr-cas9`` as ``crispr_cas9``: a link that goes nowhere."""
    altered = _WSGR.replace("crispr-cas9", "crispr_cas9")
    report = f"The court revived the dispute [source]({altered})."
    (problem,) = research.citation_problems(report, [_SEARCH, _FETCH_NIH])
    assert "may be wrong" in problem and altered in problem


def test_a_report_that_cites_nothing_it_read_is_flagged():
    (problem,) = research.citation_problems("They shared a Nobel.", [_FETCH_NIH])
    assert "cites none of the pages" in problem


_PDF = "https://arxiv.org/pdf/2401.00001"
_PDF_REFUSED = (
    "fetch_page",
    {"url": _PDF},
    {"status": "error", "error": "returns binary content"},
)
_DOWNLOAD = (
    "download_file",
    {"url": _PDF},
    "Downloaded: 2401.00001.pdf\n  Saved to: /tmp/papers/2401.00001.pdf\n  Size: 1.2 MB",
)


def test_a_downloaded_pdf_counts_once_it_is_read():
    report = f"A finding [paper]({_PDF})."
    read = ("read_file", {"file_path": "/tmp/papers/2401.00001.pdf"}, "page text")
    assert research.citation_problems(report, [_PDF_REFUSED, _DOWNLOAD, read]) == []


def test_a_downloaded_pdf_nobody_read_is_never_opened():
    report = f"A finding [paper]({_PDF})."
    (problem,) = research.citation_problems(report, [_PDF_REFUSED, _DOWNLOAD])
    assert "never opened" in problem and _PDF in problem


def test_reading_a_different_file_does_not_open_the_pdf():
    report = f"A finding [paper]({_PDF})."
    other = ("read_file", {"file_path": "/tmp/papers/notes.txt"}, "notes")
    (problem,) = research.citation_problems(report, [_DOWNLOAD, other])
    assert "never opened" in problem


def test_a_long_list_is_shortened():
    urls = [f"https://example{i}.org/page" for i in range(8)]
    report = " ".join(f"[s]({u})" for u in urls)
    (problem,) = research.citation_problems(report, [])
    assert "and 3 more" in problem


def test_the_flagship_records_its_web_calls(monkeypatch):
    with _agent() as (agent, _seen):
        monkeypatch.setattr(
            Agent, "_execute_tool_timed", lambda self, name, args: {"ok": name}
        )
        agent._execute_tool_timed("fetch_page", {"url": _NIH})
        agent._execute_tool_timed("read_file", {"file_path": "x"})
        assert list(agent._web_calls) == [
            ("fetch_page", {"url": _NIH}, {"ok": "fetch_page"})
        ]


def test_the_flagship_records_a_read_only_of_a_downloaded_file(monkeypatch):
    results = {"download_file": _DOWNLOAD[2], "read_file": "page text"}
    with _agent() as (agent, _seen):
        monkeypatch.setattr(
            Agent, "_execute_tool_timed", lambda self, name, args: results[name]
        )
        agent._execute_tool_timed(
            "read_file", {"file_path": "/tmp/papers/2401.00001.pdf"}
        )
        agent._execute_tool_timed("download_file", {"url": _PDF})
        agent._execute_tool_timed("read_file", {"file_path": "/tmp/papers/other.txt"})
        agent._execute_tool_timed(
            "read_file", {"file_path": "/tmp/papers/2401.00001.pdf"}
        )
        names = [
            (n, a.get("file_path") or a.get("url")) for n, a, _r in agent._web_calls
        ]
        assert names == [
            ("download_file", _PDF),
            ("read_file", "/tmp/papers/2401.00001.pdf"),
        ]


def test_only_a_research_turn_checks_citations():
    report = f"The court revived it [source]({_WSGR})."
    with _agent() as (agent, _seen):
        agent._web_calls = [_SEARCH]
        assert agent.answer_problems(report) == []
        agent._research_brief = research.BARE_RESEARCH_BRIEF
        assert agent.answer_problems(report) == []
        agent._research_brief = research.RESEARCH_BRIEF
        (problem,) = agent.answer_problems(report)
        assert "never opened" in problem
        repair = agent.answer_repair_prompt([problem])
        assert repair.startswith("[check:citations]") and "fetch_page" in repair
        assert "give your complete report again" in repair
        # A model answered the check ("You're right - there's a stray ]") until told not to.
        assert "without mentioning it" in repair
