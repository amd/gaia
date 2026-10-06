# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Deterministic answer matching and retrieval metric aggregation."""

from __future__ import annotations

import math
import re
import string
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

_NUMBER = re.compile(r"\(?-?\$?\s?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?\)?\s?%?")
_GOLD_NUMERIC = re.compile(
    r"^\(?-?\$?\s?(?:\d{1,3}(?:,\d{3})+|\d+)?(?:\.\d+)?\)?\s?%?$"
)
#: Relative tolerance for a numeric answer; covers rounding to the reported precision.
NUMERIC_REL_TOL = 0.01
#: A reply may state a figure in another unit scale (1,577 million = 1.577 billion).
_SCALES = (1.0, 1e3, 1e-3, 1e6, 1e-6, 1e9, 1e-9)
#: A negative gold figure is also matched by its magnitude next to one of these.
_DECLINE = re.compile(
    r"\b(decrease[sd]?|declin\w*|fell|fall\w*|drop\w*|lower|negative|loss\w*|down|"
    r"reduc\w*|shr[aiu]nk\w*|contract\w*)\b",
    re.IGNORECASE,
)

RECALL_KS = (1, 3, 5, 10, 20)


@dataclass(frozen=True)
class Number:
    value: float
    percent: bool
    year_like: bool


def _parse(token: str) -> Optional[Number]:
    t = token.strip()
    percent = t.endswith("%")
    t = t.rstrip("%").strip()
    negative = (t.startswith("(") and t.endswith(")")) or t.lstrip("(").startswith("-")
    digits = re.sub(r"[^\d.]", "", t)
    if not digits or digits == "." or digits.count(".") > 1:
        return None
    value = float(digits)
    year_like = bool(re.fullmatch(r"(19|20)\d\d", digits))
    return Number(-value if negative else value, percent, year_like)


def numbers_in(text: str) -> List[Number]:
    found = []
    for m in _NUMBER.finditer(text.replace("−", "-")):
        if not re.search(r"\d", m.group(0)):
            continue
        number = _parse(m.group(0))
        if number is not None:
            found.append(number)
    return found


def is_numeric_answer(gold: str) -> bool:
    return bool(re.search(r"\d", gold)) and bool(_GOLD_NUMERIC.match(gold.strip()))


def gold_number(gold: str) -> Number:
    values = numbers_in(gold)
    if not values:
        raise ValueError(f"Numeric gold answer has no number: {gold!r}")
    return values[0]


def numeric_match(gold: str, prediction: str) -> bool:
    """Any figure in the reply equals the gold figure within 1%.

    Unit scale (thousand/million/billion) may differ. Percent and fraction
    convert only when one side carries a ``%``. A year never matches a non-year
    gold. A negative gold needs a negative figure or a decline word; a positive
    gold accepts either sign, since filings print outflows in parentheses.
    """
    target = gold_number(gold)
    declines = bool(_DECLINE.search(prediction))
    for number in numbers_in(prediction):
        if number.year_like and not target.year_like:
            continue
        scales = list(_SCALES)
        if number.percent != target.percent:
            scales += [100.0, 0.01]
        for scale in scales:
            candidate = number.value * scale
            if target.value < 0 and candidate > 0 and not declines:
                continue
            if math.isclose(
                abs(candidate), abs(target.value), rel_tol=NUMERIC_REL_TOL, abs_tol=1e-9
            ):
                return True
    return False


def normalize_answer(text: str) -> str:
    """SQuAD-style normalization, kept Unicode-safe for non-Latin scripts."""
    text = unicodedata.normalize("NFKC", text).lower()
    punctuation = set(string.punctuation) | {"。", "，", "،", "।", "¿", "¡"}
    text = "".join(" " if ch in punctuation else ch for ch in text)
    text = re.sub(r"\b(a|an|the)\b", " ", text)
    return " ".join(text.split())


def _bounded(target: str) -> str:
    """Whole-token pattern: ASCII ends need a boundary, CJK/other ends do not."""
    pre = r"(?<![a-z0-9])" if target[0].isascii() and target[0].isalnum() else ""
    post = r"(?![a-z0-9])" if target[-1].isascii() and target[-1].isalnum() else ""
    return pre + re.escape(target) + post


def exact_match(gold: str, aliases: Sequence[str], prediction: str) -> bool:
    """The gold span (or an alias) appears as whole tokens in the reply."""
    pred = normalize_answer(prediction)
    for candidate in [gold, *aliases]:
        target = normalize_answer(candidate)
        if target and re.search(_bounded(target), pred):
            return True
    return False


def token_f1(gold: str, prediction: str) -> float:
    g = normalize_answer(gold).split()
    p = normalize_answer(prediction).split()
    if not g or not p:
        return 0.0
    common: Dict[str, int] = {}
    for t in g:
        common[t] = min(g.count(t), p.count(t))
    same = sum(common.values())
    if same == 0:
        return 0.0
    precision, recall = same / len(p), same / len(g)
    return 2 * precision * recall / (precision + recall)


def first_hit_rank(hits: Sequence[bool]) -> Optional[int]:
    for rank, hit in enumerate(hits, 1):
        if hit:
            return rank
    return None


def retrieval_summary(
    ranks: Iterable[Optional[int]], ks: Sequence[int] = RECALL_KS
) -> dict:
    """recall@k and MRR from each question's first-hit rank (None = never)."""
    ranks = list(ranks)
    n = len(ranks)
    if n == 0:
        return {"n": 0}
    out = {"n": n}
    for k in ks:
        out[f"recall@{k}"] = round(
            sum(1 for r in ranks if r is not None and r <= k) / n, 4
        )
    out["mrr"] = round(sum(1.0 / r for r in ranks if r is not None) / n, 4)
    return out


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def latency_summary(seconds: Sequence[float]) -> dict:
    if not seconds:
        return {"n": 0}
    return {
        "n": len(seconds),
        "p50_ms": round(percentile(seconds, 0.5) * 1000, 1),
        "p95_ms": round(percentile(seconds, 0.95) * 1000, 1),
        "max_ms": round(max(seconds) * 1000, 1),
    }
