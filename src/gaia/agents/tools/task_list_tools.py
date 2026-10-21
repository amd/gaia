# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""TaskListMixin — a plan the agent keeps up to date while it works.

The model writes the steps of a multi-step request once, then advances through
them with ``update_tasks``. The list belongs to the conversation, not the turn:
it is carried across follow-ups, saved with the session, and every change is
reported as one ``task_list`` event (``OutputHandler.print_task_list``) that the
Agent UI and the TUI both pin above their message box.

Invariant: while any step is unfinished exactly one is ``in_progress``; once
every step is ``completed`` none is.
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from gaia.logger import get_logger

logger = get_logger(__name__)

PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"
STATUSES = (PENDING, IN_PROGRESS, COMPLETED)

#: The model keeps the list; ``plan`` is the fallback derived from the reply
#: envelope's ``plan`` field when the model never called ``update_tasks``.
SOURCE_AGENT = "agent"
SOURCE_PLAN = "plan"

MAX_ITEMS = 12
MAX_ITEM_CHARS = 120

TOOL_NAME = "update_tasks"

#: The whole prompt cost of the feature; local models pay for every token.
PROMPT_RULES = (
    "\n**TASK LIST:** For a request that takes three or more steps, first call "
    "`update_tasks(items=[...])` with short imperative steps, call "
    "`update_tasks(advance=true)` as each one finishes, and finish them all "
    "before you answer. Skip it for questions and one-step requests. The user "
    "sees the list, so never repeat it in your reply.\n"
)

#: Most items a single ``items`` argument may carry before it is refused
#: without being read further — a guard against a runaway model, not a limit
#: the model is told about (MAX_ITEMS is).
_MAX_RAW_ITEMS = 1000

#: Tool calls into a request, with no list, before the model is reminded.
NUDGE_AFTER_CALLS = 2
#: Tool calls since the last list change before the model is reminded.
NUDGE_STALE_STEPS = 3
NUDGE_START = (
    "If this request needs more steps, call update_tasks(items=[...]) with all "
    "of its steps and done=[...] for the ones already finished, then keep it "
    "updated. If it is nearly done, ignore this."
)
NUDGE_ADVANCE = (
    "Your task list has not changed for a few steps. If a step finished, call "
    "update_tasks(advance=true)."
)

# A list marker the model typed into the step text ("- ", "2. ", "3) ").
_LIST_MARKER = re.compile(r"^(?:[-*•]|\d{1,2}[.)])\s+")
# Terminal escape sequences (CSI, OSC) and control or bidi characters: step
# text reaches a terminal verbatim.
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)?")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")


def _now() -> float:
    return round(time.time(), 3)


def _clean_text(raw: Any) -> str:
    """One display-safe line: no control or bidi characters, no list marker."""
    text = _CONTROL.sub(" ", _ANSI.sub("", str(raw)))
    return _LIST_MARKER.sub("", " ".join(text.split())).strip()


def _parse_flag(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false"):
        return value.strip().lower() == "true"
    if value is None:
        return False
    raise ValueError(f"{name} must be true or false; got {value!r}.")


def _parse_items(items: Any) -> List[str]:
    """Normalise the ``items`` argument to a list of step texts.

    Models send a JSON array, a JSON-encoded array as a string, or one step per
    line; all three mean the same list.
    """
    if isinstance(items, str):
        text = items.strip()
        if text.startswith("["):
            try:
                items = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"items looks like a JSON array but does not parse ({exc.msg}). "
                    'Pass a list of short steps, e.g. ["Read the config", "Fix the bug"].'
                ) from exc
        else:
            items = [line for line in text.splitlines() if line.strip()]
    if not isinstance(items, (list, tuple)):
        raise ValueError(
            f"items must be a list of short steps, got {type(items).__name__}."
        )
    if len(items) > MAX_ITEMS:
        raise ValueError(
            f"{len(items) if len(items) <= _MAX_RAW_ITEMS else 'over 1000'} steps "
            f"is too many to track; keep it to {MAX_ITEMS} or fewer by merging "
            "small steps."
        )
    steps: List[str] = []
    for position, raw in enumerate(items, 1):
        if isinstance(raw, Mapping):
            raw = raw.get("content") or raw.get("step") or raw.get("text") or ""
        if not isinstance(raw, (str, int, float)) or isinstance(raw, bool):
            raise ValueError(f"step {position} must be text; got {type(raw).__name__}.")
        text = _clean_text(raw)
        if not text:
            raise ValueError(f"step {position} is empty; every step needs some text.")
        if len(text) > MAX_ITEM_CHARS:
            text = text[: MAX_ITEM_CHARS - 1].rstrip() + "…"
        if text in steps:
            raise ValueError(
                f"step {position} repeats step {steps.index(text) + 1} "
                f"({text!r}); every step needs its own wording."
            )
        steps.append(text)
    return steps


def _parse_indices(value: Any, name: str) -> List[int]:
    """1-based positions from an int, a list of ints, or their string forms."""
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip().strip("[]")
        value = [part for part in text.replace(",", " ").split() if part]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a step number or a list of step numbers.")
    if len(value) > _MAX_RAW_ITEMS:
        raise ValueError(f"{name} names more steps than any list can have.")
    out = []
    for item in value:
        number = None
        if isinstance(item, int) and not isinstance(item, bool):
            number = item
        elif isinstance(item, float) and item.is_integer():
            number = int(item)
        elif isinstance(item, str) and item.strip().lstrip("+").isdigit():
            number = int(item.strip())
        if number is None:
            raise ValueError(
                f"{name} must hold step numbers (1 is the first step); got {item!r}."
            )
        out.append(number)
    return out


class TaskList:
    """The conversation's task list and the one place that changes it.

    ``on_change`` receives the full :meth:`state` after every change; hosts use
    it to emit the ``task_list`` event and to persist the list. Thread-safe:
    the UI reads ``state()`` while the agent thread writes.
    """

    def __init__(
        self,
        on_change: Optional[Callable[[Dict[str, Any]], None]] = None,
        narrate: Optional[Callable[[str, Mapping[str, Any]], str]] = None,
    ) -> None:
        self._lock = threading.RLock()
        self._items: List[Dict[str, Any]] = []
        self._source = SOURCE_AGENT
        self._current_action: Optional[str] = None
        self._action_started_at: Optional[float] = None
        self._updated_at: Optional[float] = None
        self._on_change = on_change
        self._narrate = narrate
        self._kept_this_turn = False
        # Tool calls since the model last touched the list; a list that has
        # stopped being kept must look it.
        self._steps_since_update = 0
        self._calls_this_turn = 0
        self._nudged = False

    # -- reminders ------------------------------------------------------------

    def nudge(self) -> Optional[str]:
        """A one-line reminder for the model's next step, or ``None``.

        The prompt rule alone is often skipped, so the agent reminds the model
        once a request has proved itself multi-step, and once when a kept list
        has fallen behind. At most one reminder per turn.
        """
        with self._lock:
            if self._nudged:
                return None
            if not self._kept_this_turn and not self.is_active():
                if self._calls_this_turn < NUDGE_AFTER_CALLS:
                    return None
                self._nudged = True
                return NUDGE_START
            if self.is_active() and self._steps_since_update >= NUDGE_STALE_STEPS:
                self._nudged = True
                return NUDGE_ADVANCE
            return None

    # -- reading ----------------------------------------------------------

    def state(self) -> Dict[str, Any]:
        """The full list as the ``task_list`` event and the session store carry it."""
        with self._lock:
            return {
                "items": [dict(item) for item in self._items],
                "source": self._source,
                "current_action": self._current_action,
                "action_started_at": self._action_started_at,
                "steps_since_update": self._steps_since_update,
                "updated_at": self._updated_at,
            }

    @property
    def items(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._items]

    def is_active(self) -> bool:
        """True while some step is unfinished."""
        with self._lock:
            return any(item["status"] != COMPLETED for item in self._items)

    def summary(self) -> str:
        """The list as the model reads it back in a tool result."""
        with self._lock:
            if not self._items:
                return "The task list is empty."
            marks = {COMPLETED: "[x]", IN_PROGRESS: "[>]", PENDING: "[ ]"}
            lines = [
                f"{i}. {marks[item['status']]} {item['content']}"
                for i, item in enumerate(self._items, 1)
            ]
            done = sum(1 for item in self._items if item["status"] == COMPLETED)
            lines.append(f"{done} of {len(self._items)} done.")
            return "\n".join(lines)

    # -- the model's edits --------------------------------------------------

    def update(
        self,
        items: Any = None,
        advance: bool = False,
        done: Any = None,
        start: Any = None,
    ) -> Dict[str, Any]:
        """Apply one ``update_tasks`` call; raise ``ValueError`` on a bad edit.

        Order: replace the list, mark ``done``, ``advance``, ``start``. A step
        left unstarted while others remain pending is started automatically, so
        the one-in-progress invariant holds after every call.
        """
        new_steps = _parse_items(items) if items is not None else None
        advance = _parse_flag(advance, "advance")
        done_at = _parse_indices(done, "done")
        start_at = _parse_indices(start, "start")
        if len(start_at) > 1:
            raise ValueError(
                "start takes one step number; only one step runs at a time."
            )
        if start_at and start_at[0] in done_at:
            raise ValueError(
                f"step {start_at[0]} cannot be marked done and started in one call."
            )
        with self._lock:
            # Validate against a copy so a bad call leaves the list untouched.
            items_after = (
                self._replaced(new_steps)
                if new_steps is not None
                else [dict(item) for item in self._items]
            )
            count = len(items_after)
            for position in done_at + start_at:
                if not 1 <= position <= count:
                    raise ValueError(
                        f"there is no step {position}; the list has {count} "
                        f"step{'s' if count != 1 else ''} (1 is the first)."
                    )
            now = _now()
            for position in done_at:
                _complete(items_after[position - 1], now)
            if advance:
                current = _index_of(items_after, IN_PROGRESS)
                if current is not None:
                    _complete(items_after[current], now)
                    _start_next(items_after, now, after=current)
                else:
                    _start_next(items_after, now)
            if start_at:
                for item in items_after:
                    if item["status"] == IN_PROGRESS:
                        item["status"] = PENDING
                        item["started_at"] = None
                target = items_after[start_at[0] - 1]
                target["status"] = IN_PROGRESS
                target["started_at"] = now
                target["completed_at"] = None
            if _index_of(items_after, IN_PROGRESS) is None:
                _start_next(items_after, now)
            self._kept_this_turn = True
            was_stale = self._steps_since_update > 0
            self._steps_since_update = 0
            unchanged = self._source == SOURCE_AGENT and _shape(items_after) == _shape(
                self._items
            )
            if unchanged and not was_stale:
                # A repeated call is not news; re-sending it would only churn.
                return self.state()
            self._items = items_after
            self._source = SOURCE_AGENT
            self._touch()
        return self._changed()

    def _replaced(self, steps: List[str]) -> List[Dict[str, Any]]:
        """The new list, keeping the status of steps whose text did not change."""
        previous = {item["content"]: item for item in self._items}
        out = []
        for i, text in enumerate(steps, 1):
            kept = previous.get(text)
            if kept is not None and kept["status"] != PENDING:
                item = dict(kept)
            else:
                item = {
                    "content": text,
                    "status": PENDING,
                    "started_at": None,
                    "completed_at": None,
                }
            item["id"] = i
            out.append(item)
        return out

    # -- the host's hooks ---------------------------------------------------

    def begin_turn(self) -> None:
        """A new request started; the model has not touched the list yet."""
        with self._lock:
            self._kept_this_turn = False
            self._calls_this_turn = 0
            self._nudged = False

    def end_turn(self) -> None:
        """The turn ended; nothing is running any more."""
        with self._lock:
            if self._current_action is None:
                return
            self._current_action = None
            self._action_started_at = None
            self._touch()
        self._changed()

    def action_started(self, tool: str, args: Optional[Mapping[str, Any]]) -> None:
        """A tool call began; the live action line follows it."""
        if tool == TOOL_NAME:
            return
        label = self._narrate(tool, args or {}) if self._narrate else tool
        with self._lock:
            self._calls_this_turn += 1
            if not self._items:
                return
            self._current_action = label
            self._action_started_at = _now()
            self._steps_since_update += 1
            self._touch()
        self._changed()

    def mirror_plan(self, steps: Sequence[str], current: int) -> None:
        """Derive the list from the reply envelope's ``plan`` field.

        Used only when the model has not kept the list itself this turn, and
        never over an unfinished list the model kept in an earlier turn: the
        list belongs to the conversation, and a follow-up's plan is not a
        reason to throw its progress away.
        """
        with self._lock:
            if self._kept_this_turn or not steps:
                return
            if self._source == SOURCE_AGENT and any(
                item["status"] != COMPLETED for item in self._items
            ):
                return
            now = _now()
            previous = (
                {item["id"]: item for item in self._items}
                if self._source == SOURCE_PLAN
                else {}
            )
            items = []
            # A plan longer than the card keeps the running step in view.
            first = max(0, min(current - 1, len(steps) - MAX_ITEMS))
            for i, text in enumerate(steps):
                if not first <= i < first + MAX_ITEMS:
                    continue
                text = _clean_text(text)[:MAX_ITEM_CHARS] or f"Step {i + 1}"
                old = previous.get(i + 1)
                same = old is not None and old["content"] == text
                if i < current:
                    status = COMPLETED
                elif i == current:
                    status = IN_PROGRESS
                else:
                    status = PENDING
                started = old["started_at"] if same and old["started_at"] else None
                if status != PENDING and started is None:
                    started = now
                completed = (
                    (old["completed_at"] if same and old["completed_at"] else now)
                    if status == COMPLETED
                    else None
                )
                items.append(
                    {
                        "id": i + 1,
                        "content": text,
                        "status": status,
                        "started_at": started,
                        "completed_at": completed,
                    }
                )
            self._items = items
            self._source = SOURCE_PLAN
            self._steps_since_update = 0
            self._touch()
        logger.info(
            "Task list derived from the reply's plan field (%d steps)", len(steps)
        )
        self._changed()

    def restore(self, state: Optional[Mapping[str, Any]]) -> None:
        """Make the saved list the current one; none saved means none. Never emits."""
        if not state:
            self.clear(emit=False)
            return
        restored = _validated_items(state.get("items"))
        source = state.get("source") or SOURCE_AGENT
        if source not in (SOURCE_AGENT, SOURCE_PLAN):
            raise ValueError(f"saved task list has an unknown source {source!r}")
        with self._lock:
            self._items = restored
            self._source = source
            self._steps_since_update = 0
            # A restored list was saved by a finished or interrupted turn;
            # nothing of it is running now.
            self._current_action = None
            self._action_started_at = None
            self._updated_at = state.get("updated_at")

    def clear(self, emit: bool = True, by_model: bool = False) -> None:
        """Forget the list. ``emit=False`` when the host clears its own view too.

        ``by_model``: the model cleared it, which counts as keeping the list
        this turn, so the plan fallback does not rebuild it.
        """
        with self._lock:
            if by_model:
                self._kept_this_turn = True
            if not self._items:
                return
            self._items = []
            self._source = SOURCE_AGENT
            self._current_action = None
            self._action_started_at = None
            self._touch()
        if emit:
            self._changed()

    # -- internals ----------------------------------------------------------

    def _touch(self) -> None:
        self._updated_at = _now()

    def _changed(self) -> Dict[str, Any]:
        state = self.state()
        if self._on_change is not None:
            self._on_change(state)
        return state


def _shape(items: List[Dict[str, Any]]) -> List[tuple]:
    return [(item["content"], item["status"]) for item in items]


def _timestamp(value: Any, where: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise ValueError(f"saved task list has a bad time at {where}: {value!r}")


def _validated_items(items: Any) -> List[Dict[str, Any]]:
    """A saved list's steps, checked as strictly as a live edit."""
    if not isinstance(items, list):
        raise ValueError(
            f"saved task list is malformed: items is {type(items).__name__}, "
            "expected a list"
        )
    if len(items) > MAX_ITEMS:
        raise ValueError(
            f"saved task list has {len(items)} steps; at most {MAX_ITEMS} are kept"
        )
    restored = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, Mapping):
            raise ValueError(f"saved task list is malformed at step {i}: {item!r}")
        status = item.get("status")
        content = item.get("content")
        if status not in STATUSES or not isinstance(content, str):
            raise ValueError(f"saved task list is malformed at step {i}: {item!r}")
        content = _clean_text(content)[:MAX_ITEM_CHARS]
        if not content:
            raise ValueError(f"saved task list has an empty step {i}")
        restored.append(
            {
                "id": i,
                "content": content,
                "status": status,
                "started_at": _timestamp(item.get("started_at"), f"step {i}"),
                "completed_at": _timestamp(item.get("completed_at"), f"step {i}"),
            }
        )
    if [item["status"] for item in restored].count(IN_PROGRESS) > 1:
        raise ValueError("saved task list has more than one step in progress")
    return restored


def _index_of(items: List[Dict[str, Any]], status: str) -> Optional[int]:
    for i, item in enumerate(items):
        if item["status"] == status:
            return i
    return None


def _complete(item: Dict[str, Any], now: float) -> None:
    if item["status"] == COMPLETED:
        return
    item["status"] = COMPLETED
    item["started_at"] = item.get("started_at") or now
    item["completed_at"] = now


def _start_next(items: List[Dict[str, Any]], now: float, after: int = -1) -> None:
    """Start the first pending step after *after*, else the first pending one."""
    order = list(range(after + 1, len(items))) + list(range(0, after + 1))
    for i in order:
        if items[i]["status"] == PENDING:
            items[i]["status"] = IN_PROGRESS
            items[i]["started_at"] = now
            return


def plan_step_text(step: Any) -> str:
    """One readable line for a reply-envelope plan step."""
    if isinstance(step, Mapping):
        description = step.get("description")
        if isinstance(description, str) and description.strip():
            return " ".join(description.split())
        tool = step.get("tool")
        if isinstance(tool, str) and tool:
            from gaia.ui.event_narration import derive_narration

            args = step.get("tool_args")
            return derive_narration(tool, args if isinstance(args, Mapping) else {})
        return json.dumps(step, ensure_ascii=False)
    return " ".join(str(step).split())


class TaskListMixin:
    """
    Mixin providing the agent's task list.

    Tools provided:
    - update_tasks: set, advance, mark done or replace the steps of the plan

    Every change goes to ``self.console.print_task_list`` (the ``task_list``
    event) and to the session store the host bound with
    :meth:`bind_task_list_store`. The base ``Agent`` drives the turn hooks
    through ``self.task_list``.
    """

    task_list: Optional[TaskList] = None
    _task_list_save: Optional[Callable[[Dict[str, Any]], None]] = None

    def register_task_list_tools(self) -> None:
        """Create the list and register ``update_tasks`` into _TOOL_REGISTRY."""
        from gaia.agents.base.tools import tool
        from gaia.ui.event_narration import derive_narration

        if self.task_list is None:
            self.task_list = TaskList(
                on_change=self._task_list_changed, narrate=derive_narration
            )

        @tool(display_label="Updating the task list")
        def update_tasks(
            items: Optional[List[str]] = None,
            advance: bool = False,
            done: Optional[List[int]] = None,
            start: Optional[int] = None,
        ) -> Dict[str, Any]:
            """Keep a short task list for multi-step work; the user sees it pinned while you work.

            Args:
                items: The whole plan as short imperative steps (max 12). Replaces the list; steps you keep verbatim keep their status. [] clears it.
                advance: true marks the current step done and starts the next.
                done: Step numbers (1 = first) to mark done.
                start: Step number to work on now.
            """
            return self._update_tasks(items, advance, done, start)

    def _update_tasks(self, items, advance, done, start) -> Dict[str, Any]:
        task_list = self.task_list
        if task_list is None:
            raise TypeError(
                f"{type(self).__name__} has no task list: call "
                "register_task_list_tools() before using update_tasks."
            )
        if items is None and not advance and done is None and start is None:
            return {"status": "success", "tasks": task_list.summary()}
        if isinstance(items, (list, tuple)) and not items:
            task_list.clear(by_model=True)
            return {"status": "success", "tasks": task_list.summary()}
        try:
            task_list.update(items=items, advance=advance, done=done, start=start)
        except ValueError as exc:
            return {
                "status": "error",
                "error": f"{exc} The task list was not changed.",
                "tasks": task_list.summary(),
            }
        return {"status": "success", "tasks": task_list.summary()}

    def _task_list_changed(self, state: Dict[str, Any]) -> None:
        console = getattr(self, "console", None)
        if console is not None:
            console.print_task_list(state)
        if self._task_list_save is not None:
            self._task_list_save(state)

    def bind_task_list_store(
        self,
        load: Callable[[], Optional[Mapping[str, Any]]],
        save: Callable[[Dict[str, Any]], None],
    ) -> None:
        """Load the session's saved list with *load*; save every change with *save*.

        The host owns the session: the Agent UI binds its session row, the
        stdio transport its transcript file. No-op for a profile with no list.
        *load* raises ``ValueError`` for a saved list it cannot read.
        """
        if self.task_list is None:
            return
        self._task_list_save = save
        try:
            self.task_list.restore(load())
        except ValueError as exc:
            # A broken saved list must not take the chat down with it: say so,
            # start empty, and overwrite the bad copy.
            logger.error("Discarding the saved task list: %s", exc)
            console = getattr(self, "console", None)
            if console is not None:
                console.print_warning(
                    f"The saved task list could not be read ({exc}), so it was cleared."
                )
            self.task_list.clear(emit=False)
            save(self.task_list.state())
