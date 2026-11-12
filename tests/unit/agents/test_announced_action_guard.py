# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A final answer that only announces its next action ("I will perform one
final search…") is re-prompted, and never handed to the user as the answer.

Seen on Gemma 4 E4B (NPU) in v0.25.4-rc13 and in the ai-pc-evals data tasks
(amd/gaia#6753, thread 1).
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gaia.agents.base.agent import (
    _MAX_UNFINISHED_ANSWER_REPROMPTS,
    Agent,
    _asks_for_a_plan_only,
    _asks_for_no_action,
    _asks_not_to_look,
    _split_announced_step,
    _unfinished_answer_kind,
)
from gaia.agents.base.verification import strip_verification_scope

LEMONADE_ANNOUNCEMENT = (
    "The Lemonade Server releases page lists v10.0.1 as the newest tag. "
    "Before finalizing, I will perform one final targeted search on the web "
    "to see if there is a more current announcement"
)

ANNOUNCED_ACTIONS = [
    pytest.param(LEMONADE_ANNOUNCEMENT, id="rc13-lemonade-final-search"),
    pytest.param(
        "The CSV has 1,204 rows across 12 months. I'll run the trend analysis now.",
        id="pc-evals-trend-analysis",
    ),
    pytest.param(
        "I found the sales file. Next, I'll compute the monthly totals.",
        id="next-ill",
    ),
    pytest.param("I will now check the release notes.", id="i-will-now"),
    pytest.param(
        "Got the page. I'm going to fetch the changelog to confirm.",
        id="im-going-to",
    ),
    pytest.param("Next, let me open the second report.", id="next-let-me"),
    pytest.param(
        "I've read the first PDF. "
        + "It covers the Q2 results in detail. " * 20
        + "Next, I will perform the same extraction on the second PDF.",
        id="long-paragraph",
    ),
    pytest.param(
        "The search returned nothing useful. I'll try a different query.",
        id="ill-try",
    ),
    pytest.param(
        "I have the list of files. I'll read the config next.",
        id="trailing-next",
    ),
    pytest.param(
        "The build log shows one warning. Then I'll fetch the full log to confirm.",
        id="then-ill-no-comma",
    ),
    # A second "I'll" must not pass for a plan when the last step is this turn's.
    pytest.param(
        "I'll check the releases page now, then I'll summarize what I find.",
        id="two-actions-now",
    ),
    pytest.param(
        "I'll summarize what I found below. Before finalizing, I will perform "
        "one final search to confirm the date.",
        id="promise-then-rc13",
    ),
    pytest.param(
        "I'll keep this short. I'll check the releases page now.",
        id="promise-then-step-now",
    ),
    pytest.param("We'll now check the logs to find the error.", id="well-now"),
    pytest.param("Let's check the logs now.", id="lets-now"),
]


@pytest.mark.parametrize("answer", ANNOUNCED_ACTIONS)
def test_announced_actions_are_narration(answer):
    assert _unfinished_answer_kind(answer) == "narration"


REAL_ANSWERS = [
    pytest.param(
        "Your meeting is moved to Thursday at 3 PM. I'll keep that in mind.",
        id="keep-in-mind",
    ),
    pytest.param(
        "The latest release is v10.0.1. Want me to check the changelog too?",
        id="want-me-to",
    ),
    pytest.param(
        "Total revenue was $48,210. If you'd like, I'll run the trend analysis "
        "as well.",
        id="if-youd-like-offer",
    ),
    pytest.param(
        "Saved. I'll remember that you prefer metric units.",
        id="ill-remember",
    ),
    pytest.param(
        "Done. Next time, I'll check the cache first.",
        id="next-time",
    ),
    pytest.param(
        "The build is green. Once you confirm, I'll run the release script.",
        id="once-you-confirm",
    ),
    pytest.param(
        "I can't reach that site. I'll check again later if you ask.",
        id="later",
    ),
    pytest.param("Should I run the trend analysis now?", id="question"),
    pytest.param(
        "Thanks for the details. I'll look forward to the next report.",
        id="look-forward",
    ),
    pytest.param(
        "Revenue grew 12% in Q3. I'll be happy to break it down by region.",
        id="ill-be-happy",
    ),
    # Drafted content speaks in the user's voice: its closing commitment is
    # part of the answer.
    pytest.param(
        "Hi Sam,\n\nThanks for the contract. I will review it and send comments "
        "by Friday.",
        id="email-draft-no-signoff",
    ),
    pytest.param(
        "Here's a draft reply:\n\nHi Priya,\n\nThanks for flagging the outage. "
        "I'll check with the infra team and get back to you.\n\nBest,\nAlex",
        id="email-draft-signoff",
    ),
    pytest.param(
        "Meeting notes - Q4 planning\n\n- Budget approved\n- Launch moved to "
        "Dec 2\n\nAs the owner, I will create the Jira tickets and share the "
        "timeline.",
        id="action-item-lead-clause",
    ),
    pytest.param(
        "Your goals for next week:\n1. Finish the slides\n2. I will review the "
        "budget with finance",
        id="list-item",
    ),
    pytest.param(
        "Your PR is merged. I will continue to monitor the CI.", id="continue-to"
    ),
    pytest.param(
        "Slack reply draft:\n\nThanks all! I'll review the PR this afternoon.",
        id="this-afternoon",
    ),
    pytest.param("We will review the results in the next sprint.", id="next-sprint"),
    pytest.param(
        "The tests pass on Linux and macOS. I will test it again on the next RC.",
        id="next-rc",
    ),
    pytest.param(
        "I've restarted the service and it is healthy. If the error returns, "
        "I'll check the logs.",
        id="conditional-lead",
    ),
    pytest.param(
        "I'll start with the summary: sales are up 8%, mostly from the East "
        "region, and returns fell.",
        id="announces-then-answers",
    ),
    pytest.param(
        "Dear Hiring Manager,\n\n"
        + "In my current role I built dashboards that cut reporting time by 40%. " * 12
        + "I will review your roadmap closely.\n\nSincerely,\nJordan",
        id="long-cover-letter",
    ),
    # After a denied run, an honest answer waits on the user; pushing it to
    # "do it now" would retry the declined step.
    pytest.param(
        "I'll compute the totals as soon as you allow the Python run.",
        id="denied-as-soon-as",
    ),
    pytest.param(
        "I'll run the analysis once the Python step is approved.",
        id="denied-once-approved",
    ),
    pytest.param(
        "I can't compute this without running code. I'll calculate it if you "
        "approve the run.",
        id="denied-if-you-approve",
    ),
    pytest.param(
        "Running the calculation was declined, so I'll need your approval to "
        "compute the totals.",
        id="denied-need-approval",
    ),
    pytest.param(
        "I will check the logs after you grant access.", id="denied-after-grant"
    ),
    pytest.param(
        "Python was denied, so nothing was computed. I'll calculate the regional "
        "totals when the run is allowed.",
        id="denied-when-allowed",
    ),
    pytest.param(
        "From your notes:\n\n> I'll run the tests now.",
        id="quoted-last-line",
    ),
    # A plan or tutorial the user asked for announces its steps on purpose.
    pytest.param(
        "Here's your plan for the afternoon:\n\nFirst, I'll clear my inbox. "
        "Next, I'll review the two open PRs. Finally, I'll test the release "
        "candidate.",
        id="plan-user-asked",
    ),
    pytest.param(
        "Your afternoon plan: first, I'll clear my inbox, then I'll review the "
        "PRs. Finally, I'll test the release.",
        id="plan-one-paragraph",
    ),
    pytest.param(
        "To set up the database, install Postgres and create a user called app "
        "with a password. Then create the app database and grant the user "
        "access to it.\n\nFinally, we'll test the connection to make sure it "
        "works.",
        id="tutorial-finally-well",
    ),
    pytest.param(
        "We've created the table and inserted the sample rows. Next, we'll run "
        "the migration.",
        id="tutorial-next-well",
    ),
    pytest.param("Next, we'll run the migration.", id="next-well-alone"),
    pytest.param(
        "Install Postgres and create the app user.\n\nFinally, we need to "
        "restart the service.",
        id="tutorial-finally-we-need-to",
    ),
    pytest.param(
        "Suggested commit message:\n\nfix(memory): drop stale tool errors\n\n"
        "We now retire errors from older versions. We'll re-learn real ones "
        "next time they happen.",
        id="commit-message-we-now",
    ),
    pytest.param("Next, let's run the migration.", id="tutorial-lets"),
    # "to make sure" / "to confirm" / "now that" do not turn a plan into a step.
    pytest.param(
        "Here's the migration plan:\n\nFirst, I'll back up the database. Next, "
        "I'll apply the schema change. Finally, I'll run the tests to make sure "
        "everything works.",
        id="plan-to-make-sure",
    ),
    pytest.param(
        "Your rollout plan: I'll deploy to staging first. Then I'll run the "
        "smoke tests to confirm it works.",
        id="plan-to-confirm",
    ),
    pytest.param(
        "Here's what I'd do: I'll merge the PR, then I'll cut the RC now that "
        "CI is green.",
        id="plan-now-that",
    ),
    # A present-tense verb used as a subject or with an adverb is not a step.
    pytest.param(
        "That covers setup. Creating the schema comes next.",
        id="progressive-subject-comes-next",
    ),
    pytest.param(
        "Applying the patch is the step that comes next.",
        id="progressive-subject-is",
    ),
    pytest.param(
        "Deploy: done. Tests: green. Running smoothly now.",
        id="progressive-adverb-status",
    ),
]


@pytest.mark.parametrize("answer", REAL_ANSWERS)
def test_real_answers_are_not_narration(answer):
    assert _unfinished_answer_kind(answer) is None


# ---------------------------------------------------------------------------
# Loop integration with a scripted fake LLM
# ---------------------------------------------------------------------------


class _DummyAgent(Agent):
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
        a.streaming = False
        return a


def _answer(text):
    return json.dumps({"thought": "", "answer": text})


def _call(tool, **args):
    return json.dumps({"thought": "", "tool": tool, "tool_args": args})


def _script(agent, *replies):
    replies = list(replies)
    sent = []

    def _send(messages, *_, **__):
        sent.append([dict(m) for m in messages])
        resp = MagicMock()
        resp.text = replies.pop(0)
        resp.stats = {}
        return resp

    agent.chat = MagicMock(send_messages=MagicMock(side_effect=_send))
    return sent


def _final_text(result):
    return strip_verification_scope(result["result"]).strip()


def _nudges(sent):
    return [
        m["content"]
        for m in sent[-1]
        if m.get("role") == "user"
        and "described your next step instead of doing it" in str(m.get("content"))
    ]


def test_announce_then_tool_call_then_answer_continues_once(agent):
    calls = []
    agent._tools_registry["release_lookup"] = {
        "function": lambda **kw: calls.append(kw) or {"latest": "v10.0.2"},
        "description": "Look up the latest release.",
        "parameters": {},
    }
    sent = _script(
        agent,
        _answer(LEMONADE_ANNOUNCEMENT),
        _call("release_lookup"),
        _answer("The latest Lemonade Server release is v10.0.2 (releases page)."),
    )

    result = agent.process_query(
        "What is the latest released version of Lemonade Server?", max_steps=10
    )

    assert len(sent) == 3
    assert calls == [{}]
    nudges = _nudges(sent)
    assert len(nudges) == 1
    assert "I will perform one final targeted search" in nudges[0]
    assert _final_text(result) == (
        "The latest Lemonade Server release is v10.0.2 (releases page)."
    )


def test_real_answer_with_an_offer_is_not_continued(agent):
    answer = "The latest release is v10.0.1. Want me to check the changelog too?"
    sent = _script(agent, _answer(answer))

    result = agent.process_query("Latest Lemonade release?", max_steps=10)

    assert len(sent) == 1
    assert _final_text(result) == answer


def test_endless_announcing_ends_honestly_at_the_cap(agent):
    sent = _script(
        agent,
        *[_answer("I'll run the trend analysis now.")] * 5,
    )

    result = agent.process_query("Show the sales trend", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "I'll run the trend analysis now." in final
    assert not final.endswith("I'll run the trend analysis now.")


def test_announcement_keeps_what_was_found_when_it_ends_honestly(agent):
    _script(agent, *[_answer(LEMONADE_ANNOUNCEMENT)] * 5)

    result = agent.process_query("Latest Lemonade release?", max_steps=20)

    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "lists v10.0.1 as the newest tag" in final


def test_announcement_on_the_last_step_ends_honestly(agent):
    sent = _script(agent, _answer("I'll run the trend analysis now."))

    result = agent.process_query("Show the sales trend", max_steps=1)

    assert len(sent) == 1
    assert _final_text(result).startswith("I didn't finish:")


def test_honest_ending_is_not_mistaken_for_planning_text(agent):
    # Once the re-prompts are spent, the honest ending quotes "I'll check…",
    # which is also a planning phrase; it must reach the user, not loop again.
    sent = _script(
        agent,
        _answer("I'll run the trend analysis now."),
        _answer("I'll run the trend analysis now."),
        *[_answer("I'll check the sales file now.")] * 6,
    )

    result = agent.process_query("Show the sales trend", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "I'll check the sales file now." in final


def test_planning_phrase_guard_shares_the_reprompt_budget(agent):
    # "i'll check" also trips the planning-phrase guard; it must not loop past the cap.
    sent = _script(agent, *[_answer("I'll check the releases page now.")] * 8)

    result = agent.process_query("Latest Lemonade release?", max_steps=20)

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert _final_text(result).startswith("I didn't finish:")


# rc13 integration build, Gemma 4 E4B (NPU): two edits left "oat oat milk",
# then the answer ended on a write it never made.
TODO_FIX_ANSWER = (
    'The previous edits went wrong: the file now reads "oat oat milk".\n\n'
    "**Corrected Plan:**\n"
    "1. Read the current content of `todo.txt`.\n"
    "2. Construct the exact desired content.\n"
    "3. Use `write_file` to completely replace the file with the correct "
    "content.\n\n"
    "The desired content is:\n\n"
    "```\n- eggs\n- oat milk\n- bread\n- apples\n```\n\n"
    "Executing the final write now."
)


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(TODO_FIX_ANSWER, id="todo-plan-then-executing-now"),
        pytest.param("Executing the final write now.", id="executing-now"),
        pytest.param("Okay, running the search next.", id="running-next"),
        pytest.param("Now writing the corrected file now.", id="now-writing-now"),
    ],
)
def test_progressive_announcements_are_narration(answer):
    assert _unfinished_answer_kind(answer) == "narration"


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            "Running the tests now shows 12 passing and 0 failing.",
            id="progressive-with-result",
        ),
        pytest.param("Writing to that folder needs admin rights.", id="no-cue"),
        pytest.param(
            "Hi Sam,\n\nThe report is attached. Sending the invoice now.",
            id="drafted-email",
        ),
    ],
)
def test_progressive_statements_are_not_narration(answer):
    assert _unfinished_answer_kind(answer) is None


def test_plan_ending_on_executing_now_ends_honestly(agent):
    sent = _script(agent, *[_answer(TODO_FIX_ANSWER)] * 5)

    result = agent.process_query(
        'In todo.txt change "milk" to "oat milk" and add "- apples" at the end.',
        max_steps=20,
    )

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert "Executing the final write now." in sent[1][-1]["content"]
    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "Executing the final write now." in final


# Gemma 4 E4B (NPU): after one analyze_data_file call, the answer laid out a
# run_python plan and ended without running it.
PLAN_INSTEAD_OF_WORK = (
    Path(__file__).parents[2]
    / "fixtures"
    / "agent_answers"
    / "plan_instead_of_work.txt"
).read_text(encoding="utf-8")


def test_a_prose_proceeding_line_is_narration():
    """Announcing a numbered step in prose is narration, not an answer (#6914).

    The digit in the closing used to exempt the whole answer from the
    tool-plan guard, and the progressive pattern required a "now/next" tail
    that "Proceeding with step 1." does not have.
    """
    assert (
        _unfinished_answer_kind(_OLD_FILES_PLAN + "Proceeding with step 1.")
        == "narration"
    )


def test_tool_plan_in_place_of_the_work_is_narration():
    assert _unfinished_answer_kind(PLAN_INSTEAD_OF_WORK) == "narration"
    body, step = _split_announced_step(PLAN_INSTEAD_OF_WORK)
    assert (
        step == "I will use the `run_python` tool to load the CSV data using `pandas`."
    )
    assert "Here is the plan" not in body
    assert body.startswith("The previous tool call returned a result")


def test_tool_plan_step_is_quoted_with_curly_apostrophes():
    answer = (
        "Here’s the plan:\n"
        "1. I’ll use the `run_python` tool to load the CSV.\n"
        "2. I’ll print the regional totals.\n\n"
        "This should give you the breakdown."
    )
    assert _unfinished_answer_kind(answer) == "narration"
    _, step = _split_announced_step(answer)
    assert step == "I’ll use the `run_python` tool to load the CSV."


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(
            "Here is how I would approach it:\n"
            "1. I will load the CSV with pandas.\n"
            "2. I will filter out the returned orders.\n"
            "3. I will group by region and sum units times price.",
            id="tutorial-without-a-tool",
        ),
        pytest.param(
            "Here is the plan:\n"
            "1. I will use the `run_python` tool to load the CSV.\n"
            "2. I will group by region.\n\n"
            "Results: North $12,400.50, South $9,870.00, West $15,210.25.",
            id="plan-then-results",
        ),
        pytest.param(
            "Hi team,\n\nNext steps for the migration:\n"
            "1. I will use the `deploy` tool to stage the build.\n"
            "2. I will run the smoke tests.\n\nThanks,\nSam",
            id="drafted-email-plan",
        ),
        pytest.param(
            "Your notes say:\n\n"
            "> 1. I will use the `run_python` tool to load the data.\n"
            "> 2. I will print the totals.",
            id="quoted-plan",
        ),
        pytest.param(
            "Here's the plan:\n"
            "1. I'll use the `list_files` tool to find files older than 30 days.\n"
            "2. I'll move them to an Archive folder.\n\n"
            "Want me to go ahead?",
            id="plan-awaiting-confirmation",
        ),
        pytest.param(
            "Here's the plan:\n"
            "1. I'll use the `list_files` tool to find old files.\n"
            "2. I'll move them to Archive.\n"
            "Want me to go ahead?",
            id="plan-question-right-under-the-list",
        ),
        pytest.param(
            "Done. Here's what happened:\n"
            "1. I'll use the `list_files` tool again if you add files.\n"
            "2. I'll keep the Archive folder as is.\n"
            "Moved 12 files.",
            id="results-line-right-under-the-list",
        ),
        pytest.param(
            "Here's what I propose:\n"
            "1. I'll use the `list_files` tool to find the old files.\n"
            "2. I'll move them to Archive.\n\n"
            "Let me know and I'll start.",
            id="plan-asks-permission",
        ),
        pytest.param(
            "1. I will rename `load_config` to `read_settings`.\n"
            "2. I will add a unit test.\n\n"
            "Let me know if that works for you.",
            id="code-plan-with-identifiers",
        ),
        pytest.param(
            "Proposed change:\n"
            "1. I will move `parse_args` into `cli_utils`.\n"
            "2. I will update the imports.",
            id="code-plan-no-closing-line",
        ),
    ],
)
def test_plan_look_alikes_are_not_narration(answer):
    assert _unfinished_answer_kind(answer) is None


_OLD_FILES_PLAN = (
    "Here's the plan:\n"
    "1. I'll use the `list_files` tool to find files older than 30 days.\n"
    "2. I'll move them to an Archive folder.\n\n"
)


@pytest.mark.parametrize(
    "closing",
    [
        "Once you confirm, I'll start.",
        "Reply 'go' to proceed.",
        "Reply ‘go’ to proceed.",
        'Reply with "yes" to start.',
        "Reply yes to continue.",
        "Nothing has been changed yet.",
        "Nothing’s been changed yet.",
        "I'll wait for your confirmation before running anything.",
        "Approve this and I'll run it.",
        "I'll proceed when you're ready.",
        "Ready when you are.",
        "Just say go.",
    ],
)
def test_a_plan_waiting_for_the_go_ahead_is_not_narration(closing):
    assert _unfinished_answer_kind(_OLD_FILES_PLAN + closing) is None


@pytest.mark.parametrize(
    "closing",
    [
        "Once it runs, you'll have the list.",
        "Nothing else is needed from you.",
        "This shows which folders to reply to first.",
        "The Archive folder keeps your originals.",
        "This should give you the breakdown.",
    ],
)
def test_a_plan_with_a_look_alike_closing_line_is_narration(closing):
    assert _unfinished_answer_kind(_OLD_FILES_PLAN + closing) == "narration"


def test_tool_plan_in_place_of_the_work_ends_honestly(agent):
    sent = _script(agent, *[_answer(PLAN_INSTEAD_OF_WORK)] * 5)

    result = agent.process_query(
        "What is the net revenue excluding returned orders, broken down by region?",
        max_steps=20,
    )

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert "I will use the `run_python` tool" in sent[1][-1]["content"]
    final = _final_text(result)
    assert final.startswith("I didn't finish:")
    assert "I will use the `run_python` tool" in final


# rc18, DeepSeek: the user asked for a plan and said not to run anything; the
# guard sent the plan back and the answer that replaced it had no plan in it.
NO_ACTION_PLAN_REQUEST = (
    "I want net revenue by region for a sales CSV I will attach later. Do NOT "
    "run anything in this turn. Answer only with a five-step numbered plan in "
    "the first person, each step of the form 'I will use the `run_python` tool "
    "to ...', and close with the sentence: This should give you the exact "
    "breakdown you asked for."
)
REQUESTED_PLAN = (
    "1. I will use the `run_python` tool to load the CSV.\n"
    "2. I will use the `run_python` tool to drop returned orders.\n"
    "3. I will use the `run_python` tool to compute units times unit_price.\n"
    "4. I will use the `run_python` tool to group by region.\n"
    "5. I will use the `run_python` tool to print the totals.\n\n"
    "This should give you the exact breakdown you asked for."
)


@pytest.mark.parametrize(
    "request_text",
    [
        NO_ACTION_PLAN_REQUEST,
        "Don't use any tools, just tell me how you'd do it.",
        "Without using any tools, explain it.",
        "do not call any tools to answer this",
        "Don’t run any tools yet.",
        "Don't open any files.",
        "Don't do anything yet.",
        "Just give me the plan for migrating the DB.",
        "Answer only with a short plan.",
        "Plan only, please.",
        "Use no tools for this one.",
        "Don't touch anything until I say so.",
        "Don't do anything before I confirm.",
        "Don't use any tools, but explain how you would do it.",
        "Explain the bug without opening any files.",
        "Give me only a plan, then wait for my go-ahead.",
        "Just give me a plan. I'll decide later whether to run it.",
        "Answer only with a short plan and close with a one-line summary.",
        "I want only a plan.",
        "Give me the plan only.",
        "Show the sales trend, but don't use any tools yet.",
        # Nothing before the ban asks for work: it still stops everything.
        "Hi, how are you? Don't touch anything, I just want to chat.",
        "I'll tell you what to do later. Don't touch anything until I say so.",
        "I will change the config myself later. Don't touch anything.",
    ],
)
def test_requests_not_to_act_are_recognised(request_text):
    assert _asks_for_no_action(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "What is the net revenue excluding returned orders, broken down by region?",
        # Not running is not "do nothing": the files are still to be written.
        "In ~/gaia-eval/resilience/ create totals.py and test_totals.py. "
        "Don't run anything yet.",
        "For orders.csv I want net revenue by region. Do NOT run anything in "
        "this turn.",
        "Without running anything, how would you clean this CSV?",
        "Fix foo.py and don't do anything else.",
        "Clean up the folder but don't touch anything else in it.",
        "Don't run anything destructive.",
        "Don't use pandas.",
        "Write a script but don't run it.",
        "Does config.yaml set debug? Answer only yes or no.",
        "Run the tests and don't open any other files.",
        "Don't read anything into it, just summarize report.md.",
        # rc18 round 2: each of these wants the work done.
        "Last time you answered without reading any files and got it wrong. "
        "What is the total gross in C:/x/orders.csv?",
        "Don't touch anything until you've read C:/x/orders.csv, then tell me "
        "the total.",
        "Don't do anything before you check the README.",
        "What is in C:/x/notes.txt? Don't open any files but that one.",
        "Don't open any files except config.yaml.",
        "Don't touch anything other than todo.txt.",
        "Give me only a plan for the migration, then do step 1.",
        "Just give me a plan and then carry it out.",
        "Plan only at first, and then run step 1 once it reads well.",
        "Answer only with a short plan. After that, execute it.",
        # "Plan only" inside a request to do work.
        "My migration plan only covers Postgres; extend it to MySQL.",
        "Write not only a plan but the code too.",
        "Update the plan only for step 3 and save it to plan.md",
        # A ban followed by, or following, the work to do.
        "Don't touch anything — just read config.py and tell me what it sets",
        "Don't touch anything, just read config.py and tell me what it sets",
        "Don't touch anything - open config.py and tell me what it sets",
        "Fix the bug in foo.py but don't touch anything.",
        "",
    ],
)
def test_requests_that_limit_the_work_are_not_requests_not_to_act(request_text):
    assert not _asks_for_no_action(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "Find out why test_foo.py fails. Don't touch anything.",
        "Look into the crash in server.py and explain it. Do not touch anything!",
        "Why is the build failing? Don't touch anything.",
        "Review config.py for bugs; don't touch anything for now.",
        "Explain what main.py does without touching anything.",
        "Can you check why the tests fail?\nDon't touch anything.",
    ],
)
def test_a_touch_ban_on_an_investigation_bars_edits_not_looking(request_text):
    # The edit ban still keeps the "do the step you announced" nudges off.
    assert _asks_for_no_action(request_text)
    assert not _asks_not_to_look(request_text)
    assert not _asks_for_a_plan_only(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "Hi, how are you? Don't touch anything, I just want to chat.",
        "I'll tell you what to do later. Don't touch anything until I say so.",
        "I will change the config myself later. Don't touch anything.",
        "What a mess. Don't touch anything.",
        "How are you today? Don't touch anything.",
        # The investigation is two sentences back, not the one before the ban.
        "Find out why test_foo.py fails. I will look myself first. "
        "Don't touch anything.",
        "Don't touch anything.",
    ],
)
def test_a_touch_ban_without_an_investigation_still_bars_looking(request_text):
    assert _asks_for_no_action(request_text)
    assert _asks_not_to_look(request_text)


@pytest.mark.parametrize(
    "request_text, plan_only",
    [
        (NO_ACTION_PLAN_REQUEST, True),
        ("Answer only with a short plan.", True),
        ("Plan only, please.", True),
        ("Use no tools in this turn. Answer only with a three-step plan.", True),
        # No tools, but a question to answer: an unread file keeps its note.
        (
            "Without using any tools, what does src/gaia/version.py set "
            "`__version__` to?",
            False,
        ),
        ("Don't use any tools. What's the timeout in config.py?", False),
        ("Use no tools: what does README.md say about installing?", False),
        ("Don't do anything yet.", False),
        ("Give me only a plan for the migration, then do step 1.", False),
        ("", False),
    ],
)
def test_only_a_plan_request_is_a_plan_only_turn(request_text, plan_only):
    assert _asks_for_a_plan_only(request_text) is plan_only


def test_a_plan_the_user_asked_for_is_the_answer(agent):
    assert _unfinished_answer_kind(REQUESTED_PLAN) == "narration"
    sent = _script(agent, *[_answer(REQUESTED_PLAN)] * 3)

    result = agent.process_query(NO_ACTION_PLAN_REQUEST, max_steps=20)

    assert len(sent) == 1
    assert _final_text(result) == REQUESTED_PLAN


def test_an_announced_step_the_user_forbade_is_not_pushed(agent):
    answer = "I'll run the trend analysis now."
    sent = _script(agent, *[_answer(answer)] * 3)

    result = agent.process_query(
        "Show the sales trend, but don't use any tools yet.", max_steps=20
    )

    assert len(sent) == 1
    assert _final_text(result) == answer


def test_an_announced_write_is_still_pushed_when_only_running_is_forbidden(agent):
    """gaia_resilience: "create totals.py ... Don't run anything yet" owes the write."""
    sent = _script(agent, *[_answer("I'll create totals.py now.")] * 5)

    result = agent.process_query(
        "Create totals.py with a total_with_tax function. Don't run anything yet.",
        max_steps=20,
    )

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert _final_text(result).startswith("I didn't finish:")


def test_a_plan_is_still_sent_back_when_only_extra_work_is_forbidden(agent):
    sent = _script(agent, *[_answer(REQUESTED_PLAN)] * 5)

    result = agent.process_query(
        "Net revenue by region for orders.csv, and don't do anything else.",
        max_steps=20,
    )

    assert len(sent) == _MAX_UNFINISHED_ANSWER_REPROMPTS + 1
    assert _final_text(result).startswith("I didn't finish:")
