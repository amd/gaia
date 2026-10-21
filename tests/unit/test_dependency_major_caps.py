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


_PLATFORM_ENVS = {
    "darwin-x64": {"sys_platform": "darwin", "platform_machine": "x86_64"},
    "darwin-arm64": {"sys_platform": "darwin", "platform_machine": "arm64"},
    "linux-x64": {"sys_platform": "linux", "platform_machine": "x86_64"},
    "linux-arm64": {"sys_platform": "linux", "platform_machine": "aarch64"},
    "win-x64": {"sys_platform": "win32", "platform_machine": "AMD64"},
}


@pytest.mark.parametrize("platform", list(_PLATFORM_ENVS))
@pytest.mark.parametrize("extra", ["[rag]", "[ui]"])
def test_pymupdf_resolves_to_a_wheel_on_every_platform(
    extra: str, platform: str
) -> None:
    """pymupdf 2.0 has no Intel-Mac wheel and its sdist fails to build there.

    Each platform must get exactly one pymupdf requirement (RAG/PDF support is
    never dropped), and only Intel Macs are held below 2.
    """
    env = _PLATFORM_ENVS[platform]
    active = [
        req
        for where, req in _requirements("pymupdf")
        if where == extra and (req.marker is None or req.marker.evaluate(env))
    ]
    assert len(active) == 1, f"{extra} on {platform}: active pymupdf reqs {active}"
    admits_2 = active[0].specifier.contains(Version("2.0"))
    assert admits_2 == (platform != "darwin-x64"), (
        f"{extra} on {platform} declares '{active[0]}'; only darwin-x64 must "
        "exclude pymupdf 2.x (no macOS x86_64 wheel)"
    )
    assert active[0].specifier.contains(Version("1.28.2"))
