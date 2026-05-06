# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Maps a GAIA tool call into a governance ActionRequest."""

from __future__ import annotations

from typing import Any

from .schemas import ActionRequest, new_id


def map_gaia_tool_call_to_action_request(
    tool_name: str,
    args: dict[str, Any],
    context: dict[str, Any] | None = None,
) -> ActionRequest:
    ctx = context or {}
    return ActionRequest(
        action_id=ctx.get("action_id", new_id("action")),
        actor_id=ctx.get("actor_id", "unknown-actor"),
        tool_name=tool_name,
        action_type=ctx.get("action_type", tool_name),
        args=dict(args),
        risk_tags=list(ctx.get("risk_tags", [])),
        workflow_id=ctx.get("workflow_id"),
        step_id=ctx.get("step_id"),
        source=ctx.get("source", "gaia"),
    )
