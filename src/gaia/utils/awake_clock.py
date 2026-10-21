# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""A clock for time limits that does not count time the PC was asleep.

On Windows ``time.monotonic()`` keeps counting while the machine sleeps, so a
limit like "120 s without a byte from the model" fires the instant the PC
wakes from a long sleep, even though the model server (asleep too) is fine and
about to carry on. :func:`monotonic` stops while the PC sleeps:

* Windows, S3 sleep and hibernation: ``QueryUnbiasedInterruptTime`` leaves
  them out, in 100 ns units at timer-tick resolution (about 16 ms).
* Windows, Modern Standby (S0 low-power idle, most current laptops): the
  unbiased interrupt time keeps counting, because the PC never leaves S0.
  Windows freezes desktop processes for the standby, so a monitor thread
  subscribed to the console display's power state takes a stretch in which
  the process did not run for more than :data:`STANDBY_GAP` seconds, starting
  with the display off and using next to no CPU, as standby, and leaves it
  out past the first ``STANDBY_GAP`` seconds. Each one is logged with its
  size, at WARNING. A freeze with the display on is a pause, and a busy process
  starved of its turn is not frozen; both count, and are logged. A process
  stalled for over ``STANDBY_GAP`` seconds while using little CPU with the
  display off (heavy paging, a debugger) can't be told from standby this way,
  and is excluded too, which is why each exclusion is a warning. A process
  that gets no display notifications (no console session: a service, a
  remote logon) can't see standby at all; the monitor says so once at startup.
* A monitor that dies, fails or stops beating is replaced on the next read,
  at most :data:`MAX_RESTARTS` times, each logged as an error; after that every
  read raises :class:`AwakeClockError` telling the user to restart GAIA.
* Linux and macOS: ``time.monotonic()``, which already stops during suspend.

Use it for any limit on how long something may take, so a sleep never reads
as a hang. Callers that take a clock should look this function up at call
time (``awake_clock.monotonic()``), not bind it as a default argument.
"""

from __future__ import annotations

import atexit
import sys
import threading
import time
from typing import Callable, Optional

from gaia.logger import get_logger

log = get_logger(__name__)

# Longest the process may not run, with the display off, before that is standby.
STANDBY_GAP = 10.0
HEARTBEAT = 1.0
# The process running this long while a beat is overdue: the monitor is stuck.
STUCK_AFTER = 5 * HEARTBEAT
# A gap this long between ticks means the whole process was frozen.
TICK_BREAK = 3 * HEARTBEAT
# A stuck, dead or failed monitor is replaced at most this many times in a row.
MAX_RESTARTS = 3
# A replacement this long without trouble wipes the slate for the next one.
HEALTHY_RESET = 600.0
# Windows sends the current display state on subscribing; this long is too long.
FIRST_NOTICE_WAIT = 2.0


class AwakeClockError(RuntimeError):
    """The clock can't tell standby apart from awake time on this PC."""


class SleepLedger:
    """How much standby to leave out of a raw clock, from what a monitor saw.

    The monitor calls :meth:`beat` about every :data:`HEARTBEAT` seconds and
    :meth:`display` when the console display turns on or off. A gap between
    beats longer than *gap* is standby when it began with the display off and
    the process used less than half of it in CPU time (Windows froze it, it
    was not merely starved). All of it past the first *gap* seconds is left
    out. Until the monitor has judged such a gap, :meth:`read` holds the clock
    at the point it would be left out from, so a reader that wakes first never
    sees the standby. *clock* and *cpu* are read under the lock, so readings
    and beats are ordered and the clock never goes backwards.

    A second thread calls :meth:`tick` about every :data:`HEARTBEAT` seconds and
    does nothing else, so it shows when the process was running. Standby
    freezes it with the monitor; a stuck monitor does not, which is how
    :meth:`stalled_for` tells the two apart without any reader's help.
    """

    def __init__(
        self,
        clock: Callable[[], float],
        cpu: Callable[[], float],
        gap: float = STANDBY_GAP,
        excluded: float = 0.0,
    ):
        self._clock = clock
        self._cpu = cpu
        self._lock = threading.Lock()
        self.gap = gap
        self.excluded = excluded
        self._last_beat = clock()
        self._last_cpu = cpu()
        self._display_on = True
        self._monitored = True
        self._last_tick = self._last_beat
        self._run_since = self._last_beat

    def read(self) -> float:
        """The awake time now."""
        with self._lock:
            raw = self._clock()
            held = self._monitored and not self._display_on
            if held and raw - self._last_beat > self.gap:
                raw = self._last_beat + self.gap
            return raw - self.excluded

    def tick(self) -> None:
        """The process ran just now."""
        with self._lock:
            raw = self._clock()
            if raw - self._last_tick > TICK_BREAK:
                self._run_since = raw  # the whole process was frozen until now
            self._last_tick = raw

    def stalled_for(self) -> float:
        """Seconds the process has been seen running while a beat was overdue."""
        with self._lock:
            overdue_since = max(self._run_since, self._last_beat + self.gap)
            return max(0.0, self._last_tick - overdue_since)

    def beat(self) -> float:
        """Record that the monitor ran now; returns the seconds left out."""
        with self._lock:
            report = self._beat_locked()
        return self._log(*report)

    def display(self, on: bool) -> None:
        """The console display turned on or off just now."""
        with self._lock:
            if not self._monitored:
                return
            # A gap that ends here began under the old display state.
            report = self._beat_locked()
            self._display_on = on
        self._log(*report)

    def detach(self) -> None:
        """Monitoring has ended: judge the last gap, then never hold again."""
        with self._lock:
            if not self._monitored:
                return
            report = self._beat_locked()
            self._monitored = False
        self._log(*report)

    def abandon(self) -> None:
        """Its monitor is stuck or dead: stop holding, count the missed time.

        Unlike :meth:`detach` it judges nothing (a stuck monitor is not a
        standby) and logs nothing (logging may be what it is stuck in).
        """
        with self._lock:
            self._monitored = False

    def _beat_locked(self):
        raw, cpu = self._clock(), self._cpu()
        frozen = raw - self._last_beat
        busy = cpu - self._last_cpu
        left_out = 0.0
        standby = not self._display_on and busy < frozen / 2
        if self._monitored and frozen > self.gap and standby:
            left_out = frozen - self.gap
            self.excluded += left_out
        self._last_beat, self._last_cpu = raw, cpu
        return left_out, frozen, busy, self._display_on, self.excluded

    def _log(
        self,
        left_out: float,
        frozen: float,
        busy: float,
        display_on: bool,
        excluded: float,
    ) -> float:
        if left_out:
            # WARNING, not INFO: a paging stall or a debugger pause looks the same.
            log.warning(
                "Excluded %.1f s from time limits as probable standby (display "
                "off, low CPU): the process did not run for %.1f s and used "
                "%.1f s of CPU; the first %.0f s still count. %.1f s excluded "
                "so far.",
                left_out,
                frozen,
                busy,
                self.gap,
                excluded,
            )
        elif frozen > self.gap and display_on:
            log.warning(
                "The process did not run for %.1f s with the display on. That "
                "is not standby, so it counts toward time limits.",
                frozen,
            )
        elif frozen > self.gap:
            log.warning(
                "The process did not get to run for %.1f s with the display "
                "off, but used %.1f s of CPU meanwhile, so it was busy, not in "
                "standby. It counts toward time limits.",
                frozen,
                busy,
            )
        return left_out


if sys.platform == "win32":
    import ctypes
    import itertools
    import os
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _query = _kernel32.QueryUnbiasedInterruptTime
    _query.argtypes = [ctypes.POINTER(ctypes.c_ulonglong)]
    _query.restype = wintypes.BOOL

    def _unbiased() -> float:
        ticks = ctypes.c_ulonglong()
        if not _query(ctypes.byref(ticks)):
            raise ctypes.WinError(ctypes.get_last_error())
        return ticks.value / 10_000_000

    _WM_POWERBROADCAST = 0x0218
    _PBT_POWERSETTINGCHANGE = 0x8013
    _QS_ALLINPUT = 0x04FF
    _PM_REMOVE = 0x0001
    _WAIT_OBJECT_0 = 0
    _WAIT_FAILED = 0xFFFFFFFF
    _HWND_MESSAGE = wintypes.HWND(-3)

    class _GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    # GUID_CONSOLE_DISPLAY_STATE {6FE69556-704A-47A0-8F24-C28D936FDA47}
    _GUID_CONSOLE_DISPLAY_STATE = _GUID(
        0x6FE69556,
        0x704A,
        0x47A0,
        (ctypes.c_ubyte * 8)(0x8F, 0x24, 0xC2, 0x8D, 0x93, 0x6F, 0xDA, 0x47),
    )

    class _POWERBROADCAST_SETTING(ctypes.Structure):
        _fields_ = [
            ("PowerSetting", _GUID),
            ("DataLength", wintypes.DWORD),
            ("Data", ctypes.c_ubyte * 4),
        ]

    _LRESULT = ctypes.c_ssize_t
    _WNDPROC = ctypes.WINFUNCTYPE(
        _LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
    )

    class _WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", _WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", wintypes.HANDLE),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    _user32.DefWindowProcW.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    _user32.DefWindowProcW.restype = _LRESULT
    _user32.RegisterClassW.argtypes = [ctypes.POINTER(_WNDCLASSW)]
    _user32.RegisterClassW.restype = wintypes.ATOM
    _user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    _user32.UnregisterClassW.restype = wintypes.BOOL
    _user32.CreateWindowExW.argtypes = [
        wintypes.DWORD,
        wintypes.LPCWSTR,
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HWND,
        wintypes.HMENU,
        wintypes.HINSTANCE,
        wintypes.LPVOID,
    ]
    _user32.CreateWindowExW.restype = wintypes.HWND
    _user32.DestroyWindow.argtypes = [wintypes.HWND]
    _user32.DestroyWindow.restype = wintypes.BOOL
    _user32.RegisterPowerSettingNotification.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_GUID),
        wintypes.DWORD,
    ]
    _user32.RegisterPowerSettingNotification.restype = wintypes.HANDLE
    _user32.UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
    _user32.UnregisterPowerSettingNotification.restype = wintypes.BOOL
    _user32.MsgWaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _user32.MsgWaitForMultipleObjects.restype = wintypes.DWORD
    _user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG),
        wintypes.HWND,
        wintypes.UINT,
        wintypes.UINT,
        wintypes.UINT,
    ]
    _user32.PeekMessageW.restype = wintypes.BOOL
    _user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
    _user32.DispatchMessageW.restype = _LRESULT
    _kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    _kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    _kernel32.CreateEventW.argtypes = [
        wintypes.LPVOID,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    _kernel32.CreateEventW.restype = wintypes.HANDLE
    _kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    _kernel32.SetEvent.restype = wintypes.BOOL
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL

    _kernel32.WTSGetActiveConsoleSessionId.restype = wintypes.DWORD
    _kernel32.ProcessIdToSessionId.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    _kernel32.ProcessIdToSessionId.restype = wintypes.BOOL

    def _session_ids():
        """(this process's session id, the console session id)."""
        session = wintypes.DWORD()
        if not _kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
            raise _win_error("ProcessIdToSessionId")
        return session.value, _kernel32.WTSGetActiveConsoleSessionId()

    _class_ids = itertools.count()
    _REPORT = (
        "Run `gaia diagnostics` and attach the bundle to an issue at "
        "https://github.com/amd/gaia/issues"
    )

    def _win_error(what: str) -> OSError:
        err = ctypes.WinError(ctypes.get_last_error())
        return OSError(err.errno, f"{what}: {err.strerror}", None, err.winerror)

    class _StandbyMonitor:
        """A thread that feeds a :class:`SleepLedger` from Windows.

        It owns a hidden window subscribed to ``GUID_CONSOLE_DISPLAY_STATE``
        and beats every :data:`HEARTBEAT` seconds while pumping its messages.
        """

        def __init__(self, ledger: SleepLedger):
            self.ledger = ledger
            self.hwnd: Optional[int] = None
            self.error: Optional[BaseException] = None
            self._ready = threading.Event()
            self._stopping = False
            self.started_at = 0.0
            self.notify: Optional[int] = None
            self._display_seen = False
            self._quiet_reported = False
            self._subscribed_at = 0.0
            self._stop_event = _kernel32.CreateEventW(None, True, False, None)
            if not self._stop_event:
                raise _win_error("CreateEventW")
            self._wndproc = _WNDPROC(self._on_message)
            self._thread = threading.Thread(
                target=self._run, name="gaia-awake-clock", daemon=True
            )
            self._tick_stop = threading.Event()
            self._ticker = threading.Thread(
                target=self._tick, name="gaia-awake-clock-tick", daemon=True
            )

        def start(self, timeout: float = 10.0) -> None:
            self._thread.start()
            if not self._ready.wait(timeout):
                self.stop()
                raise AwakeClockError(
                    f"The standby monitor did not start within {timeout:.0f} s."
                )
            if self.error is not None:
                self.stop(timeout)
                raise self.error
            self._ticker.start()
            self.started_at = _unbiased()

        def _tick(self) -> None:
            while not self._tick_stop.wait(HEARTBEAT):
                self.ledger.tick()

        def problem(self) -> Optional[str]:
            """Why this monitor can no longer be trusted, or ``None``."""
            if self.error is not None:
                return f"failed ({self.error})"
            if not self._stopping and not self._thread.is_alive():
                return "thread exited"
            if not self._stopping and not self._ticker.is_alive():
                return "tick thread exited"
            stalled = self.ledger.stalled_for()
            if stalled > STUCK_AFTER:
                return (
                    f"has not beaten for over {stalled + self.ledger.gap:.0f} s "
                    "while the rest of GAIA kept running"
                )
            return None

        def stop(self, timeout: float = 5.0) -> None:
            if self._stop_event is None:
                return
            self._stopping = True
            self._tick_stop.set()
            _kernel32.SetEvent(self._stop_event)
            for thread in (self._ticker, self._thread):
                if thread.is_alive() and thread is not threading.current_thread():
                    thread.join(timeout)
            if self._thread.is_alive() or self._ticker.is_alive():
                log.error(
                    "The awake-clock standby monitor did not stop within %.1f s.",
                    timeout,
                )
                return
            _kernel32.CloseHandle(self._stop_event)
            self._stop_event = None

        def _on_message(self, hwnd, msg, wparam, lparam):
            try:
                if msg == _WM_POWERBROADCAST and wparam == _PBT_POWERSETTINGCHANGE:
                    setting = ctypes.cast(
                        lparam, ctypes.POINTER(_POWERBROADCAST_SETTING)
                    ).contents
                    if bytes(setting.PowerSetting) == bytes(
                        _GUID_CONSOLE_DISPLAY_STATE
                    ):
                        # 0 off, 1 on, 2 dimmed; only "off" can precede standby.
                        state = int.from_bytes(bytes(setting.Data), "little")
                        self.ledger.display(state != 0)
                        self._report_first_notice(state)
                        log.debug("Console display state is now %d", state)
                    return 1
            except BaseException as exc:  # noqa: BLE001 — surfaced by problem()
                self.error = exc
                _kernel32.SetEvent(self._stop_event)
                return 0
            return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        def _run(self) -> None:
            instance = _kernel32.GetModuleHandleW(None)
            class_name = f"GaiaAwakeClock-{os.getpid()}-{next(_class_ids)}"
            notify = None
            registered_class = False
            try:
                wndclass = _WNDCLASSW(
                    lpfnWndProc=self._wndproc,
                    hInstance=instance,
                    lpszClassName=class_name,
                )
                if not _user32.RegisterClassW(ctypes.byref(wndclass)):
                    raise _win_error("RegisterClassW")
                registered_class = True
                # Message-only: power-setting notifications are addressed to
                # it, and system-wide broadcasts never wait on its pump.
                self.hwnd = _user32.CreateWindowExW(
                    0,
                    class_name,
                    class_name,
                    0,
                    0,
                    0,
                    0,
                    0,
                    _HWND_MESSAGE,
                    None,
                    instance,
                    None,
                )
                if not self.hwnd:
                    raise _win_error("CreateWindowExW")
                self._subscribed_at = _unbiased()
                notify = _user32.RegisterPowerSettingNotification(
                    self.hwnd, ctypes.byref(_GUID_CONSOLE_DISPLAY_STATE), 0
                )
                self.notify = notify
                if not notify:
                    raise _win_error(
                        "RegisterPowerSettingNotification(GUID_CONSOLE_DISPLAY_STATE)"
                    )
            except BaseException as exc:  # noqa: BLE001 — raised by start()
                self.error = AwakeClockError(
                    "Could not subscribe to the Windows display power state, which "
                    f"tells Modern Standby apart from awake time: {exc}. Time "
                    f"limits can't run without it. {_REPORT}"
                )
                self.error.__cause__ = exc
                self._ready.set()
                self._cleanup(notify, registered_class, class_name, instance)
                return
            self._ready.set()
            try:
                self._pump()
            except BaseException as exc:  # noqa: BLE001 — surfaced by problem()
                self.error = exc
                log.error("The awake-clock standby monitor stopped: %s", exc)
            finally:
                self._cleanup(notify, registered_class, class_name, instance)

        def _pump(self) -> None:
            msg = wintypes.MSG()
            handles = (wintypes.HANDLE * 1)(self._stop_event)
            wait_ms = int(HEARTBEAT * 1000)
            while True:
                woke = _user32.MsgWaitForMultipleObjects(
                    1, handles, False, wait_ms, _QS_ALLINPUT
                )
                if woke == _WAIT_FAILED:
                    raise _win_error("MsgWaitForMultipleObjects")
                # Before any message: a gap that just ended began under the
                # display state from before it.
                self.ledger.beat()
                if woke == _WAIT_OBJECT_0 or self.error is not None:
                    return
                self._report_no_notice()
                while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))

        def _report_first_notice(self, state: int) -> None:
            if self._display_seen:
                return
            self._display_seen = True
            log.info(
                "Receiving Windows display power notifications (display %s), "
                "so Modern Standby is left out of time limits.",
                {0: "off", 1: "on", 2: "dimmed"}.get(state, str(state)),
            )

        def _report_no_notice(self) -> None:
            if self._display_seen or self._quiet_reported:
                return
            if _unbiased() - self._subscribed_at < FIRST_NOTICE_WAIT:
                return
            self._quiet_reported = True
            session, console = _session_ids()
            if session != console:
                where = (
                    f"This process runs in session {session}, not the "
                    f"interactive console session ({console}): a service or a "
                    "remote logon gets no display notifications."
                )
            else:
                where = "Windows sent none, though this is the console session."
            log.warning(
                "No Windows display power notification arrived within %.0f s of "
                "subscribing. %s Time in Modern Standby will count toward time "
                "limits until one does.",
                FIRST_NOTICE_WAIT,
                where,
            )

        def _cleanup(self, notify, registered_class, class_name, instance) -> None:
            if notify and not _user32.UnregisterPowerSettingNotification(notify):
                log.error("%s", _win_error("UnregisterPowerSettingNotification"))
            if self.hwnd and not _user32.DestroyWindow(self.hwnd):
                log.error("%s", _win_error("DestroyWindow"))
            self.hwnd = None
            if registered_class and not _user32.UnregisterClassW(class_name, instance):
                log.error("%s", _win_error("UnregisterClassW"))

    _state_lock = threading.Lock()
    _ledger: Optional[SleepLedger] = None
    _monitor: Optional[_StandbyMonitor] = None
    _restarts = 0
    _last_problem: Optional[str] = None
    _shut_down = False

    def _gave_up() -> AwakeClockError:
        return AwakeClockError(
            "GAIA's time-limit clock stopped working: the Windows standby "
            f"monitor {_last_problem}, and {MAX_RESTARTS} restarts did not fix "
            "it. Restart GAIA. If it happens again, run `gaia diagnostics` and "
            "attach the bundle to an issue at https://github.com/amd/gaia/issues"
        )

    def _failure(what: str, detail: object) -> None:
        """Count one failure of the monitor, and say so loudly."""
        global _restarts
        _restarts += 1
        log.error(
            "The awake-clock standby monitor %s (failure %d of %d before GAIA "
            "gives up): %s",
            what,
            _restarts,
            MAX_RESTARTS + 1,
            detail,
        )

    def _start_monitor() -> SleepLedger:
        """Start a monitor, carrying over the standby already left out."""
        global _ledger, _monitor, _last_problem
        if _restarts > MAX_RESTARTS:
            raise _gave_up()
        excluded = _ledger.excluded if _ledger is not None else 0.0
        ledger = SleepLedger(_unbiased, time.process_time, excluded=excluded)
        try:
            monitor = _StandbyMonitor(ledger)
            monitor.start()
        except (AwakeClockError, OSError) as exc:
            _last_problem = "could not start"
            _failure("could not start", exc)
            if _restarts > MAX_RESTARTS:
                raise _gave_up() from exc
            raise
        _ledger, _monitor = ledger, monitor
        return ledger

    def _ensure_monitor() -> SleepLedger:
        global _monitor, _restarts, _last_problem
        with _state_lock:
            if _monitor is None or _ledger is None:
                return _start_monitor()
            if _shut_down:
                return _ledger
            problem = _monitor.problem()
            if problem is None:
                if _restarts and _unbiased() - _monitor.started_at > HEALTHY_RESET:
                    log.info(
                        "The awake-clock standby monitor has run for %.0f s "
                        "without trouble; its failure count is reset.",
                        HEALTHY_RESET,
                    )
                    _restarts = 0
                return _ledger
            _last_problem = problem
            _failure(
                problem,
                "starting a new one; the time since its last beat counts "
                "toward limits",
            )
            began = _unbiased()
            # Its time counts, unjudged; then it is told to stop, and forgotten
            # so a failed replacement is retried on the next read.
            _ledger.abandon()
            _monitor.stop(timeout=0.5)
            _monitor = None
            if _restarts > MAX_RESTARTS:
                raise _gave_up()
            ledger = _start_monitor()
            log.debug(
                "Replaced the awake-clock standby monitor in %.2f s",
                _unbiased() - began,
            )
            return ledger

    def monotonic() -> float:
        """Seconds since boot, not counting time the PC slept or was in standby.

        Raises:
            AwakeClockError: the standby monitor could not start this time, or
                it has failed more than :data:`MAX_RESTARTS` times.
            OSError: Windows refused ``QueryUnbiasedInterruptTime`` (it never
                does on Windows 7+).
        """
        return _ensure_monitor().read()

    def shutdown() -> None:
        """Stop the standby monitor; the clock keeps reading, without standby."""
        global _shut_down
        with _state_lock:
            if _monitor is None or _ledger is None or _shut_down:
                return
            _shut_down = True
            # Nothing beats any more, so nothing may be held for a beat.
            _ledger.detach()
            _monitor.stop()

    def _reset_for_tests() -> None:
        """Stop the monitor and forget it, so the next read starts a new one."""
        global _ledger, _monitor, _restarts, _last_problem, _shut_down
        with _state_lock:
            if _ledger is not None:
                _ledger.detach()
            if _monitor is not None:
                _monitor.stop()
            _ledger, _monitor, _shut_down = None, None, False
            _restarts, _last_problem = 0, None

    atexit.register(shutdown)

else:

    def monotonic() -> float:
        """Seconds since an arbitrary point, not counting time the PC slept."""
        return time.monotonic()

    def shutdown() -> None:
        """Nothing to stop: the POSIX monotonic clock already skips suspend."""
