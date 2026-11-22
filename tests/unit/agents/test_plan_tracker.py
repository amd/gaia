# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The plan tracker: parsing a plan and ticking it from tool results alone."""

import random

import pytest

from gaia.agents.base.plan_tracker import (
    DONE,
    FAILED,
    OPEN,
    PlanTextFilter,
    PlanTracker,
    parse_plan,
    plan_from_thinking,
    strip_plan,
    targets_in,
    targets_of,
)

CHANGE_TOOLS = {"write_file", "edit_file", "create_table"}
TOOLS = CHANGE_TOOLS | {"read_file", "list_files", "search_web", "fetch_page"}


def tracker(*steps):
    t = PlanTracker(tool_names=TOOLS, change_tools=CHANGE_TOOLS)
    if steps:
        assert t.set_steps(steps) or len(steps) < 3
    return t


def marks(t):
    return [s["status"] for s in t.steps]


# -- parsing ----------------------------------------------------------------


def test_parse_plan_reads_the_list_under_the_plan_line():
    text = (
        "Plan:\n1. Read notes.txt\n2. Merge the items\n3. Save actions.md\n\nStarting."
    )
    assert parse_plan(text) == ["Read notes.txt", "Merge the items", "Save actions.md"]


def test_parse_plan_accepts_bold_heading_and_bullets():
    assert parse_plan("**Plan:**\n- a\n- b\n- c") == ["a", "b", "c"]


@pytest.mark.parametrize(
    "text",
    [
        "Plan:\n1. Read notes.txt\n2. Answer",
        "The first line is 'Standup notes'.",
        "Here are 3 options:\n1. a\n2. b\n3. c",
        "",
        None,
    ],
)
def test_parse_plan_returns_none_without_a_real_plan(text):
    assert parse_plan(text) is None


def test_plan_from_thinking_takes_the_longest_numbered_run():
    text = "The user wants me to:\n1. Search the web\n2. Open the result\n3. Save a note\n\nLet me start."
    assert plan_from_thinking(text) == [
        "Search the web",
        "Open the result",
        "Save a note",
    ]
    assert plan_from_thinking("Two things:\n1. a\n2. b") is None


def test_strip_plan_removes_only_the_plan_block():
    text = "Plan:\n1. a\n2. b\n3. c\n\nReading the first file now."
    assert strip_plan(text) == "Reading the first file now."
    assert strip_plan("Plan:\n1. a\n2. b") == "Plan:\n1. a\n2. b"
    assert strip_plan("No plan here.") == "No plan here."


def test_targets_cover_files_and_web_addresses():
    step = "Fetch https://example.com/pricing and example.com/faq, save to Documents/refunds.md"
    assert targets_in(step) == {"example.com/pricing", "example.com/faq", "refunds.md"}
    assert targets_of({"url": "https://example.com/pricing"}) == {
        "example.com",
        "example.com/pricing",
    }
    assert targets_of({"file_path": "Documents/notes.txt"}) == {"notes.txt"}
    assert targets_of({"content": "a report that mentions utils.py"}) == set()


# -- the streamed-text filter -----------------------------------------------


def _stream(text, size):
    f = PlanTextFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size))
    return out + f.flush()


@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_filter_hides_a_plan_block_however_it_is_chunked(size):
    text = "Plan:\n1. Read a.txt\n2. Merge\n3. Save b.md\n\nReading the first file."
    assert _stream(text, size).strip() == "Reading the first file."


@pytest.mark.parametrize("size", [1, 4, 1000])
def test_filter_passes_ordinary_text_through_unchanged(size):
    for text in (
        "The answer is 391.",
        "Plan:\n1. only\n2. two\nDone.",
        "Planning ahead matters.\n1. a\n2. b\n3. c",
    ):
        assert _stream(text, size) == text


def test_filter_hides_a_plan_that_ends_the_reply():
    assert _stream("Plan:\n1. a\n2. b\n3. c", 2) == ""


# -- ticking ----------------------------------------------------------------


def test_history_counts_when_the_plan_arrives_late():
    t = tracker()
    t.on_tool_result("read_file", {"file_path": "Documents/notes.txt"}, True)
    t.on_tool_result("read_file", {"file_path": "Documents/meeting.txt"}, True)
    t.set_steps(
        [
            "Read notes.txt and meeting.txt",
            "Merge the action items",
            "Save to Documents/actions.md",
        ]
    )
    assert marks(t) == [DONE, OPEN, OPEN]
    t.on_tool_result(
        "write_file", {"path": "Documents/actions.md", "content": "x y"}, True
    )
    assert marks(t) == [DONE, DONE, DONE]


def test_failed_write_marks_its_step_failed_until_a_retry_succeeds():
    t = tracker("Read notes.txt", "Merge the items", "Save to actions.md")
    t.on_tool_result("read_file", {"path": "notes.txt"}, True)
    t.on_tool_result("write_file", {"path": "actions.md", "content": "x"}, False)
    t.finish()
    assert marks(t) == [DONE, DONE, FAILED]
    assert t.has_failed()
    t.on_tool_result("write_file", {"path": "actions.md", "content": "x"}, True)
    t.finish()
    assert marks(t) == [DONE, DONE, DONE]


def test_a_step_nothing_was_done_about_stays_open():
    t = tracker("Read notes.txt", "Compile a digest", "Write the digest to digest.md")
    t.on_tool_result("read_file", {"path": "notes.txt"}, True)
    t.finish()
    assert marks(t) == [DONE, OPEN, OPEN]
    assert t.unfinished_checked() == ["Write the digest to digest.md"]


def test_reading_a_file_does_not_tick_the_step_that_changes_it():
    t = tracker(
        "Read config.yaml", "Read README.md", "Compare the two and correct README.md"
    )
    t.on_tool_result("read_file", {"file_path": "p/config.yaml"}, True)
    t.on_tool_result("read_file", {"file_path": "p/README.md"}, True)
    t.finish()
    assert marks(t) == [DONE, DONE, OPEN]
    t.on_tool_result(
        "edit_file",
        {"file_path": "p/README.md", "old_content": "8080", "new_content": "4200"},
        True,
    )
    assert marks(t) == [DONE, DONE, DONE]


def test_a_step_naming_a_change_tool_is_a_change_step():
    t = tracker("Read a.txt", "Read b.txt", "If they differ use edit_file on b.txt")
    assert t.steps[2]["change"]


def test_step_reading_one_file_and_writing_another():
    t = tracker(
        "Read app.py and write a description to INDEX.md",
        "Read utils.py and write a description to INDEX.md",
        "List the logs folder",
    )
    t.on_tool_result("read_file", {"path": "p/app.py"}, True)
    assert marks(t)[:2] == [OPEN, OPEN]
    t.on_tool_result("write_file", {"path": "p/INDEX.md", "content": "x"}, True)
    assert marks(t)[:2] == [DONE, OPEN]


def test_change_step_without_a_file_name_needs_some_change():
    t = tracker("Gather the reports", "Compare revenue", "Save the summary")
    t.on_tool_result("read_file", {"path": "Q3.txt"}, True)
    t.on_tool_result("write_file", {"path": "out.md", "content": "x"}, False)
    t.finish()
    assert marks(t) == [DONE, DONE, FAILED]
    t.on_tool_result("write_file", {"path": "out.md", "content": "x"}, True)
    t.finish()
    assert marks(t) == [DONE, DONE, DONE]


def test_late_plan_does_not_credit_one_change_twice():
    t = tracker()
    t.on_tool_result("write_file", {"path": "a/report.md", "content": "x"}, True)
    t.set_steps(["Write report.md", "Save the appendix", "Tell the user"])
    t.finish()
    assert marks(t) == [DONE, OPEN, OPEN]


def test_web_steps_tick_by_address_and_fail_by_address():
    t = tracker(
        "Fetch https://example.com/pricing",
        "Fetch https://example.com/faq",
        "Write the comparison to refunds.md",
    )
    t.on_tool_result("fetch_page", {"url": "https://example.com/pricing"}, True)
    assert marks(t) == [DONE, OPEN, OPEN]
    t.on_tool_result("fetch_page", {"url": "https://example.com/faq"}, False)
    assert marks(t) == [DONE, FAILED, OPEN]


def test_a_listing_ticks_the_step_that_asks_for_one():
    t = tracker(
        "List the files in the project folder", "Read app.py", "Write report.md"
    )
    t.on_tool_result("list_files", {"path": "project"}, True)
    assert marks(t) == [DONE, OPEN, OPEN]


def test_resending_the_plan_keeps_progress():
    t = tracker("Read app.py", "Think", "Write report.md")
    t.on_tool_result("read_file", {"path": "app.py"}, True)
    t.set_steps(["Read app.py", "Read utils.py", "Write report.md"])
    assert marks(t) == [DONE, OPEN, OPEN]


def test_steps_are_cleaned_for_display():
    t = tracker(
        "Search for TODO using `search_file_content`. (Next)",
        "Read `app.py` (Done)",
        "Write `report.md` using write_file",
    )
    t2 = PlanTracker(
        tool_names={"search_file_content", "write_file"}, change_tools={"write_file"}
    )
    t2.set_steps(
        [s["text"] for s in t.steps]
        and [
            "Search for TODO using `search_file_content`. (Next)",
            "Read `app.py` (Done)",
            "Write `report.md` using write_file",
        ]
    )
    assert [s["text"] for s in t2.steps] == [
        "Search for TODO.",
        "Read app.py",
        "Write report.md",
    ]


def test_fewer_than_three_steps_is_not_a_plan():
    t = tracker()
    assert not t.set_steps(["Read a.txt", "Answer"])
    assert t.steps == []


def test_reminder_is_due_once_after_two_calls_without_a_plan():
    t = tracker()
    assert not t.needs_reminder()
    t.on_tool_result("read_file", {"path": "a.txt"}, True)
    t.on_tool_result("read_file", {"path": "b.txt"}, True)
    assert t.needs_reminder()
    t.reminded = True
    assert not t.needs_reminder()


def test_ticks_always_have_evidence_under_random_plans_and_calls():
    files = [f"f{i}.md" for i in range(5)]
    for seed in range(20000):
        rng = random.Random(seed)
        t = tracker()
        ok_read, ok_write = set(), set()
        any_change = False
        for _ in range(rng.randint(0, 14)):
            if rng.random() < 0.2:
                t.set_steps(
                    [
                        rng.choice(["Read", "Write", "Check", "Save", "Compare"])
                        + (f" {rng.choice(files)}" if rng.random() < 0.7 else " things")
                        for _ in range(rng.randint(0, 6))
                    ]
                )
            else:
                tool = rng.choice(["read_file", "write_file", "list_files"])
                name = rng.choice(files)
                ok = rng.random() < 0.6
                t.on_tool_result(tool, {"path": "d/" + name}, ok)
                if ok:
                    (ok_write if tool == "write_file" else ok_read).add(name)
                    any_change = any_change or tool == "write_file"
        t.finish()
        for s in t.steps:
            if s["status"] != DONE:
                continue
            where = f"seed {seed}: {s}"
            if s["targets"]:
                assert s["targets"] <= ok_read | ok_write, where
                assert not s["change"] or s["targets"] & ok_write, where
            elif s["change"]:
                assert any_change, where


def test_filter_releases_the_plan_when_the_reply_was_an_answer():
    f = PlanTextFilter()
    text = "Plan:\n1. a\n2. b\n3. c\n"
    shown = "".join(f.feed(text[i : i + 4]) for i in range(0, len(text), 4))
    assert shown == ""
    assert f.flush(keep_plan=True) == text


def test_an_earlier_plan_is_not_re_ticked_by_a_later_request():
    t = tracker("Read notes.txt", "Merge the items", "Save to actions.md")
    t.on_tool_result("read_file", {"path": "notes.txt"}, True)
    t.on_tool_result("write_file", {"path": "actions.md", "content": "x"}, False)
    t.finish()
    assert marks(t) == [DONE, DONE, FAILED]
    t.reset_request()
    assert not t.on_tool_result(
        "write_file", {"path": "actions.md", "content": "x"}, True
    )
    assert marks(t) == [DONE, DONE, FAILED]


# -- what a step works on -----------------------------------------------------


@pytest.mark.parametrize(
    "args, expected",
    [
        ({"file_path": "C:\\Users\\me\\My Documents\\report.docx"}, "report.docx"),
        ({"url": "https://www.example.com/page"}, "example.com/page"),
        ({"url": "https://python.org/downloads"}, "python.org"),
        ({"command": "cat notes.md | head"}, "notes.md"),
    ],
)
def test_evidence_is_read_from_awkward_arguments(args, expected):
    assert expected in targets_of(args)


def test_what_is_written_is_not_evidence_of_what_was_worked_on():
    assert targets_of({"path": "out.md", "content": "see utils.py"}) == {"out.md"}
    assert targets_of({"path": "out.md", "text": "a\nb"}) == {"out.md"}


def test_an_email_address_is_not_a_file_or_a_page():
    assert targets_in("Email bob@gmail.com about the report") == set()


@pytest.mark.parametrize(
    "step, call",
    [
        (
            "Search python.org for the release date",
            {"url": "https://python.org/downloads"},
        ),
        ("Fetch example.com/page", {"url": "https://www.example.com/page"}),
        (
            "Read the report in My Documents/report.docx",
            {"file_path": "C:\\Users\\me\\My Documents\\report.docx"},
        ),
        ("Read notes.md", {"command": "cat notes.md"}),
    ],
)
def test_a_step_ticks_for_the_awkward_spellings_of_its_target(step, call):
    t = tracker(step, "Think about it", "Tell the user")
    t.on_tool_result("fetch_page", call, True)
    assert marks(t)[0] == DONE


def test_a_step_cut_for_display_keeps_the_file_it_names():
    long = "Merge and de-duplicate every action item from both files into one list, keeping the wording of each, and save it to actions.md"
    t = tracker("Read notes.txt", "Read meeting.txt", long)
    assert len(t.steps[2]["text"]) <= 120
    assert t.steps[2]["targets"] == {"actions.md"}
    t.on_tool_result("write_file", {"path": "actions.md", "content": "x"}, True)
    assert marks(t)[2] == DONE


# -- the invariant, at its edges ------------------------------------------------


def test_the_same_step_in_a_new_request_starts_open():
    t = tracker("Read notes.md", "Think", "Update notes.md")
    t.on_tool_result("read_file", {"path": "notes.md"}, True)
    t.on_tool_result("write_file", {"path": "notes.md", "content": "x"}, True)
    assert marks(t) == [DONE, DONE, DONE]
    t.reset_request()
    t.set_steps(["Read notes.md", "Think", "Update notes.md"])
    assert marks(t) == [OPEN, OPEN, OPEN]


def test_a_change_step_naming_no_file_is_not_ticked_by_another_steps_write():
    t = tracker(
        "Fix the bug in the parser", "Update notes.md with the fix", "Tell the user"
    )
    t.on_tool_result("write_file", {"path": "notes.md", "content": "x"}, True)
    t.finish()
    assert marks(t)[0] == OPEN
    assert marks(t)[1] == DONE


def test_a_change_step_naming_two_files_needs_both_changed():
    t = tracker("Update a.py and b.py", "Run the tests", "Tell the user")
    t.on_tool_result("read_file", {"path": "a.py"}, True)
    t.on_tool_result("read_file", {"path": "b.py"}, True)
    t.on_tool_result("write_file", {"path": "a.py", "content": "x"}, True)
    assert marks(t)[0] == OPEN
    t.on_tool_result("write_file", {"path": "b.py", "content": "x"}, True)
    assert marks(t)[0] == DONE


def test_a_failed_step_ticks_nothing_before_it_when_nothing_else_worked():
    t = tracker("Search the web for prices", "Compare the options", "Save to prices.md")
    t.on_tool_result("write_file", {"path": "prices.md", "content": "x"}, False)
    t.finish()
    assert marks(t) == [OPEN, OPEN, FAILED]


def test_a_tool_ticks_a_step_only_on_its_whole_word():
    t = tracker("Ready the release notes", "Think", "Write out.md")
    t.on_tool_result("read_file", {"path": "x.txt"}, True)
    assert marks(t)[0] == OPEN


# -- reading the plan ---------------------------------------------------------------


def test_a_numbered_list_of_things_in_thinking_is_not_a_plan():
    assert plan_from_thinking("fields:\n1. name\n2. age\n3. email\n") is None


def test_sub_points_are_not_steps():
    text = (
        "Plan:\n1. Read app.py\n   - check imports\n2. Read utils.py\n3. Write out.md\n"
    )
    assert parse_plan(text) == ["Read app.py", "Read utils.py", "Write out.md"]
    assert strip_plan(text + "\nDone.") == "Done."


def test_a_huge_line_is_not_scanned():
    import time

    start = time.perf_counter()
    assert parse_plan("Plan:\n" + " " * 200_000 + "x\n1. a b\n2. c d\n3. e f\n") is None
    assert time.perf_counter() - start < 0.5


def test_only_the_adopted_plan_is_stripped_from_an_answer():
    week = "Plan:\n1. Monday: review\n2. Tuesday: write\n3. Wednesday: ship\n"
    adopted = ["Read notes.txt", "Read todo.txt", "Write the week plan"]
    assert strip_plan(week, only=adopted) == week
    same = "Plan:\n1. Read notes.txt\n2. Read todo.txt\n3. Write the week plan\n\nDone."
    assert strip_plan(same, only=adopted) == "Done."


# -- the streamed-text filter, whatever the chunking ------------------------------


@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_filter_shows_an_answers_plan_and_the_text_after_it(size):
    text = "## Plan\n1. a b\n2. c d\n3. e f\n\nLet me know.\n"
    f = PlanTextFilter()
    out = "".join(f.feed(text[i : i + size]) for i in range(0, len(text), size))
    assert out + f.flush(keep_plan=True) == text


@pytest.mark.parametrize("size", [1, 3, 7, 1000])
def test_filter_leaves_a_plan_in_the_middle_of_a_reply_alone(size):
    text = "Here is what I found.\n\nPlan:\n1. a b\n2. c d\n3. e f\n"
    assert _stream(text, size) == text


def test_ticks_always_have_evidence_from_this_request_under_random_use():
    files = [f"f{i}.md" for i in range(5)]
    for seed in range(20000):
        rng = random.Random(seed)
        t = tracker()
        ok_read, ok_write, loose_writes = set(), set(), 0
        for _ in range(rng.randint(0, 16)):
            roll = rng.random()
            if roll < 0.08:
                t.reset_request()
                ok_read, ok_write, loose_writes = set(), set(), 0
            elif roll < 0.28:
                t.set_steps(
                    [
                        rng.choice(["Read", "Write", "Check", "Save", "Compare"])
                        + (f" {rng.choice(files)}" if rng.random() < 0.7 else " things")
                        for _ in range(rng.randint(0, 6))
                    ]
                )
            else:
                tool = rng.choice(["read_file", "write_file", "list_files"])
                name = rng.choice(files)
                ok = rng.random() < 0.6
                t.on_tool_result(tool, {"path": "d/" + name}, ok)
                if ok:
                    (ok_write if tool == "write_file" else ok_read).add(name)
                    loose_writes += tool == "write_file"
        t.finish()
        if not t.planned_this_request:
            continue
        nameless_changes_done = 0
        for s in t.steps:
            if s["status"] != DONE:
                continue
            where = f"seed {seed}: {s}"
            if s["targets"] and s["change"] and not s["reads"]:
                assert s["targets"] <= ok_write, where
            elif s["targets"]:
                assert s["targets"] <= ok_read | ok_write, where
                assert not s["change"] or s["targets"] & ok_write, where
            elif s["change"]:
                nameless_changes_done += 1
        assert nameless_changes_done <= loose_writes, f"seed {seed}: {t.snapshot()}"


# -- a plan written on one line -----------------------------------------------


def test_a_plan_on_one_line_is_read():
    text = "Plan: 1. Read both files (done). 2. Extract and deduplicate action items. 3. Write the merged list to Documents/actions.md.\n\nHere is the list."
    assert parse_plan(text) == [
        "Read both files (done).",
        "Extract and deduplicate action items.",
        "Write the merged list to Documents/actions.md.",
    ]
    assert strip_plan(text).strip() == "Here is the list."


def test_a_number_that_does_not_count_up_stays_in_its_step():
    text = "Plan: 1. Read chapter 5. of the book 2. Summarize it 3. Save notes.md"
    assert parse_plan(text) == [
        "Read chapter 5. of the book",
        "Summarize it",
        "Save notes.md",
    ]


def test_a_sentence_mentioning_a_plan_is_not_one():
    assert parse_plan("Plan: I will read the files first. 1. Then merge.") is None
    assert parse_plan("The plan: 1. one 2. two") is None


@pytest.mark.parametrize("size", [1, 5, 1000])
def test_filter_holds_a_one_line_plan(size):
    text = "Plan: 1. Read a.txt 2. Merge them 3. Save b.md\n\nReading now."
    assert _stream(text, size).strip() == "Reading now."


def test_a_step_that_names_a_tool_is_done_when_that_tool_worked():
    t = PlanTracker(
        tool_names={"find_files", "write_file"}, change_tools={"write_file"}
    )
    t.set_steps(
        [
            "Use find_files to locate all Python files.",
            "Look through each of them",
            "Write the list to report.md",
        ]
    )
    assert t.steps[0]["text"] == "Locate all Python files."
    t.on_tool_result("find_files", {"query": "*.py"}, True)
    assert t.steps[0]["status"] == DONE


def test_failed_calls_are_counted_whatever_step_they_were_for():
    t = tracker("Read notes.txt", "Think", "Write out.md")
    t.on_tool_result("read_file", {"path": "log1.txt"}, False)
    assert t.failed_calls == 1
    assert not t.has_failed()
