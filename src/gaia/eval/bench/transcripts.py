# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read either harness's transcript the same way: tool calls, test runs, web use.

A GAIA transcript carries ``conversation`` (the agent's own record); a Claude
Code one carries ``events`` (its stream-json output). Everything that grades
or reports a run reads calls through :func:`tool_calls`, so both harnesses are
judged from the same evidence. Reading only GAIA's shape once told the judge
that no Claude Code attempt had run a single test.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

Call = Tuple[str, Dict[str, Any], Any]

RECORD_CAP = 14000
RESULT_CAP = 700
ARGS_CAP = 300

#: Claude Code's tools that write a file.
CC_EDIT_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
CC_SHELL_TOOLS = frozenset({"Bash"})


def clip(text: Any, cap: int) -> str:
    """Head and tail of *text*, with the cut marked."""
    text = text if isinstance(text, str) else json.dumps(text, default=str)
    if len(text) <= cap:
        return text
    head = cap * 2 // 3
    return f"{text[:head]}\n...[{len(text) - cap} chars cut]...\n{text[-(cap - head):]}"


def is_claude_code(transcript: Mapping[str, Any]) -> bool:
    return transcript.get("events") is not None


def gaia_calls(transcript: Mapping[str, Any]) -> Iterator[Call]:
    """Every GAIA tool call with its result.

    A step may carry several calls (``tool_calls``); their results follow as
    consecutive ``tool`` entries. A partial record from a timed-out run holds
    only ``tool`` entries, which are read on their own.
    """
    conv = list(transcript.get("conversation") or [])
    i = 0
    while i < len(conv):
        entry = conv[i]
        content = entry.get("content")
        if entry.get("role") == "tool" and (
            i == 0 or conv[i - 1].get("role") != "assistant"
        ):
            yield str(entry.get("name") or ""), dict(
                entry.get("tool_args") or {}
            ), content
            i += 1
            continue
        if entry.get("role") != "assistant" or not isinstance(content, dict):
            i += 1
            continue
        calls = content.get("tool_calls")
        if isinstance(calls, list) and calls:
            j = i + 1
            results = []
            while j < len(conv) and conv[j].get("role") == "tool":
                results.append(conv[j])
                j += 1
            for call in calls:
                name, args = call.get("name"), call.get("tool_args") or {}
                match = next(
                    (
                        r
                        for r in results
                        if r.get("name") == name and (r.get("tool_args") or {}) == args
                    ),
                    None,
                )
                if match is None and results:
                    match = results[0]
                if match is not None:
                    results.remove(match)
                yield str(name or ""), dict(args), (match or {}).get("content")
            i = j
            continue
        if content.get("tool"):
            result = conv[i + 1].get("content") if i + 1 < len(conv) else None
            yield str(content["tool"]), dict(content.get("tool_args") or {}), result
        i += 1


def cc_calls(transcript: Mapping[str, Any]) -> Iterator[Call]:
    """Every Claude Code tool call with its result, from the stream-json events."""
    results: Dict[str, Dict[str, Any]] = {}
    events = transcript.get("events") or []
    for event in events:
        if event.get("type") == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    results[str(block.get("tool_use_id"))] = block
    for event in events:
        if event.get("type") != "assistant":
            continue
        for block in (event.get("message") or {}).get("content") or []:
            if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                continue
            result = results.get(str(block.get("id")))
            body: Any = None
            if result:
                body = result.get("content")
                if isinstance(body, list):
                    body = "\n".join(
                        str(b.get("text", b)) for b in body if isinstance(b, dict)
                    )
            yield (
                str(block.get("name") or ""),
                dict(block.get("input") or {}),
                (
                    {"is_error": bool(result.get("is_error")), "output": body}
                    if result
                    else None
                ),
            )


def tool_calls(transcript: Mapping[str, Any]) -> List[Call]:
    reader = cc_calls if is_claude_code(transcript) else gaia_calls
    return list(reader(transcript))


def _render_result(result: Any) -> str:
    if result is None:
        return "(no result recorded)"
    if isinstance(result, dict):
        parts = [
            f"{k}={result[k]}"
            for k in ("status", "return_code", "is_error", "executed")
            if k in result
        ]
        body = "\n".join(
            str(result[k])
            for k in (
                "error",
                "stdout",
                "stderr",
                "output",
                "content",
                "message",
                "result",
            )
            if result.get(k)
        )
        if not body:
            body = json.dumps(
                {k: v for k, v in result.items() if k != "status"}, default=str
            )
        return (" ".join(parts) + "\n" + clip(body, RESULT_CAP)).strip()
    return clip(str(result), RESULT_CAP)


def tool_record(transcript: Mapping[str, Any]) -> str:
    """Every call and what it returned, cut to fit, for a judge with no tools."""
    calls = tool_calls(transcript)
    if not calls:
        return "(no tool calls)"
    lines = [
        f"[{n}] {name}({clip(json.dumps(args, default=str), ARGS_CAP)})\n"
        f"    -> {_render_result(result)}".replace("\n", "\n       ")
        for n, (name, args, result) in enumerate(calls, 1)
    ]
    return clip("\n".join(lines), RECORD_CAP)


#: A test runner's closing summary. pytest-subtests puts a word between the
#: count and the outcome ("19 subtests passed").
_TEST_SUMMARY = re.compile(
    r"(?m)^=*[ \t]*(?:\d+ (?:subtests? )?"
    r"(?:passed|failed|error|errors|skipped|deselected|xfailed|xpassed|warning|warnings)"
    r"(?:, )?)+ in \d+(?:\.\d+)?s(?: \(.*\))?[ \t]*=*[ \t]*$"
)
_UNITTEST = re.compile(r"Ran (\d+) tests? in [\d.]+s\s*\n+\s*(OK[^\n]*|FAILED[^\n]*)")
_FAILED = re.compile(r"\b[1-9]\d* (?:failed|errors?)\b")


def _output(result: Any) -> str:
    if isinstance(result, dict):
        return "\n".join(
            str(result.get(key) or "")
            for key in ("stdout", "stderr", "content", "output")
        )
    return str(result or "")


def check_runs(transcript: Mapping[str, Any]) -> List[Tuple[int, str, bool]]:
    """``(call index, summary, passed)`` for every test run found in a tool result."""
    runs = []
    for index, (name, _args, result) in enumerate(tool_calls(transcript)):
        # The tool's own record beats its output, which the model sees trimmed.
        check = result.get("check_result") if isinstance(result, dict) else None
        if isinstance(check, dict) and check.get("summary"):
            runs.append(
                (index, f"{name}: {check['summary']}", bool(check.get("passed")))
            )
            continue
        output = _output(result)
        for match in _TEST_SUMMARY.finditer(output):
            line = " ".join(match.group(0).split())
            runs.append((index, f"{name}: {line}", not _FAILED.search(line)))
        for match in _UNITTEST.finditer(output):
            runs.append(
                (
                    index,
                    f"{name}: Ran {match.group(1)} tests - {match.group(2).strip()}",
                    match.group(2).startswith("OK"),
                )
            )
    return runs


def checks_actually_run(transcript: Mapping[str, Any]) -> str:
    """Test-runner summaries read from the tool results, as fact for the judge.

    The agent's own "Verification:" footer is a claim, not evidence; a harness
    bug in that footer once reported a verified run as unverified, and the judge
    scored a truthful agent 1/5 for fabricating.
    """
    runs = check_runs(transcript)
    if not runs:
        return "No test-runner summary appears in any tool result."
    return "\n".join(
        f"- {summary} ({'passed' if ok else 'did not pass'})" for _, summary, ok in runs
    )


def cc_tests_verified(transcript: Mapping[str, Any]) -> bool:
    """A Claude Code test run passed after its last file edit."""
    calls = tool_calls(transcript)
    last_edit = max(
        (
            i
            for i, (name, _a, result) in enumerate(calls)
            if name in CC_EDIT_TOOLS and not (result or {}).get("is_error")
        ),
        default=-1,
    )
    return any(ok for index, _s, ok in check_runs(transcript) if index > last_edit)


# ---------------------------------------------------------------------------
# Web use: breaches of a "no internet" instruction, read from the tool record
# ---------------------------------------------------------------------------

#: Tools whose only job is reaching the web, in either harness.
WEB_TOOLS = frozenset(
    {
        "WebSearch",
        "WebFetch",
        "search_web",
        "fetch_page",
        "browse_url",
        "download_file",
        "web_search",
        "fetch_url",
    }
)
_URL = re.compile(r"https?://([^/\s\"'`]+)", re.I)
_LOCAL = re.compile(r"^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(:\d+)?$", re.I)
#: Fetchers: a breach unless every URL they are given is on this machine.
_FETCH_CMD = re.compile(r"(?:^|[\s;&|(`\"'])(?:curl|wget)\b", re.I)
#: Commands that reach a remote with no URL on the line. ``gh`` is the offline
#: stand-in in a task, but calling it still means looking online.
_REMOTE_CMD = re.compile(
    r"(?:^|[\s;&|(`\"'])(?:"
    r"gh\s+(?:api|pr|issue|repo|search|release|run)\b|"
    r"git\s+(?:fetch|pull|clone|ls-remote)\b|"
    r"pip3?\s+install\b|uv\s+pip\s+install\b|npm\s+(?:install|view)\b"
    r")",
    re.I,
)


def _command_text(args: Any) -> str:
    if not isinstance(args, dict):
        return str(args or "")
    return " ".join(
        str(args.get(k) or "")
        for k in ("command", "code", "cmd", "script", "url", "query")
    )


def web_uses(transcript: Mapping[str, Any]) -> List[str]:
    """One line per call that reached, or tried to reach, the internet."""
    found = []
    for name, args, _result in tool_calls(transcript):
        if name in WEB_TOOLS:
            found.append(f"{name}: {json.dumps(args, default=str)[:120]}")
            continue
        text = _command_text(args)
        hosts = _URL.findall(text)
        remote = [h for h in hosts if not _LOCAL.match(h)]
        fetch_elsewhere = _FETCH_CMD.search(text) and not hosts
        if remote or fetch_elsewhere or _REMOTE_CMD.search(text):
            found.append(f"{name}: {text.strip()[:120]}")
    return found


def cc_stats(transcript: Mapping[str, Any]) -> Dict[str, Optional[int]]:
    """Turns and tool calls from Claude Code events, also for a cut-off run."""
    events = transcript.get("events") or []
    return {
        "turns": sum(1 for e in events if e.get("type") == "assistant"),
        "tool_calls": len(tool_calls(transcript)),
    }
