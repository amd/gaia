# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Dependencies whose next major release removes APIs GAIA calls stay capped.

``pip install --pre "amd-gaia[ui]==<rc>"`` -- how release candidates are
installed -- also lets the resolver take pre-releases of every dependency.
With no upper bound it took ``httpx 1.0.dev`` (no top-level ``httpx.get`` /
``HTTPError`` / ``ConnectError``), which turned every Agent UI status call into
a 500 and a misleading "Lemonade Server is not installed", and ``APScheduler
4.0a`` (no ``apscheduler.schedulers`` / ``apscheduler.events``), which breaks
``gaia.schedule``. A future stable major would do the same to every install.

Static packaging assertions: they read setup.py, so they run in a venv that
never installed the package.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _setup_kwargs() -> dict:
    tree = ast.parse(SETUP_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "setup"
        ):
            wanted = {"install_requires", "extras_require"}
            return {
                kw.arg: ast.literal_eval(kw.value)
                for kw in node.keywords
                if kw.arg in wanted
            }
    raise AssertionError(f"No setup(...) call found in {SETUP_PY}")


def _requirements(name: str) -> list[tuple[str, Requirement]]:
    kwargs = _setup_kwargs()
    found = [("install_requires", r) for r in kwargs["install_requires"]]
    for extra, reqs in kwargs["extras_require"].items():
        found.extend((f"[{extra}]", r) for r in reqs)
    return [
        (where, Requirement(raw))
        for where, raw in found
        if Requirement(raw).name.lower() == name
    ]


@pytest.mark.parametrize(
    "package, broken_major",
    [("httpx", "1.0.dev6"), ("apscheduler", "4.0.0a6")],
)
def test_every_declaration_excludes_the_breaking_major(
    package: str, broken_major: str
) -> None:
    declared = _requirements(package)
    assert declared, f"setup.py no longer declares {package}; drop this guard"
    for where, req in declared:
        allows = req.specifier.contains(Version(broken_major), prereleases=True)
        assert not allows, (
            f"{where} declares '{req}', which admits {package} {broken_major}. "
            f"That release removes APIs GAIA imports; add an upper bound below it."
        )


def test_pymupdf_is_capped_below_2() -> None:
    """pymupdf 2.0 ships no macOS x86_64 wheel and its sdist fails to build.

    The cap is global because ``gaia init`` refuses a platform marker on an
    extra's requirement, so every platform resolves to a 1.x wheel.
    """
    declared = _requirements("pymupdf")
    assert {where for where, _ in declared} >= {"[rag]", "[ui]"}
    for where, req in declared:
        assert req.marker is None, f"{where} declares '{req}' with a marker"
        assert req.specifier.contains(Version("1.28.2")), f"{where}: '{req}'"
        assert not req.specifier.contains(Version("2.0")), f"{where}: '{req}'"


def test_cryptography_is_capped_below_49() -> None:
    """cryptography 49+ ships no macOS x86_64 wheel; its sdist needs Rust.

    48.0.1 is the last release with a universal2 wheel. One global cap, like
    pymupdf's, keeps every platform on the same version the frozen builds use.
    """
    declared = _requirements("cryptography")
    assert {where for where, _ in declared} == {"install_requires"}
    for where, req in declared:
        assert req.marker is None, f"{where} declares '{req}' with a marker"
        assert req.specifier.contains(Version("48.0.1")), f"{where}: '{req}'"
        assert not req.specifier.contains(Version("49.0.0")), f"{where}: '{req}'"


def test_extras_carry_no_platform_markers() -> None:
    """``gaia init`` adds extras by reading these requirements back from the
    installed metadata and refuses any condition beyond ``extra == ...``."""
    for extra, reqs in _setup_kwargs()["extras_require"].items():
        for raw in reqs:
            assert Requirement(raw).marker is None, (
                f"[{extra}] declares '{raw}'; `gaia init --profile ...` fails on "
                "it (gaia.agents.install_hints.gaia_extra_requirements)"
            )
