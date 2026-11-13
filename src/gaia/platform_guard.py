# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep GAIA's processes off CPython's WMI query.

On Windows, Python 3.12+ answers ``platform.uname()``, ``system()``,
``version()``, ``machine()``, ``processor()``, ``win32_ver()`` and
``platform()`` by querying WMI through ``_wmi.exec_query``. When WMI is slow
(a busy PC) that call times out, raises ``OSError`` and returns, but leaves
its worker thread running with a pointer into the returned call's stack. The
thread later writes there and the process dies with an access violation in
``_wmi.pyd``, with no Python traceback.

:func:`avoid_wmi` makes ``platform`` take the answers its own code already
falls back to (``sys.getwindowsversion``, ``ver``, the registry and the
``PROCESSOR_*`` variables), so no WMI thread is ever started here.
"""

from __future__ import annotations

import platform
import sys

_GUARD_MESSAGE = "GAIA reads platform facts without WMI"


def _no_wmi_query(*_args, **_kwargs):
    raise OSError(_GUARD_MESSAGE)


def avoid_wmi() -> None:
    """Route this process's ``platform`` lookups around WMI. Idempotent."""
    if not hasattr(platform, "_wmi_query"):
        if sys.platform == "win32" and sys.version_info >= (3, 12):
            raise RuntimeError(
                "platform._wmi_query is gone from this Python "
                f"({sys.version.split()[0]}), so GAIA can no longer keep "
                "itself off WMI, whose timed-out queries crash the process. "
                "Update src/gaia/platform_guard.py for this Python."
            )
        return
    platform._wmi_query = _no_wmi_query  # pylint: disable=protected-access
    # Fill platform's caches now, on the main thread, before any worker asks.
    platform.uname()
    platform.processor()
