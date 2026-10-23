# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""When a scheduled cycle may start: in the window, user away, on mains power,
toolchain ready, and not already tried tonight."""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import List, Optional

from gaia.learning.config import LearningConfig


@dataclass
class GateResult:
    open: bool
    reasons: List[str] = field(default_factory=list)


def _hhmm(text: str) -> time:
    h, m = text.split(":")
    return time(int(h), int(m))


def in_window(now: datetime, start: str, end: str) -> bool:
    """*now* (local) falls in [start, end); the window may wrap midnight."""
    s, e, t = _hhmm(start), _hhmm(end), now.time()
    if s == e:
        return True
    return s <= t < e if s < e else (t >= s or t < e)


def window_start_for(now: datetime, start: str) -> datetime:
    """The start of the window *now* is in (or the most recent one)."""
    s = _hhmm(start)
    candidate = now.replace(hour=s.hour, minute=s.minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)
    return candidate


def on_ac_power() -> bool:
    """True on mains power or with no battery (a desktop)."""
    import psutil

    battery = psutil.sensors_battery()
    return battery is None or bool(battery.power_plugged)


def check_gates(
    cfg: LearningConfig,
    *,
    now: datetime,
    last_activity: Optional[datetime],
    last_scheduled_start: Optional[datetime],
    toolchain_ready: bool,
    ac_power: bool,
) -> GateResult:
    """*now*, *last_activity* and *last_scheduled_start* are timezone-aware."""
    reasons = []
    local = now.astimezone()
    if not cfg.enabled:
        reasons.append("nightly learning is off")
    if not in_window(local, cfg.window_start, cfg.window_end):
        reasons.append(f"outside the {cfg.window_start}-{cfg.window_end} window")
    if last_activity is not None:
        idle = (now - last_activity).total_seconds() / 60
        if idle < cfg.min_idle_minutes:
            reasons.append(
                f"the agent was used {int(idle)} min ago (waits for {cfg.min_idle_minutes})"
            )
    if cfg.require_ac_power and not ac_power:
        reasons.append("running on battery")
    if not toolchain_ready:
        reasons.append("the training toolchain isn't ready (`gaia learn setup`)")
    if last_scheduled_start is not None:
        window_start = window_start_for(local, cfg.window_start)
        if last_scheduled_start.astimezone() >= window_start:
            reasons.append("already ran in tonight's window")
    return GateResult(open=not reasons, reasons=reasons)
