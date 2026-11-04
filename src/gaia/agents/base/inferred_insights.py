# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Parse the JSON array of profile insights the local LLM returns during
``gaia memory bootstrap --infer`` and the Memory dashboard's inference stream.

Local models regularly stop mid-array, so a reply that does not parse whole
still yields every insight object that arrived complete. Only a reply with no
complete insight at all is an error.
"""

import json
from typing import Any, Dict, List, NamedTuple, Tuple


class ParsedInsights(NamedTuple):
    """The usable insights, and whether the reply was cut off before the end."""

    insights: List[Dict[str, Any]]
    truncated: bool


def _strip_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = "\n".join(cleaned.split("\n")[1:])
        if "```" in cleaned:
            cleaned = cleaned[: cleaned.rfind("```")]
    return cleaned.strip()


def _is_insight(item: Any) -> bool:
    return (
        isinstance(item, dict)
        and isinstance(item.get("content"), str)
        and bool(item["content"].strip())
    )


def _complete_objects(text: str) -> Tuple[List[Any], bool]:
    """Every whole JSON value in the first ``[...]`` of *text*, and whether
    that array was closed (``False`` means the reply stopped inside it)."""
    start = text.find("[")
    if start == -1:
        return [], False
    decoder = json.JSONDecoder()
    items: List[Any] = []
    pos = start + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text):
            return items, False
        if text[pos] == "]":
            return items, True
        try:
            item, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            return items, False
        items.append(item)


def parse_inferred_insights(raw_response: str) -> ParsedInsights:
    """Parse the model's reply into insight dicts with non-empty ``content``.

    Raises:
        ValueError: the reply holds no complete insight object. The message
            says why, without echoing the reply itself.
    """
    if not isinstance(raw_response, str):
        raise ValueError(
            f"the model's reply is {type(raw_response).__name__}, not text"
        )
    cleaned = _strip_fences(raw_response)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as e:
        # Text around an otherwise whole array, or an array cut off mid-way.
        items, closed = _complete_objects(cleaned)
        salvaged = [i for i in items if _is_insight(i)]
        if not salvaged:
            raise ValueError(
                f"the model's reply is not a valid JSON array of insights ({e})"
            ) from e
        return ParsedInsights(salvaged, truncated=not closed)

    if not isinstance(parsed, list):
        raise ValueError(
            "the model's reply is JSON but not an array of insights "
            f"(got {type(parsed).__name__})"
        )
    return ParsedInsights([i for i in parsed if _is_insight(i)], truncated=False)
