# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Parse tool calls a DeepSeek model wrote in its DSML markup as plain text.

DeepSeek's chat template encodes a tool call as::

    <｜DSML｜function_calls>
    <｜DSML｜invoke name="edit_file">
    <｜DSML｜parameter name="file_path" string="true">a.py</｜DSML｜parameter>
    <｜DSML｜parameter name="limit" string="false">40</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜function_calls>

``string="true"`` carries the value verbatim; ``string="false"`` carries JSON.
Served through Fireworks the block tag also arrives as ``<｜DSML｜ calls>``,
with a space after the prefix on every tag.
"""

import json
import re
from typing import Any, Dict, List, Optional, Tuple

_PREFIX = "<｜DSML｜"
_CLOSE_PREFIX = "</｜DSML｜"
_BLOCK_NAMES = ("function_calls", "calls")

_OPEN_BLOCK = re.compile(r"<｜DSML｜\s*(?:function_calls|calls)\s*>")
_CLOSE_BLOCK = re.compile(r"</｜DSML｜\s*(?:function_calls|calls)\s*>")
_OPEN_INVOKE = re.compile(r'<｜DSML｜\s*invoke\s+name\s*=\s*"([^"]*)"\s*>')
_CLOSE_INVOKE = re.compile(r"</｜DSML｜\s*invoke\s*>")
_OPEN_PARAM = re.compile(
    r'<｜DSML｜\s*parameter\s+name\s*=\s*"([^"]*)"'
    r'(?:\s+string\s*=\s*"(true|false)")?\s*>'
)
_CLOSE_PARAM = re.compile(r"</｜DSML｜\s*parameter\s*>")
_WS = re.compile(r"\s*")

_CUT_OFF_HINT = (
    "the reply was cut off before the markup closed (usually the output-token "
    "limit). Re-issue the call as a real tool call, and split a large edit "
    "into smaller ones."
)


def parse_dsml_tool_calls(
    text: str,
) -> Optional[Tuple[List[Dict[str, Any]], str]]:
    """Return ``(calls, prose)`` for a reply carrying DSML tool-call markup.

    ``calls`` is ``[{"name": str, "tool_args": dict}, ...]`` and ``prose`` is
    the text around the block. Returns ``None`` when the reply has no DSML
    tag at all.

    Raises:
        ValueError: The markup is present but malformed or cut off. The
            message names the element at fault, for the model to correct.
    """
    if _PREFIX not in text and _CLOSE_PREFIX not in text:
        return None

    block = _OPEN_BLOCK.search(text)
    if block is None:
        raise ValueError(
            "DSML tool-call markup has no opening "
            f"`<｜DSML｜{_BLOCK_NAMES[0]}>` block, so no call could be read. "
            "Issue the call as a real tool call."
        )

    calls: List[Dict[str, Any]] = []
    pos = block.end()
    while True:
        pos = _WS.match(text, pos).end()
        end = _CLOSE_BLOCK.match(text, pos)
        if end:
            if not calls:
                raise ValueError(
                    "DSML tool-call block contains no `invoke` element. "
                    "Issue the call as a real tool call."
                )
            prose = (text[: block.start()] + text[end.end() :]).strip()
            return calls, prose
        invoke = _OPEN_INVOKE.match(text, pos)
        if invoke is None:
            if pos >= len(text):
                raise ValueError(
                    f"DSML tool-call markup is incomplete: {_CUT_OFF_HINT}"
                )
            raise ValueError(
                'DSML tool-call markup expected `<｜DSML｜invoke name="...">` or '
                f"the closing block tag but found {text[pos:pos + 40]!r}."
            )
        name = invoke.group(1).strip()
        if not name:
            raise ValueError("DSML `invoke` element has an empty tool name.")
        args, pos = _parse_parameters(text, invoke.end(), name)
        calls.append({"name": name, "tool_args": args})


def _parse_parameters(text: str, pos: int, tool: str) -> Tuple[Dict[str, Any], int]:
    args: Dict[str, Any] = {}
    while True:
        pos = _WS.match(text, pos).end()
        close = _CLOSE_INVOKE.match(text, pos)
        if close:
            return args, close.end()
        param = _OPEN_PARAM.match(text, pos)
        if param is None:
            if pos >= len(text):
                raise ValueError(
                    f"DSML call to {tool} never closes its `invoke` element: "
                    f"{_CUT_OFF_HINT}"
                )
            raise ValueError(
                f"DSML call to {tool} expected a `parameter` element or "
                f"`</｜DSML｜invoke>` but found {text[pos:pos + 40]!r}."
            )
        key = param.group(1)
        value_end = _CLOSE_PARAM.search(text, param.end())
        if value_end is None:
            raise ValueError(
                f"DSML parameter '{key}' of {tool} never closes: {_CUT_OFF_HINT}"
            )
        if key in args:
            raise ValueError(f"DSML call to {tool} repeats parameter '{key}'.")
        raw = text[param.end() : value_end.start()]
        if param.group(2) == "false":
            try:
                args[key] = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"DSML parameter '{key}' of {tool} is marked string=\"false\" "
                    f"but its value is not valid JSON ({exc.msg}): {raw[:80]!r}"
                ) from exc
        else:
            args[key] = raw
        pos = value_end.end()
