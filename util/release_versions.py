#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check or stamp every version field GAIA keeps in step with a source of truth.

Three sources of truth:

    core      src/gaia/version.py  (__version__, LEMONADE_VERSION), edited by hand
    released  the newest ``releases/vX.Y.Z`` page in docs/docs.json's Release
              Notes group: the latest release the docs describe
    chat      hub/agents/chat/python/pyproject.toml  ([project] version)

Every other field in ``TARGETS`` follows one of them. The flagship agent ships
at core's version (#5172). The chat agent keeps its own until Phase 8 of the
release train removes it.

The docs.json navbar follows ``released``, not core, so ``main`` can declare the
next version while the site still names the release users can install.

Usage::

    python util/release_versions.py --check             # fail on any drift (lint)
    python util/release_versions.py --stamp             # write every field from its source
    python util/release_versions.py --stamp v0.26.0-rc1 # write an RC's versions (build time)
    python util/release_versions.py --check --only flagship  # one group's fields
    python util/release_versions.py --released          # print the newest listed release

``--stamp <rc tag>`` writes the files ``util/release_tag.py stamp`` writes with
the same bytes, and the flagship's Python and npm versions too.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_UTIL = str(Path(__file__).resolve().parent)
if _UTIL not in sys.path:
    sys.path.insert(0, _UTIL)

from release_tag import parse_tag  # noqa: E402  pylint: disable=wrong-import-position

FIX_CMD = "python util/release_versions.py --stamp"

CORE_PY = "src/gaia/version.py"
DOCS_JSON = "docs/docs.json"
FLAGSHIP_INIT = "hub/agents/gaia/python/gaia_agent/__init__.py"
CHAT_PYPROJECT = "hub/agents/chat/python/pyproject.toml"

_CHAT_HELD = "the chat agent is removed in Phase 8 of the release train (#5172)"

# What `--only` selects. The stamp_version.py and bump-ui-version.mjs wrappers
# each check or stamp one group.
GROUPS = {
    "flagship": "hub/agents/gaia/",
    "agent-ui": "src/gaia/apps/webui/",
}

_CORE_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?:rc(?P<rc>[1-9]\d*))?$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
RELEASE_PAGE = re.compile(r"^releases/v(\d+(?:\.\d+){2,3})$")

_PY_VERSION = re.compile(r'(?m)^(__version__\s*=\s*")([^"\r\n]*)(")')
_LEMONADE = re.compile(r'(?m)^(LEMONADE_VERSION\s*=\s*")([^"\r\n]*)(")')
_TOML_VERSION = re.compile(r'(?m)^(version\s*=\s*")([^"\r\n]*)(")')
_YAML_VERSION = re.compile(r"(?m)^(version:[ \t]*)([^\s#]+)()")
_YAML_MIN_GAIA = re.compile(r"""(?m)^(min_gaia_version:[ \t]*["']?)([^"'\s#]+)()""")
_GAIA_FLOOR = re.compile(r'("amd-gaia(?:\[[^\]"]*\])?>=)([^",\s]+)(")')
_JSON_TOP_VERSION = re.compile(r'(?m)^(  "version":\s*")([^"]*)(")')
_NAV_GAIA = re.compile(r'("label":\s*"v)([^"\s]+)(\s*(?:\\u00b7|·)\s*Lemonade\s)')
_NAV_LEMONADE = re.compile(
    r'("label":\s*"v[^"\s]+\s*(?:\\u00b7|·)\s*Lemonade\s+)([^"\s]+)(")'
)


def _lock_root(name: str) -> re.Pattern[str]:
    # The lockfile's two root-package versions each directly follow its own name.
    return re.compile(
        r'("name":\s*"' + re.escape(name) + r'",\s*\n\s*"version":\s*")([^"]+)(")'
    )


@dataclass(frozen=True)
class Target:
    """One version field: three regex groups (prefix, version, suffix)."""

    path: str
    what: str
    pattern: re.Pattern[str]
    # core | core-floor | lemonade | released | chat
    source: str
    # None: optional; every match, however many (zero included), must agree.
    count: int | None = 1
    # Spelling an RC tag stamps: "pep440" (PyPI) or "npm"; None = not stamped.
    rc: str | None = None
    # Non-empty: drift is reported but does not fail, and --stamp leaves it.
    held: str = ""


TARGETS: tuple[Target, ...] = (
    # Core: follows src/gaia/version.py.
    Target(CORE_PY, "__version__", _PY_VERSION, "core", rc="pep440"),
    Target(
        "src/gaia/apps/webui/package.json",
        "version",
        _JSON_TOP_VERSION,
        "core",
        rc="npm",
    ),
    Target(
        "src/gaia/apps/webui/package-lock.json",
        "root version",
        _lock_root("@amd-gaia/agent-ui"),
        "core",
        count=2,
        rc="npm",
    ),
    Target(
        "hub/components/terminal-hub/gaia-agent.yaml", "version", _YAML_VERSION, "core"
    ),
    Target(DOCS_JSON, "navbar GAIA version", _NAV_GAIA, "released"),
    Target(DOCS_JSON, "navbar Lemonade version", _NAV_LEMONADE, "lemonade"),
    # Core floors: the first-party agents require the core they ship with.
    Target(
        "hub/agents/gaia/python/pyproject.toml",
        "amd-gaia floor",
        _GAIA_FLOOR,
        "core-floor",
    ),
    Target(
        "hub/agents/gaia/python/gaia-agent.yaml",
        "min_gaia_version",
        _YAML_MIN_GAIA,
        "core-floor",
    ),
    Target(
        "hub/agents/gaia/python/gaia-agent.yaml",
        "amd-gaia floor",
        _GAIA_FLOOR,
        "core-floor",
    ),
    # hub/agents/gaia/python/tests/test_npm_doc_min_version.py pins these to
    # min_gaia_version. Its CHANGELOG line is hand-written per release entry.
    Target(
        "hub/agents/gaia/npm/README.md",
        "minimum core",
        re.compile(r"(Python CLI \*\*)(\d+\.\d+\.\d+)( or newer\*\*)"),
        "core-floor",
    ),
    Target(
        "hub/agents/gaia/npm/SKILL.md",
        "minimum core",
        re.compile(r"(must also be \*\*)(\d+\.\d+\.\d+)(\+\*\*)"),
        "core-floor",
    ),
    Target(CHAT_PYPROJECT, "amd-gaia floor", _GAIA_FLOOR, "core-floor"),
    Target(
        "hub/agents/chat/python/gaia-agent.yaml",
        "min_gaia_version",
        _YAML_MIN_GAIA,
        "core-floor",
    ),
    Target(
        "hub/agents/chat/python/gaia-agent.yaml",
        "amd-gaia floor",
        _GAIA_FLOOR,
        "core-floor",
    ),
    # Flagship agent: ships at core's version. The hub manifest, the lock and the
    # doc links name hub paths, which have no pre-release channel until Phase 4,
    # so an RC stamp leaves them at X.Y.Z.
    Target(FLAGSHIP_INIT, "__version__", _PY_VERSION, "core", rc="pep440"),
    Target(
        "hub/agents/gaia/python/pyproject.toml",
        "version",
        _TOML_VERSION,
        "core",
        rc="pep440",
    ),
    Target("hub/agents/gaia/python/gaia-agent.yaml", "version", _YAML_VERSION, "core"),
    Target(
        "hub/agents/gaia/npm/package.json",
        "version",
        _JSON_TOP_VERSION,
        "core",
        rc="npm",
    ),
    Target(
        "hub/agents/gaia/npm/package-lock.json",
        "root version",
        _lock_root("@amd-gaia/gaia"),
        "core",
        count=2,
        rc="npm",
    ),
    # A bump leaves this lock's hashes stale until the agent release's pin-lock
    # PR replaces them. Phase 7 stops committing the lock.
    Target(
        "hub/agents/gaia/npm/binaries.lock.json",
        "agentVersion",
        re.compile(r'("agentVersion":\s*")([^"]+)(")'),
        "core",
    ),
    Target(
        "hub/agents/gaia/npm/binaries.lock.json",
        "sidecar componentVersion",
        re.compile(r'("sidecar"\s*:\s*\{\s*"componentVersion":\s*")([^"]+)(")'),
        "core",
    ),
    Target(
        "hub/agents/gaia/npm/binaries.lock.json",
        "sidecar baseUrl",
        re.compile(r'("baseUrl":\s*"https?://[^"]*?/agents/gaia/)([^"/]+)(/?")'),
        "core",
    ),
    # Optional doc URLs: a version badge, and links pinned to a release tag's
    # copy of a doc. The CHANGELOG is left out, so past entries keep theirs.
    *(
        Target(f"hub/agents/gaia/npm/{name}", what, pattern, "core", count=None)
        for name in ("README.md", "SPEC.md", "SKILL.md")
        for what, pattern in (
            (
                "version badge",
                re.compile(r"(img\.shields\.io/badge/version-)([^-]+)(-)"),
            ),
            (
                "pinned doc links",
                re.compile(r"(/amd/gaia/blob/(?:agent-pkg-gaia-)?v)([^\"/\s)]+)(/)"),
            ),
        )
    ),
    # Chat agent: follows its pyproject.toml.
    Target("hub/agents/chat/python/gaia-agent.yaml", "version", _YAML_VERSION, "chat"),
    Target(
        "hub/agents/chat/python/gaia_agent_chat/__init__.py",
        "__version__",
        _PY_VERSION,
        "chat",
        held=_CHAT_HELD,
    ),
)

SOURCE_FILES = {
    "core": CORE_PY,
    "core-floor": CORE_PY,
    "lemonade": CORE_PY,
    "released": DOCS_JSON,
    "chat": CHAT_PYPROJECT,
}


class VersionError(Exception):
    """A source or target can't be read: missing, unparseable, or ambiguous."""


def select(group: str) -> tuple[Target, ...]:
    """The targets one of ``GROUPS`` names."""
    if group not in GROUPS:
        raise VersionError(
            f"unknown group '{group}'; expected one of {', '.join(sorted(GROUPS))}"
        )
    return tuple(t for t in TARGETS if t.path.startswith(GROUPS[group]))


@dataclass(frozen=True)
class Sources:
    core_base: str
    core_rc: int | None
    lemonade: str
    released: str
    chat: str

    @property
    def core_pep440(self) -> str:
        return f"{self.core_base}rc{self.core_rc}" if self.core_rc else self.core_base

    @property
    def core_npm(self) -> str:
        return f"{self.core_base}-rc.{self.core_rc}" if self.core_rc else self.core_base

    def expected(self, target: Target) -> str:
        if target.source == "core":
            if target.rc == "pep440":
                return self.core_pep440
            if target.rc == "npm":
                return self.core_npm
            return self.core_base
        if target.source == "core-floor":
            return self.core_base
        return {
            "lemonade": self.lemonade,
            "released": self.released,
            "chat": self.chat,
        }[target.source]


@dataclass
class Report:
    ok: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    held: list[str] = field(default_factory=list)
    stamped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _read(root: Path, rel: str) -> str:
    # newline="" keeps each file's own line endings, so a stamp never flips CRLF.
    try:
        with open(root / rel, encoding="utf-8", newline="") as f:
            return f.read()
    except FileNotFoundError as e:
        raise VersionError(f"{rel}: file not found") from e


def _write(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _single(root: Path, rel: str, pattern: re.Pattern[str], what: str) -> str:
    matches = pattern.findall(_read(root, rel))
    if len(matches) != 1:
        raise VersionError(f"{rel}: expected one {what}, found {len(matches)}")
    return matches[0][1]


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in version.split("."))


def released_version(root: Path) -> str:
    """The newest ``releases/vX.Y.Z`` page docs.json's Release Notes group lists.

    The release PR adds its page there, so this is the release being cut or,
    once it ships, the latest one. A next-version notes stub is not listed.
    """
    try:
        docs = json.loads(_read(root, DOCS_JSON))
    except ValueError as e:
        raise VersionError(f"{DOCS_JSON}: not valid JSON: {e}") from e
    pages = [
        page
        for tab in docs.get("navigation", {}).get("tabs", [])
        if tab.get("tab") == "Releases"
        for group in tab.get("groups", [])
        if group.get("group") == "Release Notes"
        for page in group.get("pages", [])
    ]
    versions = [m.group(1) for m in map(RELEASE_PAGE.match, pages) if m]
    if not versions:
        raise VersionError(
            f"{DOCS_JSON}: the Releases tab's 'Release Notes' group lists no "
            "releases/vX.Y.Z page"
        )
    return max(versions, key=version_key)


def read_sources(root: Path) -> Sources:
    core = _single(root, CORE_PY, _PY_VERSION, "__version__")
    m = _CORE_RE.match(core)
    if not m:
        raise VersionError(
            f"{CORE_PY}: __version__ '{core}' is not X.Y.Z or X.Y.ZrcN (N >= 1)"
        )
    lemonade = _single(root, CORE_PY, _LEMONADE, "LEMONADE_VERSION")
    released = released_version(root)
    if version_key(released) > version_key(m.group("base")):
        raise VersionError(
            f"{DOCS_JSON} lists releases/v{released}, newer than the "
            f"{m.group('base')} {CORE_PY} declares. Set __version__ to the release "
            "being cut, or take that page out of the Releases tab."
        )
    chat = _single(root, CHAT_PYPROJECT, _TOML_VERSION, "[project] version")
    if not _SEMVER_RE.match(chat):
        raise VersionError(f"{CHAT_PYPROJECT}: version '{chat}' is not X.Y.Z")
    return Sources(
        core_base=m.group("base"),
        core_rc=int(m.group("rc")) if m.group("rc") else None,
        lemonade=lemonade,
        released=released,
        chat=chat,
    )


def _label(t: Target) -> str:
    return f"{t.path} ({t.what})"


def _values(text: str, t: Target) -> list[str]:
    values = [m.group(2) for m in t.pattern.finditer(text)]
    if t.count is not None and len(values) != t.count:
        raise VersionError(
            f"{_label(t)}: expected {t.count} match(es), found {len(values)}. "
            "The field moved or the format changed; update TARGETS in "
            "util/release_versions.py."
        )
    return values


def run(root: Path, stamp: bool, targets: tuple[Target, ...] = TARGETS) -> Report:
    """Check (or with ``stamp``, write) every target against its source."""
    report = Report()
    try:
        sources = read_sources(root)
    except VersionError as e:
        report.errors.append(str(e))
        return report

    # Several targets share a file, so edits accumulate here and are written
    # only if every target read cleanly: a stamp never half-applies.
    texts: dict[Path, str] = {}
    changed: set[Path] = set()
    for t in targets:
        want = sources.expected(t)
        path = root / t.path
        try:
            if path not in texts:
                texts[path] = _read(root, t.path)
            values = _values(texts[path], t)
        except VersionError as e:
            report.errors.append(str(e))
            continue
        bad = sorted({v for v in values if v != want})
        if not bad:
            report.ok.append(_label(t))
            continue
        line = (
            f"{_label(t)}: {', '.join(bad)}, expected {want} "
            f"(follows {SOURCE_FILES[t.source]})"
        )
        if t.held:
            report.held.append(f"{line} -- {t.held}")
        elif stamp:
            texts[path] = t.pattern.sub(
                lambda m, w=want: f"{m.group(1)}{w}{m.group(3)}", texts[path]
            )
            changed.add(path)
            report.stamped.append(line)
        else:
            report.drift.append(line)

    if stamp and report.errors:
        report.stamped.clear()
    elif stamp:
        for path in sorted(changed):
            _write(path, texts[path])
    return report


def stamp_rc(root: Path, tag: str, targets: tuple[Target, ...] = TARGETS) -> str:
    """Write an RC tag's versions into every ``rc`` target. Returns the PEP 440 form.

    Refuses a final tag (a final publishes what is committed) and a tree whose
    committed version is not the RC's base.
    """
    try:
        rel = parse_tag(tag)
    except ValueError as e:
        raise VersionError(str(e)) from e
    if not rel.is_rc:
        raise VersionError(
            f"{tag} is a final release; finals publish the committed version and "
            "are never stamped."
        )
    spelled = {"pep440": rel.pep440_version, "npm": rel.npm_version}
    texts: dict[Path, str] = {}
    for t in targets:
        if not t.rc:
            continue
        path = root / t.path
        if path not in texts:
            texts[path] = _read(root, t.path)
        values = _values(texts[path], t)
        want = spelled[t.rc]
        stale = sorted({v for v in values if v not in (rel.base_version, want)})
        if stale:
            raise VersionError(
                f"{_label(t)} says {', '.join(stale)}, but {tag} is a candidate for "
                f"{rel.base_version}. Land the {rel.base_version} release bump "
                f"(`{FIX_CMD}`) before tagging the RC."
            )
        texts[path] = t.pattern.sub(
            lambda m, w=want: f"{m.group(1)}{w}{m.group(3)}", texts[path]
        )
    for path, text in texts.items():
        _write(path, text)
    return rel.pep440_version


def print_report(report: Report, stamp: bool) -> int:
    print(f"  OK     {len(report.ok)} field(s) match their source")
    for line in report.stamped:
        print(f"  STAMP  {line}")
    for line in report.held:
        print(f"  HELD   {line}")
    for line in report.drift:
        print(f"  DRIFT  {line}")
    for line in report.errors:
        print(f"  ERROR  {line}")

    if report.errors:
        print(
            "\nA version source or target could not be read"
            + (", so nothing was written" if stamp else "")
            + ". Restore the file or field named above, or update TARGETS in "
            "util/release_versions.py if it moved on purpose."
        )
    if report.drift:
        print(
            f"\n{len(report.drift)} version field(s) drifted from their source. "
            f"Fix: {FIX_CMD}"
        )
    if report.errors or report.drift:
        return 1
    held = f" ({len(report.held)} held, listed above)" if report.held else ""
    if stamp:
        print(f"\nStamped {len(report.stamped)} field(s){held}.")
    else:
        print(f"\nEvery version field matches its source{held}.")
    return 0


def run_check(root: Path = REPO_ROOT) -> int:
    """Entry point for util/lint.py."""
    report = run(root, stamp=False)
    return print_report(report, stamp=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", maxsplit=1)[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--check", action="store_true", help="fail on any drift; writes nothing"
    )
    mode.add_argument(
        "--stamp",
        nargs="?",
        const="",
        metavar="RC_TAG",
        help="write every field from its source; with vX.Y.Z-rcN, write that RC's "
        "versions into version.py, the Agent UI package files, and the flagship's "
        "Python and npm versions",
    )
    mode.add_argument(
        "--released",
        action="store_true",
        help="print the newest release docs/docs.json lists (what the navbar names)",
    )
    ap.add_argument(
        "--only",
        choices=sorted(GROUPS),
        help="check or stamp only this group's fields",
    )
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.released:
        try:
            print(released_version(args.root))
        except VersionError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        return 0

    targets = select(args.only) if args.only else TARGETS
    if args.stamp:
        try:
            version = stamp_rc(args.root, args.stamp, targets)
        except VersionError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"Stamped {args.stamp}: {version}")
        return 0

    stamp = args.stamp is not None
    report = run(args.root, stamp=stamp, targets=targets)
    return print_report(report, stamp=stamp)


if __name__ == "__main__":
    sys.exit(main())
