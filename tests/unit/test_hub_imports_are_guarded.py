# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests that import a hub agent package must guard the import.

The root conftest only points a hub package at this checkout when it is already
installed. Dev machines editable-install them; the CI unit job does not. An
unguarded import passes locally and fails every platform in CI.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
UNIT = REPO_ROOT / "tests" / "unit"
HUB_PACKAGES = sorted(
    {p.parent.name for p in REPO_ROOT.glob("hub/agents/*/python/*/__init__.py")}
)


def _imported_hub_packages(source: str) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".")[0]
            if top in HUB_PACKAGES:
                found.add(top)
    return found


def _guarded(package: str, test_file: Path) -> bool:
    # importorskip, a skipif on find_spec, or an ImportError handler all count.
    name = re.escape(package)
    pattern = re.compile(
        rf"importorskip\(\s*['\"]{name}['\".]"
        rf"|find_spec\(\s*['\"]{name}['\"]"
        r"|except\s*\(?\s*(ImportError|ModuleNotFoundError)"
    )
    candidates = [test_file] + [
        d / "conftest.py" for d in test_file.parents if d.is_relative_to(UNIT)
    ]
    return any(
        c.is_file() and pattern.search(c.read_text(encoding="utf-8"))
        for c in candidates
    )


def test_hub_package_imports_in_unit_tests_are_guarded():
    unguarded = []
    for path in sorted(UNIT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for package in sorted(_imported_hub_packages(source)):
            if not _guarded(package, path):
                unguarded.append(f"{path.relative_to(REPO_ROOT)}: {package}")
    assert not unguarded, (
        "These unit tests import a hub agent package unguarded (no importorskip, "
        "find_spec skipif or ImportError handler), so they fail in CI where it "
        "isn't installed:\n  " + "\n  ".join(unguarded)
    )


def test_the_guard_catches_an_unguarded_import():
    assert _imported_hub_packages(
        "def t():\n    from gaia_agent.server import x\n"
    ) == {"gaia_agent"}
    assert _imported_hub_packages("from . import gaia_agent\n") == set()
