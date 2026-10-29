# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""What Claude actually spent on an eval run, as ``claude -p`` reported it.

A scenario's ``cost_estimate`` is the driver model's own guess at its cost, so
it says nothing reliable about tokens. The CLI's ``--output-format json`` reply
carries the real counts; this module reads them and adds them up.

Usage is recorded per *role*, the Claude session that spent it:

* ``driver`` — the ``claude -p`` session that runs a scenario. In
  ``gaia eval agent`` that one session plays the user AND judges every turn, so
  its tokens cover both jobs and cannot be split between them.
* ``judge`` — a separate judge-only call (re-scoring a saved transcript).

A role that did not run is absent, never zero.
"""

from __future__ import annotations

from typing import Iterable, Optional

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)

#: Every summed field of a usage record. ``calls`` is the CLI's ``num_turns``
#: (API round trips); ``cost_usd`` is the CLI's ``total_cost_usd`` at list price.
USAGE_FIELDS = TOKEN_FIELDS + ("calls", "cost_usd", "duration_api_ms")


def _count(value) -> int:
    return int(value) if isinstance(value, (int, float)) and value > 0 else 0


def empty_usage() -> dict:
    record: dict = {field: 0 for field in USAGE_FIELDS}
    record["cost_usd"] = 0.0
    return record


def usage_from_cli(raw) -> Optional[dict]:
    """A usage record from one ``claude -p --output-format json`` payload.

    ``None`` when the payload carries no ``usage`` block (not that JSON, or a
    CLI too old to report it). A run that never reached Claude reports zeros,
    and its ``calls`` is 0 even though the CLI counts the failed attempt.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("usage"), dict):
        return None
    usage = raw["usage"]
    record = empty_usage()
    for field in TOKEN_FIELDS:
        record[field] = _count(usage.get(field))
    record["duration_api_ms"] = _count(raw.get("duration_api_ms"))
    cost = raw.get("total_cost_usd")
    record["cost_usd"] = float(cost) if isinstance(cost, (int, float)) else 0.0
    reached = record["duration_api_ms"] or any(record[f] for f in TOKEN_FIELDS)
    record["calls"] = _count(raw.get("num_turns")) if reached else 0
    models = raw.get("modelUsage")
    if isinstance(models, dict) and models:
        record["models"] = sorted(models)
    return record


def add_usage(total: dict, record: Optional[dict]) -> dict:
    """Add ``record`` into ``total`` in place; returns ``total``."""
    if not record:
        return total
    for field in USAGE_FIELDS:
        total[field] = total.get(field, 0) + (record.get(field) or 0)
    models = set(total.get("models") or ()) | set(record.get("models") or ())
    if models:
        total["models"] = sorted(models)
    return total


def scenario_total(claude_usage: Optional[dict]) -> Optional[dict]:
    """All roles of one scenario's ``claude_usage`` summed; ``None`` if absent."""
    if not isinstance(claude_usage, dict):
        return None
    roles = [r for r in (claude_usage.get("roles") or {}).values() if r]
    if not roles:
        return None
    total = empty_usage()
    for record in roles:
        add_usage(total, record)
    return total


def merge_scenario_usage(records: Iterable[Optional[dict]]) -> Optional[dict]:
    """Fold several ``claude_usage`` values (``--iterations`` attempts) into one.

    Attempts without usage (a driver killed on timeout) are counted in
    ``attempts_without_usage`` so the sum is known to be a lower bound.
    """
    roles: dict = {}
    missing = 0
    seen = False
    for record in records:
        if not isinstance(record, dict) or not record.get("roles"):
            missing += 1
            continue
        seen = True
        for role, usage in record["roles"].items():
            add_usage(roles.setdefault(role, empty_usage()), usage)
        missing += int(record.get("attempts_without_usage") or 0)
    if not seen:
        return None
    merged: dict = {"roles": roles}
    if missing:
        merged["attempts_without_usage"] = missing
    return merged


def _rounded(record: dict) -> dict:
    out = dict(record)
    out["cost_usd"] = round(float(out.get("cost_usd") or 0.0), 4)
    return out


def aggregate_usage(results: list) -> dict:
    """Run-level Claude usage: total, by role, and by category.

    ``scenarios_without_usage`` counts results with no recorded usage (killed
    on timeout, skipped before Claude ran, or written by an older runner), so
    the totals are what was *measured*, and say so.
    """
    total = empty_usage()
    by_role: dict = {}
    by_category: dict = {}
    with_usage = 0
    without_usage = 0
    for result in results:
        usage = result.get("claude_usage")
        scenario = scenario_total(usage)
        if scenario is None:
            without_usage += 1
            continue
        with_usage += 1
        add_usage(total, scenario)
        for role, record in usage["roles"].items():
            add_usage(by_role.setdefault(role, empty_usage()), record)
        category = result.get("category", "unknown")
        bucket = by_category.setdefault(category, {**empty_usage(), "scenarios": 0})
        add_usage(bucket, scenario)
        bucket["scenarios"] += 1
    return {
        "total": _rounded(total),
        "by_role": {role: _rounded(r) for role, r in sorted(by_role.items())},
        "by_category": {c: _rounded(r) for c, r in sorted(by_category.items())},
        "scenarios_with_usage": with_usage,
        "scenarios_without_usage": without_usage,
    }


def total_tokens(record: dict) -> int:
    return sum(int(record.get(field) or 0) for field in TOKEN_FIELDS)


def describe(record: dict) -> str:
    """One line for a log: calls, the four token counts, and the reported cost."""
    return (
        f"{record.get('calls', 0):,} calls, "
        f"{record.get('input_tokens', 0):,} input, "
        f"{record.get('cache_creation_input_tokens', 0):,} cache write, "
        f"{record.get('cache_read_input_tokens', 0):,} cache read, "
        f"{record.get('output_tokens', 0):,} output tokens, "
        f"${float(record.get('cost_usd') or 0):.2f} at list price"
    )
