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
  size. A freeze with the display on is a pause, and a busy process starved
  of its turn is not frozen; both count, and are logged. A process stalled
  for over ``STANDBY_GAP`` seconds while using little CPU with the display off
  (heavy paging, say) can't be told from standby this way, and is left out.
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
    """

    def __init__(
        self,
        clock: Callable[[], float],
        cpu: Callable[[], float],
        gap: float = STANDBY_GAP,
    ):
        self._clock = clock
        self._cpu = cpu
        self._lock = threading.Lock()
        self.gap = gap
        self.excluded = 0.0
        self._last_beat = clock()
        self._last_cpu = cpu()
        self._display_on = True
        self._monitored = True

    def read(self) -> float:
        """The awake time now."""
        with self._lock:
            raw = self._clock()
            held = self._monitored and not self._display_on
            if held and raw - self._last_beat > self.gap:
                raw = self._last_beat + self.gap
            return raw - self.excluded

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

    def _beat_locked(self):
        raw, cpu = self._clock(), self._cpu()
        frozen = raw - self._last_beat
        busy = cpu - self._last_cpu
        left_out = 0.0
        if frozen > self.gap and not self._display_on and busy < frozen / 2:
            left_out = frozen - self.gap
            self.excluded += left_out
        self._last_beat, self._last_cpu = raw, cpu
        return left_out, frozen, busy, self._display_on, self.excluded

    def _log(self, left_out, frozen, busy, display_on, excluded) -> float:
        if left_out:
            log.info(
                "Left %.1f s out of time limits: the process was frozen for "
                "%.1f s with the display off, using %.1f s of CPU, which is "
                "Windows Modern Standby; the first %.0f s still count. %.1f s "
                "left out so far.",
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
            self._stop_event = _kernel32.CreateEventW(None, True, False, None)
            if not self._stop_event:
                raise _win_error("CreateEventW")
            self._wndproc = _WNDPROC(self._on_message)
            self._thread = threading.Thread(
                target=self._run, name="gaia-awake-clock", daemon=True
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

        def check(self) -> None:
            """Raise if the monitor failed or died; time limits need it."""
            if self.error is not None:
                raise AwakeClockError(
                    f"The standby monitor failed ({self.error}), so time limits "
                    f"can't tell Modern Standby from awake time. {_REPORT}"
                ) from self.error
            if not self._stopping and not self._thread.is_alive():
                raise AwakeClockError(
                    "The standby monitor thread exited, so time limits can't "
                    f"tell Modern Standby from awake time. {_REPORT}"
                )

        def stop(self, timeout: float = 5.0) -> None:
            if self._stop_event is None:
                return
            self._stopping = True
            _kernel32.SetEvent(self._stop_event)
            if (
                self._thread.is_alive()
                and self._thread is not threading.current_thread()
            ):
                self._thread.join(timeout)
                if self._thread.is_alive():
                    log.error(
                        "The awake-clock standby monitor did not stop within %.0f s.",
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
                        log.debug("Console display state is now %d", state)
                    return 1
            except BaseException as exc:  # noqa: BLE001 — surfaced by check()
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
                notify = _user32.RegisterPowerSettingNotification(
                    self.hwnd, ctypes.byref(_GUID_CONSOLE_DISPLAY_STATE), 0
                )
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
            except BaseException as exc:  # noqa: BLE001 — surfaced by check()
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
                while _user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, _PM_REMOVE):
                    _user32.TranslateMessage(ctypes.byref(msg))
                    _user32.DispatchMessageW(ctypes.byref(msg))

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
    _start_error: Optional[BaseException] = None
    _shut_down = False

    def _ensure_monitor() -> SleepLedger:
        global _ledger, _monitor, _start_error
        with _state_lock:
            if _start_error is not None:
                raise _start_error
            if _monitor is None:
                ledger = SleepLedger(_unbiased, time.process_time)
                monitor = _StandbyMonitor(ledger)
                try:
                    monitor.start()
                except BaseException as exc:
                    _start_error = exc
                    raise
                _ledger, _monitor = ledger, monitor
            if not _shut_down:
                _monitor.check()
            return _ledger

    def monotonic() -> float:
        """Seconds since boot, not counting time the PC slept or was in standby.

        Raises:
            AwakeClockError: the standby monitor could not subscribe to the
                display power state, or it has died.
            OSError: Windows refused ``QueryUnbiasedInterruptTime`` (it never
                does on Windows 7+).
        """
        return _ensure_monitor().read()

    def shutdown() -> None:
        """Stop the standby monitor; the clock keeps reading, without standby."""
        global _shut_down
        with _state_lock:
            if _monitor is None or _shut_down:
                return
            _shut_down = True
            # Nothing beats any more, so nothing may be held for a beat.
            _ledger.detach()
            _monitor.stop()

    def _reset_for_tests() -> None:
        """Stop the monitor and forget it, so the next read starts a new one."""
        global _ledger, _monitor, _start_error, _shut_down
        with _state_lock:
            if _monitor is not None:
                _monitor.stop()
            _ledger, _monitor, _start_error, _shut_down = None, None, None, False

    atexit.register(shutdown)

else:

    def monotonic() -> float:
        """Seconds since an arbitrary point, not counting time the PC slept."""
        return time.monotonic()

    def shutdown() -> None:
        """Nothing to stop: the POSIX monotonic clock already skips suspend."""
