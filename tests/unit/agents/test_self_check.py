# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A turn that built a file gets one short self-check before it answers.

The check asks for what goes wrong in built files: a stated requirement that
was not met, a result nobody looked at twice, a dirty input. It is a verdict
on the answer already given, so a turn with nothing wrong pays one short
step and its answer goes out unchanged. What code can see for itself (a slide
count, a named tab, a word limit) is checked here and raised only when wrong.
It goes out at most once a turn, never when the turn cannot afford a step,
and never leaves a finished turn "incomplete".
"""

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import Agent, self_check_enabled
from gaia.agents.base.completion import CompletionEvidence
from gaia.agents.base.self_check import (
    SELF_CHECK_TAG,
    mechanical_findings,
    miss_note,
    quotes_figures,
    read_verdict,
    self_check_prompt,
    stated_slide_count,
    stated_tabs,
    stated_word_limit,
)
from gaia.agents.base.tools import _TOOL_REGISTRY, tool
from gaia.agents.tools.file_io_tools import FileIOToolsMixin
from gaia.security import PathValidator

# ---------------------------------------------------------------------------
# What counts as an input
# ---------------------------------------------------------------------------


def _evidence(tmp_path):
    return CompletionEvidence("Build out.csv", str(tmp_path))


def _ok(evidence, tool_name, **args):
    evidence.record(tool_name, args, {"status": "success", **args}, True)


def test_a_file_read_before_the_write_is_an_input(tmp_path):
    (tmp_path / "in.csv").write_text("a\n1\n")
    (tmp_path / "out.csv").write_text("a\n1\n")
    evidence = _evidence(tmp_path)
    _ok(evidence, "read_file", file_path=str(tmp_path / "in.csv"))
    _ok(evidence, "write_file", file_path=str(tmp_path / "out.csv"))
    _ok(evidence, "read_file", file_path=str(tmp_path / "out.csv"))

    written = evidence.written_files()
    assert [Path(p).name for p in written] == ["out.csv"]
    assert [Path(p).name for p in evidence.inputs_for(written)] == ["in.csv"]


def test_reading_back_the_output_is_not_an_input(tmp_path):
    (tmp_path / "out.csv").write_text("a\n1\n")
    evidence = _evidence(tmp_path)
    _ok(evidence, "write_file", file_path=str(tmp_path / "out.csv"))
    _ok(evidence, "read_file", file_path=str(tmp_path / "out.csv"))
    assert evidence.inputs_for(evidence.written_files()) == []


# ---------------------------------------------------------------------------
# What the request states that code can check
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text, expected",
    [
        ("Build a 5-slide deck from notes.md", ("exactly", 5)),
        ("Make a deck with at most ten slides", ("at most", 10)),
        ("I need at least 8 slides", ("at least", 8)),
        ("Put it all on a single slide", ("exactly", 1)),
        ("Add two slides to deck.pptx", None),
        ("Fix the title on the first 3 slides", None),
        ("Move the chart to slide 3", None),
        ("Make a 5-slide deck and a 10-slide appendix", None),
        ("Summarise notes.md", None),
    ],
)
def test_a_deck_size_is_read_only_when_the_request_gives_one(request_text, expected):
    assert stated_slide_count(request_text) == expected


def test_named_tabs_are_read_from_quotes_and_lists():
    assert stated_tabs('Add a "Summary" tab and a sheet called "Raw Data"') == [
        "Summary",
        "Raw Data",
    ]
    assert stated_tabs("A workbook with tabs: Summary, Raw Data and Charts.") == [
        "Summary",
        "Raw Data",
        "Charts",
    ]
    assert stated_tabs("Put the totals on the first sheet") == []
    assert stated_tabs('Read the "prices.xlsx" sheet') == []


def test_a_word_limit_is_read_only_when_stated_in_words():
    assert stated_word_limit("Keep the memo under 1,200 words") == 1200
    assert stated_word_limit("a 300-word limit") == 300
    assert stated_word_limit("Keep it to two pages") is None
    assert stated_word_limit("The source is about 900 words long") is None


def _deck(path, slides):
    from pptx import Presentation

    deck = Presentation()
    for _ in range(slides):
        deck.slides.add_slide(deck.slide_layouts[6])
    deck.save(str(path))
    return str(path)


def _book(path, tabs):
    import openpyxl

    book = openpyxl.Workbook()
    book.remove(book.active)
    for title, rows in tabs.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    book.save(str(path))
    return str(path)


def test_a_deck_of_the_wrong_size_is_raised(tmp_path):
    deck = _deck(tmp_path / "deck.pptx", 7)
    assert mechanical_findings("Build a 5-slide deck", [deck]) == [
        "`deck.pptx` has 7 slides; the request says 5 slides."
    ]
    assert mechanical_findings("Build a deck of at most 5 slides", [deck]) == [
        "`deck.pptx` has 7 slides; the request says at most 5 slides."
    ]
    assert mechanical_findings("Build a 7-slide deck", [deck]) == []
    assert mechanical_findings("Build a deck of at least 5 slides", [deck]) == []


def test_a_missing_or_empty_named_tab_is_raised(tmp_path):
    book = _book(
        tmp_path / "out.xlsx", {"Summary": [["total", 3]], "Raw data": [], "Notes": []}
    )
    request = "Build out.xlsx with tabs: Summary, Raw Data and By Month."

    findings = mechanical_findings(request, [book])

    assert findings == [
        "The tab `Raw Data` in `out.xlsx` has no data.",
        "The request names a tab `By Month`; `out.xlsx` has `Summary`, "
        "`Raw data`, `Notes`.",
    ]
    assert mechanical_findings("Build out.xlsx with a 'Summary' tab", [book]) == []


def test_text_over_a_stated_word_limit_is_raised(tmp_path):
    long = "word " * 500
    assert mechanical_findings("Keep it under 300 words", [], prose=long) == [
        "The text is about 500 words; the request's limit is 300."
    ]
    assert mechanical_findings("Keep it under 300 words", [], prose="word " * 310) == []
    assert mechanical_findings("Summarise it", [], prose=long) == []


def test_an_empty_file_and_a_wrong_extension_are_raised(tmp_path):
    empty = tmp_path / "notes.md"
    empty.write_text("")

    findings = mechanical_findings(
        "Write notes.docx", [str(empty)], pending=[str(tmp_path / "notes.docx")]
    )

    assert findings == [
        "`notes.md` is empty.",
        "The request names `notes.docx`; the file written is `notes.md`.",
    ]


def test_files_that_meet_the_request_raise_nothing(tmp_path):
    deck = _deck(tmp_path / "deck.pptx", 5)
    book = _book(tmp_path / "out.xlsx", {"Summary": [["total", 3]]})
    request = 'Build a 5-slide deck and out.xlsx with a "Summary" tab'
    assert mechanical_findings(request, [deck, book]) == []


def test_only_prose_that_quotes_figures_gets_the_figures_line():
    assert quotes_figures("Revenue was 4,120 in Q1, 3,980 in Q2 and up 12.5%.")
    assert not quotes_figures("The pond is old and silent. Written in 2024.")


# ---------------------------------------------------------------------------
# The prompt and the reply
# ---------------------------------------------------------------------------


def test_the_prompt_is_a_short_form_with_a_verdict():
    text = self_check_prompt(
        ["C:/w/out.xlsx", "report.md"],
        has_inputs=True,
        findings=["`out.xlsx` is empty."],
        figure_files=["report.md"],
    )

    assert text.startswith(SELF_CHECK_TAG)
    assert "`out.xlsx`, `report.md`" in text
    for label in ("REQUIREMENTS:", "PLAUSIBILITY:", "FIGURES:", "RESULT: PASS"):
        assert label in text
    for question in ("flag, keep or drop", "duplicate or conflicting", "blanks, zeros"):
        assert question in text
    assert "Checked in code and wrong now: `out.xlsx` is empty." in text
    assert "a file that passes is not rewritten" in text
    assert "Do not re-run the analysis" in text
    assert len(text) < 1700


def test_the_prompt_asks_only_what_applies():
    from_nothing = self_check_prompt(["deck.pptx"])
    assert "REQUIREMENTS:" in from_nothing
    assert "PLAUSIBILITY:" not in from_nothing and "FIGURES:" not in from_nothing

    data_only = self_check_prompt(["out.csv"], has_inputs=True)
    assert "PLAUSIBILITY:" in data_only and "FIGURES:" not in data_only

    short = self_check_prompt(
        ["report.md"], has_inputs=True, figure_files=["report.md"], short=True
    )
    assert "REQUIREMENTS:" in short
    assert "PLAUSIBILITY:" not in short and "FIGURES:" not in short


def test_a_reply_is_read_as_a_verdict():
    passed = read_verdict("REQUIREMENTS: file - met; tab - met\nRESULT: PASS")
    assert passed.result == "pass" and passed.rest == ""

    missed = read_verdict("RESULT: MISS - the deck has no chart on slide 2")
    assert missed.result == "miss"
    assert missed.miss == "the deck has no chart on slide 2"

    contradicted = read_verdict("REQUIREMENTS: chart - NOT MET\nRESULT: PASS")
    assert contradicted.result == "miss" and "chart - NOT MET" in contradicted.miss

    answer = read_verdict("I added the missing tab; out.xlsx now has all three.")
    assert answer.result is None and answer.rest.startswith("I added")

    assert miss_note("the deck has no chart.") == (
        "Not as requested: the deck has no chart."
    )


# ---------------------------------------------------------------------------
# Through the real loop
# ---------------------------------------------------------------------------


class _FileAgent(Agent, FileIOToolsMixin):
    def _get_system_prompt(self):
        return "Use tools for files."

    def _register_tools(self):
        self.path_validator = PathValidator(allowed_paths=[str(Path.cwd())])
        self.register_file_io_tools()

        @tool
        def build_book(output_path: str, tabs: str) -> dict:
            """Save a workbook with one tab per comma-separated name."""
            # Past the file clock's slack, as any real build is.
            time.sleep(0.05)
            _book(output_path, {name.strip(): [["n", 1]] for name in tabs.split(",")})
            return {"status": "success", "output_path": output_path}


@pytest.fixture
def agent(tmp_path, monkeypatch):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GAIA_AGENT_SELF_CHECK", raising=False)
    with patch("gaia.agents.base.agent.AgentSDK"):
        instance = _FileAgent(silent_mode=True, skip_lemonade=True)
    instance.streaming = False
    instance._tool_requires_confirmation = lambda *a, **kw: False
    instance.console = MagicMock()
    instance.console.cancelled = None
    yield instance
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _script(agent, *turns):
    """Scripted model replies; returns the message lists each call was sent."""
    left = list(turns)
    sent = []

    def send(messages, *_a, **_kw):
        sent.append([dict(m) for m in messages])
        assert left, "unexpected model call"
        return MagicMock(text=json.dumps(left.pop(0)), stats={})

    agent.chat.send_messages.side_effect = send
    return sent, left


def _call(tool_name, **args):
    return {"tool": tool_name, "tool_args": args}


def _checks(messages):
    return [
        m
        for m in messages
        if m.get("role") == "user"
        and str(m.get("content", "")).startswith(SELF_CHECK_TAG)
    ]


def _log_lines(caplog):
    return [r.getMessage() for r in caplog.records if SELF_CHECK_TAG in r.getMessage()]


REQUEST = "Total the amounts in sales.csv and save the result as totals.csv"
ANSWER = "I totalled sales.csv into totals.csv: 2 rows in, total 30."
PASS = {"answer": "REQUIREMENTS: totals.csv - met\nPLAUSIBILITY: fine\nRESULT: PASS"}
WORK = (
    _call("read_file", file_path="sales.csv"),
    _call("write_file", file_path="totals.csv", content="total\n30\n"),
    _call("read_file", file_path="totals.csv"),
    {"answer": ANSWER},
)


def _sales(tmp_path):
    (tmp_path / "sales.csv").write_text("item,amount\na,10\nb,20\n")


def test_a_pass_costs_one_step_and_keeps_the_answer(agent, tmp_path, caplog):
    _sales(tmp_path)
    sent, left = _script(agent, *WORK, PASS)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        result = agent.process_query(REQUEST, max_steps=12)

    assert not left
    # Four steps of work, then exactly one more for the check.
    assert len(sent) == 5
    assert _checks(sent[3]) == []
    asked = _checks(sent[4])
    assert len(asked) == 1 and "`totals.csv`" in asked[0]["content"]
    assert "PLAUSIBILITY:" in asked[0]["content"]
    # The verdict is not the answer: the one already given goes out as it was.
    assert result["result"] == ANSWER
    assert result["status"] == "success"
    assert result["completion_gaps"] == []
    fired, done = _log_lines(caplog)
    assert fired.startswith(f"{SELF_CHECK_TAG} fired mode=full outputs=1 inputs=1")
    assert done.startswith(f"{SELF_CHECK_TAG} done verdict=pass seconds=")
    assert "model_steps=1 tool_calls=0" in done


def test_a_miss_that_cannot_be_fixed_is_stated_and_the_turn_is_done(agent, tmp_path):
    _sales(tmp_path)
    _, left = _script(
        agent,
        *WORK,
        {"answer": "RESULT: MISS - sales.csv lists item b twice, so 30 may be 10 high"},
    )

    result = agent.process_query(REQUEST, max_steps=12)

    assert not left
    assert result["result"] == (
        f"{ANSWER}\n\nNot as requested: sales.csv lists item b twice, so 30 may "
        "be 10 high."
    )
    assert result["status"] == "success"


def test_a_fix_after_the_check_brings_no_second_check(agent, tmp_path, caplog):
    _sales(tmp_path)
    fixed = "I totalled sales.csv into totals.csv: 2 rows in, total 30 (corrected)."
    sent, left = _script(
        agent,
        _call("read_file", file_path="sales.csv"),
        _call("write_file", file_path="totals.csv", content="total\n3\n"),
        _call("read_file", file_path="totals.csv"),
        {"answer": ANSWER},
        _call("write_file", file_path="totals.csv", content="total\n30\n"),
        _call("read_file", file_path="totals.csv"),
        {"answer": fixed},
    )

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        result = agent.process_query(REQUEST, max_steps=12)

    assert not left
    assert len(_checks(sent[-1])) == 1
    assert (tmp_path / "totals.csv").read_text() == "total\n30\n"
    assert result["result"].startswith(fixed)
    assert "verdict=new answer" in _log_lines(caplog)[-1]
    assert "model_steps=3 tool_calls=2" in _log_lines(caplog)[-1]


def test_a_short_acknowledgement_keeps_the_answer(agent, tmp_path):
    _sales(tmp_path)
    _script(agent, *WORK, {"answer": "All met."})

    result = agent.process_query(REQUEST, max_steps=12)

    assert result["result"] == ANSWER


@pytest.mark.parametrize(
    "request_text, turns",
    [
        (
            "What do the amounts in sales.csv add up to?",
            (
                _call("read_file", file_path="sales.csv"),
                {"answer": "The amounts in sales.csv add up to 30."},
            ),
        ),
        (
            "Write a haiku and save it as haiku.md",
            (
                _call("write_file", file_path="haiku.md", content="An old pond\n"),
                _call("read_file", file_path="haiku.md"),
                {"answer": "I wrote the haiku to haiku.md."},
            ),
        ),
        (
            "Write total.py that prints the total of sales.csv",
            (
                _call("read_file", file_path="sales.csv"),
                _call("write_file", file_path="total.py", content="print(30)\n"),
                _call("read_file", file_path="total.py"),
                {"answer": "I wrote total.py, which prints the total of sales.csv."},
            ),
        ),
    ],
    ids=["question", "text-from-nothing", "code-file"],
)
def test_a_turn_with_nothing_built_gets_no_check(
    agent, tmp_path, caplog, request_text, turns
):
    _sales(tmp_path)
    sent, left = _script(agent, *turns)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        agent.process_query(request_text, max_steps=8)

    assert not left
    assert not any(_checks(messages) for messages in sent)
    assert _log_lines(caplog) == []


def test_an_office_file_built_from_nothing_is_checked_against_the_request(
    agent, tmp_path
):
    """What code can see is raised; a tool's reported save counts as written."""
    book = str(tmp_path / "plan.xlsx")
    answer = f"I built `{book}` with the tabs you asked for."
    sent, left = _script(
        agent,
        _call("build_book", output_path=book, tabs="Summary, Tasks"),
        {"answer": answer},
        {"answer": "RESULT: MISS - there is no Risks tab"},
    )

    result = agent.process_query(
        f"Build {book} with tabs: Summary, Tasks and Risks.", max_steps=8
    )

    assert not left
    asked = _checks(sent[-1])[0]["content"]
    assert "The request names a tab `Risks`; `plan.xlsx` has `Summary`, `Tasks`." in (
        asked
    )
    assert "PLAUSIBILITY:" not in asked
    assert "No successful write" not in result["result"]
    assert result["result"].endswith("Not as requested: there is no Risks tab.")
    assert result["status"] == "success"


def test_a_pass_does_not_hide_what_code_still_sees(agent, tmp_path, caplog):
    book = str(tmp_path / "plan.xlsx")
    answer = f"I built `{book}` with the tabs you asked for."
    _script(
        agent,
        _call("build_book", output_path=book, tabs="Summary, Tasks"),
        {"answer": answer},
        {"answer": "REQUIREMENTS: three tabs - met\nRESULT: PASS"},
    )

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        result = agent.process_query(
            f"Build {book} with tabs: Summary, Tasks and Risks.", max_steps=8
        )

    assert result["result"] == (
        f"{answer}\n\nNot as requested: The request names a tab `Risks`; "
        "`plan.xlsx` has `Summary`, `Tasks`."
    )
    assert result["status"] == "success"
    assert "verdict=pass" in _log_lines(caplog)[-1]
    assert "code_findings_left=1" in _log_lines(caplog)[-1]


def test_a_fixed_miss_leaves_no_note(agent, tmp_path):
    book = str(tmp_path / "plan.xlsx")
    fixed = f"I built `{book}` with Summary, Tasks and Risks tabs."
    _script(
        agent,
        _call("build_book", output_path=book, tabs="Summary, Tasks"),
        {"answer": f"I built `{book}`."},
        _call("build_book", output_path=book, tabs="Summary, Tasks, Risks"),
        {"answer": fixed},
    )

    result = agent.process_query(
        f"Build {book} with tabs: Summary, Tasks and Risks.", max_steps=8
    )

    assert result["result"] == fixed
    assert result["status"] == "success"


# ---------------------------------------------------------------------------
# Budget, and the switch
# ---------------------------------------------------------------------------


def test_the_check_is_off_with_the_env_var(agent, tmp_path, monkeypatch, caplog):
    _sales(tmp_path)
    monkeypatch.setenv("GAIA_AGENT_SELF_CHECK", "0")
    sent, left = _script(agent, *WORK)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        result = agent.process_query(REQUEST, max_steps=12)

    assert not left and len(sent) == 4
    assert result["result"] == ANSWER
    assert _log_lines(caplog) == [
        f"{SELF_CHECK_TAG} skipped mode=skip outputs=1 inputs=1 code_findings=0 "
        "figure_files=0 step=4 (GAIA_AGENT_SELF_CHECK is off)"
    ]


def test_the_env_var_must_be_a_boolean(monkeypatch):
    monkeypatch.setenv("GAIA_AGENT_SELF_CHECK", "sometimes")
    with pytest.raises(ValueError, match="GAIA_AGENT_SELF_CHECK"):
        self_check_enabled()
    monkeypatch.setenv("GAIA_AGENT_SELF_CHECK", "on")
    assert self_check_enabled()


@pytest.mark.parametrize(
    "left_s, mode",
    [(900, "full"), (130, "short"), (40, "skip")],
)
def test_the_check_shrinks_then_goes_as_time_runs_out(agent, left_s, mode):
    """Against the turn's own pace: steps here take about a minute."""
    agent._self_check_on = True
    agent.time_limit_s = 1000
    agent._step_durations = [55.0, 60.0, 65.0]
    # The stop point keeps two typical steps back for the closing answer.
    agent._turn_started_at = time.monotonic() - (1000 - left_s - 120)

    assert agent._self_check_mode(4, 50)[0] == mode


def test_no_time_limit_and_no_step_left(agent):
    agent._self_check_on = True
    assert agent._self_check_mode(4, 50) == ("full", "no time limit")
    assert agent._self_check_mode(49, 50) == ("skip", "no step left")


def test_running_out_of_steps_during_the_check_keeps_the_finished_answer(
    agent, tmp_path, caplog
):
    _sales(tmp_path)
    _, left = _script(
        agent,
        *WORK,
        # The model spends the turn's last two steps re-reading.
        _call("read_file", file_path="sales.csv"),
        _call("read_file", file_path="totals.csv"),
    )

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        result = agent.process_query(REQUEST, max_steps=6)

    assert not left
    assert result["result"] == ANSWER
    assert result["status"] == "success"
    assert result["max_steps_reached"] is False
    assert "verdict=unanswered" in _log_lines(caplog)[-1]


def test_no_step_left_means_no_check(agent, tmp_path, caplog):
    _sales(tmp_path)
    sent, left = _script(agent, *WORK)

    with caplog.at_level(logging.INFO, logger="gaia.agents.base.agent"):
        result = agent.process_query(REQUEST, max_steps=5)

    assert not left and len(sent) == 4
    assert result["status"] == "success"
    assert "skipped mode=skip" in _log_lines(caplog)[0]
    assert "(no step left)" in _log_lines(caplog)[0]


def test_text_saved_under_an_office_name_is_raised_not_fatal(tmp_path):
    """A model that writes prose to `brief.docx` with a text tool leaves a file
    Word cannot open; python-docx and python-pptx raise their own error for it."""
    from gaia.agents.base.deliverables import read_blocks

    brief, deck = str(tmp_path / "brief.docx"), str(tmp_path / "deck.pptx")
    for path in (brief, deck):
        Path(path).write_text("CFO brief\n\nRevenue rose 4%.", encoding="utf-8")
    assert read_blocks(brief) == []
    assert read_blocks(deck) == []
    findings = mechanical_findings("Build a 3-slide deck and a brief", [brief, deck])
    assert findings == [
        "`brief.docx` is not a Word document: it holds plain text under a .docx "
        "name, so it will not open.",
        "`deck.pptx` is not a presentation: it holds plain text under a .pptx "
        "name, so it will not open.",
    ]


def test_an_empty_file_that_is_meant_to_be_empty_is_not_raised(tmp_path):
    notes = str(tmp_path / "notes.txt")
    Path(notes).write_text("", encoding="utf-8")
    assert mechanical_findings("Create notes.txt", [notes]) == ["`notes.txt` is empty."]
    assert (
        mechanical_findings("Create an empty notes.txt", [notes], empty_ok=[notes])
        == []
    )
