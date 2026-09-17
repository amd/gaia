# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Decision-point extraction tests.

All fixtures are synthetic transcripts written here, never real corpus content.

Most of these are regression tests for traps that produce a plausible-looking
wrong dataset rather than a crash — the dangerous kind. Chief among them:
Claude Code writes one JSONL record per *content block*, so a message's text and
its ``tool_use`` land in separate records. A per-record scan finds zero
co-occurrence and concludes reasoning does not exist.
"""

import json

from gaia.factory.dataset.extract import scan_transcript, split_shell_segments


def _write(tmp_path, records, name="11111111-2222-3333-4444-555555555555"):
    project = tmp_path / "C--Users-jdoe-Work-proj"
    project.mkdir(parents=True, exist_ok=True)
    path = project / f"{name}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def _user(text, **kw):
    rec = {
        "type": "user",
        "timestamp": "2026-08-01T00:00:00Z",
        "cwd": r"C:\Users\jdoe\Work\proj",
        "gitBranch": "feature/x",
        "message": {"content": [{"type": "text", "text": text}]},
    }
    rec.update(kw)
    return rec


def _assistant_block(msg_id, block):
    return {
        "type": "assistant",
        "timestamp": "2026-08-01T00:00:01Z",
        "cwd": r"C:\Users\jdoe\Work\proj",
        "gitBranch": "feature/x",
        "message": {"id": msg_id, "model": "claude-opus-5", "content": [block]},
    }


def _result(use_id, text, is_error=False, tool_use_result=None):
    rec = {
        "type": "user",
        "timestamp": "2026-08-01T00:00:02Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": use_id,
                    "is_error": is_error,
                    "content": [{"type": "text", "text": text}],
                }
            ]
        },
    }
    if tool_use_result is not None:
        rec["toolUseResult"] = tool_use_result
    return rec


def test_text_and_tool_use_in_separate_records_become_one_decision(tmp_path):
    """The trap: one API response is written as several JSONL records.

    Grouping by ``message.id`` is the only way to see a reasoning preamble
    attached to the action it preceded.
    """
    path = _write(
        tmp_path,
        [
            _user("find the bug"),
            _assistant_block(
                "msg_1", {"type": "text", "text": "Let me check the tests."}
            ),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "pytest"},
                },
            ),
            _result("t1", "2 passed"),
        ],
    )
    scan = scan_transcript(path)

    assert len(scan.decisions) == 1, "one API response must be one decision point"
    point = scan.decisions[0]
    assert point.reasoning_text.strip() == "Let me check the tests."
    assert [c.tool for c in point.calls] == ["Bash"]


def test_parallel_tools_are_one_decision_not_two(tmp_path):
    """Width is a choice. Splitting a 2-wide response makes it unmeasurable."""
    path = _write(
        tmp_path,
        [
            _user("check both"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"file_path": "a.py"},
                },
            ),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Read",
                    "input": {"file_path": "b.py"},
                },
            ),
            _result("t1", "aaa"),
            _result("t2", "bbb"),
        ],
    )
    scan = scan_transcript(path)
    assert len(scan.decisions) == 1
    assert scan.decisions[0].width == 2


def test_encrypted_thinking_is_flagged_not_treated_as_absent(tmp_path):
    """A stripped thinking block means the model reasoned and we cannot see it.

    That is different from emitting no reasoning, and conflating the two would
    misreport how much of the corpus supports reasoning analysis.
    """
    path = _write(
        tmp_path,
        [
            _user("go"),
            _assistant_block(
                "msg_1", {"type": "thinking", "thinking": "", "signature": "abc"}
            ),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ),
            _result("t1", "out"),
        ],
    )
    point = scan_transcript(path).decisions[0]
    assert point.had_thinking_block is True
    assert point.reasoning_text.strip() == ""


def test_meta_turns_do_not_start_an_episode(tmp_path):
    """Hook feedback arrives as a ``user`` record and is not human intent.

    Counting it inflated a 'user corrected the model' metric 8x upstream; here
    it would fragment episodes and destroy the depth axis.
    """
    path = _write(
        tmp_path,
        [
            _user("do the thing"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ),
            _result("t1", "ok"),
            _user("<system-reminder>stop</system-reminder>", isMeta=True),
            _assistant_block(
                "msg_2",
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                },
            ),
            _result("t2", "ok"),
        ],
    )
    scan = scan_transcript(path)
    assert len(scan.episode_prompts) == 1
    assert [d.episode_index for d in scan.decisions] == [0, 0]
    assert [d.depth_index for d in scan.decisions] == [0, 1]


def test_depth_resets_on_a_real_human_turn(tmp_path):
    path = _write(
        tmp_path,
        [
            _user("first"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ),
            _result("t1", "ok"),
            _user("second"),
            _assistant_block(
                "msg_2",
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                },
            ),
            _result("t2", "ok"),
        ],
    )
    scan = scan_transcript(path)
    assert [d.episode_index for d in scan.decisions] == [0, 1]
    assert [d.depth_index for d in scan.decisions] == [0, 0]


def test_file_content_is_recovered_from_tool_use_result(tmp_path):
    """The transcript holds the exact bytes the agent saw — better than git."""
    path = _write(
        tmp_path,
        [
            _user("read it"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Read",
                    "input": {"file_path": "a.py"},
                },
            ),
            _result(
                "t1",
                "1: import os",
                tool_use_result={
                    "type": "text",
                    "file": {"filePath": "/repo/a.py", "content": "import os\n"},
                },
            ),
        ],
    )
    obs = scan_transcript(path).decisions[0].observations[0]
    assert obs.file_content == "import os\n"
    assert obs.file_path == "/repo/a.py"


def test_missing_original_file_is_not_an_empty_file(tmp_path):
    """Claude Code nulls originalFile above ~10 KB; absence is a size effect."""
    path = _write(
        tmp_path,
        [
            _user("edit"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Edit",
                    "input": {
                        "file_path": "a.py",
                        "old_string": "x",
                        "new_string": "y",
                    },
                },
            ),
            _result(
                "t1",
                "edited",
                tool_use_result={
                    "filePath": "/repo/a.py",
                    "originalFile": None,
                    "structuredPatch": [{"lines": ["-x", "+y"]}],
                },
            ),
        ],
    )
    obs = scan_transcript(path).decisions[0].observations[0]
    assert obs.original_file == ""
    assert obs.structured_patch == [{"lines": ["-x", "+y"]}]


def test_errors_are_classified_specific_before_generic(tmp_path):
    """A timeout also carries a non-zero exit code; order decides which wins."""
    path = _write(
        tmp_path,
        [
            _user("run"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "sleep 999"},
                },
            ),
            _result("t1", "Command timed out after 120s; exit code 143", is_error=True),
        ],
    )
    point = scan_transcript(path).decisions[0]
    assert point.observations[0].error_class == "timeout"
    assert point.reference_quality == "errored"


def test_interrupt_inside_a_result_counts_as_failure(tmp_path):
    path = _write(
        tmp_path,
        [
            _user("run"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "x"},
                },
            ),
            _result("t1", "[Request interrupted by user]"),
        ],
    )
    assert scan_transcript(path).decisions[0].reference_quality == "errored"


def test_unresolved_call_is_not_success(tmp_path):
    """No result ever arrived. Unknown is not success and must not be counted so."""
    path = _write(
        tmp_path,
        [
            _user("run"),
            _assistant_block(
                "msg_1",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "x"},
                },
            ),
        ],
    )
    assert scan_transcript(path).decisions[0].reference_quality == "unresolved"


def test_session_with_no_tool_calls_yields_nothing(tmp_path):
    path = _write(
        tmp_path,
        [_user("hi"), _assistant_block("msg_1", {"type": "text", "text": "hello"})],
    )
    assert scan_transcript(path) is None


def test_malformed_lines_are_counted_not_swallowed(tmp_path):
    project = tmp_path / "C--Users-jdoe-Work-proj"
    project.mkdir(parents=True)
    path = project / "sess.jsonl"
    good = [
        _user("go"),
        _assistant_block(
            "msg_1",
            {
                "type": "tool_use",
                "id": "t1",
                "name": "Bash",
                "input": {"command": "ls"},
            },
        ),
        _result("t1", "ok"),
    ]
    path.write_text(
        "\n".join(json.dumps(r) for r in good) + "\n{not json}\n", encoding="utf-8"
    )
    scan = scan_transcript(path)
    assert scan.skipped_lines == 1
    assert len(scan.decisions) == 1


class TestShellSegments:
    """§6c: one tool call is not one step — but it is still one decision."""

    def test_sequential_separators_split(self):
        segs = split_shell_segments("cd /repo && pytest -q; echo done")
        assert [s["leader"] for s in segs] == ["cd", "pytest", "echo"]

    def test_scaffolding_is_labelled(self):
        segs = split_shell_segments("cd /repo && pytest -q")
        assert segs[0]["kind"] == "scaffolding"
        assert segs[1]["kind"] == "substantive"

    def test_pipes_are_not_split(self):
        """One dependent dataflow producing one answer, not two actions."""
        segs = split_shell_segments("grep -rn foo src | head -50")
        assert len(segs) == 1

    def test_empty_command(self):
        assert split_shell_segments("") == []

    def test_env_assignment_yields_the_variable_not_its_value(self):
        """A leaked path hid here: the leader fed an unscrubbed binary vocabulary.

        ``WT="C:/Users/someone/repo" && ls`` has no binary called
        ``WT="C:/Users/someone/repo"``, and treating it as one put an absolute
        path somewhere nothing downstream cleaned.
        """
        segs = split_shell_segments('WT="C:/Users/someone/repo" && ls')
        assert segs[0]["leader"] == "WT"
        assert segs[0]["kind"] == "scaffolding"
        assert "Users" not in segs[0]["leader"]

    def test_leader_is_length_capped(self):
        segs = split_shell_segments("./" + "a" * 400)
        assert len(segs[0]["leader"]) <= 80


def test_interleaved_messages_get_distinct_step_indices(tmp_path):
    """Two decision points must never share a step_index.

    Claude Code writes one record per content block, so a message can emit its
    text early and its ``tool_use`` after another message's records. Capturing
    the index at message-creation time gave both points the same step_index,
    which made a record's own action appear inside its `recent_steps`.
    """
    path = _write(
        tmp_path,
        [
            _user("go"),
            # msg_A opens with text only — its tool_use arrives last.
            _assistant_block("msg_A", {"type": "text", "text": "Checking."}),
            _assistant_block("msg_B", {"type": "text", "text": "Also checking."}),
            _assistant_block(
                "msg_B",
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                },
            ),
            _assistant_block(
                "msg_A",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ),
            _result("t1", "ok"),
            _result("t2", "ok"),
        ],
    )
    scan = scan_transcript(path)
    steps = [d.step_index for d in scan.decisions]
    assert steps == sorted(steps), "decisions must be ordered by step_index"
    assert len(set(steps)) == len(steps), f"duplicate step_index: {steps}"
    assert [d.depth_index for d in scan.decisions] == [0, 1]


def test_index_follows_first_tool_use_not_message_creation(tmp_path):
    """The point that dispatches first is step 0, whatever order text arrived."""
    path = _write(
        tmp_path,
        [
            _user("go"),
            _assistant_block("msg_A", {"type": "text", "text": "first to speak"}),
            _assistant_block(
                "msg_B",
                {
                    "type": "tool_use",
                    "id": "t2",
                    "name": "Read",
                    "input": {"file_path": "b"},
                },
            ),
            _assistant_block(
                "msg_A",
                {
                    "type": "tool_use",
                    "id": "t1",
                    "name": "Bash",
                    "input": {"command": "ls"},
                },
            ),
            _result("t1", "ok"),
            _result("t2", "ok"),
        ],
    )
    by_id = {d.message_id: d for d in scan_transcript(path).decisions}
    assert by_id["msg_B"].step_index == 0
    assert by_id["msg_A"].step_index == 1


class TestScriptBodies:
    """Inline script bodies are not shell, and their lines are not binaries.

    15% of shell commands in the corpus carry an inline script. Split on
    ``;``/newline as though it were shell, a ``python -c`` body yields
    "binaries" like ``open(p`` and ``encoding="utf-8`` — which then made
    well-formed commands look like they invoked programs that do not exist.
    """

    def test_heredoc_body_is_not_shell(self):
        segs = split_shell_segments(
            "python - <<'EOF'\nimport os\nprint(os.getcwd())\nEOF"
        )
        assert [s["leader"] for s in segs if s["kind"] == "substantive"] == ["python"]
        assert all(s["kind"] == "script_body" for s in segs[1:])

    def test_quoted_inline_script_body_is_not_shell(self):
        segs = split_shell_segments(
            'python -c "import json; print(json.dumps({}))" && ls'
        )
        assert [s["leader"] for s in segs if s["kind"] == "substantive"] == [
            "python",
            "ls",
        ]

    def test_shell_resumes_after_the_body_closes(self):
        """The quote tracker must reopen the shell, or everything after is body."""
        segs = split_shell_segments('python -c "a; b" && pytest -q')
        assert segs[-1]["leader"] == "pytest"
        assert segs[-1]["kind"] == "substantive"

    def test_loop_keywords_are_control_not_binaries(self):
        segs = split_shell_segments(
            "for r in 1 2; do echo $r; done && grep -rn foo src"
        )
        assert [s["leader"] for s in segs if s["kind"] == "substantive"] == ["grep"]

    def test_comment_segment_is_control(self):
        segs = split_shell_segments("# explain the next bit\nls -la")
        assert segs[0]["kind"] == "control"

    def test_unexecutable_token_is_not_a_binary(self):
        """A stray fragment the quote tracker missed must not become a binary."""
        segs = split_shell_segments("rows.append((p, 1))")
        assert segs[0]["kind"] == "script_body"
