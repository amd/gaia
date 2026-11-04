#!/usr/bin/env python
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Save the GAIA desktop window as a PNG on disk, for the report and for issues.

    python capture_window.py RUN_DIR/shots/012_settings_ar.png --cdp 9333   # preferred: fresh frame
    python capture_window.py OUT.png --pid 4242                             # the app's window, native chrome too
    python capture_window.py OUT.png --screen                               # every monitor
    python capture_window.py --list                                         # visible windows

--cdp asks the renderer for a new frame over the DevTools port that
launch_isolated.py opens, so it is always current and works when the window is
covered. It shows the page only: no title bar, window controls or native dialogs.

--pid/--hwnd use PrintWindow, which includes native chrome. Electron can stop
painting a covered window, so a covered window may come back as an old frame; use
it with the window in front (the script warns otherwise) and look at the result.

Standard library only; window and screen modes are Windows only.
"""

from __future__ import annotations

import argparse
import ctypes
import struct
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cdp  # noqa: E402

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:  # physical pixels on a scaled display; must run before any window query
        ctypes.WinDLL("shcore").SetProcessDpiAwareness(2)
    except OSError:
        user32.SetProcessDPIAware()
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]


PW_RENDERFULLCONTENT = 2
SRCCOPY = 0x00CC0020
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def owner(hwnd: int) -> tuple[int, str]:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not h:
        return pid.value, ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        return pid.value, Path(buf.value).name
    finally:
        kernel32.CloseHandle(h)


def windows() -> list[tuple[int, str, str, tuple[int, int, int, int], int]]:
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        if not n:
            return True
        title = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, title, n + 1)
        r = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        if r.right - r.left > 50 and r.bottom - r.top > 50:
            pid, exe = owner(hwnd)
            found.append(
                (hwnd, title.value, exe, (r.left, r.top, r.right, r.bottom), pid)
            )
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def grab(hwnd: int | None, rect: tuple[int, int, int, int]) -> tuple[int, int, bytes]:
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old = gdi32.SelectObject(mem_dc, bmp)
    try:
        if hwnd is None:
            gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, left, top, SRCCOPY)
        elif not user32.PrintWindow(hwnd, mem_dc, PW_RENDERFULLCONTENT):
            # Copying the screen instead would capture whatever covers the window.
            sys.exit(
                "PrintWindow failed for this window; use --cdp or a computer-use screenshot."
            )
        bi = BITMAPINFOHEADER(
            ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0
        )
        buf = ctypes.create_string_buffer(w * h * 4)
        if not gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), 0):
            sys.exit("GetDIBits failed: the window could not be read.")
        return w, h, buf.raw
    finally:
        gdi32.SelectObject(mem_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(0, screen_dc)


def write_png(path: Path, w: int, h: int, bgra: bytes) -> None:
    rows = bytearray()
    stride = w * 4
    for y in range(h):
        row = bgra[y * stride : (y + 1) * stride]
        rgb = bytearray(w * 3)
        rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
        rows += b"\x00" + rgb

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows), 6))
        + chunk(b"IEND", b"")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(png)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "out",
        nargs="?",
        type=Path,
        help="PNG to write, e.g. RUN_DIR/shots/012_settings.png",
    )
    ap.add_argument(
        "--title",
        default="GAIA Agent UI",
        help="Window title must contain this (default 'GAIA Agent UI')",
    )
    ap.add_argument(
        "--process",
        default="",
        help="Owning executable, e.g. gaia-desktop.exe (packaged) or electron.exe (dev); default any",
    )
    ap.add_argument(
        "--pid", type=int, help="Only the window of this process (the app you launched)"
    )
    ap.add_argument("--hwnd", type=int, help="Exactly this window, from --list")
    ap.add_argument(
        "--cdp",
        type=int,
        help="DevTools port (launch_isolated.py start --cdp): fresh page frame",
    )
    ap.add_argument(
        "--screen",
        action="store_true",
        help="Capture every monitor instead of one window",
    )
    ap.add_argument("--list", action="store_true", help="List visible windows and exit")
    a = ap.parse_args()

    if a.list:
        if sys.platform != "win32":
            sys.exit("--list is Windows only.")
        for hwnd, title, proc, r, pid in windows():
            print(
                f"hwnd {hwnd:>10}  pid {pid:>6}  {proc:<24} {r[2] - r[0]}x{r[3] - r[1]}  {title}"
            )
        return
    if not a.out:
        ap.error("give an output path, or --list")
    if a.cdp:
        w, h = cdp.shot(a.cdp, a.out)
        print(f"{a.out} ({w}x{h}, page via DevTools)")
        return
    if sys.platform != "win32":
        sys.exit(
            "Window and screen capture are Windows only; use --cdp, or the OS screenshot tool."
        )

    if a.screen:
        x, y = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
        rect = (x, y, x + user32.GetSystemMetrics(78), y + user32.GetSystemMetrics(79))
        w, h, px = grab(None, rect)
    else:
        if a.hwnd:
            match = [wd for wd in windows() if wd[0] == a.hwnd]
        elif a.pid:
            match = [wd for wd in windows() if wd[4] == a.pid]
        else:
            match = [
                wd
                for wd in windows()
                if a.title.lower() in wd[1].lower()
                and (not a.process or wd[2].lower() == a.process.lower())
            ]
        if not match:
            sys.exit(
                "No visible window matches. Is the app open and not minimised? "
                "`--list` shows what is there; a dev build runs as electron.exe, so pass --pid or --hwnd."
            )
        if len(match) > 1 and not a.pid:
            # Another session's GAIA window is a real risk on a shared desktop: never guess.
            listing = "\n".join(
                f"  hwnd {wd[0]}  pid {wd[4]}  {wd[2]}  {wd[1]}" for wd in match
            )
            sys.exit(
                f"More than one window matches:\n{listing}\nPass --pid of the app you launched, or --hwnd."
            )
        match.sort(
            key=lambda wd: (wd[3][2] - wd[3][0]) * (wd[3][3] - wd[3][1]), reverse=True
        )
        hwnd, title, proc, rect, _ = match[0]
        w, h, px = grab(hwnd, rect)
        if user32.GetForegroundWindow() != hwnd:
            print(
                "warning: the window is not in front, so this frame may be old. "
                "Use --cdp, or bring it forward and capture again.",
                file=sys.stderr,
            )
    write_png(a.out, w, h, px)
    print(f"{a.out} ({w}x{h})")


if __name__ == "__main__":
    main()
