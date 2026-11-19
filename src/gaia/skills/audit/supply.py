# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Supply-chain checks over a skill's declared dependencies.

Installing a skill can pull third-party packages into the agent's process, so the
dependency list is part of the skill's attack surface. Three things matter:

- **Undeclared imports.** Code importing a package the manifest never lists means
  the install either fails or silently relies on whatever the host happens to
  have — and a reviewer reading the manifest never sees the real dependency set.
- **Dependencies from outside a package index.** ``git+https://…``, a URL, or a
  local path fetches code that no index, signature, or version pin covers.
- **Unpinned versions.** An unpinned dependency means the code audited today is
  not the code that runs tomorrow, which is the mechanism behind most real
  supply-chain attacks.
"""

from __future__ import annotations

import re
import sys
from typing import Iterable, Sequence

from gaia.skills.audit.code import CodeAnalysis
from gaia.skills.audit.findings import CATEGORY_SUPPLY_CHAIN, Finding

#: Import name -> distribution name, for the cases where they differ.
IMPORT_TO_DISTRIBUTION: dict[str, str] = {
    "attr": "attrs",
    "bs4": "beautifulsoup4",
    "cv2": "opencv-python",
    "crypto": "pycryptodome",
    "dateutil": "python-dateutil",
    "dotenv": "python-dotenv",
    "fitz": "pymupdf",
    "git": "gitpython",
    "google": "google-api-python-client",
    "jwt": "pyjwt",
    "magic": "python-magic",
    "openssl": "pyopenssl",
    "pil": "pillow",
    "pkg_resources": "setuptools",
    "serial": "pyserial",
    "sklearn": "scikit-learn",
    "usb": "pyusb",
    "win32api": "pywin32",
    "win32com": "pywin32",
    "yaml": "pyyaml",
    "zmq": "pyzmq",
}

#: Packages always present in a GAIA host process — never a skill's dependency.
HOST_PROVIDED = frozenset({"gaia"})

#: A dependency string fetching code from outside a package index.
_REMOTE_SPEC_RE = re.compile(
    r"^\s*(?:-e\s+)?(?:git\+|hg\+|svn\+|bzr\+|https?://|file:|ssh://|\.{1,2}/|/)",
    re.IGNORECASE,
)

#: Extract the distribution name from a PEP 508 requirement string.
_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")

#: Any version constraint at all.
_VERSION_RE = re.compile(r"(==|>=|<=|~=|!=|>|<|@)")

_STDLIB = set(sys.stdlib_module_names)


def normalize(name: str) -> str:
    """Normalize a distribution name per PEP 503 (case + separator folding)."""
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def requirement_name(spec: str) -> str:
    """The distribution name from a requirement string, or ``''`` if unparseable."""
    match = _NAME_RE.match(spec)
    return normalize(match.group(1)) if match else ""


def distribution_for_import(module: str) -> str:
    """The distribution that provides ``module``, normalized."""
    lowered = module.lower()
    return normalize(IMPORT_TO_DISTRIBUTION.get(lowered, module))


def _declared_names(dependencies: Iterable[str]) -> set[str]:
    names: set[str] = set()
    for spec in dependencies:
        name = requirement_name(spec)
        if name:
            names.add(name)
        # A remote spec often carries the real name in an '#egg=' fragment.
        egg = re.search(r"[#&]egg=([A-Za-z0-9._-]+)", spec)
        if egg:
            names.add(normalize(egg.group(1)))
    return names


def check_supply_chain(
    dependencies: Sequence[str],
    node_dependencies: Sequence[str],
    analysis: CodeAnalysis,
    *,
    local_modules: Sequence[str] = (),
) -> tuple[Finding, ...]:
    """Check declared dependencies against the code's imports.

    Args:
        dependencies: ``metadata.gaia.requirements.dependencies``.
        node_dependencies: ``metadata.gaia.requirements.node_dependencies``.
        analysis: The code analysis, for its import list.
        local_modules: Module names provided by files inside the skill itself,
            which are never third-party dependencies.
    """
    findings: list[Finding] = []
    declared = _declared_names(dependencies)
    local = {normalize(m) for m in local_modules}

    findings.extend(_spec_findings(dependencies, "dependencies"))
    findings.extend(_spec_findings(node_dependencies, "node_dependencies"))

    seen: set[str] = set()
    for reference in analysis.imports:
        module = reference.module
        if not module or module in _STDLIB or module.lower() in HOST_PROVIDED:
            continue
        distribution = distribution_for_import(module)
        if distribution in local or normalize(module) in local:
            continue
        if distribution in declared or normalize(module) in declared:
            continue
        if distribution in seen:
            continue
        seen.add(distribution)

        findings.append(
            Finding(
                rule_id="supply.undeclared_dependency",
                severity="medium",
                category=CATEGORY_SUPPLY_CHAIN,
                message=(
                    f"Imports '{module}' but metadata.gaia.requirements."
                    f"dependencies does not declare '{distribution}'."
                ),
                file=reference.file,
                line=reference.line,
                remediation=(
                    f"Add '{distribution}' (pinned, e.g. '{distribution}==X.Y.Z') "
                    "to metadata.gaia.requirements.dependencies. An undeclared "
                    "import either fails on a clean install or silently depends "
                    "on whatever the host already has."
                ),
            )
        )

    return tuple(findings)


def _spec_findings(specs: Sequence[str], field: str) -> list[Finding]:
    """Flag remote/unpinned dependency specs."""
    findings: list[Finding] = []
    for spec in specs:
        text = spec.strip()
        if not text:
            continue

        if _REMOTE_SPEC_RE.match(text):
            findings.append(
                Finding(
                    rule_id="supply.remote_dependency",
                    severity="high",
                    category=CATEGORY_SUPPLY_CHAIN,
                    message=(
                        f"{field} entry {text!r} installs code from outside a "
                        "package index."
                    ),
                    file="SKILL.md",
                    line=0,
                    remediation=(
                        "Depend on a published, pinned release instead. A VCS, "
                        "URL, or path dependency can change under you with no "
                        "version bump, and nothing about it was audited here."
                    ),
                    snippet=text,
                )
            )
            continue

        if not _VERSION_RE.search(text):
            findings.append(
                Finding(
                    rule_id="supply.unpinned_dependency",
                    severity="low",
                    category=CATEGORY_SUPPLY_CHAIN,
                    message=f"{field} entry {text!r} has no version constraint.",
                    file="SKILL.md",
                    line=0,
                    remediation=(
                        f"Pin it, e.g. '{text}==X.Y.Z' or '{text}>=X.Y,<X+1'. "
                        "Without a pin, the code this audit inspected is not "
                        "necessarily the code that will run."
                    ),
                    snippet=text,
                )
            )
    return findings
