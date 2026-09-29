# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One table comparing several models on the same eval scenarios.

Choosing a model is a trade between four things that pull against each other:
how good the answers are, how long they take, how many tokens they burn getting
there, and what that costs. Looking at four scorecards side by side does not
answer it — the comparison has to be one row per model with the same columns.

Everything here is read from scorecards that already exist
(:func:`gaia.eval.scorecard.build_scorecard`); nothing is re-run and nothing is
estimated. A metric a run did not measure is rendered as ``—`` rather than as a
zero, because a model that reports no token counts and a model that used no
tokens are very different results and a zero makes them look identical.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from gaia.eval.config import MODEL_PRICING
from gaia.eval.quality_metrics import compute_cost
from gaia.llm.lemonade_client import cloud_model_provider


@dataclass
class ModelRun:
    """One model's results over a scenario set, flattened for comparison."""

    model: str
    scenarios: int = 0
    passed: int = 0
    avg_score: Optional[float] = None
    wall_seconds: Optional[float] = None
    steps: Optional[int] = None
    tool_calls: Optional[int] = None
    input_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    tokens_per_second: Optional[float] = None
    ttft_seconds: Optional[float] = None
    usd: Optional[float] = None

    @property
    def pass_rate(self) -> Optional[float]:
        """Share of scenarios that passed, or None when none ran."""
        if not self.scenarios:
            return None
        return self.passed / self.scenarios

    @property
    def cached_share(self) -> Optional[float]:
        """Share of the prompt the provider served from its own cache."""
        if not self.input_tokens or self.cached_tokens is None:
            return None
        return self.cached_tokens / self.input_tokens

    @property
    def usd_per_pass(self) -> Optional[float]:
        """What one passing scenario cost; None with no passes (no infinite price)."""
        if self.usd is None or not self.passed:
            return None
        return self.usd / self.passed


def _num(value: Any) -> Optional[float]:
    """A usable number, or None. Booleans are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _add(total: Optional[float], value: Any) -> Optional[float]:
    """Running sum that stays None until something real is added to it."""
    got = _num(value)
    if got is None:
        return total
    return got if total is None else total + got


def from_scorecard(scorecard: dict, model: Optional[str] = None) -> ModelRun:
    """Flatten one scorecard into a comparison row.

    The row is named by ``model``, else the scorecard's config, else its run id.
    """
    summary = scorecard.get("summary") or {}
    config = scorecard.get("config") or {}
    name = (
        model
        or config.get("model")
        or config.get("model_id")
        or scorecard.get("run_id")
        or "unknown"
    )

    run = ModelRun(model=str(name))
    run.scenarios = int(summary.get("total_scenarios") or 0)
    run.passed = int(summary.get("passed") or 0)
    run.avg_score = _num(summary.get("avg_score"))

    scenarios = scorecard.get("scenarios") or []
    latencies: list[float] = []
    for scenario in scenarios:
        perf = scenario.get("performance_summary")
        if not isinstance(perf, dict):
            continue
        run.steps = _add(run.steps, perf.get("steps"))
        run.tool_calls = _add(run.tool_calls, perf.get("tool_calls"))
        run.input_tokens = _add(run.input_tokens, perf.get("total_input_tokens"))
        run.output_tokens = _add(run.output_tokens, perf.get("total_output_tokens"))
        run.cached_tokens = _add(run.cached_tokens, perf.get("total_cached_tokens"))
        seconds = _num(perf.get("pipeline_latency_s"))
        if seconds is not None:
            latencies.append(seconds)

    run.wall_seconds = sum(latencies) if latencies else None
    for field, key in (
        ("tokens_per_second", "avg_tokens_per_second"),
        ("ttft_seconds", "avg_time_to_first_token"),
    ):
        setattr(run, field, _num((scorecard.get("performance") or {}).get(key)))

    run.usd = _price(run)
    for count in (
        "steps",
        "tool_calls",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
    ):
        value = getattr(run, count)
        if value is not None:
            setattr(run, count, int(value))
    return run


def is_unpriced_cloud_model(model: str) -> bool:
    """A cloud model with no ``MODEL_PRICING`` row — its real spend is unknown."""
    if model in MODEL_PRICING:
        return False
    return model.startswith("claude-") or cloud_model_provider(model) is not None


def _price(run: ModelRun) -> Optional[float]:
    """This run's cost from its own tokens under today's rates, not the scorecard's.

    A local model (absent from the table, not cloud-routed) is a defined $0.00;
    only an unpriced cloud model, or a run with no token counts, is None.
    """
    if run.input_tokens is None and run.output_tokens is None:
        return None
    if is_unpriced_cloud_model(run.model):
        return None
    return compute_cost(
        int(run.input_tokens or 0),
        int(run.output_tokens or 0),
        model=run.model,
        cached_input_tokens=int(run.cached_tokens or 0),
    )


def load_scorecard(path: str | Path) -> dict:
    """Read a scorecard.json, failing with the path when it is not one."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise FileNotFoundError(f"no scorecard at {p}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{p} is not valid JSON: {e}") from e
    if not isinstance(data, dict) or "summary" not in data:
        raise ValueError(
            f"{p} does not look like an eval scorecard "
            "(no 'summary' block) — point at a run's scorecard.json"
        )
    return data


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_ABSENT = "—"


def _fmt_int(value: Optional[int]) -> str:
    if value is None:
        return _ABSENT
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _fmt_pct(value: Optional[float]) -> str:
    return _ABSENT if value is None else f"{value * 100:.0f}%"


def _fmt_secs(value: Optional[float]) -> str:
    if value is None:
        return _ABSENT
    if value < 60:
        return f"{value:.1f}s"
    return f"{int(value // 60)}m {int(value % 60)}s"


def _fmt_usd(value: Optional[float], places: int = 4) -> str:
    return _ABSENT if value is None else f"${value:.{places}f}"


def _fmt_num(value: Optional[float], places: int = 1) -> str:
    return _ABSENT if value is None else f"{value:.{places}f}"


#: Column label, and how to get it out of a row. Order is the table's order:
#: quality first, because a cheap model that gets it wrong is not a saving.
_COLUMNS: tuple[tuple[str, Any], ...] = (
    ("Model", lambda r: r.model),
    ("Pass", lambda r: f"{r.passed}/{r.scenarios}" if r.scenarios else _ABSENT),
    ("Pass rate", lambda r: _fmt_pct(r.pass_rate)),
    ("Quality", lambda r: _fmt_num(r.avg_score, 2)),
    ("Time", lambda r: _fmt_secs(r.wall_seconds)),
    ("TTFT", lambda r: _fmt_secs(r.ttft_seconds)),
    ("Tok/s", lambda r: _fmt_num(r.tokens_per_second)),
    ("Steps", lambda r: _fmt_int(r.steps)),
    ("Tools", lambda r: _fmt_int(r.tool_calls)),
    ("Input", lambda r: _fmt_int(r.input_tokens)),
    ("Cached", lambda r: _fmt_pct(r.cached_share)),
    ("Output", lambda r: _fmt_int(r.output_tokens)),
    ("Cost", lambda r: _fmt_usd(r.usd)),
    ("$/pass", lambda r: _fmt_usd(r.usd_per_pass)),
)


def render_markdown(runs: Iterable[ModelRun]) -> str:
    """One markdown table, one row per model, sorted by pass rate then cost."""
    # An unknown cost must not win the cheapest-first tiebreak.
    rows = sorted(
        runs,
        key=lambda r: (
            -(r.pass_rate or 0.0),
            r.usd if r.usd is not None else float("inf"),
        ),
    )
    if not rows:
        return "No runs to compare."

    header = [label for label, _ in _COLUMNS]
    body = [[get(r) for _, get in _COLUMNS] for r in rows]
    widths = [
        max(len(header[i]), *(len(row[i]) for row in body)) for i in range(len(header))
    ]

    def line(cells: Sequence[str]) -> str:
        return (
            "| "
            + " | ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells))
            + " |"
        )

    out = [line(header), "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out.extend(line(row) for row in body)

    local = [
        r.model for r in rows if r.usd is not None and r.model not in MODEL_PRICING
    ]
    unpriced = [
        r.model
        for r in rows
        if r.usd is None
        and (r.input_tokens is not None or r.output_tokens is not None)
        and is_unpriced_cloud_model(r.model)
    ]
    if local or unpriced:
        out.append("")
    if local:
        out.append(
            "Served locally, no per-token bill: " + ", ".join(local) + " ($0.00)."
        )
    if unpriced:
        out.append(
            "No published rate for "
            + ", ".join(unpriced)
            + " — tokens are measured, dollars are not guessed."
        )
    return "\n".join(out)


def compare(scorecards: Iterable[tuple[str, str | Path]]) -> str:
    """Render a table from ``(model, scorecard path)`` pairs."""
    return render_markdown(
        from_scorecard(load_scorecard(path), model=model) for model, path in scorecards
    )
