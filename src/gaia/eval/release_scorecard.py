# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent / per-version eval scorecard: generator, parser, validator, and versioning helpers.

**Distinct from** ``src/gaia/eval/scorecard.py`` — that module is the per-eval-run
scenario PASS/FAIL aggregator (``build_scorecard``). This module produces the
outward-facing *release artifact*: a versioned Markdown file with YAML front matter
holding measured accuracy metrics, the eval recipe, and a deterministic aggregate score.

Intentionally harness-agnostic: this module imports ONLY stdlib + PyYAML.
No other loader is permitted — ``yaml.safe_load`` only.

Usage pattern::

    payload = ResultPayload(
        agent_name="email-triage",
        agent_version="0.2.4",
        ...
    )
    text = render_scorecard(payload)
    write_scorecard(payload, path)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

# Anchored semver regex — no prerelease/build suffixes permitted.
_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# Required top-level keys in the parsed front matter.
REQUIRED_FIELDS: list[str] = [
    "schema_version",
    "agent",
    "recipe",
    "results",
    "aggregate",
]


@dataclass
class ResultPayload:
    """Harness-agnostic result payload — the input to the scorecard generator.

    Fields:
        agent_name: Human-readable agent name (e.g. "Email Triage").
        agent_version: Semver version string (e.g. "0.2.4").
        dataset_reference: Repo-relative path or URL to the dataset.
        dataset_description: Short human description of the dataset.
        dataset_size: Total labeled examples available in the dataset.
        methodology: Short description of the eval methodology.
        config: Arbitrary dict of harness config (model, limit, corpus, etc.).
        test_cases_run: Number of cases actually executed this run (<= dataset_size).
        metrics: List of dicts with keys ``name`` (str), ``value`` (float 0..1),
            and optionally ``weight`` (float, default 1.0).
        aggregate_name: Name for the aggregate score (default "weighted_accuracy").
        generated_at: ISO-8601 timestamp string; informational only.
        inherited_from: If this is a patch carry-forward, the prior version string;
            otherwise None.
    """

    agent_name: str
    agent_version: str
    dataset_reference: str
    dataset_description: str
    dataset_size: int
    methodology: str
    config: dict
    test_cases_run: int
    metrics: list
    aggregate_name: str = "weighted_accuracy"
    generated_at: str = ""
    inherited_from: Optional[str] = None


def compute_aggregate(metrics: list) -> tuple:
    """Compute the weighted aggregate score over a list of metrics.

    Formula::

        round(100 * sum(weight_i * value_i) / sum(weight_i), 2)

    Args:
        metrics: List of dicts with ``name``, ``value`` (float in [0,1]),
            and optional ``weight`` (float, default 1.0).

    Returns:
        (components, value) where ``components`` is a list of dicts
        ``{metric, value, weight}`` and ``value`` is the aggregate float.

    Raises:
        ValueError: If metrics is empty or the total weight is zero.
    """
    if not metrics:
        raise ValueError("aggregate undefined: no metrics / zero total weight")

    components = []
    total_weight = 0.0
    weighted_sum = 0.0
    for m in metrics:
        w = float(m.get("weight", 1.0))
        v = float(m["value"])
        components.append({"metric": m["name"], "value": v, "weight": w})
        total_weight += w
        weighted_sum += w * v

    if total_weight == 0.0:
        raise ValueError("aggregate undefined: no metrics / zero total weight")

    value = round(100.0 * weighted_sum / total_weight, 2)
    return components, value


def render_scorecard(payload: ResultPayload) -> str:
    """Render a scorecard as Markdown with YAML front matter.

    The front matter is machine-readable; the body is a human-readable summary
    that includes the aggregate formula and a worked recomputation example.

    Args:
        payload: Populated :class:`ResultPayload`.

    Returns:
        Markdown string starting with ``---`` front matter.
    """
    _assert_valid_version(payload.agent_version)

    components, agg_value = compute_aggregate(payload.metrics)

    # Build the YAML-serialisable front-matter dict
    front: dict = {
        "schema_version": 1,
        "agent": {
            "name": payload.agent_name,
            "version": payload.agent_version,
        },
        "recipe": {
            "dataset": {
                "reference": payload.dataset_reference,
                "description": payload.dataset_description,
                "size": payload.dataset_size,
            },
            "methodology": payload.methodology,
            "config": payload.config,
        },
        "results": {
            "test_cases_run": payload.test_cases_run,
            "metrics": [
                {
                    "name": m["name"],
                    "value": float(m["value"]),
                    "weight": float(m.get("weight", 1.0)),
                }
                for m in payload.metrics
            ],
        },
        "aggregate": {
            "name": payload.aggregate_name,
            "formula": "round(100 * sum(weight_i * value_i) / sum(weight_i), 2)",
            "components": components,
            "value": agg_value,
        },
        "generated_at": payload.generated_at,
        "inherited_from": payload.inherited_from,
    }

    fm_text = yaml.dump(front, default_flow_style=False, sort_keys=False, allow_unicode=True)

    # Human-readable body with worked recompute
    metric_lines = "\n".join(
        f"  - **{c['metric']}**: {c['value']:.4f} × {c['weight']:.1f}"
        for c in components
    )
    total_w = sum(c["weight"] for c in components)
    worked = " + ".join(
        f"({c['value']:.4f} × {c['weight']:.1f})" for c in components
    )

    body = f"""# {payload.agent_name} — Eval Scorecard v{payload.agent_version}

**Aggregate score: {agg_value}** (out of 100)

## Recipe

| Field | Value |
|-------|-------|
| Dataset | [{payload.dataset_reference}]({payload.dataset_reference}) |
| Description | {payload.dataset_description} |
| Dataset size | {payload.dataset_size} labeled examples |
| Test cases run | {payload.test_cases_run} |
| Methodology | {payload.methodology} |

## Metrics

{metric_lines}

## Aggregate score recomputation

Formula: `round(100 × Σ(weightᵢ × valueᵢ) / Σ(weightᵢ), 2)`

Worked example:

```
round(100 × ({worked}) / {total_w:.1f}, 2) = {agg_value}
```

A reader can reproduce this value from the `aggregate.components` in the front
matter alone — no eval-harness access needed.
"""

    if payload.inherited_from:
        body += f"\n> **Inherited from {payload.inherited_from}** — results carried forward verbatim (patch release).\n"

    return f"---\n{fm_text}---\n{body}"


def write_scorecard(payload: ResultPayload, path: Path) -> None:
    """Write a rendered scorecard to ``path``.

    Args:
        payload: Populated :class:`ResultPayload`.
        path: Destination file path. Parent directory must exist.
    """
    path = Path(path)
    path.write_text(render_scorecard(payload), encoding="utf-8")


def parse_scorecard(source) -> dict:
    """Parse the YAML front matter from a scorecard file or string.

    Extracts the first ``---`` … ``---`` block and runs ``yaml.safe_load``
    on it only — a bare ``---`` rule in the Markdown body is never parsed.

    Args:
        source: A :class:`pathlib.Path` (file to read) or a ``str`` (raw text).

    Returns:
        Parsed front-matter dict.

    Raises:
        ValueError: If no valid front-matter block is found or YAML is invalid.
    """
    if isinstance(source, Path):
        text = source.read_text(encoding="utf-8")
    else:
        text = str(source)

    # Split on first pair of '---' delimiters
    if not text.startswith("---"):
        raise ValueError(f"Scorecard does not start with '---' front matter")

    # Find the closing '---' (first occurrence after the opening line)
    rest = text[3:]  # strip opening ---
    # The closing delimiter is a line consisting of exactly ---
    closing_match = re.search(r"\n---\n", rest)
    if closing_match is None:
        # Try end-of-string variant
        closing_match = re.search(r"\n---$", rest)
    if closing_match is None:
        raise ValueError("Scorecard front matter has no closing '---'")

    yaml_block = rest[: closing_match.start()]
    try:
        return yaml.safe_load(yaml_block) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in scorecard front matter: {exc}") from exc


def validate_scorecard(parsed: dict) -> list:
    """Validate a parsed scorecard front-matter dict.

    Args:
        parsed: Dict returned by :func:`parse_scorecard`.

    Returns:
        List of error strings. Empty list means the scorecard is valid.
    """
    errors: list[str] = []

    for key in REQUIRED_FIELDS:
        if key not in parsed:
            errors.append(f"Missing required field: '{key}'")

    return errors


def _semver_tuple(v: str) -> tuple:
    """Parse a semver string to an int tuple, or raise ValueError."""
    m = _SEMVER_RE.match(v)
    if not m:
        raise ValueError(f"Not a valid semver string: {v!r}")
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _assert_valid_version(version: str) -> None:
    """Raise ValueError if version does not match the anchored semver regex."""
    m = _SEMVER_RE.match(version)
    if not m:
        raise ValueError(
            f"Version {version!r} does not match semver pattern X.Y.Z — "
            "prerelease and build-metadata suffixes are not permitted."
        )


def _assert_safe_path(scorecards_dir: Path, version: str) -> Path:
    """Return ``scorecards_dir / f"{version}.md"`` after path-traversal guard."""
    _assert_valid_version(version)
    scorecards_dir = scorecards_dir.resolve()
    candidate = (scorecards_dir / f"{version}.md").resolve()
    if not str(candidate).startswith(str(scorecards_dir)):
        raise ValueError(
            f"Resolved scorecard path {candidate} is not inside "
            f"scorecards dir {scorecards_dir} — possible path traversal."
        )
    return candidate


def latest_version_below(scorecards_dir: Path, version: str) -> Optional[str]:
    """Return the greatest version in ``scorecards_dir`` strictly less than ``version``.

    Only files whose stem matches the anchored semver regex ``^\\d+\\.\\d+\\.\\d+$``
    are considered. Non-matching filenames (README.md, .gitkeep, etc.) are silently
    skipped.

    Args:
        scorecards_dir: Directory to scan for ``*.md`` scorecards.
        version: The candidate version string (must be valid semver).

    Returns:
        The greatest matching version string strictly below ``version``, or ``None``
        if no such version exists.

    Raises:
        ValueError: If ``version`` is not a valid semver string.
    """
    _assert_valid_version(version)
    target_tuple = _semver_tuple(version)
    scorecards_dir = Path(scorecards_dir)

    candidates: list[tuple] = []
    if scorecards_dir.is_dir():
        for p in scorecards_dir.glob("*.md"):
            m = _SEMVER_RE.match(p.stem)
            if not m:
                continue  # silently skip non-semver filenames
            t = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if t < target_tuple:
                candidates.append(t)

    if not candidates:
        return None

    best = max(candidates)
    return f"{best[0]}.{best[1]}.{best[2]}"


def carry_forward(prev_path: Path, new_version: str) -> ResultPayload:
    """Carry forward a prior scorecard's results to a new patch version.

    Reads the prior scorecard, copies all results verbatim, and sets
    ``inherited_from`` to the prior version string.

    Args:
        prev_path: Path to the prior version's scorecard ``.md`` file.
        new_version: The new version string (must be a patch bump of the prior).

    Returns:
        A :class:`ResultPayload` with results copied and ``inherited_from`` set.

    Raises:
        ValueError: If ``new_version`` is not a patch-only bump of the prior version
            (i.e. if major or minor differs). The error message contains "re-run"
            to inform the caller that a fresh eval is required.
        ValueError: If the prior scorecard cannot be parsed.
    """
    _assert_valid_version(new_version)
    prev_path = Path(prev_path)
    prev_version = prev_path.stem  # e.g. "0.2.3" from "0.2.3.md"

    prev_tuple = _semver_tuple(prev_version)
    new_tuple = _semver_tuple(new_version)

    # Only patch bumps are allowed for carry-forward.
    if prev_tuple[0] != new_tuple[0] or prev_tuple[1] != new_tuple[1]:
        raise ValueError(
            f"Cannot carry forward from {prev_version} to {new_version}: "
            f"major or minor version changed. Please re-run the eval to "
            f"generate fresh results for this release."
        )

    parsed = parse_scorecard(prev_path)

    # Extract fields from the parsed front matter
    agent = parsed.get("agent", {})
    recipe = parsed.get("recipe", {})
    dataset = recipe.get("dataset", {})
    results = parsed.get("results", {})
    metrics_raw = results.get("metrics", [])

    import datetime

    return ResultPayload(
        agent_name=agent.get("name", ""),
        agent_version=new_version,
        dataset_reference=dataset.get("reference", ""),
        dataset_description=dataset.get("description", ""),
        dataset_size=dataset.get("size", 0),
        methodology=recipe.get("methodology", ""),
        config=recipe.get("config", {}),
        test_cases_run=results.get("test_cases_run", 0),
        metrics=metrics_raw,
        aggregate_name=parsed.get("aggregate", {}).get("name", "weighted_accuracy"),
        generated_at=datetime.datetime.utcnow().isoformat(),
        inherited_from=prev_version,
    )
