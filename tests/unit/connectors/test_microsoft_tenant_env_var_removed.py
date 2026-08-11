# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drift guard: ``GAIA_MICROSOFT_TENANT`` must never reappear (#2728).

Since #2628 split Microsoft into ``microsoft`` (Personal, ``consumers``) and
``microsoft_work`` (Work or School, ``organizations``), each connector
resolves its own OAuth authority from its ``ConnectorSpec`` plus an optional
stored Directory (tenant) ID. The env var was never part of that resolution;
#2728 removed the migration guard that used to reject a conflicting value and
warn on a redundant one. This test asserts the variable is gone for good —
zero references anywhere in ``src/gaia/`` or ``docs/`` — so it cannot quietly
grow a new reader later.

Modeled on ``test_catalog_docs_url.py`` / ``test_google_console_scope_docs_drift.py``
(a live-source cross-check with no optional-package dependency).

``hub/agents/email/npm/CHANGELOG.md`` is deliberately out of scope: it is a
historical record of what a past release did, not current configuration, and
lives outside both roots this test scans.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "gaia"
_DOCS_ROOT = _REPO_ROOT / "docs"

_NEEDLE = "GAIA_MICROSOFT_TENANT"

_SCANNED_SUFFIXES = (".py", ".mdx", ".md")


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in (_SRC_ROOT, _DOCS_ROOT):
        files.extend(
            p for p in root.rglob("*") if p.is_file() and p.suffix in _SCANNED_SUFFIXES
        )
    return files


def test_guard_actually_scans_files():
    """Guard against a false-negative pass if the trees can't be found."""
    assert _SRC_ROOT.is_dir(), f"src/gaia/ not found at {_SRC_ROOT}"
    assert _DOCS_ROOT.is_dir(), f"docs/ not found at {_DOCS_ROOT}"
    files = _scanned_files()
    assert len(files) > 100, (
        f"only found {len(files)} files under {_SRC_ROOT} and {_DOCS_ROOT} — "
        "the glob is probably broken, not that the trees are actually this small"
    )


def test_env_var_never_reappears_in_src_or_docs():
    offenders: dict[str, int] = {}
    for path in _scanned_files():
        count = path.read_text(encoding="utf-8").count(_NEEDLE)
        if count:
            rel = path.relative_to(_REPO_ROOT).as_posix()
            offenders[rel] = count
    assert not offenders, (
        f"{_NEEDLE} reappeared: {offenders}. It was removed for good in #2728 "
        "— tenant resolution never read it, and the migration guard that used "
        "to validate it is gone. Do not reintroduce the variable, the guard, "
        "or docs advice referencing it."
    )
