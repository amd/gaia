# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Regression guard against re-introducing the dual-writer pattern (#976).

Before #976, both ``MCPConfig.add_server`` and the connectors framework
wrote ``mcp_servers.json``, with the legacy path bypassing the keyring and
storing API keys plaintext. The cleanup deleted the legacy writers and
narrowed the legacy router to read-only routes.

This test codifies the post-cleanup invariants as failing assertions so a
future PR cannot silently revive them. Each check is a static grep against
source files — no runtime imports, no side effects.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC = REPO_ROOT / "src"
WEBUI_SRC = SRC / "gaia" / "apps" / "webui" / "src"
WEBUI_PKG = SRC / "gaia" / "apps" / "webui" / "package.json"


def _grep(pattern: str, root: Path, *, suffixes: tuple[str, ...]) -> list[str]:
    """Return ``"file:line: text"`` strings for every match under ``root``."""
    regex = re.compile(pattern)
    hits: list[str] = []
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix not in suffixes:
            continue
        # Skip __pycache__, node_modules, build outputs, and this test file.
        if any(part in {"__pycache__", "node_modules", "dist"} for part in p.parts):
            continue
        if p.resolve() == Path(__file__).resolve():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{p.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    return hits


def test_mcpconfig_writer_methods_have_no_callers():
    """
    ``MCPConfig.add_server`` / ``remove_server`` / ``_save`` were deleted in
    #976. Any new caller is a regression — even a re-introduced definition
    on the class itself counts.
    """
    pattern = r"\bMCPConfig\.(add_server|remove_server|_save)\b"
    matches = _grep(pattern, SRC, suffixes=(".py",))
    assert not matches, (
        "MCPConfig writer methods reappeared as callers — they were removed "
        f"in #976 to enforce single-writer for mcp_servers.json:\n" + "\n".join(matches)
    )


def test_mcpconfig_class_has_no_writer_method_definitions():
    """The class itself must not redefine the writer methods."""
    config_py = SRC / "gaia" / "mcp" / "client" / "config.py"
    text = config_py.read_text(encoding="utf-8")
    for name in ("add_server", "remove_server", "_save"):
        pattern = rf"^\s*def {name}\b"
        bad = [ln for ln in text.splitlines() if re.match(pattern, ln)]
        assert not bad, (
            f"{config_py.relative_to(REPO_ROOT)} re-defines MCPConfig.{name}; "
            "it was deleted in #976. The connectors framework "
            "(McpServerHandler.configure / disconnect) is the sole writer."
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        # Legacy write routes — deleted in #976.
        r'@router\.post\("/api/mcp/servers"',
        r'@router\.delete\("/api/mcp/servers/{name}"',
        r'@router\.post\(\s*"/api/mcp/servers/{name}/enable"',
        r'@router\.post\(\s*"/api/mcp/servers/{name}/disable"',
        # Legacy catalog endpoint — replaced by /api/connectors/catalog.
        r'@router\.get\("/api/mcp/catalog"',
    ],
)
def test_legacy_mcp_routes_not_redefined(endpoint):
    mcp_router = SRC / "gaia" / "ui" / "routers" / "mcp.py"
    text = mcp_router.read_text(encoding="utf-8")
    matches = re.findall(endpoint, text)
    assert not matches, (
        f"Legacy MCP route {endpoint!r} reappeared in mcp.py — it was deleted "
        "in #976. Configuration goes through /api/connectors/* now."
    )


def test_webui_dead_mcp_stubs_not_re_added():
    """The five api.ts stubs that proxied the deleted write routes must stay gone."""
    pattern = r"\b(addMCPServer|removeMCPServer|enableMCPServer|disableMCPServer|getMCPCatalog)\b"
    matches = _grep(pattern, WEBUI_SRC, suffixes=(".ts", ".tsx"))
    assert not matches, (
        "Dead webui MCP stubs reappeared — they were deleted in #976 because "
        "no React code consumed them and the underlying routes are gone:\n"
        + "\n".join(matches)
    )


def test_rehype_raw_not_imported():
    """``rehype-raw`` was removed from MessageBubble.tsx and package.json in #976.

    Re-adding it would re-open the LLM-output XSS vector (raw <script> and
    `javascript:` URLs from agent responses executing in the Electron
    renderer). We only flag actual imports / plugin uses — comments
    explaining the security history (in markdown.ts) are allowed.
    """
    # Match real import / usage forms, not comments that mention the name.
    pattern = (
        r"^\s*import\s+.*['\"]rehype-raw['\"]"  # import default/named from 'rehype-raw'
        r"|"
        r"\bfrom\s+['\"]rehype-raw['\"]"  # `from 'rehype-raw'` after a renamed import
        r"|"
        r"\brequire\(\s*['\"]rehype-raw['\"]"  # CommonJS
        r"|"
        r"\brehypePlugins\s*=\s*\[[^\]]*\brehypeRaw\b"  # JSX prop usage
    )
    matches = _grep(pattern, WEBUI_SRC, suffixes=(".ts", ".tsx"))
    assert not matches, (
        "rehype-raw reappeared in the webui as an import / plugin use — "
        "removed in #976 for XSS hardening:\n" + "\n".join(matches)
    )

    if WEBUI_PKG.exists():
        pkg_text = WEBUI_PKG.read_text(encoding="utf-8")
        # Only flag actual dependency entries, not freeform text.
        if re.search(r'"rehype-raw"\s*:', pkg_text):
            pytest.fail(
                "rehype-raw reappeared in webui/package.json dependencies — "
                "removed in #976."
            )
