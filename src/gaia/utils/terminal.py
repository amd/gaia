# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Whether a blocking ``input()`` on this process can reach a person."""

import sys


def stdin_is_interactive() -> bool:
    """True when stdin is a real terminal a person is typing into.

    False when stdin is missing, closed, a pipe, a file, or a null device.
    ``isatty()`` alone is not enough on Windows: ``NUL`` is a character
    device, so a child started with ``stdin=DEVNULL`` reports a terminal and
    every ``input()`` it reaches fails with ``EOFError``.
    """
    stream = sys.stdin
    try:
        if stream is None or not stream.isatty():
            return False
        if sys.platform != "win32":
            return True
        return is_windows_console(stream)
    except (AttributeError, ValueError, OSError):
        return False


def is_windows_console(stream) -> bool:
    """True when *stream* is attached to a Windows console, not ``NUL``."""
    import ctypes
    import msvcrt

    mode = ctypes.c_uint32()
    handle = msvcrt.get_osfhandle(stream.fileno())
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))
