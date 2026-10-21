# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a Lemonade version-bump patch, then apply it.

The Lemonade bump workflow runs main's copy of this script on a fresh checkout
of main, before anything in the patch is applied or run:

    python -I util/check_lemonade_bump.py --repo . --patch bump.patch --target 2026.43.1

The patch may only modify these files, and only like this:

- ``src/gaia/version.py``: ``LEMONADE_VERSION``, set to ``--target``.
- ``src/gaia/llm/lemonade_embedded.py``: ``EMBEDDABLE_SHA256``.
- ``src/gaia/installer/lemonade_installer.py``: ``INSTALLER_SHA256`` and
  ``LEGACY_MSI_SHA256``. Existing legacy pins stay as they are.
- ``tests/fixtures/lemonade/release_tags.txt``: new tags appended.
- ``.md``/``.mdx`` files under docs/, cpp/, tui/ and .claude/: main's
  ``LEMONADE_VERSION`` replaced by ``--target``, and nothing else.

In the Python files every added or removed line must be a one-line pin
(``"key": "<sha256>",``) or the ``LEMONADE_VERSION`` line, so they gain no code
and no comments. Nothing is created, deleted, renamed or re-moded. Exits 0
after applying the patch to the index and work tree, and 1, with the reason,
when any check fails.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import re
import subprocess
import sys
from pathlib import Path

PIN_ASSIGNMENTS = {
    "src/gaia/version.py": ("LEMONADE_VERSION",),
    "src/gaia/llm/lemonade_embedded.py": ("EMBEDDABLE_SHA256",),
    "src/gaia/installer/lemonade_installer.py": (
        "INSTALLER_SHA256",
        "LEGACY_MSI_SHA256",
    ),
}
APPEND_ONLY_PINS = ("LEGACY_MSI_SHA256",)
VERSION_FILE = "src/gaia/version.py"
RELEASE_TAGS = "tests/fixtures/lemonade/release_tags.txt"
DOC_PATH = re.compile(r"(?:docs|cpp|tui|\.claude)/(?:[^/]+/)*[^/]+\.mdx?")
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"\d+\.\d+\.\d+")
ADDED_TAGS = re.compile(r"(?:v\d+\.\d+\.\d+\n)+")
CHANGEABLE_LINE = re.compile(
    r' {4}"[A-Za-z0-9._/-]+": "[0-9a-f]{64}",|LEMONADE_VERSION = "\d+\.\d+\.\d+"'
)


class BumpRejected(Exception):
    """The patch does something a Lemonade version bump never does."""


def _git_bytes(repo: Path, *args: str) -> bytes:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", "replace").strip()
        raise BumpRejected(f"git {' '.join(args)} failed: {error}")
    return result.stdout


def _git(repo: Path, *args: str) -> str:
    return _git_bytes(repo, *args).decode("utf-8")


def _text(data: bytes, path: str) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise BumpRejected(f"{path} is not UTF-8: {e}") from e


def changed_paths(repo: Path, patch: Path) -> list:
    """Paths the patch modifies, refusing anything but in-place text edits."""
    summary = _git(repo, "apply", "--summary", str(patch)).strip()
    if summary:
        raise BumpRejected(
            f"the patch creates, deletes, renames or re-modes files:\n{summary}"
        )
    records = _git(repo, "apply", "--numstat", "-z", str(patch)).split("\0")
    paths = []
    for record in filter(None, records):
        added, deleted, path = (record.split("\t", 2) + ["", ""])[:3]
        if not path:
            raise BumpRejected(f"the patch renames or copies a file: {record!r}")
        if added == "-" or deleted == "-":
            raise BumpRejected(f"the patch changes a binary file: {path}")
        paths.append(path)
    if not paths:
        raise BumpRejected("the patch changes nothing")
    return paths


def check_allowed(repo: Path, paths: list) -> None:
    for path in paths:
        allowed = (
            path in PIN_ASSIGNMENTS
            or path == RELEASE_TAGS
            or (DOC_PATH.fullmatch(path) and ".." not in path.split("/"))
        )
        if not allowed:
            raise BumpRejected(f"a Lemonade bump never edits {path}")
        mode = _git(repo, "ls-files", "--stage", "--", path).split(" ", 1)[0]
        if mode != "100644":
            raise BumpRejected(f"{path} is not a regular file on main")


def _pin_values(source: bytes, path: str) -> tuple:
    """The module with its pin assignments blanked, and those assignments' values.

    Parsed from bytes, so a coding declaration is read as Python would read it.
    """
    names = PIN_ASSIGNMENTS[path]
    tree = ast.parse(source)
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        else:
            continue
        if isinstance(target, ast.Name) and target.id in names:
            if target.id in values:
                raise BumpRejected(f"{path} assigns {target.id} twice")
            values[target.id] = node.value
            node.value = ast.Constant(None)
    missing = set(names) - set(values)
    if missing:
        raise BumpRejected(f"{path} no longer assigns {sorted(missing)}")
    return ast.dump(tree), values


def _literal_pins(node, path: str, name: str) -> dict:
    if not isinstance(node, ast.Dict):
        raise BumpRejected(f"{path}: {name} is not a dict literal")
    pins = {}
    for key, value in zip(node.keys, node.values):
        ok = (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(value, ast.Constant)
            and isinstance(value.value, str)
            and SHA256.fullmatch(value.value)
        )
        if not ok:
            raise BumpRejected(
                f"{path}: every {name} entry must map a string to a SHA-256 digest"
            )
        if key.value in pins:
            raise BumpRejected(f"{path}: {name} pins {key.value} twice")
        pins[key.value] = value.value
    return pins


def _changed_lines(old: list, new: list) -> list:
    matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    changed = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            changed += old[i1:i2] + new[j1:j2]
    return changed


def check_python(path: str, old: bytes, new: bytes, target: str) -> None:
    for line in _changed_lines(
        _text(old, path).split("\n"), _text(new, path).split("\n")
    ):
        if not CHANGEABLE_LINE.fullmatch(line):
            raise BumpRejected(f"{path} changes a line that is not a pin: {line!r}")
    old_shape, old_values = _pin_values(old, path)
    new_shape, new_values = _pin_values(new, path)
    if old_shape != new_shape:
        raise BumpRejected(f"{path} changes more than its pinned values")
    for name, node in new_values.items():
        if name == "LEMONADE_VERSION":
            ok = isinstance(node, ast.Constant) and node.value == target
            if not ok or not VERSION.fullmatch(target):
                raise BumpRejected(f"{path}: LEMONADE_VERSION must be {target!r}")
            continue
        pins = _literal_pins(node, path, name)
        if name in APPEND_ONLY_PINS:
            kept = _literal_pins(old_values[name], path, name)
            if list(pins.items())[: len(kept)] != list(kept.items()):
                raise BumpRejected(f"{path}: {name} may only gain entries at its end")


def check_release_tags(old: str, new: str) -> None:
    if not (old.endswith("\n") and new.startswith(old)):
        raise BumpRejected(f"{RELEASE_TAGS} may only gain tags at its end")
    if not ADDED_TAGS.fullmatch(new[len(old) :]):
        raise BumpRejected(f"{RELEASE_TAGS} must gain one or more vX.Y.Z tags")


def _swap_version(line: str, current: str, target: str) -> str:
    """*line* with each standalone *current* replaced by *target*.

    A minimum version ("10.2.0+", "10.2.0 or later") and a longer number that
    merely contains *current* are left alone.
    """
    pattern = rf"(?<![\d.]){re.escape(current)}(?!\d|\.\d|\+|\s+or later)"
    return re.sub(pattern, target, line)


def check_doc(path: str, old: str, new: str, current: str, target: str) -> None:
    # Split on "\n" only: other line separators must not change unnoticed.
    old_lines, new_lines = old.split("\n"), new.split("\n")
    same = len(old_lines) == len(new_lines) and all(
        a == b or _swap_version(a, current, target) == b
        for a, b in zip(old_lines, new_lines)
    )
    if not same:
        raise BumpRejected(
            f"{path} changes more than Lemonade's version ({current} -> {target})"
        )


def current_version(repo: Path) -> str:
    _, values = _pin_values(
        _git_bytes(repo, "show", f"HEAD:{VERSION_FILE}"), VERSION_FILE
    )
    node = values["LEMONADE_VERSION"]
    if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
        raise BumpRejected(f"main's {VERSION_FILE} has no LEMONADE_VERSION string")
    return node.value


def check_and_apply(repo: Path, patch: Path, target: str) -> list:
    """Validate *patch* against *repo*'s HEAD, then apply it to the index."""
    if not VERSION.fullmatch(target):
        raise BumpRejected(f"--target {target!r} is not a version")
    current = current_version(repo)
    paths = changed_paths(repo, patch)
    check_allowed(repo, paths)
    _git(repo, "apply", "--check", "--index", str(patch))
    _git(repo, "apply", "--index", str(patch))
    staged = _git(repo, "diff", "--cached", "--name-only", "-z").split("\0")
    if set(filter(None, staged)) != set(paths):
        raise BumpRejected(
            f"the applied patch touched {sorted(set(staged) ^ set(paths))}"
        )
    for path in paths:
        old = _git_bytes(repo, "show", f"HEAD:{path}")
        new = _git_bytes(repo, "show", f":{path}")
        if path in PIN_ASSIGNMENTS:
            check_python(path, old, new, target)
        elif path == RELEASE_TAGS:
            check_release_tags(_text(old, path), _text(new, path))
        else:
            check_doc(path, _text(old, path), _text(new, path), current, target)
    return paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    try:
        paths = check_and_apply(args.repo, args.patch.resolve(), args.target)
    except BumpRejected as e:
        print(f"Rejected the Lemonade bump patch: {e}", file=sys.stderr)
        return 1
    print("Applied the Lemonade bump patch:\n  " + "\n  ".join(paths))
    return 0


if __name__ == "__main__":
    sys.exit(main())
