# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Console helpers: whether ``input()`` can reach a person, and how to start a
child process without a console window popping up on Windows."""

import sys

# Windows process-creation flags. Literal so they resolve off Windows too, where
# ``subprocess`` does not define them.
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000


def windows_hidden_popen_kwargs() -> dict:
    """``subprocess.Popen`` kwargs that keep a child's console hidden on Windows.

    The child gets a console of its own with no window, and every console
    program it starts inherits that console. Never use ``DETACHED_PROCESS``
    for a background process: one with no console at all makes Windows open
    a new, visible, blank console for each console program it starts.

    Returns:
        ``{"creationflags": CREATE_NO_WINDOW}`` on Windows, ``{}`` elsewhere.
    """
    if sys.platform != "win32":
        return {}
    return {"creationflags": CREATE_NO_WINDOW}


def background_popen_kwargs() -> dict:
    """``subprocess.Popen`` kwargs for a long-running server GAIA owns.

    Its own process group (Windows) or session (POSIX), so a tree-kill reaches
    its children and not the caller, and it outlives the process that started
    it. On Windows it also gets a hidden console (see
    :func:`windows_hidden_popen_kwargs`).
    """
    if sys.platform != "win32":
        return {"start_new_session": True}
    return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}


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
