# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression: retired email-relay symbols must never reappear (issue #2109).

Issue #2109 retired the in-process ``EmailProxyAgent`` tool loop and the
frontend "fence-injection" hack, replacing both with a sidecar relay and a
render-card system. This guard fails loudly the moment any of the deleted
names resurfaces anywhere in the live codebase, the same way
``test_amd_gaia_urls.py`` guards the docs URL prefix.

``docs/plans/`` is intentionally excluded from the scan: historical plan
documents may legitimately still describe the retired behavior in past tense.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_THIS_FILE = Path(__file__).resolve()

_SCAN_ROOTS = (
    _REPO_ROOT / "src",
    _REPO_ROOT / "tests",
    _REPO_ROOT / "docs" / "guides",
    _REPO_ROOT / "docs" / "sdk",
    _REPO_ROOT / "docs" / "spec",
)

# Retired by issue #2109 (in-process EmailProxyAgent tool loop + frontend
# fence-injection hack), replaced by a sidecar relay + render-card system.
_FORBIDDEN_STRINGS = (
    "EmailProxyAgent",
    "_build_email_proxy_agent",
    "proxy_agent.py",
    "promoteStructuredPayloads",
    "STRUCTURED_PAYLOAD_KINDS",
    "_capture_render_payload",
    "_drain_render_payloads",
    "_strip_echoed_render_cards",
)

_SCAN_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".jsx", ".md", ".mdx", ".json"}
_SKIP_PATH_PARTS = {"node_modules", "dist", ".turbo", "__pycache__"}


def test_no_stale_email_relay_symbols():
    for root in _SCAN_ROOTS:
        assert root.is_dir(), f"expected scan root missing: {root}"

    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _SCAN_EXTENSIONS:
                continue
            if path.resolve() == _THIS_FILE:
                continue
            if any(part in _SKIP_PATH_PARTS for part in path.parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            rel = path.relative_to(_REPO_ROOT)
            for lineno, line in enumerate(text.splitlines(), start=1):
                for needle in _FORBIDDEN_STRINGS:
                    if needle in line:
                        offenders.append(f"{rel}:{lineno}: {needle}")

    assert not offenders, (
        "Retired email-relay symbols reappeared (see issue #2109) — the "
        "in-process EmailProxyAgent tool loop and the frontend fence-injection "
        "hack were deleted in favor of a sidecar relay + render-card system:\n  "
        + "\n  ".join(offenders)
    )
