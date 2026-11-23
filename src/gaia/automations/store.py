# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every automation GAIA runs on its own, in one file every app can read.

Four kinds, by when GAIA speaks:

* ``reminder`` — once, at a time. A fixed message (``say``), or a prompt.
* ``summary`` — every interval; the answer is always posted.
* ``trigger`` — every interval a read-only ``check`` runs with no model; the
  prompt runs only when what it prints changes (see :mod:`.checks`).
* ``alert`` — every interval the agent judges the prompt's condition and stays
  silent unless something needs the user (see :mod:`.runner`). The heartbeat is
  an alert whose prompt is the user's checklist.

Stored in ``~/.gaia/automations/automations.json``. A repeating automation
that came due while nothing was running runs once when it next can, not once
per missed interval; one inside its quiet hours waits until they end.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.automations import files
from gaia.automations.timing import (
    TimingError,
    day_allowed,
    in_quiet_hours,
    next_slot,
)
from gaia.logger import get_logger

log = get_logger(__name__)

KIND_REMINDER = "reminder"
KIND_SUMMARY = "summary"
KIND_TRIGGER = "trigger"
KIND_ALERT = "alert"
KINDS = (KIND_REMINDER, KIND_SUMMARY, KIND_TRIGGER, KIND_ALERT)

#: Enough for a person's real routines; past it, something is looping.
MAX_AUTOMATIONS = 25
#: Failed check runs in a row before the user is told.
CHECK_FAILURES_BEFORE_ALERT = 3


class AutomationError(ValueError):
    """A request the user can fix; the message says how."""


@dataclass
class Automation:
    id: str
    name: str
    kind: str
    #: What the agent does when it runs. Empty for a ``say`` reminder.
    prompt: str = ""
    #: A reminder's exact text, sent without the model.
    say: str = ""
    #: Repeat interval; 0 for a one-shot reminder.
    every_seconds: int = 0
    next_at: float = 0.0
    #: A trigger's read-only command.
    check: str = ""
    #: ``"22:00-07:00"``: a run due inside waits until it ends.
    quiet: str = ""
    #: ``"mon,tue,wed,thu,fri"``: a run due on another day is skipped.
    days: str = ""
    paused: bool = False
    created_at: float = 0.0
    last_run_at: float = 0.0
    runs: int = 0
    #: A trigger's last output, and whether it has a baseline.
    last_output: str = ""
    checked: bool = False
    failures: int = 0
    #: An alert's last message headline, so the same news is not sent twice.
    last_alert: str = ""

    @property
    def repeats(self) -> bool:
        return self.every_seconds > 0


def path() -> Path:
    from gaia.home import gaia_home

    return gaia_home() / "automations" / "automations.json"


def _locked():
    from gaia.utils.file_lock import file_lock

    return file_lock(path())


def load() -> List[Automation]:
    data = files.read_json_or_set_aside(path(), "automations")
    if data is None:
        return []
    names = {f.name for f in fields(Automation)}
    try:
        return [
            Automation(**{k: v for k, v in item.items() if k in names})
            for item in data.get("automations", [])
        ]
    except TypeError as e:
        raise files.CorruptStateFile(
            f"{path()} is not usable: {e}. Delete it to clear your automations."
        ) from e


def save(items: List[Automation]) -> None:
    files.write_json(path(), {"automations": [asdict(a) for a in items]})


def find(items: List[Automation], ref: str) -> Optional[Automation]:
    """By id, else by exact name (case-insensitive). Never a partial match: an
    approved "remove S" must not remove "Standup digest"."""
    ref = (ref or "").strip()
    if not ref:
        return None
    lowered = ref.lower()
    # An id wins over a name anywhere in the list: a name can look like an id.
    return next((a for a in items if a.id == ref), None) or next(
        (a for a in items if a.name.lower() == lowered), None
    )


def _missing(ref: str, items: List[Automation]) -> AutomationError:
    names = ", ".join(repr(a.name) for a in items) or "none yet"
    return AutomationError(
        f"No automation is called or numbered {ref!r}. Its exact name, or its id "
        f"from the list, is needed. Names: {names}."
    )


def add(item: Automation) -> Automation:
    with _locked():
        items = load()
        if len(items) >= MAX_AUTOMATIONS:
            raise AutomationError(
                f"You already have {MAX_AUTOMATIONS} automations. List them and "
                "remove one first."
            )
        if any(a.name.lower() == item.name.lower() for a in items):
            raise AutomationError(
                f"An automation is already called {item.name!r}. Pick another name, "
                "or edit that one."
            )
        item.id = item.id or uuid.uuid4().hex[:8]
        item.created_at = item.created_at or time.time()
        items.append(item)
        save(items)
        return item


def update(ref: str, **changes: Any) -> Automation:
    """Change fields of the automation named or numbered *ref*; returns it."""
    with _locked():
        items = load()
        item = find(items, ref)
        if item is None:
            raise _missing(ref, items)
        new_name = changes.get("name")
        if new_name and any(
            a is not item and a.name.lower() == new_name.lower() for a in items
        ):
            raise AutomationError(f"An automation is already called {new_name!r}.")
        for key, value in changes.items():
            setattr(item, key, value)
        save(items)
    if changes.get("paused"):
        _drop_waiting(item.id)
    return item


def _drop_waiting(item_id: str = "") -> None:
    """A stopped automation must not speak later from the daemon's queue."""
    from gaia.automations import outbox

    if item_id:
        outbox.drop_for(item_id)
    else:
        outbox.drop_all()


def remove(ref: str) -> Automation:
    with _locked():
        items = load()
        item = find(items, ref)
        if item is None:
            raise _missing(ref, items)
        save([a for a in items if a is not item])
    _drop_waiting(item.id)
    return item


def clear() -> int:
    with _locked():
        count = len(load())
        save([])
    _drop_waiting()
    return count


def take_due(now: float) -> List[Automation]:
    """Return what is due now and move each to its next slot.

    One-shots are removed as they are taken. A repeating one inside its quiet
    hours is left due, so it runs as soon as they end.
    """
    with _locked():
        items = load()
        local = datetime.fromtimestamp(now).astimezone()
        due: List[Automation] = []
        kept: List[Automation] = []
        changed = False
        for item in items:
            ready = not item.paused and item.next_at <= now
            if ready and item.repeats and _quiet(item, local):
                ready = False
            if ready and item.repeats and not day_allowed(item.days, local):
                # Not one of its days: on to the next slot, without running.
                item.next_at = _after(item.next_at, item.every_seconds, now)
                changed = True
                ready = False
            if not ready:
                kept.append(item)
                continue
            changed = True
            due.append(item)
            item.last_run_at, item.runs = now, item.runs + 1
            if not item.repeats:
                continue
            # Skip missed slots: one run on return, not one per interval missed.
            item.next_at = _after(item.next_at, item.every_seconds, now)
            kept.append(item)
        if changed:
            save(kept)
        return due


def _quiet(item: Automation, local: datetime) -> bool:
    try:
        return in_quiet_hours(item.quiet, local)
    except TimingError as e:
        # Only a hand edit gets here; the CLI checks quiet hours on the way in.
        log.error("Automation %r has unreadable quiet hours, ignored: %s", item.name, e)
        return False


def _after(slot: float, every: int, now: float) -> float:
    """The first slot after *now*. Whole days step in local time, so
    "every day at 8" stays at 8 across a daylight-saving change."""
    while slot <= now:
        slot = next_slot(slot, every)
    return slot


def record_check(
    item_id: str,
    *,
    output: str = "",
    failed: bool = False,
    check: str = "",
    ran: bool = False,
) -> int:
    """Store one check run; returns the failures in a row (0 after a success),
    or with *ran*, 1 if it was stored and 0 if not.

    Ignored when the automation's check is no longer *check*: it was edited
    while this run took place, and the edit's baseline stands.
    """
    with _locked():
        items = load()
        item = next((a for a in items if a.id == item_id), None)
        if item is None or (check and item.check != check):
            return 0
        if failed:
            item.failures += 1
        else:
            item.failures, item.last_output, item.checked = 0, output, True
        save(items)
        return 1 if ran else item.failures


def record_news(item_id: str, news: str) -> Optional[bool]:
    """Keep *news* as an alert's last headline, in one locked step.

    Returns None when the automation is gone, False when *news* is what it
    last said, and True when it was stored. Empty *news* clears it.
    """
    with _locked():
        items = load()
        item = next((a for a in items if a.id == item_id), None)
        if item is None:
            return None
        if news and news == item.last_alert:
            return False
        if item.last_alert != news:
            item.last_alert = news
            save(items)
        return True


def snapshot() -> List[Automation]:
    """Every automation, read under the lock another process writes with."""
    with _locked():
        return load()


def describe(item: Automation, now: Optional[float] = None) -> Dict[str, Any]:
    """What a list shows: plain words, no internals."""
    from gaia.automations.timing import format_interval

    now = now or time.time()
    when = datetime.fromtimestamp(item.next_at).astimezone()
    out: Dict[str, Any] = {
        "id": item.id,
        "name": item.name,
        "kind": item.kind,
        "status": "paused" if item.paused else "active",
        "next": when.strftime("%a %d %b %H:%M"),
    }
    if item.repeats:
        out["every"] = format_interval(item.every_seconds)
    if item.quiet:
        out["quiet_hours"] = item.quiet
    if item.days:
        out["days"] = item.days
    if item.say:
        out["says"] = item.say
    if item.prompt:
        out["prompt"] = item.prompt
    if item.check:
        out["check"] = item.check
    if item.failures:
        out["failing_checks"] = item.failures
    return out
