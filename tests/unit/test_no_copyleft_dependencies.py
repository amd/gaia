# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA must not ship AGPL/GPL code: PyMuPDF (AGPL-3.0) stays out.

PDF rendering and image extraction moved to pypdfium2 (Apache-2.0 /
BSD-3-Clause). These checks fail if PyMuPDF comes back as an import in shipped
code, a declared requirement, or a CI install.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    tomllib = pytest.importorskip("tomli")

REPO = Path(__file__).resolve().parents[2]

#: Distribution names (PEP 503-normalized) GAIA must never depend on.
FORBIDDEN_DISTRIBUTIONS = {"pymupdf", "pymupdfb", "fitz"}
#: Import names of those distributions.
FORBIDDEN_IMPORTS = {"pymupdf", "fitz"}
_SKIP_DIRS = {"node_modules", ".venv", "venv", "build", "dist", "__pycache__"}


def _shipped_python_files():
    for root in (REPO / "src", REPO / "hub"):
        for path in root.rglob("*.py"):
            if not _SKIP_DIRS.intersection(path.relative_to(REPO).parts):
                yield path


def _forbidden_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module]
        else:
            continue
        found += [n for n in names if n.split(".")[0] in FORBIDDEN_IMPORTS]
    return found


def test_no_shipped_code_imports_pymupdf():
    offenders = []
    for path in _shipped_python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for name in _forbidden_imports(source):
            offenders.append(f"{path.relative_to(REPO)}: import {name}")
    assert not offenders, (
        "PyMuPDF is AGPL-3.0 and must not ship with GAIA; use pypdfium2 "
        "(gaia.rag.pdf_utils / gaia.utils.pdf_page_to_image):\n" + "\n".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "import fitz",
        "import pymupdf",
        "import pymupdf.utils as u",
        "from fitz import open",
        "from pymupdf import Document",
        "def f():\n    import fitz\n",
        "try:\n    import pymupdf\nexcept ImportError:\n    pass\n",
    ],
)
def test_the_import_scan_catches_every_spelling(source):
    assert _forbidden_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "import pypdfium2",
        "s = 'import fitz'",  # a string, e.g. the skill audit's lookup table
        "import fitzgerald",
        "from .fitz import x",  # a relative, local module
    ],
)
def test_the_import_scan_ignores_what_is_not_an_import_of_pymupdf(source):
    assert not _forbidden_imports(source)


def _setup_py_requirements() -> list[Requirement]:
    reqs = []
    for node in ast.walk(ast.parse((REPO / "setup.py").read_text(encoding="utf-8"))):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                reqs.append(Requirement(node.value))
            except InvalidRequirement:
                continue
    return reqs


def _pyproject_requirements() -> list[tuple[Path, Requirement]]:
    found = []
    for path in sorted((REPO / "hub").rglob("pyproject.toml")):
        if _SKIP_DIRS.intersection(path.relative_to(REPO).parts):
            continue
        project = tomllib.loads(path.read_text(encoding="utf-8")).get("project", {})
        specs = list(project.get("dependencies", []))
        for extra in project.get("optional-dependencies", {}).values():
            specs += extra
        found += [(path, Requirement(spec)) for spec in specs]
    return found


def test_setup_py_declares_no_forbidden_requirement():
    reqs = _setup_py_requirements()
    assert any(canonicalize_name(r.name) == "pypdfium2" for r in reqs)
    bad = [str(r) for r in reqs if canonicalize_name(r.name) in FORBIDDEN_DISTRIBUTIONS]
    assert not bad, f"setup.py declares AGPL/GPL requirements: {bad}"


def test_hub_packages_declare_no_forbidden_requirement():
    bad = [
        f"{path.relative_to(REPO)}: {req}"
        for path, req in _pyproject_requirements()
        if canonicalize_name(req.name) in FORBIDDEN_DISTRIBUTIONS
    ]
    assert not bad, f"hub packages declare AGPL/GPL requirements: {bad}"


@pytest.mark.parametrize("spelling", ["PyMuPDF>=1.24", "pymupdf", "PyMuPDFb==1.0"])
def test_the_requirement_check_normalizes_names(spelling):
    assert canonicalize_name(Requirement(spelling).name) in FORBIDDEN_DISTRIBUTIONS


_PYMUPDF = re.compile(r"\bpymupdf\b", re.IGNORECASE)


def _uncommented_mentions(text: str) -> list[str]:
    """Workflow lines naming pymupdf outside a ``#`` comment."""
    return [
        line.strip()
        for line in text.splitlines()
        if _PYMUPDF.search(line.split("#", 1)[0])
    ]


def test_ci_never_installs_pymupdf():
    offenders = [
        f"{path.name}: {line}"
        for path in sorted((REPO / ".github" / "workflows").glob("*.yml"))
        for line in _uncommented_mentions(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"workflows still install pymupdf: {offenders}"


@pytest.mark.parametrize(
    "line",
    [
        'uv pip install --system pytest "pymupdf<2" cvss',
        "pip install PyMuPDF==1.24.0",
        "  - run: pip install pymupdf  # needed for PDFs",
    ],
)
def test_the_workflow_scan_catches_an_install(line):
    assert _uncommented_mentions(line)


def test_the_workflow_scan_ignores_comments():
    assert not _uncommented_mentions(
        "      # GNU dbm, or PyMuPDF. Read off the archive."
    )
