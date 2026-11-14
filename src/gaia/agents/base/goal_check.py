# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Goal mode checker: does the goal hold after this round?

Three tiers, cheapest and most trustworthy first:

1. The goal's own check commands. A non-zero exit is "not met", whatever any
   model says.
2. The output files the goal names. One that doesn't exist is "not met".
3. An evaluator call on the agent's own model, in a fresh context, that sees
   tool results and labels the answer as claims. It can only say "met" with a
   quote that really appears in this round's tool results or check output.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.agents.base.completion import named_outputs
from gaia.agents.base.goal_run import GoalLedger, GoalSpec, GoalVerdict
from gaia.utils.process_tree import kill_tree, popen_kwargs

CHECK_TIMEOUT_S = 600
#: Characters of one tool result the evaluator sees: its start and its end,
#: where test runners and builds print their verdict.
_RESULT_HEAD_CHARS = 500
_RESULT_TAIL_CHARS = 1000
_DIGEST_CHARS = 16000
_EARLIER_EVIDENCE_CHARS = 6000
_OUTPUT_TAIL_CHARS = 2000
_ANSWER_CHARS = 3000
#: Shortest quote accepted as evidence; shorter ones match by accident.
_MIN_QUOTE_CHARS = 8
EVALUATOR_MAX_TOKENS = 4096

EVALUATOR_SYSTEM_PROMPT = """You check whether a goal has been reached.

You are given the goal, evidence proven in earlier rounds, the results of this
round's tool calls, the output of any check commands, and the agent's final
answer. The final answer is only a claim: the agent may say it did or verified
things it never did. Tool arguments are what the agent sent, not results.
Judge only from evidence, tool results and check output. Evidence from earlier
rounds still counts unless this round's work could have changed it.

Reply with JSON:
- met: true only if the evidence, tool results or check output show every part
  of the goal holds now.
- missing: what still has to be done or shown, as short concrete items. Empty
  only when met.
- evidence: short quotes copied exactly, character for character, from this
  round's tool results or check output, one for each part of the goal they
  show holds. Give them even when the goal is not met yet: they are kept for
  later rounds. When met, at least one must come from this round.
- blocked: true only if the work cannot continue without the user, for
  example a file that does not exist, a denied permission or a sign-in.
- question: when blocked, the one question the user must answer. Otherwise
  empty."""


class GoalCheckError(RuntimeError):
    """The goal could not be checked; the run stops rather than guess."""


@dataclass
class CheckResult:
    command: str
    exit_code: int
    output_tail: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


def run_checks(
    checks: Sequence[str], cwd: str, timeout: float = CHECK_TIMEOUT_S
) -> List[CheckResult]:
    """Run each check command the user gave, in *cwd*, and keep its exit code.

    Output goes to a file and stdin is closed, so a grandchild holding the
    output or a command waiting for input can't outlast the timeout, which
    kills the whole process tree.
    """
    results = []
    for command in checks:
        with tempfile.TemporaryFile() as output:
            # The user's own command line, as they typed it.
            proc = subprocess.Popen(  # pylint: disable=consider-using-with
                command,
                shell=True,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                **popen_kwargs(),
            )
            try:
                exit_code = proc.wait(timeout=timeout)
                note = ""
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                exit_code = -1
                note = f"timed out after {timeout:.0f} s\n"
            output.seek(0)
            text = output.read().decode("utf-8", errors="replace")
        results.append(
            CheckResult(command, exit_code, note + text[-_OUTPUT_TAIL_CHARS:])
        )
    return results


def missing_outputs(condition: str, cwd: str) -> List[str]:
    """Output files the goal names that don't exist on disk."""
    missing = []
    for path in named_outputs(condition):
        full = os.path.join(cwd, os.path.expandvars(os.path.expanduser(path)))
        if not os.path.exists(full):
            missing.append(path)
    return missing


def tool_results(conversation: Sequence[Any]) -> List[Tuple[str, Any, str]]:
    """``(tool name, arguments, result text)`` for each tool call this round."""
    found = []
    for message in conversation or []:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        found.append((message.get("name") or "tool", message.get("tool_args"), content))
    return found


def tool_digest(results: Sequence[Tuple[str, Any, str]]) -> List[str]:
    """What the evaluator sees of each result, newest kept when they don't all fit."""
    entries = []
    for name, args, content in results:
        header = f"[{name}]"
        if args:
            header += (
                " args: " + json.dumps(args, ensure_ascii=False, default=str)[:300]
            )
        entries.append(f"{header}\nresult:\n{_clip(content)}")
    return _newest_within(entries, _DIGEST_CHARS)


def _clip(text: str) -> str:
    if len(text) <= _RESULT_HEAD_CHARS + _RESULT_TAIL_CHARS:
        return text
    return f"{text[:_RESULT_HEAD_CHARS]}\n…\n{text[-_RESULT_TAIL_CHARS:]}"


def _newest_within(items: Sequence[str], limit: int) -> List[str]:
    """The newest *items* whose total length fits *limit*, oldest first."""
    kept: List[str] = []
    total = 0
    for item in reversed(items):
        if total + len(item) > limit:
            break
        kept.append(item)
        total += len(item)
    return list(reversed(kept))


def evaluator_response_format() -> Dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "goal_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "met": {"type": "boolean"},
                    "missing": {"type": "array", "items": {"type": "string"}},
                    "evidence": {"type": "array", "items": {"type": "string"}},
                    "blocked": {"type": "boolean"},
                    "question": {"type": "string"},
                },
                "required": ["met", "missing", "evidence", "blocked", "question"],
                "additionalProperties": False,
            },
        },
    }


def evaluator_prompt(
    spec: GoalSpec,
    ledger: GoalLedger,
    digest: Sequence[str],
    checks: Sequence[CheckResult],
    answer: str,
) -> str:
    parts = [f"GOAL:\n{spec.condition.strip()}"]
    if ledger.written:
        parts.append("FILES WRITTEN IN EARLIER ROUNDS:\n" + "\n".join(ledger.written))
    earlier = _newest_within(ledger.cited(), _EARLIER_EVIDENCE_CHARS)
    if earlier:
        parts.append(
            "EVIDENCE PROVEN IN EARLIER ROUNDS:\n"
            + "\n".join(f"- {q}" for q in earlier)
        )
    if checks:
        parts.append(
            "CHECK COMMANDS:\n"
            + "\n\n".join(
                f"$ {c.command}\nexit code {c.exit_code}\n{c.output_tail}"
                for c in checks
            )
        )
    parts.append(
        "TOOL RESULTS THIS ROUND:\n"
        + ("\n\n".join(digest) if digest else "(no tool calls this round)")
    )
    parts.append(
        "AGENT'S FINAL ANSWER (claims, not evidence):\n"
        + (answer or "")[:_ANSWER_CHARS]
    )
    return "\n\n".join(parts)


def parse_verdict(
    text: str, sources: Sequence[str], earlier: Sequence[str]
) -> GoalVerdict:
    """Read the evaluator's JSON, keeping only quotes found in real output.

    *sources* are this round's tool results and check output; *earlier* are
    quotes proven in earlier rounds. A "met" needs a quote from *sources*:
    with none, the evaluator claimed the goal holds without showing where the
    tools say so now, and the verdict becomes not met.
    """
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, TypeError) as exc:
        raise GoalCheckError(
            f"The goal evaluator's reply was not JSON ({exc}): {str(text)[:200]!r}"
        ) from exc
    if not isinstance(data, dict):
        raise GoalCheckError(f"The goal evaluator replied with {type(data).__name__}.")
    current = [_squash(s) for s in sources]
    proven = [_squash(q) for q in earlier]
    quotes = []
    fresh = False
    for quote in data.get("evidence") or []:
        if not isinstance(quote, str):
            continue
        needle = _squash(quote)
        if len(needle) < _MIN_QUOTE_CHARS:
            continue
        now = any(needle in h for h in current)
        if now or any(needle in h for h in proven):
            quotes.append(quote.strip())
            fresh = fresh or now
    missing = [str(m).strip() for m in data.get("missing") or [] if str(m).strip()]
    met = bool(data.get("met"))
    if met and not fresh:
        met = False
        missing.append(
            "Show the goal holds now: no tool result or check output from this "
            "round confirms it."
        )
    blocked = bool(data.get("blocked")) and not met
    return GoalVerdict(
        met=met,
        missing=[] if met else missing,
        evidence=quotes,
        blocked=blocked,
        question=str(data.get("question") or "").strip() if blocked else "",
        decided_by="evaluator",
    )


def check_goal(
    spec: GoalSpec,
    ledger: GoalLedger,
    result: Dict[str, Any],
    chat: Any,
    cwd: str,
    sampling: Optional[Dict[str, Any]] = None,
) -> GoalVerdict:
    """Check the goal after one round. *chat* is a temperature-0 AgentSDK."""
    checks = run_checks(spec.checks, cwd)
    failed = [c for c in checks if not c.passed]
    if failed:
        return GoalVerdict(
            met=False,
            missing=[
                f"`{c.command}` fails (exit {c.exit_code}): "
                f"{_last_line(c.output_tail)}"
                for c in failed
            ],
            decided_by="check",
        )
    absent = missing_outputs(spec.condition, cwd)
    if absent:
        return GoalVerdict(
            met=False,
            missing=[f"{path} does not exist yet" for path in absent],
            checks_passed=len(checks),
            decided_by="evidence",
        )

    results = tool_results(result.get("conversation") or [])
    digest = tool_digest(results)
    prompt = evaluator_prompt(spec, ledger, digest, checks, result.get("result") or "")
    try:
        response = chat.send_messages(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=EVALUATOR_SYSTEM_PROMPT,
            tools=[],
            response_format=evaluator_response_format(),
            max_tokens=EVALUATOR_MAX_TOKENS,
            **(sampling or {}),
        )
    except Exception as exc:  # noqa: BLE001 - re-raised with context
        raise GoalCheckError(f"The goal evaluator call failed: {exc}") from exc
    if getattr(response, "finish_reason", None) == "length":
        raise GoalCheckError(
            f"The goal evaluator used its whole {EVALUATOR_MAX_TOKENS}-token reply "
            "budget without finishing its verdict."
        )
    # Results only: arguments are text the agent wrote, not proof.
    sources = [content for _, _, content in results]
    sources += [c.output_tail for c in checks]
    verdict = parse_verdict(getattr(response, "text", ""), sources, ledger.cited())
    verdict.checks_passed = len(checks)
    return verdict


def _strip_fences(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _squash(text: str) -> str:
    return " ".join(str(text).split()).lower()


def _last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1][:200] if lines else "no output"
