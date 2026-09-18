#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Single-source version stamping for the GAIA flagship agent package.

The package version lives in files of several types (Python, YAML, TOML, JSON,
Markdown) with no sync tool, so references drift -- a lock ``baseUrl`` can
statically point at a stale deployment long after the package moved on. This
script makes ``__version__`` in ``gaia_agent/__init__.py`` the ONE source of
truth and stamps every other file from it.

Unlike the email agent's equivalent, the CORE targets are REQUIRED: an absent
file, or a file whose version field cannot be found, is a hard FAILURE rather
than a warning. A publish that silently skipped ``binaries.lock.json`` because
the field moved would ship a lock pointing at the previous release's directory.
Only genuinely optional targets (README badges, pinned doc links) skip with a
warning.

Usage::

  python hub/agents/gaia/python/packaging/stamp_version.py
      # stamp every target to match __version__

  python hub/agents/gaia/python/packaging/stamp_version.py --check
      # verify only; print each mismatch and exit non-zero (the CI / publish gate)

``API_VERSION`` in ``gaia_agent/server.py`` is intentionally NOT touched --
it is the wire-contract version, independent of the package build version.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# packaging/ -> python/ : the package root holds the Python-side targets; the
# npm-side targets are reached relative to the repo root.
GAIA_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = GAIA_ROOT.parents[3]  # hub/agents/gaia/python -> repo root
NPM_ROOT = REPO_ROOT / "hub" / "agents" / "gaia" / "npm"

INIT_PY = GAIA_ROOT / "gaia_agent" / "__init__.py"

_VERSION_RE = re.compile(r'(?m)^__version__\s*=\s*"([^"]+)"')
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+")


@dataclass
class Rule:
    """One (file, regex) version reference to stamp/verify.

    Each pattern must capture exactly three groups: (prefix, version, suffix).
    The version is group 2; prefix/suffix are written back verbatim so unrelated
    formatting never churns.
    """

    label: str
    path: Path
    pattern: re.Pattern
    field: str
    required: bool


@dataclass
class Result:
    stamped: list[str] = field(default_factory=list)
    already_ok: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def read_agent_version() -> str:
    if not INIT_PY.exists():
        sys.exit(f"ERROR: source of truth not found: {INIT_PY}")
    m = _VERSION_RE.search(INIT_PY.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"ERROR: could not parse __version__ from {INIT_PY}")
    version = m.group(1)
    if not _SEMVER_RE.match(version):
        sys.exit(f"ERROR: __version__ '{version}' is not a valid x.y.z version")
    return version


def build_rules() -> list[Rule]:
    return [
        # gaia-agent.yaml: top-level unquoted `version: <v>` (NOT min_gaia_version).
        Rule(
            "gaia-agent.yaml",
            GAIA_ROOT / "gaia-agent.yaml",
            re.compile(r"(?m)^(version:[ \t]*)(\S+)([ \t]*)$"),
            "version",
            required=True,
        ),
        # pyproject.toml: the [project] `version = "<v>"` (only top-level match).
        Rule(
            "pyproject.toml",
            GAIA_ROOT / "pyproject.toml",
            re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")'),
            "version",
            required=True,
        ),
        # npm package.json: the package's own top-level `"version": "<v>"`.
        Rule(
            "npm package.json",
            NPM_ROOT / "package.json",
            re.compile(r'(?m)^(  "version":\s*")([^"]+)(")'),
            "version",
            required=True,
        ),
        Rule(
            "binaries.lock.json (agentVersion)",
            NPM_ROOT / "binaries.lock.json",
            re.compile(r'("agentVersion":\s*")([^"]+)(")'),
            "agentVersion",
            required=True,
        ),
        # baseUrl trailing version segment (.../agents/gaia/<v>).
        # gen_binaries_lock.py derives both from --version.
        Rule(
            "binaries.lock.json (baseUrl)",
            NPM_ROOT / "binaries.lock.json",
            re.compile(r'("baseUrl":\s*"https?://[^"]*?/agents/gaia/)([^"/]+)(/?")'),
            "baseUrl",
            required=True,
        ),
        # The SIDECAR lane's componentVersion — anchored on "sidecar" so the tui
        # lane, which tracks terminal-hub and not this package, is never touched.
        Rule(
            "binaries.lock.json (sidecar componentVersion)",
            NPM_ROOT / "binaries.lock.json",
            re.compile(r'("sidecar"\s*:\s*\{\s*"componentVersion":\s*")([^"]+)(")'),
            "componentVersion",
            required=True,
        ),
        # Optional: shields.io static version badges in the shipped READMEs.
        *(
            Rule(
                f"{where} README version badge",
                root / "README.md",
                re.compile(r"(img\.shields\.io/badge/version-)([^-]+)(-)"),
                "version badge",
                required=False,
            )
            for where, root in (("npm", NPM_ROOT), ("python", GAIA_ROOT))
        ),
        # Optional: version-pinned doc links to the release tag's rendered copy.
        *(
            Rule(
                f"npm {name} pinned doc links",
                NPM_ROOT / name,
                re.compile(r"(/amd/gaia/blob/agent-pkg-gaia-v)([^\"/\s)]+)(/)"),
                "pinned doc link version",
                required=False,
            )
            for name in ("README.md", "CHANGELOG.md", "SPEC.md", "SKILL.md")
        ),
    ]


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def process(version: str, check_only: bool) -> Result:
    result = Result()
    for rule in build_rules():
        if not rule.path.exists():
            msg = f"{rule.label}: file absent ({_rel(rule.path)})"
            (result.errors if rule.required else result.skipped).append(msg)
            continue
        text = rule.path.read_text(encoding="utf-8")
        matches = list(rule.pattern.finditer(text))
        if not matches:
            msg = f"{rule.label}: {rule.field} not found in {_rel(rule.path)}"
            (result.errors if rule.required else result.skipped).append(msg)
            continue

        current_values = {m.group(2) for m in matches}
        if check_only:
            bad = sorted(v for v in current_values if v != version)
            if bad:
                result.mismatches.append(
                    f"{rule.label}: {rule.field} = {', '.join(bad)} "
                    f"-- expected {version} ({_rel(rule.path)})"
                )
            else:
                result.already_ok.append(rule.label)
            continue

        if current_values == {version}:
            result.already_ok.append(rule.label)
            continue
        new_text = rule.pattern.sub(
            lambda m: f"{m.group(1)}{version}{m.group(3)}", text
        )
        rule.path.write_text(new_text, encoding="utf-8")
        result.stamped.append(
            f"{rule.label}: {', '.join(sorted(current_values))} -> {version}"
        )
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Stamp/verify the gaia agent package version from __init__.py."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every target matches __version__; exit non-zero on any "
        "mismatch or missing required target (CI / publish gate). No writes.",
    )
    args = parser.parse_args(argv)

    version = read_agent_version()
    print(f"__version__ (source of truth): {version}\n")

    result = process(version, check_only=args.check)

    for line in result.skipped:
        print(f"  SKIP  {line}")
    for label in result.already_ok:
        print(f"  OK    {label}" + ("" if args.check else f" (already {version})"))
    for line in result.stamped:
        print(f"  STAMP {line}")

    failed = result.errors + result.mismatches
    if failed:
        print()
        for line in result.errors:
            print(f"  FAIL  {line}  [REQUIRED TARGET]")
        for line in result.mismatches:
            print(f"  FAIL  {line}")
        if result.errors:
            print(
                "\nA required version target is missing. Every core target "
                "(gaia-agent.yaml, pyproject.toml, npm/package.json, "
                "npm/binaries.lock.json) must exist and carry a stampable version "
                "field before this package can be published."
            )
        if result.mismatches:
            print(
                "\nVersion drift detected. Run "
                "`python hub/agents/gaia/python/packaging/stamp_version.py` "
                "to sync every target to __version__."
            )
        return 1

    if args.check:
        print(f"\nAll targets match __version__ ({version}).")
    else:
        print(f"\nDone. {len(result.stamped)} file(s) stamped to v{version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
