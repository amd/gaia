# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Report a test session that leaves the real ``~/.gaia`` different from how it found it.

In CI (``CI`` set, as GitHub Actions does) or with
``GAIA_ENFORCE_REAL_HOME_GUARD=1`` the session fails. Anywhere else it ends
with one warning naming the same paths, because a GAIA app left running on a
developer's machine writes to that folder too, as do other jobs on a
self-hosted runner (``RUNNER_ENVIRONMENT=self-hosted``). ``GAIA_ALLOW_REAL_HOME_WRITES=1``
skips the check.

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
import warnings
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

import pytest

#: Set to ``1`` to run tests that are meant to touch the real ``~/.gaia``.
ALLOW_ENV = "GAIA_ALLOW_REAL_HOME_WRITES"
#: Set to ``1`` to fail the session outside CI as well.
ENFORCE_ENV = "GAIA_ENFORCE_REAL_HOME_GUARD"

SKIP, WARN, FAIL = "skip", "warn", "fail"
_FALSY = ("", "0", "false", "no", "off")


def guard_mode(environ: Mapping[str, str]) -> str:
    """What a changed home does to the session: nothing, a warning, or a failure."""
    if environ.get(ALLOW_ENV) == "1":
        return SKIP
    if environ.get(ENFORCE_ENV) == "1":
        return FAIL
    in_ci = environ.get("CI", "").strip().lower() not in _FALSY
    # A self-hosted runner shares its home with other jobs on the machine.
    shared = environ.get("RUNNER_ENVIRONMENT") == "self-hosted"
    return FAIL if in_ci and not shared else WARN


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
                # Not child.stat(): on NTFS that is the parent's lazily updated copy.
                stat = os.lstat(child.path)
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


def describe(found: List[str], root: Path) -> str:
    """The message for a session that left *found* behind in *root*."""
    shown = found[:50]
    if len(found) > len(shown):
        shown.append(f"... and {len(found) - len(shown)} more")
    return (
        f"This test session left the real GAIA home {root} different from how "
        "it found it:\n  "
        + "\n  ".join(shown)
        + "\nPoint the test that did it at tmp_path (the mock_home fixture, or "
        "GAIA_HOME; a child process needs HOME and USERPROFILE too). A GAIA "
        "app running on this machine during the tests changes that folder as "
        f"well: close it, or set {ALLOW_ENV}=1 to skip this check."
    )


def settle(found: List[str], root: Path, mode: str) -> None:
    """Fail or warn about *found*, as *mode* says; nothing found is silence."""
    if not found or mode == SKIP:
        return
    if mode == FAIL:
        pytest.fail(describe(found, root), pytrace=False)
    warnings.warn(
        pytest.PytestWarning(
            describe(found, root)
            + f" This fails in CI; set {ENFORCE_ENV}=1 to fail here too."
        )
    )


@pytest.fixture(scope="session", autouse=True)
def _guard_real_gaia_home_session():
    """Compare the real ``~/.gaia`` before and after the session."""
    mode = guard_mode(os.environ)
    if mode == SKIP:
        yield
        return
    root = real_gaia_home()
    before = list_home(root)
    yield
    settle(changes(before, list_home(root)), root, mode)
