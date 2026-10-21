# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The awake clock runs while the PC is awake and stops while it sleeps."""

import logging
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import gaia
from gaia.utils import awake_clock

LEDGER_LOG = "gaia.utils.awake_clock"


def test_it_runs_with_the_process():
    start = awake_clock.monotonic()
    time.sleep(0.3)
    elapsed = awake_clock.monotonic() - start
    # Windows reads it at timer-tick resolution, about 16 ms.
    assert 0.25 <= elapsed <= 1.0


def test_it_never_goes_backwards():
    readings = [awake_clock.monotonic() for _ in range(1000)]
    assert readings == sorted(readings)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows clock source")
def test_on_windows_it_is_the_unbiased_interrupt_time_less_standby():
    """The interrupt time that leaves out sleep and hibernation, less standby."""
    import ctypes

    awake_clock.monotonic()
    ticks = ctypes.c_ulonglong()
    assert ctypes.windll.kernel32.QueryUnbiasedInterruptTime(ctypes.byref(ticks))
    expected = ticks.value / 1e7 - awake_clock._ledger.excluded
    assert awake_clock.monotonic() == pytest.approx(expected, abs=0.1)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX clock source")
def test_elsewhere_it_is_the_monotonic_clock():
    """Linux and macOS stop ``time.monotonic`` during suspend already."""
    assert awake_clock.monotonic() == pytest.approx(time.monotonic(), abs=0.1)


# ── Modern Standby: the ledger the Windows monitor feeds ──────────────────


class _Machine:
    """The raw clock and process CPU time a ledger reads, moved by the test."""

    def __init__(self, now=1000.0):
        self.now = now
        self.cpu = 0.0

    def frozen(self, seconds):
        """Windows froze the process: time passes, no CPU is used."""
        self.now += seconds

    def starved(self, seconds):
        """The monitor was starved while another thread kept a core busy."""
        self.now += seconds
        self.cpu += seconds

    def ledger(self, gap=10.0):
        return awake_clock.SleepLedger(lambda: self.now, lambda: self.cpu, gap)


def _beating(machine, ledger, seconds):
    """The monitor running and beating once a second for *seconds*."""
    for _ in range(int(seconds)):
        machine.now += 1.0
        ledger.beat()


def test_standby_is_left_out_past_the_first_gap_and_logged(caplog):
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    pc.frozen(7200.0)  # two hours in Modern Standby
    with caplog.at_level(logging.INFO, logger=LEDGER_LOG):
        ledger.beat()
    assert ledger.read() == pytest.approx(1010.0)
    assert ledger.excluded == pytest.approx(7190.0)
    # A warning: a paging stall or a debugger pause would read the same.
    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "Excluded 7190.0 s from time limits as probable standby" in record.message
    assert "(display off, low CPU)" in record.message
    assert "did not run for 7200.0 s" in record.message


def test_a_monitor_that_stops_beating_while_the_process_runs_is_stalled():
    """The tick thread kept running, so the missing beats are not standby."""
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    for _ in range(25):  # ticks, no beats: the monitor is stuck
        pc.now += 1.0
        ledger.tick()
    assert ledger.stalled_for() == pytest.approx(15.0)
    ledger.beat()
    assert ledger.stalled_for() == 0.0


def test_a_frozen_process_is_never_taken_for_a_stuck_monitor():
    """Standby freezes the tick thread too, so its first tick after is fresh."""
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    pc.frozen(3600.0)
    ledger.tick()  # thawed: ticks before the monitor gets to beat
    pc.now += 1.0
    ledger.tick()
    # Only the second since thawing counts, not the hour asleep.
    assert ledger.stalled_for() == pytest.approx(1.0)
    assert ledger.stalled_for() < awake_clock.STUCK_AFTER
    assert ledger.read() == pytest.approx(1010.0)  # still held for the beat


def test_a_replacement_ledger_carries_the_standby_already_left_out():
    pc = _Machine()
    ledger = awake_clock.SleepLedger(lambda: pc.now, lambda: pc.cpu, excluded=500.0)
    assert ledger.read() == pytest.approx(500.0)


def test_a_freeze_with_the_display_on_counts_and_is_logged(caplog):
    """Awake but not running (a debugger, a VM pause) is not standby."""
    pc = _Machine()
    ledger = pc.ledger()
    pc.frozen(300.0)
    with caplog.at_level(logging.WARNING, logger=LEDGER_LOG):
        ledger.beat()
    assert ledger.read() == 1300.0
    assert ledger.excluded == 0.0
    assert "did not run for 300.0 s with the display on" in caplog.text


def test_a_busy_process_starved_with_the_display_off_counts_and_is_logged(caplog):
    """A thread holding the GIL is not Windows freezing the process."""
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    pc.starved(60.0)
    with caplog.at_level(logging.WARNING, logger=LEDGER_LOG):
        ledger.beat()
    assert ledger.read() == 1060.0
    assert ledger.excluded == 0.0
    assert "used 60.0 s of CPU meanwhile, so it was busy" in caplog.text


def test_a_screen_off_pc_that_keeps_running_counts_all_of_it():
    """Display off but no standby: the process runs, so a hang still trips."""
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    _beating(pc, ledger, 600)
    assert ledger.read() == 1600.0
    assert ledger.excluded == 0.0


def test_a_reader_that_wakes_before_the_monitor_never_sees_the_standby():
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    pc.now += 0.5
    before = ledger.read()
    held = []
    for seconds in (10.5, 3589.0, 3600.0):
        pc.frozen(seconds)
        held.append(ledger.read())
    assert held == [1010.0, 1010.0, 1010.0]
    ledger.beat()
    pc.now += 0.1
    after = ledger.read()
    assert before <= held[0] <= after
    assert after == pytest.approx(1010.1)


def test_the_wake_notification_does_not_hide_the_standby_it_ends():
    """Display-on arrives as the process thaws, before the monitor's beat."""
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    pc.frozen(3600.0)
    ledger.display(True)
    assert ledger.excluded == pytest.approx(3590.0)
    pc.now += 30.0
    assert ledger.read() == pytest.approx(1040.0)


def test_after_shutdown_a_late_display_off_never_holds_the_clock():
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    ledger.detach()
    ledger.display(False)  # a notification still in flight during shutdown
    pc.frozen(60.0)
    assert ledger.read() == pytest.approx(1060.0)


def test_a_standby_that_ends_at_shutdown_is_still_left_out():
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    pc.frozen(60.0)
    ledger.detach()
    assert ledger.read() == pytest.approx(1010.0)


def test_a_monitor_beating_after_shutdown_leaves_nothing_out():
    """A stop that timed out must not move the clock back later."""
    pc = _Machine()
    ledger = pc.ledger()
    ledger.display(False)
    ledger.detach()
    pc.frozen(60.0)
    seen = ledger.read()
    ledger.beat()
    assert ledger.read() == seen == pytest.approx(1060.0)


def test_readings_never_go_backwards_through_standbys_and_pauses():
    pc = _Machine(0.0)
    ledger = pc.ledger()
    readings = []
    for step, display_on in enumerate([True, False, False, True, False] * 20):
        ledger.display(display_on)
        for jump in (0.5, 1.0, 30.0 * (step % 3), 1.0):
            if step % 4 == 1:
                pc.starved(jump)
            else:
                pc.frozen(jump)
            readings.append(ledger.read())
            if jump <= 1.0:
                ledger.beat()
            readings.append(ledger.read())
    assert readings == sorted(readings)


# ── The real Windows monitor, end to end ─────────────────────────────────

_CHILD = textwrap.dedent("""
    import ctypes, logging, sys
    from ctypes import wintypes
    sys.path.insert(0, {src!r})
    from gaia.utils import awake_clock as ac

    out = sys.stdout
    handler = logging.StreamHandler(out)
    handler.setFormatter(logging.Formatter("LOG %(message)s"))
    logger = logging.getLogger("gaia.utils.awake_clock")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    ac.monotonic()
    monitor = getattr(ac, "_monitor", None)  # None: nothing listens for standby
    if monitor is not None:
        ac._ledger.gap = {gap}
    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t

    def display(state):
        # What Windows sends the monitor's window when the display turns on or off.
        if monitor is None:
            return
        setting = ac._POWERBROADCAST_SETTING()
        setting.PowerSetting = ac._GUID_CONSOLE_DISPLAY_STATE
        setting.DataLength = 4
        setting.Data[0] = state
        user32.SendMessageW(
            ac._monitor.hwnd, 0x0218, 0x8013, ctypes.addressof(setting))

    for line in sys.stdin:
        cmd = line.strip()
        if cmd == "off":
            display(0)
        elif cmd == "on":
            display(1)
        elif cmd == "exit-monitor":
            # Wakes the pump on its stop event without stop(): it exits clean.
            ac._kernel32.SetEvent(ac._monitor._stop_event)
        elif cmd == "corrupt":
            user32.SendMessageW(ac._monitor.hwnd, 0x0218, 0x8013, 0)
        elif cmd == "pid":
            import os
            print("R", os.getpid(), flush=True)
            continue
        elif cmd == "quit":
            ac.shutdown()
            print("R", ac._monitor._thread.is_alive(), ac._monitor.hwnd, flush=True)
            break
        try:
            print("R", ac.monotonic(), flush=True)
        except Exception as exc:
            print("R", type(exc).__name__, exc, flush=True)
""")


class _Child:
    """A process running the awake clock, which the test can freeze like DAM."""

    def __init__(self, tmp_path, gap):
        src = str(Path(gaia.__file__).resolve().parents[1])
        env = dict(os.environ, GAIA_HOME=str(tmp_path))
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _CHILD.format(src=src, gap=gap)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.logs = []
        # A venv's python.exe is a launcher; freeze the interpreter it starts.
        self.pid = int(self.ask("pid"))

    def ask(self, cmd):
        self.proc.stdin.write(cmd + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            assert line, f"child exited (code {self.proc.poll()})"
            if line.startswith("LOG "):
                self.logs.append(line[4:].strip())
            elif line.startswith("R "):
                answer = line[2:].strip()
                try:
                    return float(answer)
                except ValueError:
                    return answer

    def freeze(self, seconds):
        """Suspend every thread, as Windows does to a desktop app in standby.

        Returns when the suspend took effect, on ``time.perf_counter``.
        """
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        ntdll = ctypes.WinDLL("ntdll")
        ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
        ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x0800, False, self.pid)
        assert handle, ctypes.get_last_error()
        try:
            assert ntdll.NtSuspendProcess(handle) == 0
            suspended = time.perf_counter()
            time.sleep(seconds)
            assert ntdll.NtResumeProcess(handle) == 0
        finally:
            kernel32.CloseHandle(handle)
        return suspended

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait(10)
        self.proc.stdin.close()
        self.proc.stdout.close()


@pytest.fixture
def child(tmp_path):
    made = []

    def make(gap=GAP):
        made.append(_Child(tmp_path, gap))
        return made[-1]

    yield make
    for c in made:
        c.close()


# Well clear of the monitor's 1 s heartbeat, so its jitter is never a freeze.
GAP = 2.5
HEARTBEAT = 1.0
FREEZE = 8.0
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 monitor")


@windows_only
def test_a_process_frozen_through_standby_wakes_with_the_standby_left_out(child):
    c = child()
    began = time.perf_counter()
    start = c.ask("off")
    suspended = c.freeze(FREEZE)
    first_read = c.ask("read")  # may run before the monitor has woken
    woke = c.ask("on")
    time.sleep(1.2)
    later = c.ask("read")
    wall = time.perf_counter() - began

    # Held at the gap past the last beat before the freeze.
    assert first_read - start <= (suspended - began) + GAP + 0.5
    assert start <= first_read <= woke <= later
    # All of the freeze is left out but the gap and up to one heartbeat.
    assert later - start <= wall - (FREEZE - GAP - HEARTBEAT - 0.5)
    assert any(line.startswith("Excluded ") for line in c.logs), c.logs


@windows_only
def test_a_process_frozen_with_the_display_on_counts_every_second(child):
    c = child()
    start = c.ask("on")  # whatever the runner's real display is doing
    c.freeze(4.0)
    time.sleep(1.2)
    later = c.ask("read")
    assert later - start >= 4.0
    assert any("with the display on" in line for line in c.logs), c.logs


@windows_only
def test_a_monitor_that_fails_is_replaced_and_says_why(child):
    c = child()
    before = c.ask("read")
    c.ask("corrupt")  # the window procedure fails on a NULL setting
    time.sleep(1.5)
    after = c.ask("read")
    assert isinstance(after, float) and after >= before, after
    failed = [line for line in c.logs if "failure 1 of 4" in line]
    assert failed and "failed (NULL pointer access)" in failed[0], c.logs


@windows_only
def test_a_monitor_thread_that_exits_is_replaced_and_says_why(child):
    c = child()
    c.ask("read")
    c.ask("exit-monitor")
    time.sleep(1.5)
    assert isinstance(c.ask("read"), float)
    assert any(
        "thread exited" in line and "failure 1 of 4" in line for line in c.logs
    ), c.logs


@windows_only
def test_shutdown_stops_the_monitor_and_releases_its_window(child):
    c = child()
    c.ask("read")
    assert c.ask("quit") == "False None"
    assert c.proc.wait(10) == 0


def _display_message(hwnd, state):
    """Send *hwnd* the notification Windows sends when the display changes."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    user32.SendMessageW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    setting = awake_clock._POWERBROADCAST_SETTING()
    setting.PowerSetting = awake_clock._GUID_CONSOLE_DISPLAY_STATE
    setting.DataLength = 4
    setting.Data[0] = state
    user32.SendMessageW(hwnd, 0x0218, 0x8013, ctypes.addressof(setting))


@pytest.fixture
def fresh_clock():
    """A monitor started for this test only, and stopped after it."""
    awake_clock._reset_for_tests()
    yield
    awake_clock._reset_for_tests()


@windows_only
def test_a_stuck_monitor_is_replaced_and_its_time_counts(
    fresh_clock, monkeypatch, caplog
):
    """A monitor wedged in a log call must not hold every limit forever."""
    monkeypatch.setattr(awake_clock, "STUCK_AFTER", 1.0)
    first = awake_clock.monotonic()
    stuck, ledger = awake_clock._monitor, awake_clock._ledger
    ledger.gap = 1.0
    _display_message(stuck.hwnd, 0)
    release = threading.Event()
    monkeypatch.setattr(ledger, "_log", lambda *_a: release.wait(30) and 0.0)
    began = time.perf_counter()
    try:
        with caplog.at_level(logging.ERROR, logger=LEDGER_LOG):
            while awake_clock._monitor is stuck:
                assert time.perf_counter() - began < 15, "never replaced"
                awake_clock.monotonic()
                time.sleep(0.1)
        counted = awake_clock.monotonic() - first
        wall = time.perf_counter() - began
    finally:
        release.set()
    assert "has not beaten for" in caplog.text and "failure 1 of 4" in caplog.text
    # Replaced within a few beats, without waiting on the wedged monitor.
    assert wall < 10.0
    # The stuck stretch counts toward limits: nothing said it was standby.
    assert counted >= wall - 0.3


@windows_only
def test_a_monitor_that_cannot_start_is_retried_then_names_the_fix(
    fresh_clock, monkeypatch, caplog
):
    attempts = []

    def refuse(_hwnd, _guid, _flags):
        attempts.append(1)
        return None

    monkeypatch.setattr(awake_clock._user32, "RegisterPowerSettingNotification", refuse)
    with caplog.at_level(logging.ERROR, logger=LEDGER_LOG):
        for _ in range(awake_clock.MAX_RESTARTS):
            with pytest.raises(awake_clock.AwakeClockError, match="display power"):
                awake_clock.monotonic()
        for _ in range(2):  # out of restarts: every reader is told what to do
            with pytest.raises(
                awake_clock.AwakeClockError, match="Restart GAIA"
            ) as excinfo:
                awake_clock.monotonic()
    assert len(attempts) == awake_clock.MAX_RESTARTS + 1
    assert "could not start (failure 1 of 4 before GAIA gives up)" in caplog.text
    gave_up = str(excinfo.value)
    # Short and actionable: the subscribe error's own report line isn't repeated.
    assert gave_up.count("gaia diagnostics") == 1
    assert "could not start, and 3 restarts did not fix it" in gave_up
    monkeypatch.undo()
    awake_clock._reset_for_tests()
    assert awake_clock.monotonic() > 0


@windows_only
def test_a_replacement_that_cannot_start_is_retried_not_skipped(
    fresh_clock, monkeypatch
):
    """A failed restart must not leave the clock running with no monitor."""
    awake_clock.monotonic()
    old = awake_clock._monitor
    attempts = []

    def refuse(_hwnd, _guid, _flags):
        attempts.append(1)
        return None

    monkeypatch.setattr(awake_clock._user32, "RegisterPowerSettingNotification", refuse)
    awake_clock._kernel32.SetEvent(old._stop_event)  # the monitor thread exits
    old._thread.join(5)
    for expected in (1, 2):
        with pytest.raises(awake_clock.AwakeClockError, match="display power"):
            awake_clock.monotonic()
        assert len(attempts) == expected
    monkeypatch.undo()
    awake_clock.monotonic()
    assert awake_clock._monitor is not old
    assert awake_clock._monitor.problem() is None


def _end_monitor_thread():
    """Make the current monitor's thread exit, as a crash would."""
    monitor = awake_clock._monitor
    awake_clock._kernel32.SetEvent(monitor._stop_event)
    monitor._thread.join(5)


@windows_only
def test_a_healthy_replacement_wipes_the_failure_count(
    fresh_clock, monkeypatch, caplog
):
    """Weeks apart, unrelated failures must not add up to giving up."""
    awake_clock.monotonic()
    with caplog.at_level(logging.ERROR, logger=LEDGER_LOG):
        _end_monitor_thread()
        awake_clock.monotonic()
        assert awake_clock._restarts == 1
        monkeypatch.setattr(awake_clock, "HEALTHY_RESET", 0.2)
        time.sleep(0.3)
        awake_clock.monotonic()
        assert awake_clock._restarts == 0
        _end_monitor_thread()
        awake_clock.monotonic()
    # The next failure counts from one again, and the log says so.
    assert caplog.text.count("failure 1 of 4") == 2
    assert "failure 2 of 4" not in caplog.text


@windows_only
def test_the_last_failure_says_it_gives_up_not_that_it_restarts(fresh_clock, caplog):
    awake_clock.monotonic()
    with caplog.at_level(logging.ERROR, logger=LEDGER_LOG):
        for _ in range(awake_clock.MAX_RESTARTS):
            _end_monitor_thread()
            awake_clock.monotonic()
        _end_monitor_thread()
        with pytest.raises(awake_clock.AwakeClockError, match="Restart GAIA"):
            awake_clock.monotonic()
    [last] = [r.message for r in caplog.records if "failure 4 of 4" in r.message]
    assert "giving up" in last and "starting a new one" not in last


@windows_only
def test_a_replacement_monitor_keeps_the_standby_already_left_out(fresh_clock):
    """Restarting must not jump every running limit forward by past standbys."""
    awake_clock.monotonic()
    old = awake_clock._monitor
    awake_clock._ledger.excluded = 3600.0  # an hour of standby left out earlier
    before = awake_clock.monotonic()
    awake_clock._kernel32.SetEvent(old._stop_event)  # the monitor thread exits
    old._thread.join(5)
    after = awake_clock.monotonic()
    assert awake_clock._monitor is not old
    assert awake_clock._ledger.excluded == 3600.0
    assert 0 <= after - before < 5.0


@windows_only
def test_stopping_the_monitor_unsubscribes_and_destroys_its_window(
    fresh_clock, monkeypatch
):
    import ctypes

    user32 = awake_clock._user32
    calls = []
    real_unsubscribe = user32.UnregisterPowerSettingNotification
    real_destroy = user32.DestroyWindow

    def unsubscribe(handle):
        calls.append(("unsubscribe", handle))
        return real_unsubscribe(handle)

    def destroy(hwnd):
        calls.append(("destroy", hwnd))
        return real_destroy(hwnd)

    monkeypatch.setattr(user32, "UnregisterPowerSettingNotification", unsubscribe)
    monkeypatch.setattr(user32, "DestroyWindow", destroy)
    awake_clock.monotonic()
    monitor = awake_clock._monitor
    hwnd, notify = monitor.hwnd, monitor.notify
    awake_clock._reset_for_tests()
    assert ("unsubscribe", notify) in calls
    assert ("destroy", hwnd) in calls
    assert not ctypes.windll.user32.IsWindow(hwnd)
    assert not monitor._thread.is_alive()
    assert not monitor._ticker.is_alive()


def _in_console_session():
    session, console = awake_clock._session_ids()
    return session == console


@windows_only
def test_the_monitor_says_once_that_display_notices_arrive(fresh_clock, caplog):
    if not _in_console_session():
        pytest.skip("outside the console session Windows sends no display notices")
    with caplog.at_level(logging.INFO, logger=LEDGER_LOG):
        awake_clock.monotonic()
        deadline = time.perf_counter() + 5
        while "Receiving Windows display" not in caplog.text:
            assert time.perf_counter() < deadline, caplog.text
            time.sleep(0.05)
        _display_message(awake_clock._monitor.hwnd, 1)
    assert caplog.text.count("Receiving Windows display") == 1


@windows_only
def test_a_monitor_that_gets_no_display_notices_says_so(
    fresh_clock, monkeypatch, caplog
):
    """No console session (a service, a remote logon): standby will count."""
    user32 = awake_clock._user32
    monkeypatch.setattr(awake_clock, "FIRST_NOTICE_WAIT", 0.3)
    monkeypatch.setattr(user32, "RegisterPowerSettingNotification", lambda *_a: 1)
    monkeypatch.setattr(user32, "UnregisterPowerSettingNotification", lambda _h: 1)
    with caplog.at_level(logging.WARNING, logger=LEDGER_LOG):
        awake_clock.monotonic()
        deadline = time.perf_counter() + 5
        while "No Windows display power notification" not in caplog.text:
            assert time.perf_counter() < deadline, caplog.text
            time.sleep(0.05)
        time.sleep(1.5)
    assert caplog.text.count("No Windows display power notification") == 1
    assert "Modern Standby will count toward time limits" in caplog.text
