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

``docs/releases/`` gets the same carve-out, for the same reason: release notes
announcing a removal have to name the variable, and unlike the changelog above
they live inside ``_DOCS_ROOT``, so ``_scanned_files()`` excludes the
directory explicitly.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "gaia"
_DOCS_ROOT = _REPO_ROOT / "docs"
_DOCS_RELEASES_ROOT = _DOCS_ROOT / "releases"

_NEEDLE = "GAIA_MICROSOFT_TENANT"

_SCANNED_SUFFIXES = (".py", ".mdx", ".md")


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for root in (_SRC_ROOT, _DOCS_ROOT):
        files.extend(
            p
            for p in root.rglob("*")
            if p.is_file()
            and p.suffix in _SCANNED_SUFFIXES
            and not p.is_relative_to(_DOCS_RELEASES_ROOT)
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


def test_docs_releases_exclusion_is_scoped(tmp_path, monkeypatch):
    """The ``docs/releases/`` carve-out must not blind the guard to other docs.

    Builds a throwaway tree so a reintroduction anywhere else under ``docs/``
    still trips the guard, while a release note announcing a removal stays
    exempt.
    """
    src_root = tmp_path / "src" / "gaia"
    docs_root = tmp_path / "docs"
    releases_root = docs_root / "releases"
    guides_root = docs_root / "guides"
    for directory in (src_root, releases_root, guides_root):
        directory.mkdir(parents=True)

    (releases_root / "v9.9.9.mdx").write_text(
        f"### `{_NEEDLE}` is gone\n", encoding="utf-8"
    )
    (guides_root / "misplaced.mdx").write_text(
        f"Set `{_NEEDLE}` to configure your tenant.\n", encoding="utf-8"
    )

    module = sys.modules[__name__]
    monkeypatch.setattr(module, "_SRC_ROOT", src_root)
    monkeypatch.setattr(module, "_DOCS_ROOT", docs_root)
    monkeypatch.setattr(module, "_DOCS_RELEASES_ROOT", releases_root)

    offenders = {
        p.relative_to(tmp_path).as_posix(): p.read_text(encoding="utf-8").count(_NEEDLE)
        for p in _scanned_files()
        if p.read_text(encoding="utf-8").count(_NEEDLE)
    }

    assert offenders == {"docs/guides/misplaced.mdx": 1}, (
        "docs/releases/ should be excluded and docs/guides/ should still be "
        f"caught, got: {offenders}"
    )
