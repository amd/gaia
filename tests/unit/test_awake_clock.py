# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The awake clock runs while the PC is awake and stops while it sleeps."""

import logging
import os
import subprocess
import sys
import textwrap
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


def _beating(ledger, start, seconds):
    """The monitor beating once a second for *seconds*, from *start*."""
    for tick in range(1, int(seconds) + 1):
        ledger.beat(start + tick)
    return start + int(seconds)


def test_standby_is_left_out_past_the_first_gap_and_logged(caplog):
    ledger = awake_clock.SleepLedger(1000.0, gap=10.0)
    ledger.display(False, 1000.0)
    with caplog.at_level(logging.INFO, logger=LEDGER_LOG):
        ledger.beat(1000.0 + 7200.0)  # two hours frozen in standby
    assert ledger.read(1000.0 + 7200.0) == pytest.approx(1010.0)
    assert ledger.excluded == pytest.approx(7190.0)
    assert "Left 7190.0 s out" in caplog.text
    assert "frozen for 7200.0 s with the display off" in caplog.text


def test_a_freeze_with_the_display_on_counts_and_is_logged(caplog):
    """Awake but not running (a debugger, a VM pause) is not standby."""
    ledger = awake_clock.SleepLedger(1000.0, gap=10.0)
    with caplog.at_level(logging.WARNING, logger=LEDGER_LOG):
        ledger.beat(1300.0)
    assert ledger.read(1300.0) == 1300.0
    assert ledger.excluded == 0.0
    assert "did not run for 300.0 s with the display on" in caplog.text


def test_a_screen_off_pc_that_keeps_running_counts_all_of_it():
    """Display off but no standby: the process runs, so a hang still trips."""
    ledger = awake_clock.SleepLedger(1000.0, gap=10.0)
    ledger.display(False, 1000.0)
    now = _beating(ledger, 1000.0, 600)
    assert ledger.read(now) == now
    assert ledger.excluded == 0.0


def test_a_reader_that_wakes_before_the_monitor_never_sees_the_standby():
    ledger = awake_clock.SleepLedger(1000.0, gap=10.0)
    ledger.display(False, 1000.0)
    before = ledger.read(1000.5)
    held = [ledger.read(1000.0 + t) for t in (11.0, 3600.0, 7200.0)]
    assert held == [1010.0, 1010.0, 1010.0]
    ledger.beat(7200.5)
    after = ledger.read(7200.6)
    assert before <= held[0] <= after
    assert after == pytest.approx(1010.1)


def test_the_wake_notification_does_not_hide_the_standby_it_ends():
    """Display-on arrives as the process thaws, before the monitor's beat."""
    ledger = awake_clock.SleepLedger(1000.0, gap=10.0)
    ledger.display(False, 1000.0)
    ledger.display(True, 1000.0 + 3600.0)
    assert ledger.excluded == pytest.approx(3590.0)
    later = 1000.0 + 3600.0 + 30.0
    assert ledger.read(later) == pytest.approx(1040.0)


def test_readings_never_go_backwards_through_standbys_and_pauses():
    ledger = awake_clock.SleepLedger(0.0, gap=10.0)
    raw, readings = 0.0, []
    for step, display_on in enumerate([True, False, False, True, False] * 20):
        ledger.display(display_on, raw)
        for jump in (0.5, 1.0, 30.0 * (step % 3), 1.0):
            raw += jump
            readings.append(ledger.read(raw))
            if jump <= 1.0:
                ledger.beat(raw)
            readings.append(ledger.read(raw))
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
        """Suspend every thread, as Windows does to a desktop app in standby."""
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
            time.sleep(seconds)
            assert ntdll.NtResumeProcess(handle) == 0
        finally:
            kernel32.CloseHandle(handle)

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
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Win32 monitor")


@windows_only
def test_a_process_frozen_through_standby_wakes_with_the_standby_left_out(child):
    c = child()
    start = c.ask("off")
    c.freeze(6.0)
    first_read = c.ask("read")  # may run before the monitor has woken
    woke = c.ask("on")
    time.sleep(1.2)
    later = c.ask("read")

    # Held at most a heartbeat plus the gap past the display-off reading.
    assert first_read - start <= 1.0 + GAP + 0.3
    assert start <= first_read <= woke <= later
    # 6 s frozen and 1.2 s awake after it; only the gap and the 1.2 s count.
    assert later - start < 1.0 + GAP + 1.2 + 0.5
    assert any(line.startswith("Left ") for line in c.logs), c.logs


@windows_only
def test_a_process_frozen_with_the_display_on_counts_every_second(child):
    c = child()
    start = c.ask("read")
    c.freeze(4.0)
    time.sleep(1.2)
    later = c.ask("read")
    assert later - start >= 4.0
    assert any("with the display on" in line for line in c.logs), c.logs


@windows_only
def test_a_dead_monitor_stops_every_time_limit_loudly(child):
    c = child()
    c.ask("read")
    c.ask("corrupt")  # the window procedure fails on a NULL setting
    time.sleep(1.5)
    answer = c.ask("read")
    assert isinstance(answer, str) and answer.startswith("AwakeClockError"), answer


@windows_only
def test_shutdown_stops_the_monitor_and_releases_its_window(child):
    c = child()
    c.ask("read")
    assert c.ask("quit") == "False None"
    assert c.proc.wait(10) == 0


@windows_only
def test_a_monitor_that_cannot_subscribe_fails_loudly(monkeypatch):
    def refuse(_hwnd, _guid, _flags):
        return None

    awake_clock._reset_for_tests()
    monkeypatch.setattr(awake_clock._user32, "RegisterPowerSettingNotification", refuse)
    try:
        for _ in range(2):  # every reader is told, not just the first
            with pytest.raises(awake_clock.AwakeClockError, match="display power"):
                awake_clock.monotonic()
    finally:
        monkeypatch.undo()
        awake_clock._reset_for_tests()
    assert awake_clock.monotonic() > 0
