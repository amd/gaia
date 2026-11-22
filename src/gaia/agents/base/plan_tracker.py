# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A plan the model writes once and the agent loop ticks.

The model opens its reply to a multi-step request with ``Plan:`` and a
numbered list. From then on nothing here asks the model anything: a step is
marked done, failed or left open from the results of the tool calls alone.

Invariant: a step that names a file or web address, or describes a change, is
never marked done without a successful tool call of this request behind it.
"""

from __future__ import annotations

import os
import re
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
)
from urllib.parse import urlparse

THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"

OPEN = "open"
DONE = "done"
FAILED = "failed"

MIN_STEPS = 3
MAX_STEPS = 12
MAX_STEP_CHARS = 120
#: A line longer than this is never a plan heading or step, and is not scanned.
_MAX_LINE = 400
#: A whole plan on one line is longer than any heading or step line.
_MAX_INLINE = 2000

PLAN_INSTRUCTION = (
    "==== PLANS ====\n"
    "Every time the user sends a request that needs three or more steps, begin "
    "your reply to that request with the line 'Plan:' and a short numbered list "
    "of every step in plain words, naming the file or page each step works on. "
    "Make your first tool call in that same reply. This applies to each new "
    "request in a conversation, not only the first. Write no plan for simpler "
    "requests. Never repeat or update a plan."
)
PLAN_REMINDER = (
    "[Agent note] The request you are working on now has several steps and has "
    "no plan yet. Start your next reply with the line 'Plan:' and a short "
    "numbered list of every step of this request in plain words, then continue "
    "with your next tool call in that same reply."
)
SEND_BACK = (
    "[Agent note] These steps of your plan are not done yet:\n{steps}\n"
    "Carry them out now with your tools. If one is not needed, say so. Then "
    "give your complete answer again: it replaces the one you just gave."
)
#: Sent with each request, where a model attends more than to its system prompt.
PLAN_HINT = (
    "\n\n(If this takes three or more steps, open your reply with the Plan: list.)"
)
_ASKS_FOR_PLAN = re.compile(r"\bplan(?:s|ning)?\b", re.I)
#: Tool calls into a request, with no plan, before the one reminder.
REMIND_AFTER_CALLS = 2
#: Bookkeeping tools: they neither count toward the reminder nor tick a step.
IGNORED_TOOLS = frozenset({"update_tasks", "load_tools"})
#: Arguments that carry what is written, not what is worked on.
CONTENT_ARGS = frozenset(
    {"content", "new_content", "old_content", "text", "body", "code", "data"}
)

_PLAN_HEAD = re.compile(r"^\W{0,8}plan\W{0,8}$", re.I)
_INLINE_HEAD = re.compile(r"^\W{0,8}plan\W{0,3}:\W{0,3}(?=1[.)][ \t])", re.I)
_INLINE_NUMBER = re.compile(r"(?:^|(?<=\s))(\d{1,2})[.)][ \t]+")
_NUMBERED = re.compile(r"^ {0,3}\d{1,2}[.)][ \t]+(\S.*?)\s*$")
_BULLET = re.compile(r"^ ?[-*•][ \t]+(\S.*?)\s*$")
_STATUS_SUFFIX = re.compile(
    r"\s*\((?:done|next|completed|complete|in progress|pending|current)\)\s*$", re.I
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_FILE = re.compile(r"\w[\w-]*(?:\.[\w-]+)*\.[A-Za-z]{2,5}\b")
_URL = re.compile(r"(?:https?://)?((?:[\w-]+\.)+[a-z]{2,8})(/[\w./-]*)?", re.I)
_CHANGE_WORDS = (
    "write|save|update|fix|create|add|append|edit|modify|change|replace|delete|"
    "remove|rename|move|correct|adjust|rewrite|overwrite|patch|insert|store|record"
)
#: A change step with one of these also reads something it names.
_READ_WORDS = re.compile(
    r"\b(?:read|open|check|compare|review|look|scan|search|find|from|using|based on)\b",
    re.I,
)


def plans_enabled() -> bool:
    """Whether the agent asks for a plan and ticks it. On by default.

    ``GAIA_AGENT_PLANS=0`` turns it off. Read at call time, like the other
    ``GAIA_AGENT_*`` knobs.
    """
    raw = os.environ.get("GAIA_AGENT_PLANS")
    if raw is None or raw == "":
        return True
    normalized = raw.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(
        f"GAIA_AGENT_PLANS must be a boolean (1/0, true/false, on/off), "
        f"got {raw!r}. Unset it to keep plans on."
    )


def asks_for_plan(request: str) -> bool:
    """Whether the user's own words ask for a plan, which is then theirs to read."""
    return bool(_ASKS_FOR_PLAN.search(request or ""))


def _host(netloc: str) -> str:
    host = netloc.lower()
    return host[4:] if host.startswith("www.") else host


def targets_in(text: str) -> Set[str]:
    """The file names and web addresses a step mentions, lower-cased."""
    text = _EMAIL.sub(" ", text or "")
    out = set()

    def address(m: "re.Match[str]") -> str:
        if not m.group(2):
            return m.group(0)
        out.add(_host(m.group(1)) + m.group(2).rstrip("/.").lower())
        return " "

    rest = _URL.sub(address, text)
    return out | {m.lower() for m in _FILE.findall(rest)}


def targets_of(args: Optional[Mapping[str, Any]]) -> Set[str]:
    """What a tool call worked on: file names and web addresses in its arguments."""
    out: Set[str] = set()
    for key, value in (args or {}).items():
        if key in CONTENT_ARGS or not isinstance(value, str):
            continue
        if not 4 <= len(value) < 300 or "\n" in value:
            continue
        if value.lower().startswith(("http://", "https://")):
            parsed = urlparse(value)
            host = _host(parsed.netloc)
            out.add(host)
            out.add(host + parsed.path.rstrip("/.").lower())
        elif any(c.isspace() for c in value) and not re.search(r"[\\/]", value):
            # A command line or a query: the files it names.
            out.update(m.lower() for m in _FILE.findall(_EMAIL.sub(" ", value)))
        else:
            out.add(re.split(r"[\\/]", value)[-1].strip().lower())
            out.update(m.lower() for m in _FILE.findall(value))
    return {key for key in out if len(key) >= 4}


def _is_head(line: str) -> bool:
    return len(line) <= _MAX_LINE and bool(_PLAN_HEAD.match(line.strip()))


def _inline(line: str) -> Optional[List[str]]:
    """The steps of a plan written on one line, ``Plan: 1. a 2. b 3. c``.

    Only numbers counting up from 1 split it, so a number inside a step
    ("Keep 2. versions") stays part of that step.
    """
    line = line.strip()
    if len(line) > _MAX_INLINE:
        return None
    head = _INLINE_HEAD.match(line)
    if head is None:
        return None
    rest = line[head.end() :]
    cuts = []
    for m in _INLINE_NUMBER.finditer(rest):
        if int(m.group(1)) == len(cuts) + 1:
            cuts.append(m)
    items = [
        rest[m.end() : nxt.start() if nxt is not None else len(rest)].strip()
        for m, nxt in zip(cuts, cuts[1:] + [None])
    ]
    return [item for item in items if item] or None


def _plan_at(lines: Sequence[str], i: int) -> "tuple[List[str], int]":
    """The plan that starts at line *i*, and the index after it; no plan is ``[]``."""
    if _is_head(lines[i]):
        return _block(lines, i)
    inline = _inline(lines[i])
    return (inline, i + 1) if inline else ([], i + 1)


def _block(lines: Sequence[str], start: int) -> "tuple[List[str], int]":
    """The list under the heading at *start*: its items and the index after it.

    The first item fixes the style; with numbered items, bullets are sub-points.
    """
    items: List[str] = []
    style = None
    end = start + 1
    for j in range(start + 1, len(lines)):
        line = lines[j]
        if len(line) > _MAX_LINE:
            break
        numbered, bullet = _NUMBERED.match(line), _BULLET.match(line)
        if style is None and (numbered or bullet):
            style = _NUMBERED if numbered else _BULLET
        m = style.match(line) if style is not None else None
        if m:
            items.append(m.group(1))
            end = j + 1
        elif not line.strip() or (items and (numbered or bullet or line[:1] in " \t")):
            end = j + 1 if items else end
        else:
            break
    return items, end


def parse_plan(text: Optional[str]) -> Optional[List[str]]:
    """The list under a ``Plan:`` line, or ``None`` when there is none."""
    lines = (text or "").splitlines()
    for i in range(len(lines)):
        items, _ = _plan_at(lines, i)
        if len(items) >= MIN_STEPS:
            return items[:MAX_STEPS]
    return None


def plan_from_thinking(text: Optional[str]) -> Optional[List[str]]:
    """The longest numbered list of steps in a model's thinking text.

    Some models list the steps of a request in their thinking and not in the
    reply. Only the first reply to a request is read this way, and only a list
    whose every item reads as a step (two words or more).
    """
    best: List[str] = []
    run: List[str] = []
    for line in (text or "").splitlines() + [""]:
        m = _NUMBERED.match(line) if len(line) <= _MAX_LINE else None
        if m:
            run.append(m.group(1))
            continue
        if len(run) > len(best):
            best = run
        run = []
    if len(best) < MIN_STEPS or any(len(item.split()) < 2 for item in best):
        return None
    return best[:MAX_STEPS]


def strip_plan(text: str, only: Optional[Sequence[str]] = None) -> str:
    """*text* without its ``Plan:`` block, which the task list already shows.

    With *only*, the block is removed only when it is that plan: a plan the
    user asked for in the answer is theirs to read.
    """
    lines = text.splitlines(keepends=True)
    bare = [line.rstrip("\r\n") for line in lines]
    for i in range(len(bare)):
        items, end = _plan_at(bare, i)
        if len(items) < MIN_STEPS:
            continue
        if only is not None and [" ".join(x.split()) for x in items[:MAX_STEPS]] != [
            " ".join(x.split()) for x in only
        ]:
            continue
        return "".join(lines[:i] + lines[end:]).lstrip("\n")
    return text


class PlanTextFilter:
    """Keeps a streamed reply's opening ``Plan:`` block off the screen.

    A model may stream its thinking first, inside ``<think>`` tags (or with only
    the closing tag, when the template opened the block). That passes straight
    through, and the reply is judged from where the thinking ends. A reply that
    does not open with a plan passes straight through too. One that does is held
    whole until it ends, because only then is it known whether it was a step of
    the work (the plan is dropped) or the answer (it is shown).
    """

    def __init__(self) -> None:
        self._held = ""
        self._passing = False
        self._thinking = False
        # Thinking ended once; the reply after it is judged once.
        self._rearmed = False

    def feed(self, chunk: str) -> str:
        """Add *chunk*; return the text that is safe to show now."""
        self._held += chunk
        shown = []
        while True:
            out, again = self._step()
            shown.append(out)
            if not again:
                return "".join(shown)

    def _step(self) -> "tuple[str, bool]":
        held = self._held
        if self._thinking or self._passing:
            if self._rearmed:
                self._held = ""
                return held, False
            at = held.find(THINK_CLOSE)
            if at >= 0:
                cut = at + len(THINK_CLOSE)
                self._held = held[cut:]
                self._thinking = self._passing = False
                self._rearmed = True
                return held[:cut], True
            keep = _partial_close(held)
            self._held = held[len(held) - keep :]
            return held[: len(held) - keep], False
        lead = held.lstrip()
        if not self._rearmed and lead:
            if lead.startswith(THINK_OPEN):
                self._thinking = True
                return "", True
            if THINK_OPEN.startswith(lead):
                return "", False
        first = held.lstrip("\r\n")
        line = first.split("\n", 1)[0]
        if "\n" not in first and len(first) <= _MAX_INLINE:
            # Still the first line: wait while it could be a plan heading.
            if len(first) <= _MAX_LINE or _INLINE_HEAD.match(first.strip()):
                return "", False
        if _is_head(line) or _INLINE_HEAD.match(line.strip()):
            return "", False
        self._passing = True
        return "", True

    def flush(self, keep_plan: bool = False) -> str:
        """The reply ended; return what was held, without the plan unless *keep_plan*."""
        out, self._held = self._held, ""
        if self._passing or self._thinking or keep_plan:
            return out
        return strip_plan(out)


def _partial_close(text: str) -> int:
    """Length of *text*'s tail that may be the start of a closing think tag."""
    for size in range(min(len(text), len(THINK_CLOSE) - 1), 0, -1):
        if THINK_CLOSE.startswith(text[-size:]):
            return size
    return 0


class PlanTracker:
    """Marks the steps of a plan from what the tool calls of this request did."""

    def __init__(
        self,
        tool_names: Iterable[str] = (),
        change_tools: Iterable[str] = (),
        is_change: Optional[Callable[[str], bool]] = None,
    ):
        self._tool_names = set(tool_names)
        self._change_tools = set(change_tools)
        self._is_change = is_change or (lambda name: name in self._change_tools)
        names = "|".join(sorted(re.escape(n) for n in self._change_tools))
        self._change = re.compile(
            r"\b(" + _CHANGE_WORDS + ("|" + names if names else "") + r")\b", re.I
        )
        self.steps: List[Dict[str, Any]] = []
        self._raw: List[str] = []
        self._read: Set[str] = set()
        self._written: Set[str] = set()
        self._failed: Set[str] = set()
        self._changes: List[Set[str]] = []
        self._last_change_failed: Optional[Set[str]] = None
        self._ok_verbs: Set[str] = set()
        self._ok_calls = 0
        self._ok_tools: Set[str] = set()
        self._failed_calls = 0
        self._tool_calls = 0
        self.reminded = False
        self.sent_back = False
        self.planned_this_request = False

    # -- lifecycle ----------------------------------------------------------

    def reset_request(self) -> None:
        """A new user request: forget what earlier tool calls did."""
        self._read = set()
        self._written = set()
        self._failed = set()
        self._changes = []
        self._last_change_failed = None
        self._ok_verbs = set()
        self._ok_calls = 0
        self._ok_tools = set()
        self._failed_calls = 0
        self._tool_calls = 0
        self.reminded = False
        self.sent_back = False
        self.planned_this_request = False

    @property
    def tool_calls(self) -> int:
        return self._tool_calls

    # -- the plan -----------------------------------------------------------

    def set_steps(self, items: Sequence[str]) -> bool:
        """Adopt *items* as the plan. Returns whether the list changed."""
        before = self.snapshot()
        # Progress carries over only within a request: an earlier request's
        # "Update notes.md" says nothing about this one's.
        old = (
            {s["text"]: s["status"] for s in self.steps}
            if self.planned_this_request
            else {}
        )
        steps = []
        raw = []
        for item in list(items)[:MAX_STEPS]:
            full = self._clean(str(item))
            if not full:
                continue
            text = full
            if len(text) > MAX_STEP_CHARS:
                text = text[: MAX_STEP_CHARS - 1].rstrip() + "…"
            change = bool(self._change.search(full))
            named = set(re.findall(r"\w+", str(item))) & self._tool_names
            steps.append(
                {
                    "text": text,
                    "tools": named,
                    # From the whole step: the cut for display must not cut a name.
                    "targets": targets_in(full),
                    "change": change,
                    "reads": change and bool(_READ_WORDS.search(full)),
                    "status": old.get(text, OPEN),
                }
            )
            raw.append(" ".join(str(item).split()))
        if len(steps) < MIN_STEPS:
            return False
        self.steps = steps
        self._raw = raw
        self.planned_this_request = True
        self._evaluate()
        return self.snapshot() != before

    def is_this_plan(self) -> Sequence[str]:
        """The plan as the model wrote it, for telling it from another plan."""
        return list(self._raw)

    def _clean(self, text: str) -> str:
        text = " ".join(text.split())
        text = _STATUS_SUFFIX.sub("", text)

        def tool_phrase(m: "re.Match[str]") -> str:
            return "" if m.group(1) in self._tool_names else m.group(0)

        text = re.sub(
            r"\s+(?:using|with|via)\s+`?(\w+)`?(?=[\s.,]|$)", tool_phrase, text
        )
        lead = re.match(r"(?:use|call|run)\s+`?(\w+)`?\s+to\s+(\w)", text, re.I)
        if lead and lead.group(1) in self._tool_names:
            text = lead.group(2).upper() + text[lead.end() :]
        return text.replace("`", "").strip("* ").strip()

    # -- evidence -----------------------------------------------------------

    def on_tool_result(
        self, tool: str, args: Optional[Mapping[str, Any]], ok: bool
    ) -> bool:
        """Record one finished tool call. Returns whether any step changed."""
        if tool in IGNORED_TOOLS:
            return False
        before = self.snapshot()
        self._tool_calls += 1
        keys = targets_of(args)
        change = self._is_change(tool)
        if ok:
            (self._written if change else self._read).update(keys)
            self._failed -= keys
            self._ok_calls += 1
            self._ok_verbs.add(tool.split("_", 1)[0].lower())
            self._ok_tools.add(tool)
            if change:
                self._changes.append(keys)
                self._last_change_failed = None
        else:
            self._failed |= keys
            self._failed_calls += 1
            if change:
                self._last_change_failed = keys
        # An earlier request's plan is history: only this request's plan is ticked.
        if self.planned_this_request:
            self._evaluate()
        return self.snapshot() != before

    def finish(self) -> bool:
        """The model gave its answer. Returns whether any step changed."""
        before = self.snapshot()
        if self.planned_this_request:
            self._evaluate(final=True)
        return self.snapshot() != before

    def _evaluate(self, final: bool = False) -> None:
        named = {t for s in self.steps if s["change"] for t in s["targets"]}
        loose = sum(1 for keys in self._changes if not keys & named)
        loose_failure = self._last_change_failed is not None and not (
            self._last_change_failed & named
        )
        seen = self._read | self._written
        # Every checkable step is worked out afresh from the evidence, never
        # carried: two steps with the same wording must not share one write.
        for s in self.steps:
            if s["targets"]:
                if not s["change"]:
                    reached = s["targets"] <= seen
                elif s["reads"]:
                    # "Read a.py and write b.md": all seen, and something written.
                    reached = s["targets"] <= seen and bool(
                        s["targets"] & self._written
                    )
                else:
                    reached = s["targets"] <= self._written
                if reached:
                    s["status"] = DONE
                elif s["targets"] & self._failed:
                    s["status"] = FAILED
                else:
                    s["status"] = OPEN
            elif s["change"]:
                if loose > 0:
                    loose -= 1
                    s["status"] = DONE
                elif loose_failure:
                    s["status"] = FAILED
                else:
                    s["status"] = OPEN
        checked = [i for i, s in enumerate(self.steps) if s["targets"] or s["change"]]
        done_to = max(
            (i for i in checked if self.steps[i]["status"] == DONE), default=-1
        )
        # A failed step shows earlier thinking happened only if something else worked.
        failed_to = (
            max((i for i in checked if self.steps[i]["status"] == FAILED), default=-1)
            if self._ok_calls
            else -1
        )
        all_checked_done = all(self.steps[i]["status"] == DONE for i in checked)
        clean = not self._failed and not loose_failure
        for i, s in enumerate(self.steps):
            if s["targets"] or s["change"]:
                continue
            verb_hit = any(
                re.search(r"\b" + re.escape(v) + r"(?:s|ing|ed)?\b", s["text"], re.I)
                for v in self._ok_verbs
            )
            if (
                verb_hit
                or bool(s["tools"] & self._ok_tools)
                or i < max(done_to, failed_to)
                or (final and all_checked_done and clean and self._ok_calls)
            ):
                s["status"] = DONE

    # -- reading ------------------------------------------------------------

    def snapshot(self) -> List[tuple]:
        return [(s["text"], s["status"]) for s in self.steps]

    def unfinished_checked(self) -> List[str]:
        """Open steps a tool call can still settle: what a send-back names.

        A change step that names no file is asked for only when no change has
        succeeded at all; once one has, there is no telling whose it was.
        """
        return [
            s["text"]
            for s in self.steps
            if s["status"] == OPEN
            and (s["targets"] or (s["change"] and not self._changes))
        ]

    def has_failed(self) -> bool:
        return any(s["status"] == FAILED for s in self.steps)

    @property
    def failed_calls(self) -> int:
        """Tool calls of this request that failed, whatever step they were for."""
        return self._failed_calls

    def needs_reminder(self) -> bool:
        """Whether the one plan reminder is due for this request."""
        return (
            not self.planned_this_request
            and not self.reminded
            and self._tool_calls >= REMIND_AFTER_CALLS
        )
