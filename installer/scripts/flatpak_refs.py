# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Print the Flatpak refs electron-builder needs installed before it can build.

Reads the ``flatpak:`` block of an electron-builder.yml, so the build machine
installs exactly the runtime, SDK and base app the config names::

    flatpak install --user -y flathub $(python flatpak_refs.py electron-builder.yml)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIRED = ("runtime", "runtimeVersion", "sdk", "base", "baseVersion")


def flatpak_settings(text: str) -> dict[str, str]:
    """The scalar ``key: value`` pairs directly under the top-level ``flatpak:``."""
    settings: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if re.match(r"^flatpak:\s*(#.*)?$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        if inside:
            m = re.match(r"^  ([A-Za-z]+):\s*[\"']?([^\"'#\s]+)[\"']?\s*(#.*)?$", line)
            if m:
                settings[m.group(1)] = m.group(2)
    return settings


def refs(text: str, source: str = "electron-builder.yml") -> list[str]:
    """``id//branch`` for the runtime, the SDK and the base app."""
    settings = flatpak_settings(text)
    missing = [key for key in REQUIRED if key not in settings]
    if missing:
        raise ValueError(
            f"{source} has no flatpak.{', flatpak.'.join(missing)}. "
            "The Flatpak build needs all of "
            f"{', '.join(REQUIRED)} under a top-level `flatpak:` block; "
            "see https://www.electron.build/flatpak"
        )
    return [
        f"{settings['runtime']}//{settings['runtimeVersion']}",
        f"{settings['sdk']}//{settings['runtimeVersion']}",
        f"{settings['base']}//{settings['baseVersion']}",
    ]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <electron-builder.yml>", file=sys.stderr)
        return 2
    path = Path(argv[1])
    print(" ".join(refs(path.read_text(encoding="utf-8"), str(path))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
