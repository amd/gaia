# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The daemon owns the automations tick; the Discord bridge delivers.

Covers the tick lease, the daemon's pass, the outbox the bridge drains, and the
case that matters most: with both up, nothing due runs or arrives twice.
"""

from __future__ import annotations

import json
import logging
import time
from unittest import mock

import pytest

from gaia.automations import cli, daemon_tick, outbox, runner, store, tick
from gaia.messaging.discord import state

from .test_discord_adapter import DM, make


@pytest.fixture(autouse=True)
def gaia_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIA_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("GAIA_HOME", raising=False)
    # The real one copies gaia.exe on Windows; a check here needs no PATH.
    monkeypatch.setattr(daemon_tick, "_check_env", lambda: None)


def _paired():
    state.save_config(
        state.BridgeConfig(
            bot_id="900",
            owner_id="111",
            owner_name="me",
            dm_channel_id=DM,
            instance_id="a" * 16,
        )
    )


def _reminder(at, say="Stretch", name="Stretch"):
    return store.add(
        store.Automation("", name, store.KIND_REMINDER, say=say, next_at=at)
    )


def _summary(at, name="Digest"):
    return store.add(
        store.Automation(
            "",
            name,
            store.KIND_SUMMARY,
            prompt="summarize my PRs",
            every_seconds=600,
            next_at=at,
        )
    )


def _texts(ad):
    return [m["text"] for m in ad.transport.sent]


# ----------------------------------------------------------------------
# The tick lease
# ----------------------------------------------------------------------


def test_the_daemon_takes_the_tick_from_a_running_bridge():
    now = time.time()
    assert tick.claim(tick.OWNER_DISCORD, now)
    assert tick.claim(tick.OWNER_DAEMON, now + 1)
    assert tick.holder(now + 1).owner == tick.OWNER_DAEMON
    assert not tick.claim(tick.OWNER_DISCORD, now + 2), "the bridge must stand by"


def test_a_daemon_that_stopped_refreshing_frees_the_tick():
    now = time.time()
    assert tick.claim(tick.OWNER_DAEMON, now)
    later = now + tick.STALE_SECONDS + 1
    assert tick.holder(later) is None
    assert tick.claim(tick.OWNER_DISCORD, later)


def test_a_dead_daemon_frees_the_tick_at_once(monkeypatch):
    now = time.time()
    assert tick.claim(tick.OWNER_DAEMON, now, pid=4242)
    monkeypatch.setattr(tick, "_alive", lambda pid: pid != 4242)
    assert tick.claim(tick.OWNER_DISCORD, now + 1)


def test_a_stopped_daemon_hands_the_tick_back():
    now = time.time()
    assert tick.claim(tick.OWNER_DAEMON, now)
    tick.release(tick.OWNER_DAEMON)
    assert tick.holder(now) is None
    assert tick.claim(tick.OWNER_DISCORD, now)


def test_an_unknown_owner_is_refused():
    with pytest.raises(ValueError, match="unknown automations tick owner"):
        tick.claim("telegram")


def test_an_unreadable_lease_says_how_to_fix_it():
    tick.path().parent.mkdir(parents=True, exist_ok=True)
    tick.path().write_text('{"owner": "daemon"}', encoding="utf-8")
    with pytest.raises(store.files.CorruptStateFile, match="Delete it"):
        tick.claim(tick.OWNER_DAEMON)


# ----------------------------------------------------------------------
# The daemon's pass
# ----------------------------------------------------------------------


def test_the_daemon_runs_a_due_reminder_with_the_bridge_stopped():
    _paired()
    now = time.time()
    _reminder(now - 1)
    daemon_tick.AutomationsTick()(now)
    assert store.load() == [], "the reminder was taken"
    waiting = outbox.pending()
    assert [(d.kind, d.text) for d in waiting] == [(outbox.KIND_POST, "⏰ **Stretch**")]


def test_with_nothing_to_deliver_through_nothing_is_taken_and_it_says_so(caplog):
    now = time.time()
    _reminder(now - 1)
    run = daemon_tick.AutomationsTick()
    with caplog.at_level(logging.ERROR, logger="gaia.automations.daemon_tick"):
        run(now)
        run(now + 30)
    assert len(store.load()) == 1, "what is due must wait for a messaging app"
    assert outbox.pending() == []
    said = [r for r in caplog.records if "nothing can deliver them" in r.message]
    assert len(said) == 1, "said once, not every pass"


def test_a_repeating_run_waits_once_however_many_times_it_came_due():
    _paired()
    now = time.time()
    _summary(now - 1)
    run = daemon_tick.AutomationsTick()
    for step in range(4):
        run(now + step * 601)
    waiting = outbox.pending()
    assert len(waiting) == 1, "a bridge off for a while gets one digest"
    assert waiting[0].kind == outbox.KIND_TURN
    assert waiting[0].heading == "🗓️ **Digest**"


def test_an_alert_queued_for_later_keeps_its_filter():
    _paired()
    now = time.time()
    store.add(
        store.Automation(
            "",
            "Outage",
            store.KIND_ALERT,
            prompt="is anything down?",
            every_seconds=600,
            next_at=now - 1,
        )
    )
    daemon_tick.AutomationsTick()(now)
    (waiting,) = outbox.pending()
    assert waiting.alert and waiting.quiet
    keep = outbox.answer_filter(waiting)
    assert isinstance(keep, runner.AlertFilter)
    assert keep(runner.NOTHING) is None
    assert keep("API is down\nsince 9") == "API is down\nsince 9"
    assert keep("API is down\nstill") is None, "the same news is not sent twice"


def test_a_queued_turn_refuses_a_filter_it_cannot_store():
    item = _summary(time.time())
    sink = outbox.OutboxSink(lambda: None).for_item(item)
    with pytest.raises(TypeError, match="only carry the alert filter"):
        sink.start_turn("p", "h", quiet=True, answer_filter=lambda a: a)


def test_another_daemon_holding_the_tick_is_logged(caplog, monkeypatch):
    _paired()
    now = time.time()
    _reminder(now - 1)
    assert tick.claim(tick.OWNER_DAEMON, now, pid=4242)
    monkeypatch.setattr(tick, "_alive", lambda pid: True)
    with caplog.at_level(logging.ERROR, logger="gaia.automations.daemon_tick"):
        daemon_tick.AutomationsTick()(now + 1)
    assert len(store.load()) == 1
    assert any("another daemon process" in r.message for r in caplog.records)


def test_a_damaged_automations_file_reaches_the_dm():
    _paired()
    store.path().parent.mkdir(parents=True, exist_ok=True)
    store.path().write_text("{not json", encoding="utf-8")
    daemon_tick.AutomationsTick()(time.time())
    (waiting,) = outbox.pending()
    assert "damaged" in waiting.text and "set up your automations again" in waiting.text


# ----------------------------------------------------------------------
# The bridge, with and without the daemon
# ----------------------------------------------------------------------


def test_with_both_up_a_due_reminder_arrives_exactly_once():
    ad, clock = make()
    state.save_config(ad.config)
    ad.on_ready()
    _reminder(clock.now - 1)
    daemon = daemon_tick.AutomationsTick()
    for _ in range(3):
        daemon(clock.now)
        ad.tick()
        clock.now += 60
    assert _texts(ad).count("⏰ **Stretch**") == 1
    assert outbox.pending() == []


def test_with_both_up_a_summary_runs_once_per_slot():
    ad, clock = make()
    state.save_config(ad.config)
    ad.on_ready()
    _summary(clock.now - 1)
    daemon = daemon_tick.AutomationsTick()
    # The bridge ticks first: it must still leave the run to the daemon.
    daemon(clock.now)
    ad.tick()
    ad.tick()
    daemon(clock.now + 1)
    ad.tick()
    turns = ad._agent().turns
    assert len(turns) == 1, [t.text for t in turns]
    assert turns[0].text.startswith("summarize my PRs")


def test_the_bridge_skips_the_tick_while_the_daemon_holds_it(monkeypatch):
    ad, clock = make()
    ad.on_ready()
    _reminder(clock.now - 1)
    assert tick.claim(tick.OWNER_DAEMON, clock.now)
    take_due = mock.Mock(wraps=store.take_due)
    monkeypatch.setattr(store, "take_due", take_due)
    ad.tick()
    take_due.assert_not_called()
    assert len(store.load()) == 1, "the daemon runs it, not the bridge"


def test_without_a_daemon_the_bridge_still_runs_what_is_due():
    ad, clock = make()
    ad.on_ready()
    _reminder(clock.now - 1)
    ad.tick()
    assert "⏰ **Stretch**" in _texts(ad)
    assert tick.holder(clock.now).owner == tick.OWNER_DISCORD


def test_a_reminder_queued_while_the_bridge_was_off_arrives_late_and_says_so():
    _paired()
    now = time.time()
    _reminder(now - 1)
    daemon = daemon_tick.AutomationsTick()
    daemon(now)
    # The bridge comes back an hour later.
    ad, clock = make()
    clock.now = now + 3600
    ad.on_ready()
    daemon(clock.now)
    ad.tick()
    (sent,) = [t for t in _texts(ad) if t.startswith("⏰")]
    assert sent.startswith("⏰ **Stretch**\n-# Due ")
    assert "sent late because the Discord bridge wasn't running" in sent
    assert outbox.pending() == []


def test_a_queued_summary_runs_as_a_turn_when_the_bridge_reconnects():
    _paired()
    now = time.time()
    _summary(now - 1)
    daemon_tick.AutomationsTick()(now)
    ad, clock = make()
    clock.now = now + 30
    ad.on_ready()
    assert tick.claim(tick.OWNER_DAEMON, clock.now)
    ad.tick()
    (turn,) = ad._agent().turns
    assert turn.text.startswith("summarize my PRs")
    assert turn.context.channel_id == DM
    assert turn.context.heading == "🗓️ **Digest**", "on time: no late note"


def test_a_post_discord_refuses_stays_queued(monkeypatch):
    _paired()
    now = time.time()
    _reminder(now - 1)
    daemon_tick.AutomationsTick()(now)
    ad, clock = make()
    clock.now = now + 10
    ad.on_ready()
    assert tick.claim(tick.OWNER_DAEMON, clock.now)
    monkeypatch.setattr(ad.transport, "send", lambda *a, **k: "")
    ad.tick()
    assert [d.text for d in outbox.pending()] == ["⏰ **Stretch**"]


def test_a_turn_that_cannot_start_stays_queued_and_is_reported_once(monkeypatch):
    _paired()
    now = time.time()
    _summary(now - 1, name="One")
    _summary(now - 1, name="Two")
    daemon_tick.AutomationsTick()(now)
    ad, clock = make()
    clock.now = now + 10
    ad.on_ready()
    assert tick.claim(tick.OWNER_DAEMON, clock.now)
    monkeypatch.setattr(
        ad._agent(), "submit", mock.Mock(side_effect=RuntimeError("agent gone"))
    )
    polls = mock.Mock()
    monkeypatch.setattr(ad, "_watcher", lambda: polls)
    ad.tick()
    ad.tick()
    assert sorted(d.name for d in outbox.pending()) == ["One", "Two"]
    told = [t for t in _texts(ad) if "can't run right now" in t]
    assert len(told) == 1 and "agent gone" in told[0], _texts(ad)
    assert polls.poll.call_count == 2, "the GitHub watches still run"
    placeholders = [m["id"] for m in ad.transport.sent if "Running…" in m["text"]]
    closed = {e[1] for e in ad.transport.edits if e[3]}
    assert len(placeholders) == 1, "a retry must not post another dead message"
    assert set(placeholders) <= closed, "no placeholder left open"

    # The agent answers again: both run, the retried one without a placeholder.
    started = mock.Mock(return_value=0)
    monkeypatch.setattr(ad._agent(), "submit", started)
    ad.tick()
    assert started.call_count == 2 and outbox.pending() == []
    assert len([m for m in ad.transport.sent if "Running…" in m["text"]]) == 2


def test_a_crash_mid_delivery_keeps_what_was_not_sent(monkeypatch):
    _paired()
    now = time.time()
    for name in ("A", "B", "C"):
        _reminder(now - 1, say=name, name=name)
    daemon_tick.AutomationsTick()(now)
    ad, clock = make()
    clock.now = now + 10
    ad.on_ready()
    assert tick.claim(tick.OWNER_DAEMON, clock.now)
    real_remove = outbox.remove
    calls = []

    def remove_then_die(delivery_id):
        calls.append(delivery_id)
        if len(calls) == 2:
            raise SystemExit("killed")
        real_remove(delivery_id)

    monkeypatch.setattr(outbox, "remove", remove_then_die)
    with pytest.raises(SystemExit):
        ad.tick()
    assert [d.text for d in outbox.pending()] == [
        "⏰ **B**",
        "⏰ **C**",
    ], "B was sent but not yet removed: sent again rather than lost"


def test_an_outbox_that_cannot_be_written_loses_no_reminder(monkeypatch):
    _paired()
    now = time.time()
    for name in ("A", "B", "C"):
        _reminder(now - 1, say=name, name=name)
    monkeypatch.setattr(
        outbox, "enqueue", mock.Mock(side_effect=PermissionError("locked by AV"))
    )
    daemon_tick.AutomationsTick()(now)
    assert sorted(a.name for a in store.load()) == [
        "A",
        "B",
        "C",
    ], "each reminder goes back to retry, none is dropped"


def test_a_closed_bridge_hands_the_tick_back():
    ad, clock = make()
    ad.on_ready()
    ad.tick()
    assert tick.holder(clock.now).owner == tick.OWNER_DISCORD
    ad.close()
    assert tick.holder(clock.now) is None


def test_the_list_shows_what_waits_for_the_bridge():
    _paired()
    now = time.time()
    _reminder(now - 1)
    daemon_tick.AutomationsTick()(now)
    out = []
    cli.run_list(as_json=False, emit=out.append)
    assert len(out) == 1 and "Stretch" in out[0]
    assert "reaches your Discord DM when the Discord bridge runs" in out[0]


# ----------------------------------------------------------------------
# Stopping an automation stops what already waits for it
# ----------------------------------------------------------------------


def _queued_summary(name="Digest"):
    _paired()
    now = time.time()
    item = _summary(now - 1, name=name)
    daemon_tick.AutomationsTick()(now)
    assert [d.name for d in outbox.pending()] == [name]
    return item


def _run(action, ref):
    out = []
    code = cli.run_set(ref=ref, action=action, emit=out.append)
    return code, out[-1]


def test_removing_an_automation_drops_what_waited_for_it():
    _queued_summary()
    assert _run("remove", "Digest") == (0, "Removed 'Digest'.")
    assert outbox.pending() == []
    ad, clock = make()
    ad.on_ready()
    ad.tick()
    assert ad._agent().turns == [], "a removed summary must not run on reconnect"


def test_pausing_an_automation_drops_what_waited_for_it():
    _queued_summary()
    assert _run("pause", "Digest")[0] == 0
    assert outbox.pending() == []
    assert _run("resume", "Digest")[0] == 0
    assert outbox.pending() == [], "resuming waits for the next slot"


def test_removing_everything_drops_what_waited_but_keeps_gaias_own_notice():
    _queued_summary()
    outbox.enqueue(
        outbox.Delivery("n1", "", "automations", outbox.KIND_POST, "file damaged")
    )
    assert cli.run_remove_all(emit=lambda _: None) == 0
    assert [d.id for d in outbox.pending()] == ["n1"]


def test_a_reminder_that_already_ran_can_still_be_stopped_by_name_or_list_id():
    _paired()
    now = time.time()
    _reminder(now - 1, say="A", name="Call Sam")
    _reminder(now - 1, say="B", name="Stretch")
    daemon_tick.AutomationsTick()(now)
    assert store.load() == [], "both ran; only their messages wait"

    assert _run("remove", "call sam") == (
        0,
        "Dropped the waiting message from 'Call Sam'.",
    )
    listed = []
    cli.run_list(as_json=True, emit=listed.append)
    (row,) = [r for r in json.loads(listed[0]) if r["kind"] == "waiting"]
    assert _run("remove", row["id"]) == (
        0,
        "Dropped the waiting message from 'Stretch'.",
    )
    assert outbox.pending() == []


def test_removing_a_waiting_id_that_is_gone_says_so():
    code, said = _run("remove", "waiting:nope")
    assert code == 2 and "Nothing is waiting" in said and "List again" in said


def test_an_unknown_name_is_still_refused():
    code, said = _run("remove", "Nope")
    assert code == 2 and "No automation is called" in said
