# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Every message and doc that sends users to Settings names the tab the UI
actually shows, "Connectors", never "Connections" (#2115).
"""

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCAN_ROOTS = [_ROOT / "src" / "gaia", _ROOT / "hub" / "agents", _ROOT / "docs"]
_STALE_RE = re.compile(r"Settings\s*(?:→|->)\s*Connections\b")
_SCAN_EXTENSIONS = {
    ".py",
    ".tsx",
    ".ts",
    ".js",
    ".md",
    ".mdx",
    ".json",
    ".yaml",
    ".yml",
}
_SKIP_PATH_PARTS = {"node_modules", "dist", "__pycache__"}
# The UI still recognises the old wording from agent builds released before the rename.
_ALLOWED = {
    Path("src/gaia/apps/webui/src/components/__tests__/EmailConnectCta.test.ts"),
}


def test_settings_path_names_the_connectors_tab():
    assert all(root.is_dir() for root in _SCAN_ROOTS), _SCAN_ROOTS
    offenders: list[str] = []
    for root in _SCAN_ROOTS:
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in _SCAN_EXTENSIONS:
                continue
            if any(part in _SKIP_PATH_PARTS for part in path.parts):
                continue
            rel = path.relative_to(_ROOT)
            if rel in _ALLOWED:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in _STALE_RE.finditer(text):
                line_num = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{rel}:{line_num}")
    assert (
        not offenders
    ), "Settings has no Connections tab; say Settings → Connectors:\n  " + "\n  ".join(
        offenders
    )
