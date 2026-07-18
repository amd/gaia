# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression: every ``https://amd-gaia.ai/<path>`` literal in src/gaia/
must point at /docs/ or be on the site-root allowlist (issue #1058).

The Mintlify Documentation tab serves content under ``/docs/``. Bare paths
like ``/guides/agent-ui`` or ``/connectors/google`` return 404. Only a small
fixed set of resources (install scripts, the bare root) live at the site root.

Verified live during plan reflection (2026-05-15) by curl probe.
"""

import re
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "src" / "gaia"
_URL_RE = re.compile(r"https://amd-gaia\.ai(/[^\s'\"<>)\\,;`]*)?")
_ALLOWLIST = {
    "https://amd-gaia.ai",
    "https://amd-gaia.ai/",
    "https://amd-gaia.ai/install.ps1",
    "https://amd-gaia.ai/install.sh",
}
_SCAN_EXTENSIONS = {".py", ".tsx", ".ts", ".js", ".cjs", ".mjs", ".json", ".md"}
_SKIP_PATH_PARTS = {"node_modules", "dist", ".turbo"}


def test_amd_gaia_urls_in_src_use_docs_prefix():
    offenders: list[str] = []
    for path in _SRC.rglob("*"):
        if not path.is_file() or path.suffix not in _SCAN_EXTENSIONS:
            continue
        if any(part in _SKIP_PATH_PARTS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for match in _URL_RE.finditer(text):
            url = match.group(0).rstrip(".,);'\"`")
            if url in _ALLOWLIST or url.startswith("https://amd-gaia.ai/docs/"):
                continue
            line_num = text.count("\n", 0, match.start()) + 1
            rel = path.relative_to(_SRC.parents[1])
            offenders.append(f"{rel}:{line_num}: {url}")
    assert (
        not offenders
    ), "amd-gaia.ai URLs missing /docs/ prefix (see issue #1058):\n  " + "\n  ".join(
        offenders
    )
