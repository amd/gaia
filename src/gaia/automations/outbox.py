# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What the daemon ran and the Discord bridge has not delivered yet.

The daemon owns the tick (see :mod:`.tick`) but has no Discord connection and
no agent the user can answer Allow/Deny to. So it does the model-free part
itself (timing, trigger checks) and queues the rest here: a message to post,
or an agent turn to run. The bridge delivers the queue on every tick it is
connected, removing each entry only once it is sent. With the bridge stopped
the queue waits and says so in ``gaia automation list``. Removing or pausing
an automation drops what waits for it.

A repeating automation keeps one entry: a newer run replaces the one still
waiting, so a bridge that was off for a day gets one summary, not 24.
Stored in ``~/.gaia/automations/outbox.json``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable, Dict, List, Optional

from gaia.automations import files, store
from gaia.automations.runner import AlertFilter, AnswerFilter
from gaia.logger import get_logger

log = get_logger(__name__)

KIND_POST = "post"
KIND_TURN = "turn"

#: Delivered this long after it came due, a message says it is late.
LATE_SECONDS = 120


@dataclass
class Delivery:
    id: str
    automation_id: str
    name: str
    kind: str
    #: A post's text, or a turn's prompt.
    text: str
    heading: str = ""
    quiet: bool = False
    #: The turn's answer goes through the alert filter of ``automation_id``.
    alert: bool = False
    repeats: bool = False
    due_at: float = 0.0


def path() -> Path:
    from gaia.home import gaia_home

    return gaia_home() / "automations" / "outbox.json"


def _locked():
    from gaia.utils.file_lock import file_lock

    return file_lock(path())


def _load() -> List[Delivery]:
    data = files.read_json_or_set_aside(path(), "undelivered automation messages")
    if data is None:
        return []
    names = {f.name for f in fields(Delivery)}
    try:
        return [
            Delivery(**{k: v for k, v in d.items() if k in names})
            for d in data.get("deliveries", [])
        ]
    except TypeError as e:
        raise files.CorruptStateFile(
            f"{path()} is not usable: {e}. Delete it to drop what is waiting."
        ) from e


def _save(items: List[Delivery]) -> None:
    files.write_json(path(), {"deliveries": [asdict(d) for d in items]})


def enqueue(delivery: Delivery) -> None:
    with _locked():
        items = _load()
        if delivery.repeats:
            items = [
                d
                for d in items
                if not (
                    d.automation_id == delivery.automation_id
                    and d.kind == delivery.kind
                )
            ]
        items.append(delivery)
        _save(items)


def pending() -> List[Delivery]:
    with _locked():
        return _load()


def _drop(gone: Callable[[Delivery], bool]) -> List[Delivery]:
    with _locked():
        items = _load()
        dropped = [d for d in items if gone(d)]
        if dropped:
            _save([d for d in items if not gone(d)])
        return dropped


def remove(delivery_id: str) -> Optional[Delivery]:
    """Drop one delivery: it was sent, or the user no longer wants it."""
    dropped = _drop(lambda d: d.id == delivery_id)
    return dropped[0] if dropped else None


def drop_for(automation_id: str) -> int:
    """Drop what waits for a removed or paused automation; returns how many."""
    if not automation_id:
        return 0
    return len(_drop(lambda d: d.automation_id == automation_id))


def drop_named(name: str) -> List[Delivery]:
    """Drop what waits under *name*: a reminder that already ran has no other handle."""
    lowered = name.strip().lower()
    return _drop(lambda d: bool(d.automation_id) and d.name.lower() == lowered)


def drop_all() -> int:
    """Drop everything waiting for an automation (not GAIA's own notices)."""
    return len(_drop(lambda d: bool(d.automation_id)))


def late_note(delivery: Delivery, now: float) -> str:
    """A line saying the message is late, or "" when it is on time."""
    if now - delivery.due_at < LATE_SECONDS:
        return ""
    when = time.strftime("%a %H:%M", time.localtime(delivery.due_at))
    return f"-# Due {when}; sent late because the Discord bridge wasn't running."


def answer_filter(delivery: Delivery) -> Optional[AnswerFilter]:
    return AlertFilter(delivery.automation_id) if delivery.alert else None


class OutboxSink:
    """The daemon's sink: queues each automation's output for the bridge."""

    def __init__(self, check_env: Callable[[], Optional[Dict[str, str]]]) -> None:
        self._check_env = check_env

    def for_item(self, item: store.Automation) -> "_ItemSink":
        return _ItemSink(item, self._check_env)


class _ItemSink:
    def __init__(
        self,
        item: store.Automation,
        check_env: Callable[[], Optional[Dict[str, str]]],
    ) -> None:
        self._item = item
        self._env = check_env

    def _queue(self, kind: str, text: str, **extra) -> None:
        enqueue(
            Delivery(
                id=uuid.uuid4().hex[:8],
                automation_id=self._item.id,
                name=self._item.name,
                kind=kind,
                text=text,
                repeats=self._item.repeats,
                due_at=self._item.last_run_at or time.time(),
                **extra,
            )
        )

    def post(self, text: str) -> bool:
        try:
            self._queue(KIND_POST, text)
        except (files.StateFileError, OSError) as e:
            log.error("Could not queue %s for Discord: %s", self._item.name, e)
            return False
        return True

    def start_turn(
        self,
        prompt: str,
        heading: str,
        *,
        quiet: bool,
        answer_filter: Optional[AnswerFilter] = None,
    ) -> None:
        if answer_filter is not None and not isinstance(answer_filter, AlertFilter):
            raise TypeError(
                "An automation turn queued for later can only carry the alert "
                f"filter, not {type(answer_filter).__name__}."
            )
        self._queue(
            KIND_TURN,
            prompt,
            heading=heading,
            quiet=quiet,
            alert=answer_filter is not None,
        )

    def check_env(self) -> Optional[Dict[str, str]]:
        return self._env()
