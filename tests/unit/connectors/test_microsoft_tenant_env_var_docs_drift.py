# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Drift guard: no LIVE doc may present ``GAIA_MICROSOFT_TENANT`` as a way to
configure GAIA (#2628). The variable is no longer read for tenant resolution
at all — it is validated only for conflict (plan amendment A2) — so a doc
telling a reader to set it is actively wrong advice, not merely stale.

Modeled on ``test_catalog_docs_url.py`` / ``test_google_console_scope_docs_drift.py``
(a live-source <-> docs cross-check with no optional-package dependency).

One page is allow-listed: ``docs/connectors/microsoft.mdx`` MUST still name
the variable — that is exactly the page telling users to stop using it (the
"Migrating from GAIA_MICROSOFT_TENANT" section). Historical CHANGELOG entries
are excluded per the plan ("historical CHANGELOG entries stay as written") —
they describe what a past release did, not current configuration.
"""

from __future__ import annotations

from pathlib import Path

_DOCS_ROOT = Path(__file__).resolve().parents[3] / "docs"

_NEEDLE = "GAIA_MICROSOFT_TENANT"

# The one page allowed to mention the variable — it is the migration guide
# telling users to stop using it.
_ALLOWED_RELATIVE_PATHS = {"connectors/microsoft.mdx"}


def _doc_files() -> list[Path]:
    assert _DOCS_ROOT.is_dir(), f"docs/ not found at {_DOCS_ROOT}"
    return [
        p
        for p in _DOCS_ROOT.rglob("*")
        if p.is_file()
        and p.suffix in (".mdx", ".md")
        and "changelog" not in p.name.lower()
    ]


def test_guard_actually_scans_files():
    """Guard against a false-negative pass if the docs tree can't be found."""
    files = _doc_files()
    assert len(files) > 50, (
        f"only found {len(files)} doc files under {_DOCS_ROOT} — the glob is "
        "probably broken, not that docs/ is actually this small"
    )


def test_no_live_doc_presents_the_env_var_as_current_configuration():
    offenders: dict[str, int] = {}
    for path in _doc_files():
        rel = path.relative_to(_DOCS_ROOT).as_posix()
        if rel in _ALLOWED_RELATIVE_PATHS:
            continue
        count = path.read_text(encoding="utf-8").count(_NEEDLE)
        if count:
            offenders[rel] = count
    assert not offenders, (
        f"{_NEEDLE} still appears in live docs outside the allow-listed "
        f"migration page {sorted(_ALLOWED_RELATIVE_PATHS)}: {offenders}. "
        "The variable is no longer read for tenant resolution (#2628) — "
        "remove the reference or move the guidance into "
        "docs/connectors/microsoft.mdx's migration section."
    )


def test_allowed_page_still_mentions_it_as_a_migration_note():
    # Positive control: if this ever drops to zero, either the migration
    # section was deleted (bad — users still need it) or the allowlist entry
    # is now stale and should be removed.
    path = _DOCS_ROOT / "connectors" / "microsoft.mdx"
    assert path.is_file(), path
    text = path.read_text(encoding="utf-8")
    assert _NEEDLE in text
    assert "Migrating from" in text
