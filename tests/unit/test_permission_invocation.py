# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Permission previews disclose when their combined arguments are truncated."""

from gaia.ui.sse_translation import (
    INVOCATION_TOTAL_CHARS,
    CanonicalTranslator,
    render_invocation,
)


def test_combined_arguments_disclose_hidden_characters():
    args = {"first": "x" * 500, "second": "y" * 500, "third": "z" * 500}
    complete = ", ".join(f'{key}="{args[key]}"' for key in sorted(args))

    rendered = render_invocation(args)

    hidden = len(complete) - INVOCATION_TOTAL_CHARS
    assert rendered == (
        complete[:INVOCATION_TOTAL_CHARS] + f"… [+{hidden:,} more characters not shown]"
    )


def test_short_arguments_are_shown_in_full():
    assert render_invocation({"command": "git status"}) == 'command="git status"'


def test_a_value_with_quotes_stays_unambiguous():
    assert render_invocation({"content": 'print("hello")'}) == (
        "content='print(\"hello\")'"
    )
    assert render_invocation({"content": 'say "it\'s"'}) == (
        'content="say \\"it\'s\\""'
    )
    assert render_invocation({"content": "a\\\"b'c"}) == ('content="a\\\\\\"b\'c"')


def test_a_multi_line_value_keeps_its_lines_and_raw_backslashes():
    rendered = render_invocation(
        {"file_path": "C:\\notes\\list.txt", "content": 'line 1\nsay "it\'s"\n'}
    )
    assert rendered == (
        'file_path="C:\\notes\\list.txt"\ncontent:\n    line 1\n    say "it\'s"'
    )


def test_a_long_multi_line_value_discloses_hidden_lines():
    rendered = render_invocation({"content": "\n".join(f"l{i}" for i in range(20))})
    assert rendered.endswith("    l11\n    … [+8 more lines not shown]")


def test_a_block_keeps_tab_indents_and_shows_control_characters():
    rendered = render_invocation({"content": "def f():\n\treturn 1\x1b[2K\n"})
    assert rendered == "\ncontent:\n    def f():\n        return 1\\x1b[2K"


def test_the_prompt_puts_blocks_under_the_question():
    translator = CanonicalTranslator(run_id="r1", agent_id="gaia", debug=False)
    [only_block] = translator.translate(
        {"type": "permission_request", "tool": "send_now", "args": {"body": "a\nb"}}
    )
    assert only_block["summary"] == "Run 'send_now'?\nbody:\n    a\n    b"

    [mixed] = translator.translate(
        {
            "type": "permission_request",
            "tool": "send_now",
            "args": {"to": "a@b.c", "body": "a\nb"},
        }
    )
    assert mixed["summary"] == "Run 'send_now' with to=\"a@b.c\"?\nbody:\n    a\n    b"


class TestCalendarSummary:
    """The TUI shows only this text, so the approval must be decidable from it."""

    PEOPLE = [f"person{i}@example.com" for i in range(60)]

    def _summary(self, tool, **args):
        events = CanonicalTranslator(run_id="r", agent_id="gaia").translate(
            {"type": "permission_request", "tool": tool, "args": args}
        )
        return events[0]["summary"]

    def test_create_keeps_the_title_and_every_attendee_however_long_the_list(self):
        summary = self._summary(
            "calendar/create_calendar_event",
            title="Budget review",
            start="2026-11-03T09:00:00-05:00",
            end="2026-11-03T10:00:00-05:00",
            notify_attendees=self.PEOPLE,
            calendar_account="me@example.com",
            description="d" * 500,
        )
        assert "Title: Budget review" in summary
        assert "When: 2026-11-03T09:00:00-05:00 to 2026-11-03T10:00:00-05:00" in summary
        assert "Account: me@example.com" in summary
        assert "d" * 500 in summary
        assert all(person in summary for person in self.PEOPLE)
        assert "not shown" not in summary

    def test_update_says_what_changes(self):
        summary = self._summary(
            "calendar/update_calendar_event",
            event_id="abc",
            expected_title="<<<UNTRUSTED_TEXT>>>Budget<<<END_UNTRUSTED_TEXT>>>",
            expected_start="2026-11-03T14:00:00Z",
            notify_attendees=["dana@x.com"],
            new_title="Budget sync",
            new_start="2026-11-04T14:00:00Z",
            new_end="2026-11-04T15:00:00Z",
            new_location="Room 9",
            new_description="New agenda",
        )
        assert summary.splitlines() == [
            "Event: Budget",
            "When: 2026-11-03T14:00:00Z",
            "New title: Budget sync",
            "New time: 2026-11-04T14:00:00Z to 2026-11-04T15:00:00Z",
            "New location: Room 9",
            "New description: New agenda",
            "Notifies: dana@x.com",
        ]

    def test_cancel_says_when_nobody_is_told(self):
        summary = self._summary(
            "calendar/cancel_calendar_event",
            event_id="abc",
            expected_title="Budget",
            expected_start="2026-11-03T14:00:00Z",
            notify_attendees=[],
        )
        assert summary.splitlines()[-1] == "Notifies: nobody"
