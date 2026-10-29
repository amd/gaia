# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Shared JSON envelope helpers for email agent tools (single source — #1232).

Note: unrelated to the "envelope" REST/MCP request wrappers in contract.py.
"""

import json
from typing import Any


def _envelope_ok(data: Any) -> str:
    return json.dumps({"ok": True, "data": data}, default=str)


def _envelope_err(message: str, data: Any = None) -> str:
    """``data`` carries what the failed call did produce, e.g. per-item results."""
    body = {"ok": False, "error": message}
    if data is not None:
        body["data"] = data
    return json.dumps(body, default=str)
