# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Recover the auto-fix outcome Claude stated in its last reply but didn't save.

Usage: autofix_outcome.py EXEC_FILE OUTCOME_FILE SUMMARY_FILE

Reads the claude-code-action transcript (EXEC_FILE). When a reply carries an
``Outcome: A|B|C|FAILED`` line, writes it and the sentence after it to OUTCOME_FILE.
Otherwise appends the last reply to SUMMARY_FILE so a maintainer can see why the run
stopped, and writes nothing: the workflow step then fails. Model text is untrusted,
so it goes only to files, never to stdout, where a line could act as a workflow command.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

OUTCOME_LINE = re.compile(r"^[\s>*_`#-]*Outcome:\s*(A|B|C|FAILED)[\s*_`.]*$")
MARKUP = re.compile(r"^[\s>*_`#-]+|[\s*_`]+$")
LAST_REPLY_CHARS = 2000


def _messages(raw: str) -> list:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Some action versions write one JSON message per line.
        data = [json.loads(line) for line in raw.splitlines() if line.strip()]
    return data if isinstance(data, list) else [data]


def replies(raw: str) -> list[str]:
    """Every text Claude wrote, oldest first: assistant text blocks and the final result."""
    texts = []
    for msg in _messages(raw):
        if not isinstance(msg, dict):
            continue
        if msg.get("type") == "result" and isinstance(msg.get("result"), str):
            texts.append(msg["result"])
        elif msg.get("type") == "assistant":
            content = (msg.get("message") or {}).get("content") or []
            for block in content if isinstance(content, list) else []:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(block.get("text") or "")
    return [t for t in texts if t.strip()]


def stated_outcome(texts: list[str]) -> str | None:
    """The newest ``Outcome:`` line and the reason after it, as the outcome file reads."""
    for text in reversed(texts):
        lines = text.splitlines()
        for i in range(len(lines) - 1, -1, -1):
            m = OUTCOME_LINE.match(lines[i])
            if not m:
                continue
            reason = next(
                (MARKUP.sub("", ln) for ln in lines[i + 1 :] if MARKUP.sub("", ln)),
                "Claude stated this outcome in its reply but gave no reason.",
            )
            return f"Outcome: {m.group(1)}\n{reason}\n"
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    exec_file, outcome_file, summary_file = (Path(a) for a in argv[1:])
    try:
        texts = replies(exec_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"Could not read the transcript {exec_file}: {e}", file=sys.stderr)
        return 0
    outcome = stated_outcome(texts)
    if outcome:
        outcome_file.write_text(outcome, encoding="utf-8")
        return 0
    last = texts[-1][-LAST_REPLY_CHARS:] if texts else "(Claude wrote no reply.)"
    with summary_file.open("a", encoding="utf-8") as f:
        f.write("## Auto-fix stopped without an outcome\n\nClaude's last reply:\n\n")
        f.write("".join(f"> {ln}\n" for ln in last.splitlines()) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
