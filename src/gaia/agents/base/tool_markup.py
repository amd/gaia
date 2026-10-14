# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Tool calls a model wrote out as its own native markup instead of a call.

Some models fall back to their chat template's tool-call syntax in plain
assistant text. DeepSeek does it in two dialects:

* DSML (V3.2+), with a fullwidth bar ``｜`` (U+FF5C) or an ASCII ``|``::

    <｜DSML｜function_calls>
    <｜DSML｜invoke name="run_python">
    <｜DSML｜parameter name="code" string="true">print(1)</｜DSML｜parameter>
    </｜DSML｜invoke>
    </｜DSML｜function_calls>

* the older special-token form::

    <｜tool▁calls▁begin｜><｜tool▁call▁begin｜>get_weather<｜tool▁sep｜>
    {"city": "Tokyo"}<｜tool▁call▁end｜><｜tool▁calls▁end｜>

``parse_deepseek_tool_calls`` turns either into tool calls, and
``strip_tool_markup`` removes any supported markup from text a user will see.
Markup inside fenced or inline code is an example, not a call, and is left alone.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

_BAR = r"[|｜]"
_DSML_TAG = re.compile(
    rf"<\s*(/?)\s*{_BAR}\s*DSML\s*{_BAR}\s*([A-Za-z_]+)"
    r"((?:\s+[A-Za-z_]+\s*=\s*\"[^\"]*\")*)\s*/?\s*>"
)
_DSML_CONTAINERS = frozenset({"calls", "function_calls", "tool_calls"})
# Where a DSML call starts: an opening container, or an invoke naming its tool.
_DSML_OPEN = re.compile(
    rf"<\s*{_BAR}\s*DSML\s*{_BAR}\s*"
    r"(?:(?:calls|function_calls|tool_calls)\s*>|invoke\s+name\s*=)"
)
_ATTR = re.compile(r"([A-Za-z_]+)\s*=\s*\"([^\"]*)\"")

_SEP = r"[▁_]"
_DS_BEGIN = re.compile(rf"<{_BAR}tool{_SEP}calls?{_SEP}begin{_BAR}>")
_DS_CALLS_BEGIN = re.compile(rf"<{_BAR}tool{_SEP}calls{_SEP}begin{_BAR}>")
_DS_CALLS_END = re.compile(rf"<{_BAR}tool{_SEP}calls{_SEP}end{_BAR}>")
_DS_CALL_BEGIN = re.compile(rf"<{_BAR}tool{_SEP}call{_SEP}begin{_BAR}>")
_DS_CALL_END = re.compile(rf"<{_BAR}tool{_SEP}call{_SEP}end{_BAR}>")
_DS_TOOL_SEP = re.compile(rf"<{_BAR}tool{_SEP}sep{_BAR}>")
_DS_ANY = re.compile(rf"<{_BAR}tool{_SEP}(?:calls?{_SEP}(?:begin|end)|sep){_BAR}>")
_JSON_FENCE = re.compile(r"\A```(?:json)?\s*(.*?)\s*```\Z", re.DOTALL)

_CODE = re.compile(r"(```|~~~)[\s\S]*?(?:\1|\Z)|`[^`\n]+`")

# (opening marker, closing marker) for the other markup families stripped from answers.
_STRIP_BLOCKS = (
    (_DS_CALLS_BEGIN, _DS_CALLS_END),
    (_DS_CALL_BEGIN, _DS_CALL_END),
    (re.compile(r"<tool_call>"), re.compile(r"</tool_call>")),
    (re.compile(r"<function_calls>"), re.compile(r"</function_calls>")),
    (re.compile(r"<invoke\s+name\s*="), re.compile(r"</invoke>")),
    (
        re.compile(r"(?:<\|start\|>assistant)?<\|channel\|>\s*commentary\s+to="),
        re.compile(r"<\|call\|>"),
    ),
)
_ANY_MARKUP = re.compile(
    "|".join(
        [_DSML_OPEN.pattern, _DS_ANY.pattern]
        + [opening.pattern for opening, _ in _STRIP_BLOCKS[2:]]
    )
)


class MalformedToolMarkup(ValueError):
    """Native tool-call markup that is present but cannot be run as written."""


@dataclass
class MarkupToolCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MarkupParse:
    calls: List[MarkupToolCall]
    prose: str


def _outside_code(pattern: re.Pattern, text: str, pos: int = 0) -> Optional[int]:
    """Index of the first *pattern* match at or after *pos* not inside code."""
    rest = text[pos:]
    code = [(m.start(), m.end()) for m in _CODE.finditer(rest)]
    for match in pattern.finditer(rest):
        if not any(start <= match.start() < end for start, end in code):
            return pos + match.start()
    return None


def has_tool_markup(text: str) -> bool:
    """Whether *text* carries tool-call markup of any supported format."""
    return bool(text) and _outside_code(_ANY_MARKUP, text) is not None


def parse_deepseek_tool_calls(text: str) -> Optional[MarkupParse]:
    """Tool calls written as DeepSeek markup in *text*, or ``None`` if it has none.

    Raises:
        MalformedToolMarkup: the markup is there but truncated or unparseable,
            so it must be retried rather than shown to the user.
    """
    if not text:
        return None
    start = _outside_code(_DSML_OPEN, text)
    if start is not None:
        return _parse_dsml(text, start)
    start = _outside_code(_DS_BEGIN, text)
    if start is not None:
        return _parse_special_tokens(text, start)
    return None


def _parse_dsml(text: str, start: Optional[int]) -> MarkupParse:
    calls: List[MarkupToolCall] = []
    prose: List[str] = []
    pos = 0
    while start is not None:
        prose.append(text[pos:start])
        pos = _parse_dsml_block(text, start, calls)
        start = _outside_code(_DSML_OPEN, text, pos)
    prose.append(text[pos:])
    if not calls:
        raise MalformedToolMarkup("DSML tool-call markup contained no invoke.")
    return MarkupParse(calls=calls, prose="".join(prose).strip())


def _parse_dsml_block(text: str, pos: int, calls: List[MarkupToolCall]) -> int:
    """Parse one container (or one bare invoke) at *pos*; return where it ends."""
    current: Optional[MarkupToolCall] = None
    in_container = False
    while True:
        tag = _DSML_TAG.search(text, pos)
        if tag is None:
            break
        closing, kind, attrs = tag.group(1) == "/", tag.group(2).lower(), tag.group(3)
        pos = tag.end()

        if kind in _DSML_CONTAINERS:
            if closing and current is None:
                return pos
            if closing or in_container or current is not None:
                raise MalformedToolMarkup(
                    f"DSML tool call '{current.name if current else '?'}' has "
                    "mismatched tags."
                )
            in_container = True
        elif kind == "invoke" and closing:
            if current is None:
                raise MalformedToolMarkup("DSML closing invoke tag with no call.")
            calls.append(current)
            current = None
            if not in_container:
                return pos
        elif kind == "invoke":
            if current is not None:
                raise MalformedToolMarkup(
                    f"DSML tool call '{current.name}' is missing its closing "
                    "invoke tag."
                )
            name = dict(_ATTR.findall(attrs)).get("name", "").strip()
            if not name:
                raise MalformedToolMarkup("DSML invoke tag has no tool name.")
            current = MarkupToolCall(name=name)
        elif kind == "parameter" and not closing and current is not None:
            attr = dict(_ATTR.findall(attrs))
            pname = attr.get("name", "").strip()
            if not pname:
                raise MalformedToolMarkup(
                    f"DSML parameter of '{current.name}' has no name."
                )
            close = _find_close(text, pos, {"parameter"})
            if close is None:
                raise MalformedToolMarkup(
                    f"DSML tool call '{current.name}' was cut off inside parameter "
                    f"'{pname}' before it was finished."
                )
            current.args[pname] = _dsml_value(
                current.name, pname, text[pos : close.start()], attr
            )
            pos = close.end()
        else:
            raise MalformedToolMarkup(
                f"Unexpected DSML tag '{'/' if closing else ''}{kind}'."
            )
    name = current.name if current is not None else "?"
    raise MalformedToolMarkup(
        f"DSML tool call '{name}' was cut off before its closing tag."
    )


def _find_close(text: str, pos: int, kinds: set) -> Optional[re.Match]:
    for tag in _DSML_TAG.finditer(text, pos):
        if tag.group(1) == "/" and tag.group(2).lower() in kinds:
            return tag
    return None


def _dsml_value(tool: str, pname: str, raw: str, attr: Dict[str, str]) -> Any:
    if attr.get("string", "").lower() == "true":
        return raw
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        raise MalformedToolMarkup(
            f"DSML parameter '{pname}' of '{tool}' is not valid JSON ({exc}); "
            'mark text values string="true".'
        ) from exc


def _parse_special_tokens(text: str, start: int) -> MarkupParse:
    calls_end = _DS_CALLS_END.search(text, start)
    limit = calls_end.start() if calls_end else len(text)
    calls: List[MarkupToolCall] = []
    pos = start
    while True:
        call_begin = _DS_CALL_BEGIN.search(text, pos, limit)
        if call_begin is None:
            break
        call_end = _DS_CALL_END.search(text, call_begin.end(), limit)
        if call_end is None:
            raise MalformedToolMarkup(
                "DeepSeek tool call was cut off before <｜tool▁call▁end｜>."
            )
        calls.append(_special_token_call(text[call_begin.end() : call_end.start()]))
        pos = call_end.end()
    if not calls:
        raise MalformedToolMarkup("DeepSeek tool-call markup contained no call.")
    tail = text[calls_end.end() :] if calls_end else text[pos:]
    return MarkupParse(calls=calls, prose=(text[:start] + tail).strip())


def _special_token_call(segment: str) -> MarkupToolCall:
    parts = _DS_TOOL_SEP.split(segment, maxsplit=1)
    if len(parts) != 2:
        raise MalformedToolMarkup("DeepSeek tool call has no <｜tool▁sep｜>.")
    head, rest = parts[0].strip(), parts[1].strip()
    if head == "function":
        # V3: function<｜tool▁sep｜>name\n```json\n{...}\n```
        name, _, rest = rest.partition("\n")
        name, rest = name.strip(), rest.strip()
    else:
        name = head
    fenced = _JSON_FENCE.match(rest)
    if fenced:
        rest = fenced.group(1)
    if not name:
        raise MalformedToolMarkup("DeepSeek tool call has no tool name.")
    try:
        args = json.loads(rest) if rest else {}
    except json.JSONDecodeError as exc:
        raise MalformedToolMarkup(
            f"Arguments of DeepSeek tool call '{name}' are not valid JSON: {exc}"
        ) from exc
    if not isinstance(args, dict):
        raise MalformedToolMarkup(
            f"Arguments of DeepSeek tool call '{name}' must be a JSON object."
        )
    return MarkupToolCall(name=name, args=args)


def _dsml_block_end(text: str, start: int) -> Optional[int]:
    opening = _DSML_TAG.match(text, start)
    if opening is None:
        return None
    kinds = (
        _DSML_CONTAINERS if opening.group(2).lower() in _DSML_CONTAINERS else {"invoke"}
    )
    close = _find_close(text, opening.end(), kinds)
    return close.end() if close else None


def _strip_blocks(
    text: str, opening: re.Pattern, block_end: Callable[[str, int], Optional[int]]
) -> str:
    while True:
        start = _outside_code(opening, text)
        if start is None:
            return text
        end = block_end(text, start)
        # A block with no closing marker was cut off: it runs to the end.
        text = text[:start] + (text[end:] if end is not None else "")


def _closing_after(closing: re.Pattern) -> Callable[[str, int], Optional[int]]:
    def _end(text: str, start: int) -> Optional[int]:
        match = closing.search(text, start + 1)
        return match.end() if match else None

    return _end


def _sub_outside_code(pattern: re.Pattern, text: str) -> str:
    while True:
        start = _outside_code(pattern, text)
        if start is None:
            return text
        match = pattern.match(text, start)
        text = text[:start] + text[match.end() :]


def strip_tool_markup(text: str) -> str:
    """*text* without tool-call markup; a block cut off runs to the end."""
    if not has_tool_markup(text):
        return text
    text = _strip_blocks(text, _DSML_OPEN, _dsml_block_end)
    for opening, closing in _STRIP_BLOCKS:
        text = _strip_blocks(text, opening, _closing_after(closing))
    text = _sub_outside_code(_DS_ANY, text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()
