# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Permission previews disclose when their combined arguments are truncated."""

import pytest

from gaia.ui.sse_translation import (
    INVOCATION_TOTAL_CHARS,
    CanonicalTranslator,
    render_command_summary,
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


class TestLookAlikeNamesInTheTerminalCard:
    """The TUI shows only the summary, so a joiner in a name must read as a code."""

    ZWJ = "\u200d"

    def _summary(self, tool, **args):
        [event] = CanonicalTranslator(run_id="r", agent_id="gaia").translate(
            {"type": "permission_request", "tool": tool, "args": args}
        )
        return event["summary"]

    def test_a_joiner_in_a_written_path_shows_as_a_code(self):
        summary = self._summary(
            "write_file", file_path=f"inv{self.ZWJ}oice.txt", content="x"
        )
        assert "inv[U+200D]oice.txt" in summary
        assert self.ZWJ not in summary

    def test_marks_and_selectors_in_a_url_show_as_codes(self):
        summary = self._summary(
            "fetch_page", url="https://exa\u200emple.com/a\ufe0f", note="ok"
        )
        assert "exa[U+200E]mple.com/a[U+FE0F]" in summary

    @pytest.mark.parametrize(
        "tool, key",
        [
            ("write_file", "file_path"),
            ("fetch_page", "url"),
            ("forget_memory", "id"),
            ("open_project", "project_root"),
        ],
    )
    @pytest.mark.parametrize(
        "between, code",
        [
            (chr(9), "[U+0009]"),
            (chr(13), "[U+000D]"),
            (chr(10), "[U+000A]"),
            (chr(13) + chr(10), "[U+000D][U+000A]"),
        ],
        ids=["tab", "cr", "lf", "crlf"],
    )
    def test_whitespace_in_a_name_shows_as_a_code_on_one_line(
        self, tool, key, between, code
    ):
        summary = self._summary(tool, **{key: f"inv{self.ZWJ}oice.txt{between}b"})
        assert f"inv[U+200D]oice.txt{code}b" in summary
        assert self.ZWJ not in summary
        assert chr(9) not in summary and chr(13) not in summary
        # Not split into an indented block: the name stays on its own line.
        assert f"\n{key}:\n" not in summary

    def test_a_path_access_question_codes_the_path(self):
        summary = self._summary(
            "allow_path_access", path=f"C:/inv{self.ZWJ}oice", kind="folder"
        )
        assert "C:/inv[U+200D]oice" in summary
        assert self.ZWJ not in summary

    def test_a_trusted_folder_and_its_files_are_coded(self):
        summary = self._summary(
            "trust_project_folder",
            folder=f"C:/pro{self.ZWJ}j",
            files=[f"AGENTS{self.ZWJ}.md"],
        )
        assert "C:/pro[U+200D]j" in summary
        assert "AGENTS[U+200D].md" in summary

    def test_a_joiner_in_a_shell_program_and_its_folder_is_coded(self):
        summary = self._summary(
            "run_shell_command",
            command=f"g{self.ZWJ}it status",
            working_directory=f"/tmp/re{self.ZWJ}po",
        )
        assert "g[U+200D]it status" in summary
        assert "re[U+200D]po" in summary
        assert self.ZWJ not in summary

    def test_an_unvetted_command_keeps_its_lines_but_codes_joiners(self):
        summary = render_command_summary(
            "run_shell_command",
            {
                "command": f"echo a\ng{self.ZWJ}it push",
                "working_directory": "/w",
                "_risk_note": "unchecked",
            },
            workspace="/w",
        )
        assert summary.startswith("echo a\ng[U+200D]it push\nin /w")

    def test_a_script_path_is_coded(self):
        summary = render_command_summary(
            "execute_python_file", {"file_path": f"/w/ru{self.ZWJ}n.py"}, workspace="/w"
        )
        assert "ru[U+200D]n.py" in summary

    def test_a_bidi_override_in_any_one_line_value_is_coded(self):
        assert render_invocation({"query": "abc\u202edef"}) == 'query="abc[U+202E]def"'

    def test_emoji_and_persian_in_prose_values_still_read(self):
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        persian = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"
        assert render_invocation({"message": f"{family} {persian}"}) == (
            f'message="{family} {persian}"'
        )

    def test_a_plain_name_is_unchanged(self):
        assert render_invocation(
            {"file_path": "C:/notes/caf\u00e9 \u65e5\u672c.txt"}
        ) == ('file_path="C:/notes/caf\u00e9 \u65e5\u672c.txt"')

    def test_a_joiner_in_an_argument_name_is_coded(self):
        assert render_invocation({f"pa{self.ZWJ}th": "a"}) == 'pa[U+200D]th="a"'


class TestLinesOfABlockFollowTheirRule:
    """A value split over lines goes to a block, which must not drop the rule."""

    def test_a_joiner_in_a_path_with_a_line_break_is_coded(self):
        rendered = render_invocation({"path": "inv\u200doice.txt\nnotes"})
        assert "inv[U+200D]oice.txt" in rendered
        assert "\u200d" not in rendered

    def test_a_bidi_override_in_a_body_line_is_coded(self):
        rendered = render_invocation({"content": "line one\nab\u202ecd"})
        assert "ab[U+202E]cd" in rendered

    def test_a_body_keeps_its_emoji_and_persian(self):
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        persian = "\u0645\u06cc\u200c\u062e\u0648\u0627\u0647\u0645"
        rendered = render_invocation({"content": f"{family}\n{persian}"})
        assert family in rendered and persian in rendered
        assert "[U+" not in rendered

    def test_a_multi_line_command_codes_its_joiners(self):
        rendered = render_invocation({"command": "echo a\nr\u200dm -rf x"})
        assert "r[U+200D]m -rf x" in rendered
