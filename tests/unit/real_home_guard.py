# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail a test session that leaves the real ``~/.gaia`` different from how it found it.

The per-test guard in ``conftest.py`` sees this process's ``open``, ``mkdir``
and ``rename`` calls. This one compares a listing of the folder before and
after the session, so it also catches a child process, a deleted file, a
SQLite database, and a write made outside any test (collection, a session
fixture).

Standard library only, and nothing here imports ``gaia``: the guard's own test
loads it into a pytest session of its own.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

#: Set to ``1`` to run tests that are meant to touch the real ``~/.gaia``.
ALLOW_ENV = "GAIA_ALLOW_REAL_HOME_WRITES"

#: Entries listed at most, so a home holding a model cache still costs well
#: under a second. A listing cut here reports changes, not new or removed
#: paths; a file created or removed still shows as its folder changing.
MAX_ENTRIES = 20_000

#: (is a folder, size in bytes or -1 for a folder, modified time in ns).
Entry = Tuple[bool, int, int]


class Listing:
    """What ``~/.gaia`` held at one moment; ``entries`` is None when it is missing."""

    def __init__(self, entries: Optional[Dict[str, Entry]], complete: bool = True):
        self.entries = entries
        self.complete = complete


def real_gaia_home() -> Path:
    return Path.home() / ".gaia"


def list_home(root: Path, max_entries: int = MAX_ENTRIES) -> Listing:
    """List *root* without following links, reading or creating anything."""
    try:
        top = os.stat(root)
    except OSError:
        return Listing(None)
    entries: Dict[str, Entry] = {"": (True, -1, top.st_mtime_ns)}
    pending = [str(root)]
    while pending:
        folder = pending.pop()
        try:
            with os.scandir(folder) as found:
                children = sorted(found, key=lambda child: child.name)
        except OSError:  # removed or unreadable since it was listed
            continue
        for child in children:
            if len(entries) >= max_entries:
                return Listing(entries, complete=False)
            try:
                is_folder = child.is_dir(follow_symlinks=False)
                stat = child.stat(follow_symlinks=False)
            except OSError:
                continue
            relative = os.path.relpath(child.path, root)
            entries[relative] = (
                is_folder,
                -1 if is_folder else stat.st_size,
                stat.st_mtime_ns,
            )
            if is_folder:
                pending.append(child.path)
    return Listing(entries)


def changes(before: Listing, after: Listing) -> List[str]:
    """One line per path the session created, changed or removed."""
    if before.entries is None:
        return [] if after.entries is None else ["created ."]
    if after.entries is None:
        return ["removed ."]
    found = []
    whole = before.complete and after.complete
    for path in sorted(set(before.entries) | set(after.entries)):
        was, now = before.entries.get(path), after.entries.get(path)
        if was is not None and now is not None:
            if was != now:
                found.append(f"changed {path or '.'}")
        elif whole:
            found.append(f"{'created' if was is None else 'removed'} {path}")
    return found


@pytest.fixture(scope="session", autouse=True)
def _guard_real_gaia_home_session():
    """Fail the session when it created or changed anything in the real ``~/.gaia``."""
    if os.environ.get(ALLOW_ENV) == "1":
        yield
        return
    root = real_gaia_home()
    before = list_home(root)
    yield
    found = changes(before, list_home(root))
    if found:
        shown = found[:50]
        if len(found) > len(shown):
            shown.append(f"... and {len(found) - len(shown)} more")
        pytest.fail(
            f"This test session left the real GAIA home {root} different from "
            "how it found it:\n  "
            + "\n  ".join(shown)
            + "\nPoint the test that did it at tmp_path (the mock_home fixture, "
            "or GAIA_HOME; a child process needs HOME and USERPROFILE too). A "
            "GAIA app running on this machine during the tests changes that "
            f"folder as well: close it, or set {ALLOW_ENV}=1 to skip this check.",
            pytrace=False,
        )
