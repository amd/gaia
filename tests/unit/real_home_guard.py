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
import stat
import tempfile
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


# Only these well-known system symlinks are treated as benign: an unexpected
# symlink in a home or temp ancestor must not be trusted automatically when
# realpath() cannot be resolved.
_BENIGN_SYSTEM_SYMLINKS = frozenset(
    {
        "/var",
        "/tmp",
        "/etc",
        "/private/var",
        "/private/tmp",
        "/private/etc",
    }
)


def _is_benign_symlink(path) -> bool:
    return os.path.normpath(path) in _BENIGN_SYSTEM_SYMLINKS


def _contains_symlink(path) -> bool:
    """Whether *path* or any existing ancestor of it is a symbolic link.

    ``os.lstat`` is used instead of ``os.path.lexists`` so that an
    inaccessible ancestor is not mistaken for a missing one: a stat failure
    (other than ``FileNotFoundError``) is treated as suspicious.
    """
    current = os.path.abspath(os.fspath(path))
    while True:
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            # The path (or this ancestor) does not exist: not a symlink here.
            pass
        except OSError:
            # Cannot stat the path (for example permission denied): treat as
            # suspicious rather than assuming there is no symlink.
            return True
        else:
            if stat.S_ISLNK(mode) and not _is_benign_symlink(current):
                return True
        parent = os.path.dirname(current)
        if not parent or parent == current:
            break
        current = parent
    return False


def is_within(path, root) -> bool:
    """Whether *path* is *root* or sits underneath it, on the resolved forms.

    A temporary folder reached through the ``/var`` symlink on macOS still
    matches its ``/private/var`` spelling, and the case rules of a Windows or
    macOS home folder decide.
    """
    try:
        target = os.path.normcase(os.path.realpath(os.fspath(path)))
        parent = os.path.normcase(os.path.realpath(os.fspath(root)))
    except (OSError, ValueError):
        if _contains_symlink(path) or _contains_symlink(root):
            return True
        target = os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(path))))
        parent = os.path.normcase(os.path.normpath(os.path.abspath(os.fspath(root))))
    prefix = parent if parent.endswith(os.sep) else parent + os.sep
    return target == parent or target.startswith(prefix)


def is_temporary_home(path) -> bool:
    """Whether *path* is the system temporary folder or sits inside it.

    A run started with a temporary HOME has no developer install to protect:
    ``<that HOME>/.gaia`` belongs to the test run, not to the person.
    """
    return is_within(path, tempfile.gettempdir())


def account_home() -> Optional[Path]:
    """The user account's own home directory, whatever a process's ``HOME`` says.

    A run given a temporary ``HOME`` is a test run; this is the folder that holds
    the person's real GAIA state, and it is the one a guard still protects.
    """
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        pass
    if os.name == "nt":
        home = _windows_account_home()
        if home is not None:
            return home
        # Do not fall back to USERPROFILE here: a test run may have overwritten
        # it with a temporary folder, which would leave the real account home
        # unprotected. Failing to find a home makes the guards refuse rather
        # than trust a (possibly tampered) value.
        return None
    user_home = os.environ.get("HOME")
    return Path(user_home) if user_home else None


def _windows_account_home() -> Optional[Path]:
    """The account home on Windows, from the registry rather than the env.

    ``USERPROFILE`` is the account home on Windows, but a test run overwrites it
    with a temporary folder, so the volatile environment key is read instead.
    Without it, a run given a temporary ``HOME`` and ``USERPROFILE`` would find
    its own folder where the real account's state lives.
    """
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Volatile Environment") as key:
            image, _type = winreg.QueryValueEx(key, "USERPROFILE", 0)
        return Path(image) if image else None
    except (ImportError, KeyError, OSError, ValueError):
        return None


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
