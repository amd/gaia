#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Check or stamp every version field GAIA keeps in step with a source of truth.

Three sources of truth, each edited by hand and nowhere else:

    core      src/gaia/version.py  (__version__, LEMONADE_VERSION)
    flagship  hub/agents/gaia/python/gaia_agent/__init__.py  (__version__)
    chat      hub/agents/chat/python/pyproject.toml  ([project] version)

Every other field in ``TARGETS`` follows one of them. The flagship and chat
agents keep their own versions until they join core's (#5172).

Usage::

    python util/release_versions.py --check             # fail on any drift (lint)
    python util/release_versions.py --stamp             # write every field from its source
    python util/release_versions.py --stamp v0.26.0-rc1 # write an RC's versions (build time)

``--stamp <rc tag>`` writes the same files, with the same bytes, as
``util/release_tag.py stamp``.
"""

from __future__ import annotations

import argparse
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
FLAGSHIP_INIT = "hub/agents/gaia/python/gaia_agent/__init__.py"
CHAT_PYPROJECT = "hub/agents/chat/python/pyproject.toml"

# The flagship 0.3.1 binary is frozen from hub/agents/{gaia,chat}/python, and its
# publish reads the npm lock, so these stay as shipped until v0.25.1 final (#5172).
_HELD = "left as shipped until v0.25.1 final and flagship 0.3.1 publish (#5172)"

_CORE_RE = re.compile(r"^(?P<base>\d+\.\d+\.\d+)(?:rc(?P<rc>[1-9]\d*))?$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")

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
    # core | core-floor | lemonade | flagship | chat
    source: str
    count: int = 1
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
    Target("hub/components/agent-ui/gaia-agent.yaml", "version", _YAML_VERSION, "core"),
    Target(
        "hub/components/terminal-hub/gaia-agent.yaml", "version", _YAML_VERSION, "core"
    ),
    Target("docs/docs.json", "navbar GAIA version", _NAV_GAIA, "core"),
    Target("docs/docs.json", "navbar Lemonade version", _NAV_LEMONADE, "lemonade"),
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
    # Flagship agent: follows gaia_agent/__init__.py.
    Target(
        "hub/agents/gaia/python/pyproject.toml", "version", _TOML_VERSION, "flagship"
    ),
    Target(
        "hub/agents/gaia/python/gaia-agent.yaml", "version", _YAML_VERSION, "flagship"
    ),
    Target(
        "hub/agents/gaia/npm/package.json", "version", _JSON_TOP_VERSION, "flagship"
    ),
    Target(
        "hub/agents/gaia/npm/package-lock.json",
        "root version",
        _lock_root("@amd-gaia/gaia"),
        "flagship",
        count=2,
        held=_HELD,
    ),
    Target(
        "hub/agents/gaia/npm/binaries.lock.json",
        "agentVersion",
        re.compile(r'("agentVersion":\s*")([^"]+)(")'),
        "flagship",
    ),
    Target(
        "hub/agents/gaia/npm/binaries.lock.json",
        "sidecar componentVersion",
        re.compile(r'("sidecar"\s*:\s*\{\s*"componentVersion":\s*")([^"]+)(")'),
        "flagship",
    ),
    Target(
        "hub/agents/gaia/npm/binaries.lock.json",
        "sidecar baseUrl",
        re.compile(r'("baseUrl":\s*"https?://[^"]*?/agents/gaia/)([^"/]+)(/?")'),
        "flagship",
    ),
    # Chat agent: follows its pyproject.toml.
    Target("hub/agents/chat/python/gaia-agent.yaml", "version", _YAML_VERSION, "chat"),
    Target(
        "hub/agents/chat/python/gaia_agent_chat/__init__.py",
        "__version__",
        _PY_VERSION,
        "chat",
        held=_HELD,
    ),
)

SOURCE_FILES = {
    "core": CORE_PY,
    "core-floor": CORE_PY,
    "lemonade": CORE_PY,
    "flagship": FLAGSHIP_INIT,
    "chat": CHAT_PYPROJECT,
}


class VersionError(Exception):
    """A source or target can't be read: missing, unparseable, or ambiguous."""


@dataclass(frozen=True)
class Sources:
    core_base: str
    core_rc: int | None
    lemonade: str
    flagship: str
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
            "flagship": self.flagship,
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


def read_sources(root: Path) -> Sources:
    core = _single(root, CORE_PY, _PY_VERSION, "__version__")
    m = _CORE_RE.match(core)
    if not m:
        raise VersionError(
            f"{CORE_PY}: __version__ '{core}' is not X.Y.Z or X.Y.ZrcN (N >= 1)"
        )
    lemonade = _single(root, CORE_PY, _LEMONADE, "LEMONADE_VERSION")
    flagship = _single(root, FLAGSHIP_INIT, _PY_VERSION, "__version__")
    chat = _single(root, CHAT_PYPROJECT, _TOML_VERSION, "[project] version")
    for rel, value in ((FLAGSHIP_INIT, flagship), (CHAT_PYPROJECT, chat)):
        if not _SEMVER_RE.match(value):
            raise VersionError(f"{rel}: version '{value}' is not X.Y.Z")
    return Sources(
        core_base=m.group("base"),
        core_rc=int(m.group("rc")) if m.group("rc") else None,
        lemonade=lemonade,
        flagship=flagship,
        chat=chat,
    )


def _label(t: Target) -> str:
    return f"{t.path} ({t.what})"


def _values(text: str, t: Target) -> list[str]:
    values = [m.group(2) for m in t.pattern.finditer(text)]
    if len(values) != t.count:
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
        "versions into version.py and the Agent UI package files",
    )
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.stamp:
        try:
            version = stamp_rc(args.root, args.stamp)
        except VersionError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"Stamped {args.stamp}: {version}")
        return 0

    stamp = args.stamp is not None
    report = run(args.root, stamp=stamp)
    return print_report(report, stamp=stamp)


if __name__ == "__main__":
    sys.exit(main())
