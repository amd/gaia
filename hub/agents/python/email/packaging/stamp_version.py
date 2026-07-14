#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Single-source version stamping for the @amd-gaia/agent-email package.

The package version lives in many files of different types (Python, YAML, TOML,
JSON, Markdown, HTML) with no sync tool, so references drift — a README image URL
or a lock ``baseUrl`` can statically point at a stale deployment long after the
package itself moved on. This script makes ``AGENT_VERSION`` in
``gaia_agent_email/version.py`` the ONE source of truth and stamps every other
file from it (mirrors ``installer/version/bump-ui-version.mjs`` for the Agent UI).

Usage:
  python hub/agents/python/email/packaging/stamp_version.py
      # read AGENT_VERSION from version.py and stamp every present target to match

  python hub/agents/python/email/packaging/stamp_version.py --check
      # verify every present target matches AGENT_VERSION; print each mismatch and
      # exit non-zero (the CI / publish-time gate). Mirrors bump-ui-version.mjs --check.

Targets that are absent (file missing, or the version field/URL not found) are
SKIPPED WITH A WARNING, never failed — some targets (npm README image URL,
assets/architecture.html) live on other in-flight branches and aren't on main
yet, so the script must work across that partial state and stamp them correctly
once those branches merge.

``API_VERSION`` (the REST/contract version, == contract SCHEMA_VERSION) in
version.py is intentionally NOT touched — it is the contract version, independent
of the package build version.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# packaging/ -> email/ : the email package root holds every Python-side target;
# the npm-side targets are reached relative to the repo root (four levels up).
EMAIL_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = (
    EMAIL_ROOT.parent.parent.parent.parent
)  # hub/agents/python/email -> repo root
NPM_ROOT = REPO_ROOT / "hub" / "agents" / "npm" / "agent-email"

VERSION_PY = EMAIL_ROOT / "gaia_agent_email" / "version.py"

_AGENT_VERSION_RE = re.compile(r'AGENT_VERSION\s*=\s*"([^"]+)"')
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
    # Human-readable name of the field this rule targets (for warnings).
    field: str


@dataclass
class Result:
    stamped: list[str] = field(default_factory=list)
    already_ok: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    mismatches: list[str] = field(default_factory=list)


def read_agent_version() -> str:
    if not VERSION_PY.exists():
        sys.exit(f"ERROR: source of truth not found: {VERSION_PY}")
    m = _AGENT_VERSION_RE.search(VERSION_PY.read_text(encoding="utf-8"))
    if not m:
        sys.exit(f"ERROR: could not parse AGENT_VERSION from {VERSION_PY}")
    version = m.group(1)
    if not _SEMVER_RE.match(version):
        sys.exit(f"ERROR: AGENT_VERSION '{version}' is not a valid x.y.z version")
    return version


def build_rules() -> list[Rule]:
    """Every version reference downstream of AGENT_VERSION.

    Patterns intentionally match only the version token (group 2), leaving every
    surrounding byte untouched so diffs stay minimal and JSON/TOML formatting is
    never reserialized.
    """
    return [
        # gaia-agent.yaml: top-level unquoted `version: <v>` (NOT min_gaia_version).
        Rule(
            "gaia-agent.yaml",
            EMAIL_ROOT / "gaia-agent.yaml",
            re.compile(r"(?m)^(version:[ \t]*)(\S+)([ \t]*)$"),
            "version",
        ),
        # pyproject.toml: the [project] `version = "<v>"` (only top-level match).
        Rule(
            "pyproject.toml",
            EMAIL_ROOT / "pyproject.toml",
            re.compile(r'(?m)^(version\s*=\s*")([^"]+)(")'),
            "version",
        ),
        # npm package.json: the package's own top-level `"version": "<v>"`.
        Rule(
            "npm package.json",
            NPM_ROOT / "package.json",
            re.compile(r'(?m)^(  "version":\s*")([^"]+)(")'),
            "version",
        ),
        # binaries.lock.json: agentVersion field.
        Rule(
            "binaries.lock.json (agentVersion)",
            NPM_ROOT / "binaries.lock.json",
            re.compile(r'("agentVersion":\s*")([^"]+)(")'),
            "agentVersion",
        ),
        # binaries.lock.json: baseUrl trailing version segment
        # (.../agents/email/<v>). gen_binaries_lock.py derives both from --version.
        Rule(
            "binaries.lock.json (baseUrl)",
            NPM_ROOT / "binaries.lock.json",
            re.compile(r'("baseUrl":\s*"https?://[^"]*?/agents/email/)([^"/]+)(/?")'),
            "baseUrl",
        ),
        # npm assets/architecture.html: <span ... id="ver">v<v></span> badge.
        Rule(
            "npm architecture.html badge",
            NPM_ROOT / "assets" / "architecture.html",
            re.compile(r'(<span[^>]*id="ver"[^>]*>v)([^<]+)(</span>)'),
            'id="ver" badge',
        ),
        # Versioned hub doc links (https://hub.amd-gaia.ai/agents/email/<v>/<file>)
        # in the npm markdown docs — they pin cross-references to the R2 copy of
        # the shipping version instead of the mutable GitHub main blob.
        *(
            Rule(
                f"npm {name} hub doc links",
                NPM_ROOT / name,
                re.compile(r"(https://hub\.amd-gaia\.ai/agents/email/)([^\"/\s)]+)(/)"),
                "hub doc link version",
            )
            for name in ("README.md", "CHANGELOG.md", "EVALUATION.md")
        ),
    ]


def process(version: str, check_only: bool) -> Result:
    result = Result()
    for rule in build_rules():
        if not rule.path.exists():
            result.skipped.append(f"{rule.label}: file absent ({_rel(rule.path)})")
            continue
        text = rule.path.read_text(encoding="utf-8")
        matches = list(rule.pattern.finditer(text))
        if not matches:
            result.skipped.append(
                f"{rule.label}: {rule.field} not found in {_rel(rule.path)}"
            )
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

        # Stamp mode: rewrite every match's version token to `version`.
        if current_values == {version}:
            result.already_ok.append(rule.label)
            continue
        new_text = rule.pattern.sub(
            lambda m: f"{m.group(1)}{version}{m.group(3)}", text
        )
        rule.path.write_text(new_text, encoding="utf-8")
        old = ", ".join(sorted(current_values))
        result.stamped.append(f"{rule.label}: {old} -> {version}")
    return result


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Stamp/verify the agent-email package version from version.py."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every present target matches AGENT_VERSION; exit non-zero on "
        "any mismatch (CI / publish gate). Does not modify files.",
    )
    args = parser.parse_args(argv)

    version = read_agent_version()
    print(f"AGENT_VERSION (source of truth): {version}\n")

    result = process(version, check_only=args.check)

    for line in result.skipped:
        print(f"  SKIP  {line}")

    if args.check:
        for label in result.already_ok:
            print(f"  OK    {label}")
        if result.mismatches:
            print()
            for line in result.mismatches:
                print(f"  FAIL  {line}")
            print(
                "\nVersion drift detected. Run "
                "`python hub/agents/python/email/packaging/stamp_version.py` "
                "to sync every target to AGENT_VERSION."
            )
            return 1
        print(f"\nAll present targets match AGENT_VERSION ({version}).")
        return 0

    for line in result.stamped:
        print(f"  STAMP {line}")
    for label in result.already_ok:
        print(f"  OK    {label} (already {version})")
    print(f"\nDone. {len(result.stamped)} file(s) stamped to v{version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
