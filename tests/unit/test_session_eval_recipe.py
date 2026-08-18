# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The session-replay eval recipe: dataset extraction, judging, scoring.

The premise of this recipe is that a real Claude Code session is a recorded
expert doing the job we want GAIA to do, so its answers are a reference nobody
had to hand-write. That only holds if the extractor is picky about what counts
as a "turn" — a transcript is mostly machinery, and a dataset built naively from
one measures the harness rather than the agent.
"""

from __future__ import annotations

import json

import pytest

from gaia.eval.session_dataset import (
    MAX_PROMPT_CHARS,
    SessionTurn,
    _is_machinery,
    extract,
    select,
)
from gaia.eval.session_eval import PASS_SCORE, CaseResult, judge, report, scorecard


def write_session(tmp_path, records, name="sess"):
    path = tmp_path / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def user(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(text, tools=()):
    blocks = [{"type": "thinking", "thinking": "internal"}]
    blocks += [{"type": "tool_use", "name": t} for t in tools]
    blocks += [{"type": "text", "text": text}]
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


class TestOnlyRealExchangesBecomeCases:
    def test_a_prompt_and_its_answer_are_paired(self, tmp_path):
        path = write_session(
            tmp_path, [user("Fix the failing test in cart.py"), assistant("Fixed it.")]
        )
        turns = extract(path)

        assert len(turns) == 1
        assert turns[0].prompt == "Fix the failing test in cart.py"
        assert turns[0].reference == "Fixed it."

    def test_thinking_and_tool_calls_are_not_the_reference(self, tmp_path):
        """The reference is what the user was SHOWN — the only comparable part."""
        path = write_session(
            tmp_path, [user("Do the thing please"), assistant("Done.", tools=["Bash"])]
        )
        turn = extract(path)[0]

        assert turn.reference == "Done."
        assert "internal" not in turn.reference
        assert turn.tools_used == ["Bash"]

    def test_the_last_text_block_wins(self, tmp_path):
        """Earlier text is narration on the way to the answer."""
        path = write_session(
            tmp_path,
            [
                user("Investigate the flaky test"),
                assistant("Let me look at that."),
                assistant("It was a race in the fixture."),
            ],
        )
        assert extract(path)[0].reference == "It was a race in the fixture."

    @pytest.mark.parametrize(
        "text",
        [
            "<command-name>/compact</command-name>",
            "<local-command-stdout>ok</local-command-stdout>",
            "Stop hook feedback: [keep going]",
            "A session-scoped Stop hook is now active with condition: x",
            "<task-notification><task-id>abc</task-id></task-notification>",
            "MONITOR TICK — check the subtasks",
            "[Attached image: C:\\tmp\\shot.png]",
            "[Request interrupted by user]",
        ],
    )
    def test_machinery_is_not_a_question_anyone_asked(self, text):
        assert _is_machinery(text)

    def test_a_machinery_record_produces_no_case(self, tmp_path):
        path = write_session(
            tmp_path, [user("Stop hook feedback: [go on]"), assistant("Continuing.")]
        )
        assert extract(path) == []

    def test_subagent_transcripts_are_skipped(self, tmp_path):
        """Its prompt was written by an agent, not by the user."""
        records = [user("A real question about the repo"), assistant("A real answer.")]
        for record in records:
            record["isSidechain"] = True
        assert extract(write_session(tmp_path, records)) == []

    def test_a_truncated_final_line_does_not_break_extraction(self, tmp_path):
        """A live session is appended to while it is being read."""
        path = write_session(tmp_path, [user("Question here"), assistant("Answer.")])
        with path.open("a", encoding="utf-8") as handle:
            handle.write('{"type": "assis')

        assert len(extract(path)) == 1


class TestFollowUpsCarryTheirContext:
    def test_the_previous_exchange_is_attached(self, tmp_path):
        path = write_session(
            tmp_path,
            [
                user("Index the repository for me please"),
                assistant("Indexed 400 files."),
                user("How many were Python?"),
                assistant("312 of them."),
            ],
        )
        turns = extract(path)

        assert turns[0].context == "", "the first turn has nothing before it"
        assert "Index the repository" in turns[1].context
        assert "Indexed 400 files" in turns[1].context

    def test_context_is_bounded(self, tmp_path):
        """Enough to answer the follow-up, not enough to hand over the answer."""
        path = write_session(
            tmp_path,
            [
                user("q" * 5000),
                assistant("a" * 20000),
                user("And the second part?"),
                assistant("Here it is."),
            ],
        )
        assert len(extract(path)[1].context) < 2000


class TestSelectionKeepsTheSampleHonest:
    def _turn(self, prompt, reference="R" * 500, ident="x"):
        return SessionTurn(id=ident, session="s", prompt=prompt, reference=reference)

    def test_a_pasted_dump_is_not_a_question(self):
        chosen = select([self._turn("p" * (MAX_PROMPT_CHARS + 1))], limit=5)
        assert chosen == []

    def test_a_bare_acknowledgement_is_not_a_task(self):
        assert select([self._turn("ok")], limit=5) == []

    def test_a_thin_answer_cannot_be_judged(self):
        assert (
            select(
                [self._turn("A perfectly reasonable question here", "short")], limit=5
            )
            == []
        )

    def test_a_context_dependent_opener_is_refused(self):
        """ "Also do the other one" scores the harness, not the agent."""
        assert (
            select([self._turn("also do that for the other file too")], limit=5) == []
        )

    def test_a_real_task_survives(self):
        chosen = select(
            [self._turn("Fix the failing tests in the cart module")], limit=5
        )
        assert len(chosen) == 1


class TestJudging:
    class _Client:
        def __init__(self, reply):
            self.reply = reply
            self.seen = ""

        def get_completion(self, prompt):
            self.seen = prompt
            return self.reply

    def _case(self):
        return {"prompt": "Fix it", "reference": "I fixed the discount bug."}

    def test_a_score_is_parsed_from_the_reply(self):
        client = self._Client(
            '{"score": 4, "verdict": "solid", "rationale": "minor gap"}'
        )
        verdict = judge(self._case(), "I fixed it.", client)

        assert verdict["score"] == 4.0
        assert verdict["verdict"] == "solid"

    def test_prose_around_the_json_is_tolerated(self):
        client = self._Client(
            'Sure!\n{"score": 5, "verdict": "great", "rationale": "x"}\nDone'
        )
        assert judge(self._case(), "answer", client)["score"] == 5.0

    def test_a_judge_that_did_not_answer_is_an_error_not_a_zero(self):
        """Scoring it 0 would blame the agent for a broken measurement."""
        with pytest.raises(ValueError):
            judge(self._case(), "answer", self._Client("I could not comply."))

    def test_the_judge_is_told_to_score_substance_not_style(self):
        client = self._Client('{"score": 3, "verdict": "v", "rationale": "r"}')
        judge(self._case(), "answer", client)

        assert "SUBSTANCE" in client.seen
        assert "CLAIMS WORK IT DID NOT DO" in client.seen


class TestScorecard:
    def _results(self):
        return [
            CaseResult(id="a", prompt="p", reference="r", score=5.0),
            CaseResult(id="b", prompt="p", reference="r", score=4.0),
            CaseResult(id="c", prompt="p", reference="r", score=0.0),
            CaseResult(id="d", prompt="p", reference="r", error="ran out of time"),
        ]

    def test_errors_are_excluded_from_the_mean(self):
        """A case that never ran is not a zero — that would flatter or punish."""
        card = scorecard(self._results())

        assert card["scored"] == 3
        assert card["errors"] == 1
        assert card["mean_score"] == 3.0

    def test_dishonesty_is_counted_separately(self):
        assert scorecard(self._results())["dishonest"] == 1

    def test_pass_rate_uses_the_threshold(self):
        card = scorecard(self._results())
        assert card["passed"] == 2
        assert card["pass_rate"] == 67

    def test_an_all_error_run_does_not_divide_by_zero(self):
        card = scorecard([CaseResult(id="a", prompt="p", reference="r", error="x")])
        assert card["mean_score"] == 0.0 and card["pass_rate"] == 0

    def test_passed_matches_the_threshold_constant(self):
        assert CaseResult(id="a", prompt="p", reference="r", score=PASS_SCORE).passed
        assert not CaseResult(
            id="b", prompt="p", reference="r", score=PASS_SCORE - 0.1
        ).passed


class TestReport:
    def test_it_leads_with_the_number_and_names_the_failures(self):
        results = [
            CaseResult(
                id="good", prompt="p", reference="r", score=5.0, verdict="nailed it"
            ),
            CaseResult(
                id="bad",
                prompt="explain the retry logic",
                reference="r",
                score=1.0,
                verdict="missed the point",
                rationale="never read the file",
            ),
        ]
        text = report(results, scorecard(results), "claude-sonnet-5")

        assert "claude-sonnet-5" in text
        assert "3.0/5" in text
        assert "Where it struggled" in text
        assert "never read the file" in text
        # The worst case is listed first so a reader meets the problem, not the win.
        assert text.index("`bad`") < text.index("`good`")

    def test_dishonesty_is_called_out_when_present(self):
        results = [CaseResult(id="a", prompt="p", reference="r", score=0.0)]
        assert "claiming work not done" in report(results, scorecard(results), "x")


class TestABenchmarkCannotMutateTheMachineItMeasures:
    """Replaying a real session means replaying real instructions.

    "lets commit and push those changes" and "discard any local changes, we
    dont need them" both appeared in the first ten cases drawn from these
    transcripts — and an eval typically runs with confirmations off. Nothing
    happened, because the shell refuses git writes, but that is a defence we
    happen to have rather than one this recipe arranged.
    """

    def _turn(self, prompt):
        return SessionTurn(id="x", session="s", prompt=prompt, reference="R" * 500)

    @pytest.mark.parametrize(
        "prompt",
        [
            "restarting it fixed it. lets commit and push those changes.",
            "discard any local changes, we dont need them.",
            "delete the stale branches on the remote",
            "go ahead and merge them directly",
            "please rm the temp directory and start over",
            "force a release to the hub",
        ],
    )
    def test_a_destructive_instruction_is_never_replayed(self, prompt):
        assert select([self._turn(prompt)], limit=5) == []

    def test_an_ordinary_task_is_unaffected(self):
        chosen = select(
            [self._turn("Explain how the retry backoff is calculated")], limit=5
        )
        assert len(chosen) == 1
