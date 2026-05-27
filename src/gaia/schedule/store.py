"""Schedule persistence: read/write ``~/.gaia/schedules.toml``.

The store is the single source of truth so schedules survive daemon restarts.
The file is intentionally human-readable and hand-editable.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# tomllib is stdlib on 3.11+; fall back to tomli on 3.10 (python_requires>=3.10).
try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib  # type: ignore[no-redef]

import tomli_w

DEFAULT_STORE_PATH = Path(os.path.expanduser("~/.gaia/schedules.toml"))


@dataclass
class Schedule:
    """One scheduled job. Exactly one of ``skill`` or ``prompt`` must be set."""

    name: str
    cron: str
    skill: Optional[str] = None
    prompt: Optional[str] = None
    sink: str = "stdout"
    sink_args: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: str = ""

    def __post_init__(self) -> None:
        if bool(self.skill) == bool(self.prompt):
            raise ValueError(
                f"schedule {self.name!r}: set exactly one of --skill or --prompt "
                f"(got skill={self.skill!r}, prompt={self.prompt!r})"
            )
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_toml_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "cron": self.cron,
            "sink": self.sink,
            "enabled": self.enabled,
            "created_at": self.created_at,
        }
        if self.skill:
            d["skill"] = self.skill
        if self.prompt:
            d["prompt"] = self.prompt
        if self.sink_args:
            d["sink_args"] = self.sink_args
        return d

    @classmethod
    def from_toml_dict(cls, name: str, data: Dict[str, Any]) -> "Schedule":
        return cls(
            name=name,
            cron=data["cron"],
            skill=data.get("skill"),
            prompt=data.get("prompt"),
            sink=data.get("sink", "stdout"),
            sink_args=data.get("sink_args", {}) or {},
            enabled=data.get("enabled", True),
            created_at=data.get("created_at", ""),
        )


class ScheduleStore:
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
        doc = {
            "schedules": {s.name: s.to_toml_dict() for s in schedules.values()}
        }
        with open(self.path, "wb") as f:
            tomli_w.dump(doc, f)

    def add(self, schedule: Schedule) -> None:
        schedules = self.load()
        if schedule.name in schedules:
            raise ValueError(
                f"schedule {schedule.name!r} already exists in {self.path}; "
                f"remove it first or pick a different --name"
            )
        schedules[schedule.name] = schedule
        self.save(schedules)

    def remove(self, name: str) -> None:
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
        schedules = self.load()
        if name not in schedules:
            raise KeyError(
                f"no schedule named {name!r} in {self.path}; "
                f"run `gaia schedule list` to see registered schedules"
            )
        schedules[name].enabled = enabled
        self.save(schedules)
        return schedules[name]
