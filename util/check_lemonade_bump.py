# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check a Lemonade version-bump patch, then apply it.

The Lemonade bump workflow runs main's copy of this script on a fresh checkout
of main, before anything in the patch is applied or run:

    python -I util/check_lemonade_bump.py --repo . --patch bump.patch --target 2026.43.1

The patch may only modify these files, and only like this:

- ``src/gaia/version.py``: ``LEMONADE_VERSION``, set to ``--target``.
- ``src/gaia/llm/lemonade_embedded.py``: ``EMBEDDABLE_SHA256``, pinning exactly
  the target's four embeddable archives.
- ``src/gaia/installer/lemonade_installer.py``: ``INSTALLER_SHA256``, pinning
  exactly the target's five installers, and ``LEGACY_MSI_SHA256``, which keeps
  every existing pin and gains exactly the two MSIs of the outgoing version
  (with the digests main pinned for them) and of each release appended to the
  tag list before the target, matched as text.
- ``tests/fixtures/lemonade/release_tags.txt``: newer tags appended in order,
  ending at the target.
- The target itself must be newer than main's ``LEMONADE_VERSION``.
- ``.md``/``.mdx`` files that main's util/check_doc_versions.py scans: only
  the Lemonade pins it finds for main's ``LEMONADE_VERSION``, set to
  ``--target``. Its own patterns decide, so version floors stay put.

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
import importlib.util
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
# The assets a bump to {v} must pin, no more and no fewer. A drift test holds these
# to the URLs LemonadeInstaller and EmbeddedLemonade actually download.
EXACT_PINS = {
    "INSTALLER_SHA256": (
        "v{v}/lemonade.msi",
        "v{v}/lemonade-server-minimal.msi",
        "v{v}/lemonade-server_{v}-debian13_amd64.deb",
        "v{v}/lemonade-server_{v}-debian13_arm64.deb",
        "v{v}/Lemonade-{v}-Darwin.pkg",
    ),
    "EMBEDDABLE_SHA256": (
        "lemonade-embeddable-{v}-windows-x64.zip",
        "lemonade-embeddable-{v}-ubuntu-x64.tar.gz",
        "lemonade-embeddable-{v}-ubuntu-arm64.tar.gz",
        "lemonade-embeddable-{v}-macos-arm64.tar.gz",
    ),
}
# The outgoing version's installers, which uninstalling it still downloads.
OUTGOING_LEGACY_PINS = ("v{v}/lemonade.msi", "v{v}/lemonade-server-minimal.msi")
VERSION_FILE = "src/gaia/version.py"
RELEASE_TAGS = "tests/fixtures/lemonade/release_tags.txt"
DOC_RULES = "util/check_doc_versions.py"
DOC_PATH = re.compile(r"(?:docs|cpp|tui|\.claude)/(?:[^/]+/)*[^/]+\.mdx?")
SHA256 = re.compile(r"[0-9a-f]{64}")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
ADDED_TAGS = re.compile(r"(?:v[0-9]+\.[0-9]+\.[0-9]+\n)+")
CHANGEABLE_LINE = re.compile(
    r' {4}"[A-Za-z0-9._/-]+": "[0-9a-f]{64}",|LEMONADE_VERSION = "[0-9]+\.[0-9]+\.[0-9]+"'
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


def _load_doc_rules(repo: Path):
    """Main's check_doc_versions.py, the source of which docs and phrasings may change."""
    spec = importlib.util.spec_from_file_location(
        "lemonade_bump_doc_rules", repo.resolve() / DOC_RULES
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _scanned_doc(rules, path: str) -> bool:
    """Whether check_doc_versions scans *path*. Its .go files are left to people."""
    if path.endswith(".go"):
        return False
    for root, pattern in rules.SCAN_PATHS:
        prefix = root.relative_to(rules.PROJECT_ROOT).as_posix() + "/"
        if path.startswith(prefix) and path.endswith(pattern.rsplit("*", 1)[1]):
            return not rules.should_exclude(rules.PROJECT_ROOT / path)
    return False


def check_allowed(repo: Path, paths: list, rules) -> None:
    for path in paths:
        segments = path.split("/")
        clean = "\\" not in path and not {"", ".", ".."} & set(segments)
        allowed = clean and (
            path in PIN_ASSIGNMENTS
            or path == RELEASE_TAGS
            or (DOC_PATH.fullmatch(path) and _scanned_doc(rules, path))
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


def check_python(
    path: str, old: bytes, new: bytes, current: str, target: str, skipped: list
) -> None:
    """*skipped*: the releases appended to the tag list before the target."""
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
        if name in EXACT_PINS:
            expected = {key.format(v=target) for key in EXACT_PINS[name]}
            if set(pins) != expected:
                raise BumpRejected(
                    f"{path}: {name} must pin exactly {sorted(expected)}, "
                    f"not {sorted(pins)}"
                )
        if name in APPEND_ONLY_PINS:
            kept = _literal_pins(old_values[name], path, name)
            if list(pins.items())[: len(kept)] != list(kept.items()):
                raise BumpRejected(f"{path}: {name} may only gain entries at its end")
            # Exactly the MSIs of the outgoing release and of each skipped one, as text.
            gained = set(list(pins)[len(kept) :])
            expected = {
                key.format(v=version)
                for version in [current, *skipped]
                for key in OUTGOING_LEGACY_PINS
            }
            if gained != expected:
                raise BumpRejected(
                    f"{path}: {name} must gain exactly {sorted(expected)}, "
                    f"not {sorted(gained)}"
                )
            # Moved, not re-pinned: the digests main already verified for the outgoing MSIs.
            was_installed = _literal_pins(
                old_values["INSTALLER_SHA256"], path, "INSTALLER_SHA256"
            )
            for key in sorted(k.format(v=current) for k in OUTGOING_LEGACY_PINS):
                if pins[key] != was_installed.get(key):  # present: checked above
                    raise BumpRejected(
                        f"{path}: {name} must keep the outgoing {key} with the digest "
                        "INSTALLER_SHA256 pinned on main"
                    )


def check_release_tags(old: str, new: str, current: str, target: str) -> None:
    if not (old.endswith("\n") and new.startswith(old)):
        raise BumpRejected(f"{RELEASE_TAGS} may only gain tags at its end")
    added = new[len(old) :]
    if not ADDED_TAGS.fullmatch(added):
        raise BumpRejected(f"{RELEASE_TAGS} must gain one or more vX.Y.Z tags")
    versions = [_as_tuple(tag[1:]) for tag in added.split()]
    ordered = all(a < b for a, b in zip([_as_tuple(current)] + versions, versions))
    if not (ordered and versions[-1] == _as_tuple(target)):
        raise BumpRejected(
            f"{RELEASE_TAGS} must gain releases newer than {current}, in order, "
            f"ending at {target}"
        )


def _doc_patterns(rules, path: str, current: str) -> list:
    """The patterns check_doc_versions applies to *path*, floors already excluded."""
    patterns = [pattern for pattern, _ in rules.build_lemonade_patterns(current)]
    if path in rules.BARE_VERSION_PIN_FILES:
        patterns.append(rules.build_bare_pin_pattern()[0])
    return [re.compile(pattern) for pattern in patterns]


def _swap_version(line: str, patterns: list, current: str, target: str) -> str:
    """*line* with each pin check_doc_versions finds for *current* set to *target*."""
    spans = {
        match.span("version")
        for pattern in patterns
        for match in pattern.finditer(line)
        if match.group("version") == current
    }
    for start, end in sorted(spans, reverse=True):
        line = line[:start] + target + line[end:]
    return line


def check_doc(path: str, old: str, new: str, current: str, target: str, rules) -> None:
    patterns = _doc_patterns(rules, path, current)
    # Split on "\n" only: other line separators must not change unnoticed.
    old_lines, new_lines = old.split("\n"), new.split("\n")
    same = len(old_lines) == len(new_lines) and all(
        a == b or _swap_version(a, patterns, current, target) == b
        for a, b in zip(old_lines, new_lines)
    )
    if not same:
        raise BumpRejected(
            f"{path} changes more than Lemonade's version ({current} -> {target})"
        )


def _as_tuple(version: str) -> tuple:
    """*version* as numbers; it must already be in canonical X.Y.Z form."""
    if not VERSION.fullmatch(version):
        raise BumpRejected(f"{version!r} is not a version")
    try:
        parts = tuple(int(part) for part in version.split("."))
    except ValueError as e:  # Past Python's int-from-string digit limit.
        raise BumpRejected(f"{version[:40]!r}... is not a version") from e
    if ".".join(str(part) for part in parts) != version:
        raise BumpRejected(f"{version!r} is not a version: no leading zeros")
    return parts


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
    if _as_tuple(target) <= _as_tuple(current):
        raise BumpRejected(f"--target {target} must be newer than main's {current}")
    rules = _load_doc_rules(repo)
    paths = changed_paths(repo, patch)
    check_allowed(repo, paths, rules)
    # A bump moves every pin, so a file it leaves alone keeps the old version's pins.
    unchanged = sorted((set(PIN_ASSIGNMENTS) | {RELEASE_TAGS}) - set(paths))
    if unchanged:
        raise BumpRejected(f"a bump to {target} must also change {unchanged}")
    _git(repo, "apply", "--check", "--index", str(patch))
    _git(repo, "apply", "--index", str(patch))
    staged = _git(repo, "diff", "--cached", "--name-only", "-z").split("\0")
    if set(filter(None, staged)) != set(paths):
        raise BumpRejected(
            f"the applied patch touched {sorted(set(staged) ^ set(paths))}"
        )
    # Tags first: the legacy pins may gain the MSIs of exactly the releases appended.
    old_tags = _text(_git_bytes(repo, "show", f"HEAD:{RELEASE_TAGS}"), RELEASE_TAGS)
    new_tags = _text(_git_bytes(repo, "show", f":{RELEASE_TAGS}"), RELEASE_TAGS)
    check_release_tags(old_tags, new_tags, current, target)
    skipped = [tag[1:] for tag in new_tags[len(old_tags) :].split()[:-1]]
    for path in paths:
        old = _git_bytes(repo, "show", f"HEAD:{path}")
        new = _git_bytes(repo, "show", f":{path}")
        if path in PIN_ASSIGNMENTS:
            check_python(path, old, new, current, target, skipped)
        elif path == RELEASE_TAGS:
            continue
        else:
            check_doc(path, _text(old, path), _text(new, path), current, target, rules)
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
