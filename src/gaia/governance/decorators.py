# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Decorator-based risk tagging — the idiomatic Python alternative to
maintaining a central ``risk_tags`` dict on every agent.

Usage::

    from gaia import tool
    from gaia.governance import govern

    @tool
    @govern(risk="blocked", reason="destructive filesystem operation")
    def wipe_disk() -> dict:
        ...

    @tool
    @govern(risk="review")
    def send_money(amount: float, recipient: str) -> dict:
        ...

The mixin reads ``__gaia_governance__`` off the tool function at call
time and merges those tags with any dict passed via
``governance_risk_tags=``. Tags are **additive** (union, deduplicated):
decorator tags come first, then dict tags are appended. Neither side
overrides the other.
"""

from __future__ import annotations

from typing import Any, Callable

_ATTR = "__gaia_governance__"


def govern(
    *,
    risk: str | list[str],
    reason: str = "",
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach governance metadata to a tool function.

    ``risk`` may be a single tag ("blocked", "review", or any custom
    tag your policy engine understands) or a list of tags.
    ``reason`` is optional free-form text surfaced in decision reports.
    """
    tags = [risk] if isinstance(risk, str) else list(risk)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        existing = getattr(fn, _ATTR, None) or {}
        merged_tags = list(dict.fromkeys([*existing.get("risk_tags", []), *tags]))
        setattr(
            fn,
            _ATTR,
            {
                "risk_tags": merged_tags,
                "reason": reason or existing.get("reason", ""),
            },
        )
        return fn

    return decorator


def read_risk_tags(fn: Callable[..., Any] | None) -> list[str]:
    """Return risk tags declared via :func:`govern`, or an empty list."""
    if fn is None:
        return []
    meta = getattr(fn, _ATTR, None)
    if not meta:
        return []
    return list(meta.get("risk_tags", []))
