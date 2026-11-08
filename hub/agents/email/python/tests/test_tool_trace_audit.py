# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep the email-agent shared-helper trace audit honest (#2923)."""

from __future__ import annotations

import ast
from pathlib import Path

TOOLS_ROOT = Path(__file__).parents[1] / "gaia_agent_email" / "tools"
AUDIT_PATH = Path(__file__).parents[1] / "TOOL_TRACE_AUDIT.md"

# These helpers are deliberately not Agent tools. They run from the Agent UI,
# REST surface, or scheduler, so adding a model-facing tool trace would be
# misleading. Their callers are documented in TOOL_TRACE_AUDIT.md.
NON_TOOL_HELPERS = {
    "build_attention_view_impl",
    "detect_meeting_request_impl",
    "execute_scheduled_send_impl",
    "execute_snooze_impl",
}


def _impl_nodes() -> dict[str, tuple[Path, ast.FunctionDef | ast.AsyncFunctionDef]]:
    result = {}
    for path in sorted(TOOLS_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and node.name.endswith("_impl"):
                result[node.name] = (path, node)
    return result


def _owns_trace(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(child, ast.Call)
        and (
            isinstance(child.func, ast.Name)
            and child.func.id == "log_tool_call"
            or isinstance(child.func, ast.Attribute)
            and child.func.attr == "log_tool_call"
        )
        for child in ast.walk(node)
    )


def test_every_shared_helper_is_traced_or_explicitly_non_tool():
    helpers = _impl_nodes()
    missing = sorted(
        name
        for name, (_, node) in helpers.items()
        if name not in NON_TOOL_HELPERS and not _owns_trace(node)
    )

    assert not missing, (
        "Every Agent-facing *_impl helper must own a log_tool_call trace; "
        "classify intentional non-tool helpers in NON_TOOL_HELPERS. Missing: "
        + ", ".join(missing)
    )


def test_trace_audit_documents_every_shared_helper():
    audit = AUDIT_PATH.read_text(encoding="utf-8")
    undocumented = sorted(name for name in _impl_nodes() if name not in audit)

    assert not undocumented, (
        "TOOL_TRACE_AUDIT.md must list every shared helper and its trace owner: "
        + ", ".join(undocumented)
    )

    for name in NON_TOOL_HELPERS:
        assert name in audit
        assert "not themselves registered Agent tools" in audit
