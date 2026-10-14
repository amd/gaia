# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Whether a tool result reports a failure — one reading for every consumer.

Tools return dicts, JSON strings (the email agent's ``{"ok": false, ...}``
envelope, the code-index tools' ``{"error": ...}``), or plain text. The agent
loop, its failure detectors, completion evidence, turn metrics and the Agent
UI must all agree on which of those failed.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Optional, Tuple

#: Statuses the agent loop treats as a failed call. ``denied`` is separate there:
#: a refusal is not a bug to recover from.
ERROR_STATUSES: Tuple[str, ...] = ("error",)

#: Statuses where the tool did not do its job, refusals included.
FAILED_STATUSES: Tuple[str, ...] = ("error", "denied")

# Only the envelope tools build: "Error: ..." or "Error reading file: ...".
# Anchored and case-sensitive so prose that mentions an error never matches.
_PLAIN_ERROR_RE = re.compile(r"\AError(?::| [a-z]+ing [^\n:]{1,80}:)\s")


def tool_result_dict(result: Any) -> Optional[Dict[str, Any]]:
    """The result as a dict: itself, or the JSON object a string encodes."""
    if isinstance(result, dict):
        return result
    if isinstance(result, str):
        text = result.strip()
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                return None
            if isinstance(parsed, dict):
                return parsed
    return None


def tool_result_failed(
    result: Any, failed_statuses: Tuple[str, ...] = ERROR_STATUSES
) -> bool:
    """Whether *result* reports that the tool call failed.

    A non-zero ``return_code`` counts unless the tool marked it
    ``expected_exit`` (grep's exit 1 is "no match"). A bare ``error`` counts
    only when nothing in the result claims success.
    """
    data = tool_result_dict(result)
    if data is None:
        return isinstance(result, str) and bool(_PLAIN_ERROR_RE.match(result))
    status = data.get("status")
    if (
        status in failed_statuses
        or data.get("success") is False
        or data.get("ok") is False
        or data.get("has_errors") is True
    ):
        return True
    if data.get("return_code") not in (None, 0) and not data.get("expected_exit"):
        return True
    error = data.get("error")
    return (
        isinstance(error, str)
        and bool(error.strip())
        and status is None
        and "ok" not in data
        and "success" not in data
    )


def tool_result_error_fields(result: Any) -> Dict[str, Any]:
    """The fields to read a failed result's error from, for any result shape."""
    data = tool_result_dict(result)
    if data is not None:
        return data
    return {"error": str(result).strip()} if result is not None else {}
