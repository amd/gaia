# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The display adapters Windows records in the registry, one walk for all readers."""

from __future__ import annotations

from typing import Any, Dict, Iterator

#: Windows' display-adapter class; each adapter's subkey names the adapter.
DISPLAY_CLASS_KEY = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)


def iter_display_adapters(*extra_values: str) -> Iterator[Dict[str, Any]]:
    """Yield each adapter's ``DriverDesc`` and whichever *extra_values* it has.

    A subkey that can't be opened or has no ``DriverDesc`` ("Properties" and
    other non-adapter subkeys) is skipped; an extra value that can't be read is
    left out of that adapter's dict. Yields nothing when the class key can't be
    opened. Windows only: the caller checks the platform.
    """
    import winreg  # pylint: disable=import-error

    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, DISPLAY_CLASS_KEY)
    except OSError:
        return
    with root:
        index = 0
        while True:
            try:
                subkey = winreg.EnumKey(root, index)
            except OSError:
                return
            index += 1
            try:
                with winreg.OpenKey(root, subkey) as adapter:
                    values = {
                        "DriverDesc": winreg.QueryValueEx(adapter, "DriverDesc")[0]
                    }
                    for name in extra_values:
                        try:
                            values[name] = winreg.QueryValueEx(adapter, name)[0]
                        except OSError:
                            pass  # absent value: the caller decides what that means
            except OSError:
                continue
            yield values
