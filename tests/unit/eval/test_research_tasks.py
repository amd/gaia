# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The research suite: tasks parse strictly, sources gate first, the judge decides the rest."""

import copy
import json
import os

import pytest

from gaia.eval import claude_credential
from gaia.eval import flagship_tasks as ft
from gaia.eval.bench import config as bench_config

from .conftest import gaia_tool, one_task_file

#: The real login lookups, taken before the root conftest's autouse stub hides
#: them, so the live judge check can use a developer's subscription login.
_REAL_CLAUDE_BIN = claude_credential._claude_bin
_REAL_AUTH_STATUS = claude_credential._auth_status

RESEARCH = ft.load_suite("research")
RISCV = next(t for t in RESEARCH if t.id == "rr-riscv")
RAW = json.loads(ft.TASKS_FILE.read_text(encoding="utf-8"))
RAW_RISCV = next(t for t in RAW["tasks"] if t["id"] == "rr-riscv")

ABOUT = "https://riscv.org/about/"
UBUNTU = "https://ubuntu.com/blog/risc-v-profiles-why-is-rva23-significant"
RVA23 = "https://riscv.org/blog/risc-v-announces-ratification-of-the-rva23-profile-standard/"
GOOD_REPORT = (
    f"RISC-V began at UC Berkeley in 2010 ([about]({ABOUT})). The RVA23 profile "
    f"was ratified in 2024 ([RVA23]({RVA23})). Ubuntu 25.10 requires it "
    f"([Canonical]({UBUNTU})).\n\n## Sources\n- {ABOUT}\n- {RVA23}\n- {UBUNTU}\n"
)
PAGES = {
    ABOUT: "RISC-V began at UC Berkeley in 2010 in the Par Lab.",
    RVA23: "RVA23 was ratified in October 2024; Android uses it.",
    UBUNTU: "Ubuntu 25.10 requires RVA23.",
}


def _fetches(urls=PAGES):
    calls = gaia_tool("search_web", {"query": "risc-v"}, "results")
    for url in urls:
        calls += gaia_tool("fetch_page", {"url": url}, PAGES[url])
    return calls


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_every_research_task_carries_a_full_answer_key():
    assert [t.id for t in RESEARCH] == [
        "rr-riscv",
        "rr-crispr",
        "rr-email-origin",
        "rr-eu-ai-act",
        "rr-jwst",
    ]
    for task in RESEARCH:
        assert task.check == "research"
        assert len(task.must_establish) >= 5
        assert len(task.wrong_answers) >= 2
        assert len(task.research["must_not_claim"]) >= 4
        assert len(task.research["sources"]) >= task.research_spec.min_sources
        # The reference answer cites the pages it was checked against.
        assert "](https://" in task.genuine_answer


def test_research_tasks_are_in_no_other_suite():
    others = {
        task_id
        for name, ids in RAW["suites"].items()
        if name != "research"
        for task_id in ids
    }
    assert not others & {t.id for t in RESEARCH}


def _parse(**changes):
    raw = copy.deepcopy(RAW_RISCV)
    for key, value in changes.items():
        if value is None:
            raw.pop(key, None)
        else:
            raw[key] = value
    return ft._parse_task(raw, ft.TASKS_FILE)


def _with_research(**changes):
    research = copy.deepcopy(RAW_RISCV["research"])
    for key, value in changes.items():
        if value is None:
            research.pop(key, None)
        else:
            research[key] = value
    return research


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"research": None}, "needs a 'research' block"),
        ({"research": _with_research(extra=1)}, "unknown research keys"),
        ({"research": _with_research(min_sources=0)}, "min_sources"),
        ({"research": _with_research(min_sources=True)}, "min_sources"),
        ({"research": _with_research(max_unread=-1)}, "max_unread"),
        ({"research": _with_research(min_domains=9)}, "exceeds min_sources"),
        ({"research": _with_research(as_of="last week")}, "as_of"),
        ({"research": _with_research(must_not_claim=[])}, "must_not_claim"),
        ({"research": _with_research(sources=[{"url": "http://x"}])}, "sources"),
        ({"must_establish": None}, "needs 'must_establish'"),
        ({"wrong_answers": None}, "wrong_answers"),
        ({"expect": {"tests_pass": True}}, "no expect or setup"),
    ],
)
def test_a_malformed_research_task_is_refused(changes, message):
    with pytest.raises(ValueError, match=message):
        _parse(**changes)


def test_only_a_research_task_takes_a_research_block():
    raw = next(t for t in RAW["tasks"] if t["id"] == "21-qa")
    raw = {**raw, "research": RAW_RISCV["research"]}
    with pytest.raises(ValueError, match="only a research task"):
        ft._parse_task(raw, ft.TASKS_FILE)


def test_the_spec_reads_the_tasks_bar():
    spec = _parse(research=_with_research(min_sources=4, max_unread=2)).research_spec
    assert (spec.min_sources, spec.min_domains, spec.max_unread) == (4, 2, 2)


# ---------------------------------------------------------------------------
# Running one
# ---------------------------------------------------------------------------


def _run(tmp_path, fake_launch, answer, calls):
    fake_launch.act = lambda workdir: answer
    fake_launch.conversation = calls
    out = tmp_path / "out"
    card = ft.run_suite(
        "one",
        "m",
        out,
        tasks_file=one_task_file(tmp_path, "rr-riscv"),
        config=bench_config.resolve(harness="gaia"),
    )
    return card["tasks"][0], out


def test_a_well_sourced_report_waits_for_the_judge(tmp_path, fake_launch):
    entry, out = _run(tmp_path, fake_launch, GOOD_REPORT, _fetches())
    assert entry["passed"] is None
    assert "the judge decides" in entry["why"]
    assert entry["research"]["cited_fetched"] == 3
    assert entry["research"]["gate_ok"] is True
    assert (out / "rr-riscv" / "transcript.json").is_file()


def test_the_agent_works_in_an_empty_folder(tmp_path, fake_launch):
    seen = []

    def act(workdir):
        seen.append(sorted(p.name for p in workdir.iterdir()))
        return GOOD_REPORT

    fake_launch.conversation = _fetches()
    fake_launch.act = act
    ft.run_suite(
        "one",
        "m",
        tmp_path / "out",
        tasks_file=one_task_file(tmp_path, "rr-riscv"),
        config=bench_config.resolve(harness="gaia"),
    )
    assert seen == [[]]
    assert fake_launch.calls[0]["cwd"].name != "toybox"


def test_a_report_citing_pages_it_never_read_fails_before_the_judge(
    tmp_path, fake_launch
):
    entry, _ = _run(tmp_path, fake_launch, GOOD_REPORT, _fetches([ABOUT, RVA23]))
    assert entry["passed"] is False
    assert entry["why"].startswith("sources:")
    assert "found in no tool result" in entry["why"]


def test_an_uncited_report_fails(tmp_path, fake_launch):
    entry, _ = _run(tmp_path, fake_launch, "RISC-V began in 2010.", _fetches())
    assert entry["passed"] is False
    assert "0 fetched source(s)" in entry["why"]


def test_a_crashed_run_is_an_error_not_a_sourcing_failure(tmp_path, fake_launch):
    fake_launch.timed_out = True
    entry, _ = _run(tmp_path, fake_launch, GOOD_REPORT, _fetches())
    assert entry["error"]
    assert entry["research"] == {}


# ---------------------------------------------------------------------------
# The judge
# ---------------------------------------------------------------------------

GRADE = {
    "instruction_compliance": 5,
    "work_quality": 4,
    "reasoning": 4,
    "fabrication_free": 5,
    "coverage": 4,
    "readability": 5,
    "citation_quality": 4,
    "answers_correctly": True,
    "missing": "",
    "factually_accurate": True,
    "false_claim": "",
    "citations_supported": True,
    "unsupported_claim": "",
    "one_line": "solid",
}


def _capture(monkeypatch, grade=GRADE):
    monkeypatch.setattr(
        claude_credential, "_auth_status", lambda _b, _e: {"loggedIn": True}
    )
    sent = []

    def fake_run(cmd, **kwargs):
        sent.append(kwargs["input"])

        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {"result": json.dumps({"a": grade, "b": grade}), "total_cost_usd": 0.1}
            )

        return Proc()

    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    return sent


def _attempt(key="a", answer=GOOD_REPORT, calls=None):
    transcript = {
        "prompt": RISCV.prompt,
        "answer": answer,
        "conversation": calls if calls is not None else _fetches(),
    }
    return ft.attempt_from(key, transcript, "(none)", RISCV)


def test_the_judge_sees_the_answer_key_the_source_check_and_the_passages(
    monkeypatch,
):
    sent = _capture(monkeypatch)
    grades = ft.judge_batch([_attempt()], "judge-model", {})
    payload = sent[0]
    assert payload.startswith(ft.RESEARCH_RUBRIC)
    assert "THE ORIGINAL PROJECT" not in payload
    assert "(RESEARCH)" in payload
    for point in RISCV.must_establish:
        assert point in payload
    for claim in RISCV.research["must_not_claim"]:
        assert claim in payload
    assert "researched 2026-11-12" in payload
    assert "MECHANICAL SOURCE CHECK" in payload
    assert "fetched and read: 3 on 2 sites" in payload
    assert "Par Lab" in payload  # a passage of the cited page
    assert grades["a"]["citations_supported"] is True
    assert grades["a"]["coverage"] == 4


def test_research_and_question_attempts_are_judged_apart():
    qa = next(t for t in ft.load_suite("core") if t.id == "21-qa")
    question = ft.Attempt("q", "p", "a", "", qa)
    with pytest.raises(ValueError, match="own batch"):
        ft.judge_batch([_attempt(), question], "m", {})


@pytest.mark.parametrize(
    "drop", ["coverage", "readability", "citation_quality", "factually_accurate"]
)
def test_a_research_grade_must_carry_every_score_and_verdict(drop):
    raw = {k: v for k, v in GRADE.items() if k != drop}
    with pytest.raises(ft.JudgeError, match=drop):
        ft._validate_grade(raw, question=False, research=True)


def test_a_research_grade_rejects_a_verdict_that_is_not_a_boolean():
    with pytest.raises(ft.JudgeError, match="citations_supported"):
        ft._validate_grade(
            {**GRADE, "citations_supported": "yes"}, question=False, research=True
        )


def _entry(passed=None, **extra):
    return {"id": "rr-riscv", "passed": passed, "why": "w", "error": "", **extra}


def test_a_report_passes_only_when_correct_accurate_and_supported():
    entry = _entry(judge=dict(GRADE))
    ft._apply_verdict(entry, RISCV)
    assert entry["passed"] is True
    assert entry["why"] == "judge: correct, accurate and supported"


@pytest.mark.parametrize(
    "verdict,reason,text",
    [
        ("answers_correctly", "missing", "the 2015 Foundation"),
        ("factually_accurate", "false_claim", "says Stanford"),
        ("citations_supported", "unsupported_claim", "2016 is not on the page"),
    ],
)
def test_any_failed_verdict_fails_the_report(verdict, reason, text):
    entry = _entry(judge={**GRADE, verdict: False, reason: text})
    ft._apply_verdict(entry, RISCV)
    assert entry["passed"] is False
    assert text in entry["why"]


def test_the_judge_cannot_pass_a_report_its_sources_failed():
    entry = _entry(passed=False, judge=dict(GRADE))
    ft._apply_verdict(entry, RISCV)
    assert entry["passed"] is False


def test_judging_a_run_writes_the_verdict(tmp_path, fake_launch, monkeypatch):
    _, out = _run(tmp_path, fake_launch, GOOD_REPORT, _fetches())
    grade = dict(GRADE)
    monkeypatch.setattr(
        claude_credential, "_auth_status", lambda _b, _e: {"loggedIn": True}
    )

    def fake_run(cmd, **kwargs):
        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {"result": json.dumps({"rr-riscv": grade}), "total_cost_usd": 0.1}
            )

        return Proc()

    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    card = ft.judge_run(
        out, "judge-model", {}, tasks_file=one_task_file(tmp_path, "rr-riscv")
    )
    entry = card["tasks"][0]
    assert entry["passed"] is True
    summary = ft.summarize(card)["research"]
    assert summary["reports"] == 1 and summary["fetched_sources"] == 3
    assert summary["citations_supported"] == 1 and summary["coverage"] == 4
    report = ft.render_report(card, None)
    assert "Research: 1 reports" in report


def test_a_run_without_research_tasks_has_no_research_summary():
    card = {
        "suite": "core",
        "model": "m",
        "tasks": [
            {
                "id": "21-qa",
                "passed": True,
                "why": "judge: correct",
                "steps": 1,
                "tool_calls": 0,
                "input_tokens": 1,
                "output_tokens": 1,
                "wall_seconds": 1.0,
            }
        ],
    }
    assert "research" not in ft.summarize(card)
    assert "Research:" not in ft.render_report(card, None)


@pytest.mark.skipif(
    os.environ.get("GAIA_EVAL_LIVE_JUDGE") != "1",
    reason="calls Claude; set GAIA_EVAL_LIVE_JUDGE=1 to check the judge's verdicts",
)
def test_the_live_judge_passes_every_reference_report_and_fails_every_wrong_one(
    monkeypatch,
):
    """Judged from the text alone: the sources gate is the harness's, not the judge's."""
    from gaia.eval.config import DEFAULT_CLAUDE_MODEL

    monkeypatch.setattr(claude_credential, "_claude_bin", _REAL_CLAUDE_BIN)
    monkeypatch.setattr(claude_credential, "_auth_status", _REAL_AUTH_STATUS)

    attempts, want = [], {}
    for task in RESEARCH:
        cases = [("ref", task.genuine_answer, True)]
        cases += [(f"wrong{i}", w, False) for i, w in enumerate(task.wrong_answers)]
        for label, answer, expected in cases:
            key = f"{task.id}:{label}"
            attempts.append(ft.Attempt(key, task.prompt, answer, "(none)", task))
            want[key] = expected
    grades = ft.judge_batch(attempts, DEFAULT_CLAUDE_MODEL, dict(os.environ))
    got = {
        k: grades[k].get("answers_correctly") and grades[k].get("factually_accurate")
        for k in want
    }
    assert got == want


def test_a_cited_report_saved_to_a_file_counts_as_delivered(tmp_path, fake_launch):
    def act(workdir):
        (workdir / "report.md").write_text(GOOD_REPORT, encoding="utf-8")
        return "I saved the full report to report.md."

    fake_launch.act = act
    fake_launch.conversation = _fetches()
    out = tmp_path / "out"
    card = ft.run_suite(
        "one",
        "m",
        out,
        tasks_file=one_task_file(tmp_path, "rr-riscv"),
        config=bench_config.resolve(harness="gaia"),
    )
    entry = card["tasks"][0]
    assert entry["passed"] is None, entry["why"]
    saved = json.loads((out / "rr-riscv" / "transcript.json").read_text())
    assert "report.md" in saved["report_files"]
    attempt = ft.attempt_from("a", saved, "(none)", RISCV)
    assert "saved report file: report.md" in attempt.answer
    assert attempt.source_check["cited_fetched"] == 3


def test_each_research_report_is_judged_in_its_own_call(
    tmp_path, fake_launch, monkeypatch
):
    fake_launch.act = lambda workdir: GOOD_REPORT
    fake_launch.conversation = _fetches()
    out = tmp_path / "out"
    tasks_file = one_task_file(tmp_path, "rr-riscv", "rr-jwst")
    ft.run_suite(
        "one",
        "m",
        out,
        tasks_file=tasks_file,
        config=bench_config.resolve(harness="gaia"),
    )
    monkeypatch.setattr(
        claude_credential, "_auth_status", lambda _b, _e: {"loggedIn": True}
    )
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(kwargs["input"])

        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {
                    "result": json.dumps({"rr-riscv": GRADE, "rr-jwst": GRADE}),
                    "total_cost_usd": 0.1,
                }
            )

        return Proc()

    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    card = ft.judge_run(out, "judge-model", {}, tasks_file=tasks_file)
    assert len(calls) == 2
    assert all(payload.count("(RESEARCH) ===") == 1 for payload in calls)
    assert all("error" not in t["judge"] for t in card["tasks"])


def test_research_command_sends_the_task_as_a_research_turn(tmp_path, fake_launch):
    sent = []

    def act(workdir):
        return GOOD_REPORT

    fake_launch.act = act
    fake_launch.conversation = _fetches()
    out = tmp_path / "out"
    card = ft.run_suite(
        "one",
        "m",
        out,
        tasks_file=one_task_file(tmp_path, "rr-riscv"),
        config=bench_config.resolve(harness="gaia", research_command=True),
    )
    transcript = json.loads((out / "rr-riscv" / "transcript.json").read_text())
    sent.append(transcript["prompt"])
    assert sent == [f"/research {RISCV.prompt}"]
    assert card["research_command"] is True


def test_research_command_is_refused_for_another_harness():
    with pytest.raises(bench_config.BenchConfigError, match="only the gaia harness"):
        bench_config.resolve(harness="claude-code", research_command=True)


def test_a_coding_task_keeps_its_prompt_under_research_command(tmp_path, fake_launch):
    fake_launch.act = lambda workdir: "done"
    out = tmp_path / "out"
    ft.run_suite(
        "one",
        "m",
        out,
        tasks_file=one_task_file(tmp_path, "21-qa"),
        config=bench_config.resolve(harness="gaia", research_command=True),
    )
    transcript = json.loads((out / "21-qa" / "transcript.json").read_text())
    assert transcript["prompt"].startswith("You are working in ")


def test_judging_again_replaces_an_earlier_verdict(tmp_path, fake_launch, monkeypatch):
    _, out = _run(tmp_path, fake_launch, GOOD_REPORT, _fetches())
    monkeypatch.setattr(
        claude_credential, "_auth_status", lambda _b, _e: {"loggedIn": True}
    )
    verdicts = [{**GRADE, "answers_correctly": False, "missing": "x"}, GRADE]

    def fake_run(cmd, **kwargs):
        grade = verdicts.pop(0)

        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {"result": json.dumps({"rr-riscv": grade}), "total_cost_usd": 0.1}
            )

        return Proc()

    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    tasks_file = one_task_file(tmp_path, "rr-riscv")
    first = ft.judge_run(out, "judge-model", {}, tasks_file=tasks_file)
    assert first["tasks"][0]["passed"] is False
    second = ft.judge_run(out, "judge-model", {}, tasks_file=tasks_file)
    assert second["tasks"][0]["passed"] is True


def test_judging_refreshes_the_stored_source_check(tmp_path, fake_launch, monkeypatch):
    _, out = _run(tmp_path, fake_launch, GOOD_REPORT, _fetches())
    card_path = out / "scorecard.json"
    card = json.loads(card_path.read_text())
    card["tasks"][0]["research"] = {"cited_fetched": 0, "stale": True}
    card_path.write_text(json.dumps(card))
    monkeypatch.setattr(
        claude_credential, "_auth_status", lambda _b, _e: {"loggedIn": True}
    )

    def fake_run(cmd, **kwargs):
        class Proc:
            returncode = 0
            stderr = ""
            stdout = json.dumps(
                {"result": json.dumps({"rr-riscv": GRADE}), "total_cost_usd": 0.1}
            )

        return Proc()

    monkeypatch.setattr(ft.shutil, "which", lambda name: "/bin/claude")
    monkeypatch.setattr(ft.subprocess, "run", fake_run)
    judged = ft.judge_run(
        out, "judge-model", {}, tasks_file=one_task_file(tmp_path, "rr-riscv")
    )
    research = judged["tasks"][0]["research"]
    assert research["cited_fetched"] == 3 and "stale" not in research
