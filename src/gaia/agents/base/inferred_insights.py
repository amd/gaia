# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Parse the JSON array of profile insights the local LLM returns during
``gaia memory bootstrap --infer`` and the Memory dashboard's inference stream.

Local models regularly stop mid-array, so a reply that does not parse whole
still yields every insight object before the point where it stops parsing:
the end of a cut-off reply, or a malformed item, which cannot be skipped
because its end is unknown. Only a reply with no usable insight is an error.
"""

import json
import math
from typing import Any, Dict, List, NamedTuple, Optional, Tuple


class ParsedInsights(NamedTuple):
    """The usable insights, and whether part of the reply could not be read.

    ``incomplete`` is ``True`` when the array stopped parsing before its
    closing ``]`` (the reply was cut off or an item was malformed), or when an
    item was dropped for having no usable ``content``.
    """

    insights: List[Dict[str, Any]]
    incomplete: bool


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
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Text around an otherwise whole array, or an array that stops
            # parsing part-way (cut off, or a malformed item).
            items, closed = _complete_objects(cleaned)
            salvaged = [i for i in items if _is_insight(i)]
            if not salvaged:
                raise ValueError(
                    f"the model's reply is not a valid JSON array of insights ({e})"
                ) from e
            return ParsedInsights(
                salvaged, incomplete=not closed or len(salvaged) < len(items)
            )
    except RecursionError as e:
        raise ValueError("the model's reply is nested too deeply to parse") from e

    if not isinstance(parsed, list):
        raise ValueError(
            "the model's reply is JSON but not an array of insights "
            f"(got {type(parsed).__name__})"
        )
    insights = [i for i in parsed if _is_insight(i)]
    if parsed and not insights:
        raise ValueError(
            f"none of the {len(parsed)} item(s) in the model's reply has a "
            "non-empty text `content`"
        )
    return ParsedInsights(insights, incomplete=len(insights) < len(parsed))


DEFAULT_CONFIDENCE = 0.7

_CONFIDENCE_WORDS = {"low": 0.4, "medium": 0.7, "high": 0.9}


def normalize_confidence(value: Any) -> Optional[float]:
    """Turn an insight's ``confidence`` into a float in ``[0, 1]``.

    Numbers (and numeric strings) are clamped to ``[0, 1]``. The words
    ``low``/``medium``/``high`` (any case, surrounding whitespace ignored)
    map to 0.4/0.7/0.9. Anything else, including NaN and infinity, returns
    ``None`` so the caller can skip the insight and say why, rather than guess
    a value.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        word = value.strip().lower()
        if word in _CONFIDENCE_WORDS:
            return _CONFIDENCE_WORDS[word]
        try:
            number = float(word)
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))
