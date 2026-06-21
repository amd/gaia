# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for the schedule store (``gaia.schedule.store``).

Covers the :class:`Schedule` dataclass (TOML round-trip including the new
``last_run`` / ``next_run`` / ``session_ref`` fields, the exactly-one-of
skill/prompt invariant, and the ``created_at`` auto-stamp) and the
:class:`TomlScheduleStore` CRUD surface (add/get/remove/set_enabled/mark_run),
plus the structural :class:`ScheduleStore` Protocol.

All tests use ``tmp_path`` — no real ``~/.gaia/schedules.toml`` is touched.
"""

from __future__ import annotations

import pytest

from gaia.schedule import TomlScheduleStore as ReexportedTomlStore
from gaia.schedule.store import Schedule, ScheduleStore, TomlScheduleStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_schedule(name: str = "daily", **overrides) -> Schedule:
    """A valid prompt-based Schedule with sensible defaults for overriding."""
    kwargs = {
        "name": name,
        "cron": "0 9 * * *",
        "prompt": "say hello",
    }
    kwargs.update(overrides)
    return Schedule(**kwargs)


def _store(tmp_path) -> TomlScheduleStore:
    return TomlScheduleStore(tmp_path / "schedules.toml")


# ===========================================================================
# 1. Schedule dataclass — invariants
# ===========================================================================


class TestScheduleInvariants:

    def test_prompt_only_is_valid(self):
        sched = Schedule(name="p", cron="* * * * *", prompt="hi")
        assert sched.prompt == "hi"
        assert sched.skill is None

    def test_skill_only_is_valid(self):
        sched = Schedule(name="s", cron="* * * * *", skill="my-skill")
        assert sched.skill == "my-skill"
        assert sched.prompt is None

    def test_both_skill_and_prompt_raises(self):
        with pytest.raises(ValueError, match="exactly one of"):
            Schedule(name="x", cron="* * * * *", skill="s", prompt="p")

    def test_neither_skill_nor_prompt_raises(self):
        with pytest.raises(ValueError, match="exactly one of"):
            Schedule(name="x", cron="* * * * *")

    def test_created_at_auto_stamped_when_missing(self):
        sched = _make_schedule()
        assert sched.created_at  # non-empty ISO timestamp
        # Parseable as an ISO8601 datetime.
        from datetime import datetime

        datetime.fromisoformat(sched.created_at)

    def test_created_at_preserved_when_provided(self):
        sched = _make_schedule(created_at="2020-01-01T00:00:00+00:00")
        assert sched.created_at == "2020-01-01T00:00:00+00:00"

    def test_new_optional_fields_default_to_none(self):
        sched = _make_schedule()
        assert sched.last_run is None
        assert sched.next_run is None
        assert sched.session_ref is None


# ===========================================================================
# 2. Schedule TOML round-trip
# ===========================================================================


class TestScheduleToToml:

    def test_required_fields_present(self):
        d = _make_schedule().to_toml_dict()
        assert d["cron"] == "0 9 * * *"
        assert d["sink"] == "stdout"
        assert d["enabled"] is True
        assert d["created_at"]

    def test_prompt_present_skill_absent(self):
        d = _make_schedule(prompt="hi").to_toml_dict()
        assert d["prompt"] == "hi"
        assert "skill" not in d

    def test_skill_present_prompt_absent(self):
        d = Schedule(name="s", cron="* * * * *", skill="sk").to_toml_dict()
        assert d["skill"] == "sk"
        assert "prompt" not in d

    def test_sink_args_omitted_when_empty(self):
        d = _make_schedule().to_toml_dict()
        assert "sink_args" not in d

    def test_sink_args_present_when_set(self):
        d = _make_schedule(sink="file", sink_args={"path": "/tmp/x.md"}).to_toml_dict()
        assert d["sink_args"] == {"path": "/tmp/x.md"}

    def test_new_fields_omitted_when_none(self):
        d = _make_schedule().to_toml_dict()
        assert "last_run" not in d
        assert "next_run" not in d
        assert "session_ref" not in d

    def test_last_run_present_when_set(self):
        d = _make_schedule(last_run="2026-01-01T00:00:00+00:00").to_toml_dict()
        assert d["last_run"] == "2026-01-01T00:00:00+00:00"

    def test_next_run_present_when_set(self):
        d = _make_schedule(next_run="2026-01-02T00:00:00+00:00").to_toml_dict()
        assert d["next_run"] == "2026-01-02T00:00:00+00:00"

    def test_session_ref_present_when_set(self):
        d = _make_schedule(session_ref="abc-123").to_toml_dict()
        assert d["session_ref"] == "abc-123"


class TestScheduleFromToml:

    def test_minimal_dict_defaults(self):
        sched = Schedule.from_toml_dict("n", {"cron": "* * * * *", "prompt": "hi"})
        assert sched.name == "n"
        assert sched.sink == "stdout"
        assert sched.enabled is True
        assert sched.sink_args == {}
        assert sched.last_run is None
        assert sched.next_run is None
        assert sched.session_ref is None

    def test_full_round_trip_preserves_all_fields(self):
        original = _make_schedule(
            name="full",
            sink="file",
            sink_args={"path": "/tmp/log.md"},
            enabled=False,
            last_run="2026-01-01T00:00:00+00:00",
            next_run="2026-01-02T00:00:00+00:00",
            session_ref="sess-9",
            created_at="2025-12-31T00:00:00+00:00",
        )
        revived = Schedule.from_toml_dict(original.name, original.to_toml_dict())
        assert revived == original

    def test_from_toml_round_trips_new_fields(self):
        data = {
            "cron": "* * * * *",
            "prompt": "hi",
            "last_run": "2026-01-01T00:00:00+00:00",
            "next_run": "2026-01-02T00:00:00+00:00",
            "session_ref": "sess-1",
        }
        sched = Schedule.from_toml_dict("n", data)
        assert sched.last_run == "2026-01-01T00:00:00+00:00"
        assert sched.next_run == "2026-01-02T00:00:00+00:00"
        assert sched.session_ref == "sess-1"

    def test_null_sink_args_coerced_to_dict(self):
        sched = Schedule.from_toml_dict(
            "n", {"cron": "* * * * *", "prompt": "hi", "sink_args": None}
        )
        assert sched.sink_args == {}


# ===========================================================================
# 3. TomlScheduleStore — persistence + CRUD
# ===========================================================================


class TestTomlScheduleStore:

    def test_load_missing_file_returns_empty(self, tmp_path):
        store = _store(tmp_path)
        assert store.load() == {}

    def test_add_then_get(self, tmp_path):
        store = _store(tmp_path)
        sched = _make_schedule("a")
        store.add(sched)
        got = store.get("a")
        assert got.name == "a"
        assert got.prompt == "say hello"

    def test_add_persists_across_store_instances(self, tmp_path):
        path = tmp_path / "schedules.toml"
        TomlScheduleStore(path).add(_make_schedule("a"))
        # A brand new store instance reads the same file from disk.
        reloaded = TomlScheduleStore(path).load()
        assert "a" in reloaded
        assert reloaded["a"].prompt == "say hello"

    def test_add_duplicate_raises_value_error(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a"))
        with pytest.raises(ValueError, match="already exists"):
            store.add(_make_schedule("a"))

    def test_get_missing_raises_key_error(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(KeyError, match="gaia schedule list"):
            store.get("nope")

    def test_remove(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a"))
        store.remove("a")
        assert store.load() == {}

    def test_remove_missing_raises_key_error(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(KeyError, match="gaia schedule list"):
            store.remove("nope")

    def test_set_enabled_toggles_and_persists(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a", enabled=True))
        returned = store.set_enabled("a", False)
        assert returned.enabled is False
        # Persisted to disk, not just mutated in memory.
        assert store.get("a").enabled is False

    def test_set_enabled_missing_raises_key_error(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(KeyError, match="gaia schedule list"):
            store.set_enabled("nope", False)

    def test_save_load_multiple_round_trip(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a"))
        store.add(_make_schedule("b", skill="sk", prompt=None))
        loaded = store.load()
        assert set(loaded) == {"a", "b"}
        assert loaded["b"].skill == "sk"


# ===========================================================================
# 4. TomlScheduleStore.mark_run
# ===========================================================================


class TestMarkRun:

    def test_mark_run_sets_last_run(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a"))
        returned = store.mark_run("a", "2026-06-21T09:00:00+00:00")
        assert returned.last_run == "2026-06-21T09:00:00+00:00"
        # Persisted.
        assert store.get("a").last_run == "2026-06-21T09:00:00+00:00"

    def test_mark_run_sets_next_run_when_provided(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a"))
        store.mark_run(
            "a",
            "2026-06-21T09:00:00+00:00",
            next_run="2026-06-22T09:00:00+00:00",
        )
        got = store.get("a")
        assert got.last_run == "2026-06-21T09:00:00+00:00"
        assert got.next_run == "2026-06-22T09:00:00+00:00"

    def test_mark_run_leaves_next_run_untouched_when_omitted(self, tmp_path):
        store = _store(tmp_path)
        store.add(_make_schedule("a", next_run="2026-01-01T00:00:00+00:00"))
        store.mark_run("a", "2026-06-21T09:00:00+00:00")
        got = store.get("a")
        assert got.last_run == "2026-06-21T09:00:00+00:00"
        # next_run was not passed, so the prior value survives.
        assert got.next_run == "2026-01-01T00:00:00+00:00"

    def test_mark_run_missing_raises_key_error(self, tmp_path):
        store = _store(tmp_path)
        with pytest.raises(KeyError, match="gaia schedule list"):
            store.mark_run("nope", "2026-06-21T09:00:00+00:00")


# ===========================================================================
# 5. ScheduleStore Protocol (structural typing)
# ===========================================================================


class TestScheduleStoreProtocol:

    def test_concrete_store_satisfies_protocol(self, tmp_path):
        store = _store(tmp_path)
        # @runtime_checkable Protocol — structural, no inheritance.
        assert isinstance(store, ScheduleStore)

    def test_protocol_not_inherited_by_concrete(self):
        # TomlScheduleStore must NOT inherit the Protocol — pure duck typing.
        assert ScheduleStore not in TomlScheduleStore.__mro__

    def test_reexport_is_concrete_store(self):
        assert ReexportedTomlStore is TomlScheduleStore
