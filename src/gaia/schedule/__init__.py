"""Cron-based recurring execution for GAIA skills and prompts (issue #892).

A minimal scheduler: register a skill or prompt to run on a cron schedule, and
deliver the agent's output to a configurable sink (stdout, file, notification,
Telegram). Persisted in ``~/.gaia/schedules.toml`` and driven by a long-running
``gaia schedule daemon``.
"""

from gaia.schedule.store import Schedule, ScheduleStore, TomlScheduleStore

__all__ = ["Schedule", "ScheduleStore", "TomlScheduleStore"]
