# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Schedule persistence: read/write ``~/.gaia/schedules.toml``.

The store is the single source of truth so schedules survive daemon restarts.
The file is intentionally human-readable and hand-editable.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    List,
    NamedTuple,
    Optional,
    Protocol,
    runtime_checkable,
)

# tomllib is stdlib on 3.11+; fall back to tomli on 3.10 (python_requires>=3.10).
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

from gaia.schedule.lock import store_lock

DEFAULT_STORE_PATH = Path(os.path.expanduser("~/.gaia/schedules.toml"))


#: States of a one-time schedule. Only ``pending`` ever fires.
ONE_SHOT_PENDING = "pending"
ONE_SHOT_RUNNING = "running"
ONE_SHOT_COMPLETED = "completed"
ONE_SHOT_FAILED = "failed"
ONE_SHOT_STATES = (
    ONE_SHOT_PENDING,
    ONE_SHOT_RUNNING,
    ONE_SHOT_COMPLETED,
    ONE_SHOT_FAILED,
)

_RELATIVE_AT = re.compile(r"\+((?:\d+[dhm])+)")
_RELATIVE_PART = re.compile(r"(\d+)([dhm])")
_UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60}
_AT_EXAMPLES = (
    "an ISO datetime like 2026-11-02T09:00 or a delay like +30m, +2h or +1d12h"
)


def parse_at(value: str, now: Optional[datetime] = None) -> datetime:
    """Turn an ``--at`` value into an aware UTC datetime strictly after *now*.

    Accepts an ISO datetime (no offset means local time) or a relative
    ``+<n><unit>`` with units ``d``, ``h``, ``m`` (``+2h``, ``+1h30m``).
    Raises :class:`ValueError` for anything unreadable or not in the future.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    text = value.strip()
    relative = _RELATIVE_AT.fullmatch(text)
    if relative:
        seconds = sum(
            int(n) * _UNIT_SECONDS[unit]
            for n, unit in _RELATIVE_PART.findall(relative.group(1))
        )
        if seconds <= 0:
            raise ValueError(
                f"--at {value!r} is not in the future; give {_AT_EXAMPLES}"
            )
        return (now + timedelta(seconds=seconds)).replace(microsecond=0)
    # A date alone would silently mean midnight.
    if text.startswith("+") or not re.search(r"\d[T ]\d", text):
        raise ValueError(
            f"--at {value!r} is not a time GAIA can read; give {_AT_EXAMPLES}"
        )
    try:
        moment = datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
        )
    except ValueError as e:
        raise ValueError(
            f"--at {value!r} is not a time GAIA can read; give {_AT_EXAMPLES}"
        ) from e
    # No offset means the user's wall clock.
    moment = (moment if moment.tzinfo else moment.astimezone()).astimezone(timezone.utc)
    if moment <= now:
        raise ValueError(
            f"--at {value!r} is in the past (it is now "
            f"{now.astimezone():%Y-%m-%d %H:%M %Z}); give a future time"
        )
    return moment


@dataclass
class Schedule:
    """One scheduled job. Exactly one of ``skill`` or ``prompt`` must be set,
    and exactly one of ``cron`` (repeats) or ``at`` (runs once)."""

    name: str
    cron: Optional[str] = None
    skill: Optional[str] = None
    prompt: Optional[str] = None
    sink: str = "stdout"
    sink_args: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    session_ref: Optional[str] = None
    created_at: str = ""
    #: Runs that came due while nothing could run them, and the latest one.
    missed_count: int = 0
    last_missed: Optional[str] = None
    #: ``missed_count`` is a lower bound: one stretch passed the count cap.
    missed_at_least: bool = False
    #: When it was last resumed: runs due while it was paused are not missed.
    resumed_at: Optional[str] = None
    #: Since when missed runs are counted. Unset on a schedule saved before
    #: they were, whose old ``next_run`` cannot be trusted to count from.
    missed_since: Optional[str] = None
    #: A one-time schedule's due time (ISO, UTC). Unset on a cron schedule.
    at: Optional[str] = None
    #: A one-time schedule's state, one of ``ONE_SHOT_STATES``.
    state: Optional[str] = None

    def __post_init__(self) -> None:
        if bool(self.skill) == bool(self.prompt):
            raise ValueError(
                f"schedule {self.name!r}: set exactly one of --skill or --prompt "
                f"(got skill={self.skill!r}, prompt={self.prompt!r})"
            )
        if bool(self.cron) == bool(self.at):
            raise ValueError(
                f"schedule {self.name!r}: set exactly one of --cron or --at "
                f"(got cron={self.cron!r}, at={self.at!r})"
            )
        if self.at and self.state is None:
            self.state = ONE_SHOT_PENDING
        if self.at and self.state not in ONE_SHOT_STATES:
            raise ValueError(
                f"schedule {self.name!r} has an unknown state {self.state!r}; "
                f"expected one of {', '.join(ONE_SHOT_STATES)}"
            )
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    @property
    def one_shot(self) -> bool:
        return bool(self.at)

    def due_at(self) -> datetime:
        """A one-time schedule's due time as an aware UTC datetime."""
        try:
            due = datetime.fromisoformat(self.at or "")
        except ValueError as e:
            raise ValueError(
                f"schedule {self.name!r} has an unreadable at {self.at!r} in the "
                "schedule store; fix or delete that line in schedules.toml"
            ) from e
        return (due if due.tzinfo else due.replace(tzinfo=timezone.utc)).astimezone(
            timezone.utc
        )

    def to_toml_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "sink": self.sink,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }
        if self.cron:
            d["cron"] = self.cron
        if self.at:
            d["at"] = self.at
            d["state"] = self.state
        if self.skill:
            d["skill"] = self.skill
        if self.prompt:
            d["prompt"] = self.prompt
        if self.sink_args:
            d["sink_args"] = self.sink_args
        if self.last_run:
            d["last_run"] = self.last_run
        if self.next_run:
            d["next_run"] = self.next_run
        if self.session_ref:
            d["session_ref"] = self.session_ref
        if self.missed_count:
            d["missed_count"] = self.missed_count
        if self.last_missed:
            d["last_missed"] = self.last_missed
        if self.missed_at_least:
            d["missed_at_least"] = True
        if self.resumed_at:
            d["resumed_at"] = self.resumed_at
        if self.missed_since:
            d["missed_since"] = self.missed_since
        return d

    @classmethod
    def from_toml_dict(cls, name: str, data: Dict[str, Any]) -> "Schedule":
        return cls(
            name=name,
            cron=data.get("cron"),
            at=data.get("at"),
            state=data.get("state"),
            skill=data.get("skill"),
            prompt=data.get("prompt"),
            sink=data.get("sink", "stdout"),
            sink_args=data.get("sink_args", {}) or {},
            enabled=data.get("enabled", True),
            last_run=data.get("last_run"),
            next_run=data.get("next_run"),
            session_ref=data.get("session_ref"),
            created_at=data.get("created_at", ""),
            missed_count=int(data.get("missed_count", 0)),
            last_missed=data.get("last_missed"),
            missed_at_least=bool(data.get("missed_at_least", False)),
            resumed_at=data.get("resumed_at"),
            missed_since=data.get("missed_since"),
        )


class MissedRuns(NamedTuple):
    """What one check found: due times nothing ran (ISO, oldest first), the
    next run the daemon expects, and since when misses are counted."""

    missed: List[str]
    next_run: Optional[str]
    since: Optional[str] = None
    #: The count stopped at its cap: there were at least that many.
    at_least: bool = False


@runtime_checkable
class ScheduleStore(Protocol):
    """Storage-backend interface for schedules.

    :class:`TomlScheduleStore` is the default, file-backed implementation. The
    Protocol exists so an alternative backend (e.g. database-backed) can be
    swapped in without changing the daemon or CLI.
    """

    def load(self) -> Dict[str, Schedule]: ...

    def save(self, schedules: Dict[str, Schedule]) -> None: ...

    def add(self, schedule: Schedule) -> None: ...

    def remove(self, name: str) -> None: ...

    def get(self, name: str) -> Schedule: ...

    def set_enabled(self, name: str, enabled: bool) -> Schedule: ...

    def mark_run(
        self, name: str, last_run: str, next_run: Optional[str] = None
    ) -> Schedule: ...

    def account_missed(
        self, name: str, decide: Callable[[Schedule], Optional[MissedRuns]]
    ) -> Optional[Schedule]: ...

    def claim_one_shot(
        self, name: str, now: datetime, *, due_only: bool = True
    ) -> Optional[Schedule]: ...

    def finish_one_shot(self, name: str, finished_at: str, ok: bool) -> Schedule: ...


class TomlScheduleStore:
    """Load/save a collection of :class:`Schedule` objects to a TOML file."""

    def __init__(self, path: Path = DEFAULT_STORE_PATH):
        self.path = Path(path)

    def load(self) -> Dict[str, Schedule]:
        if not self.path.exists():
            return {}
        with open(self.path, "rb") as f:
            raw = tomllib.load(f)
        schedules = raw.get("schedules", {})
        return {
            name: Schedule.from_toml_dict(name, data)
            for name, data in schedules.items()
        }

    def save(self, schedules: Dict[str, Schedule]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schedules": {s.name: s.to_toml_dict() for s in schedules.values()}}
        pending = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=self.path.parent, delete=False
            ) as f:
                pending = Path(f.name)
                tomli_w.dump(doc, f)
                # os.replace is atomic against concurrent readers, but only
                # once the data itself is actually on disk -- without this a
                # crash between the write and the rename can still surface a
                # zero-length store.
                f.flush()
                os.fsync(f.fileno())
            os.replace(pending, self.path)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)

    def add(self, schedule: Schedule) -> None:
        # A new schedule counts the runs it misses from the moment it exists.
        schedule.missed_since = schedule.missed_since or schedule.created_at
        with store_lock(self.path):
            schedules = self.load()
            if schedule.name in schedules:
                raise ValueError(
                    f"schedule {schedule.name!r} already exists in {self.path}; "
                    f"remove it first or pick a different --name"
                )
            schedules[schedule.name] = schedule
            self.save(schedules)

    def remove(self, name: str) -> None:
        with store_lock(self.path):
            schedules = self.load()
            if name not in schedules:
                raise KeyError(
                    f"no schedule named {name!r} in {self.path}; "
                    f"run `gaia schedule list` to see registered schedules"
                )
            del schedules[name]
            self.save(schedules)

    def get(self, name: str) -> Schedule:
        schedules = self.load()
        if name not in schedules:
            raise KeyError(
                f"no schedule named {name!r} in {self.path}; "
                f"run `gaia schedule list` to see registered schedules"
            )
        return schedules[name]

    def set_enabled(self, name: str, enabled: bool) -> Schedule:
        with store_lock(self.path):
            schedules = self.load()
            if name not in schedules:
                raise KeyError(
                    f"no schedule named {name!r} in {self.path}; "
                    f"run `gaia schedule list` to see registered schedules"
                )
            if enabled and not schedules[name].enabled:
                schedules[name].resumed_at = datetime.now(timezone.utc).isoformat()
                schedules[name].next_run = None
            schedules[name].enabled = enabled
            self.save(schedules)
            return schedules[name]

    def mark_run(
        self, name: str, last_run: str, next_run: Optional[str] = None
    ) -> Schedule:
        with store_lock(self.path):
            schedules = self.load()
            if name not in schedules:
                raise KeyError(
                    f"no schedule named {name!r} in {self.path}; "
                    f"run `gaia schedule list` to see registered schedules"
                )
            schedules[name].last_run = last_run
            if next_run is not None:
                schedules[name].next_run = next_run
            self.save(schedules)
            return schedules[name]

    def account_missed(
        self, name: str, decide: Callable[[Schedule], Optional[MissedRuns]]
    ) -> Optional[Schedule]:
        """Apply what *decide* says the schedule missed, under the store lock.

        *decide* sees the schedule as it is on disk now and returns the runs
        it missed, or None to leave it alone. Returns the updated schedule, or
        None when there is no such schedule any more.
        """
        with store_lock(self.path):
            schedules = self.load()
            schedule = schedules.get(name)
            if schedule is None:
                return None
            outcome = decide(schedule)
            if outcome is None:
                return schedule
            if outcome.missed:
                schedule.missed_count += len(outcome.missed)
                schedule.last_missed = outcome.missed[-1]
                schedule.missed_at_least = schedule.missed_at_least or outcome.at_least
            schedule.next_run = outcome.next_run
            schedule.missed_since = schedule.missed_since or outcome.since
            self.save(schedules)
            return schedule

    def claim_one_shot(
        self, name: str, now: datetime, *, due_only: bool = True
    ) -> Optional[Schedule]:
        """Move a pending one-time schedule to ``running``, under the store lock.

        Returns it when this caller won the claim and must run it, or None when
        it is gone, already claimed, or (with *due_only*) paused or not yet due.
        The claim is what keeps a one-time schedule from ever running twice.
        """
        with store_lock(self.path):
            schedules = self.load()
            schedule = schedules.get(name)
            if (
                schedule is None
                or not schedule.one_shot
                or schedule.state != ONE_SHOT_PENDING
            ):
                return None
            if due_only and (not schedule.enabled or schedule.due_at() > now):
                return None
            schedule.state = ONE_SHOT_RUNNING
            self.save(schedules)
            return schedule

    def finish_one_shot(self, name: str, finished_at: str, ok: bool) -> Schedule:
        """Record how a claimed one-time schedule's only run ended."""
        with store_lock(self.path):
            schedules = self.load()
            if name not in schedules:
                raise KeyError(
                    f"no schedule named {name!r} in {self.path}; it was removed "
                    "while its one-time run was in progress"
                )
            schedule = schedules[name]
            schedule.last_run = finished_at
            schedule.state = ONE_SHOT_COMPLETED if ok else ONE_SHOT_FAILED
            self.save(schedules)
            return schedule
