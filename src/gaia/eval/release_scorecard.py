# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Per-agent eval scorecard: generator, parser, validator, and versioning helpers.

**Distinct from** ``src/gaia/eval/scorecard.py`` — that module is the per-eval-run
scenario PASS/FAIL aggregator (``build_scorecard``). This module produces the
outward-facing *release artifact*: a single ``SCORECARD.md`` file (updated in
place per release, versioned via the publish snapshot — the same way README.md
works) with YAML front matter holding measured accuracy metrics, the eval recipe,
a deterministic aggregate score, and a Reproduction section.

Storage convention: ``<agent-npm-root>/SCORECARD.md``  (NOT ``scorecards/<ver>.md``).
Per-version uniqueness comes from the publish snapshot in R2 (the hub stores every
doc per version at ``agents/<id>/<version>/SCORECARD.md``).

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

import math
import re
from dataclasses import dataclass
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
        reproduction_command: Optional exact shell command(s) to reproduce this
            scorecard run.  Rendered in the ``## Reproduction`` section.  If None,
            a generic pointer to the docs/skill is rendered instead.
        notes: Optional free-form Markdown appended verbatim to the end of the
            body (e.g. dataset pointers, a worked example, a metric glossary).
            Agent-specific; the core renderer emits it unchanged, wrapped in
            invisible HTML-comment markers so :func:`carry_forward` can preserve
            it across a patch release without re-running the eval.
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
    reproduction_command: Optional[str] = None
    breakdown: Optional[dict] = None
    environment: Optional[dict] = None
    notes: Optional[str] = None


# Invisible markers bounding the free-form ``notes`` block in the rendered body.
# HTML comments render to nothing on GitHub/npm, so they don't clutter the page,
# but let ``carry_forward`` recover the block verbatim on a patch release.
_NOTES_START = "<!-- scorecard:notes:start -->"
_NOTES_END = "<!-- scorecard:notes:end -->"


def _extract_notes(text: str) -> Optional[str]:
    """Recover the free-form notes block from a rendered scorecard, if present."""
    start = text.find(_NOTES_START)
    end = text.find(_NOTES_END)
    if start == -1 or end == -1 or end < start:
        return None
    block = text[start + len(_NOTES_START) : end].strip()
    return block or None


def _md_cell(value) -> str:
    """Escape a value for safe inclusion in a single Markdown table cell.

    Pipes would split the cell and newlines would break the row, so a stray
    one in a caller-supplied value (e.g. an unusual version/hardware string)
    would silently corrupt the rendered table.
    """
    return str(value).replace("|", "\\|").replace("\n", " ")


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
    that includes the aggregate formula, a worked recomputation example, and a
    Reproduction section so any reader can reproduce the result from scratch.

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
            **({"environment": payload.environment} if payload.environment else {}),
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
            **({"breakdown": payload.breakdown} if payload.breakdown else {}),
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

    fm_text = yaml.dump(
        front, default_flow_style=False, sort_keys=False, allow_unicode=True
    )

    # Human-readable body with worked recompute
    metric_lines = "\n".join(
        f"  - **{c['metric']}**: {c['value']:.4f} × {c['weight']:.1f}"
        for c in components
    )
    total_w = sum(c["weight"] for c in components)
    worked = " + ".join(f"({c['value']:.4f} × {c['weight']:.1f})" for c in components)

    # Reproduction section
    if payload.reproduction_command:
        repro_body = (
            "Run the following commands from the repository root:\n\n"
            f"```sh\n{payload.reproduction_command}\n```\n\n"
            "See [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) "
            "and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) "
            "for the full setup guide."
        )
    else:
        repro_body = (
            "See the [eval-scorecard docs](https://amd-gaia.ai/docs/reference/eval-scorecard) "
            "and the [`adding-eval-scorecard` skill](.claude/skills/adding-eval-scorecard/SKILL.md) "
            "for the full reproduction recipe."
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

## Reproduction

{repro_body}
"""

    if payload.environment:
        env_rows = "\n".join(
            f"| {_md_cell(k)} | {_md_cell(v)} |" for k, v in payload.environment.items()
        )
        body += (
            f"\n## Environment\n\n| Field | Value |\n|-------|-------|\n{env_rows}\n"
        )

    if payload.breakdown:
        per_cat = payload.breakdown.get("per_category", [])
        cat_rows = "\n".join(
            f"| {_md_cell(r['category'])} | {r['total']} | {r['correct']} "
            f"| {r['accuracy']:.4f} |"
            for r in per_cat
        )
        # The breakdown pools every (email, run) observation, so for a multi-run
        # eval the per-category totals = test_cases_run × n_runs. Label it so a
        # reader doesn't read the N× counts as failing to reconcile with
        # results.test_cases_run.
        try:
            n_runs = int((payload.config or {}).get("n_runs", 0) or 0)
        except (TypeError, ValueError):
            n_runs = 0
        if n_runs > 1:
            heading = f"## Category breakdown (pooled across all {n_runs} runs)"
            note = (
                f"\n_Each of the {payload.test_cases_run} test cases is scored once "
                f"per run, so the totals below sum to test_cases_run × {n_runs}._\n"
            )
        else:
            heading = "## Category breakdown"
            note = ""
        breakdown_section = (
            f"\n{heading}\n{note}\n"
            "| Category | Total | Correct | Accuracy |\n"
            "|----------|-------|---------|----------|\n"
            f"{cat_rows}\n"
        )
        top_conf = payload.breakdown.get("top_confusions", [])
        if top_conf:
            conf_lines = "\n".join(
                f"  - {_md_cell(c['expected'])} → {_md_cell(c['predicted'])}: "
                f"{c['count']}"
                for c in top_conf
            )
            breakdown_section += f"\n**Top confusions:**\n\n{conf_lines}\n"
        body += breakdown_section

    if payload.notes:
        body += f"\n{_NOTES_START}\n{payload.notes.strip()}\n{_NOTES_END}\n"

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
        raise ValueError("Scorecard does not start with '---' front matter")

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

    # Top-level required keys
    for key in REQUIRED_FIELDS:
        if key not in parsed:
            errors.append(f"Missing required field: '{key}'")

    def _section(name: str):
        """Return the section dict if present and a dict, else record an error."""
        value = parsed.get(name)
        if name in parsed and not isinstance(value, dict):
            errors.append(
                f"Field '{name}' must be a mapping, got {type(value).__name__}"
            )
            return None
        return value if isinstance(value, dict) else None

    # agent.{name, version}
    agent = _section("agent")
    if agent is not None:
        for sub in ("name", "version"):
            if sub not in agent:
                errors.append(f"Missing required field: 'agent.{sub}'")

    # recipe.{dataset.{reference, size}, methodology, config}
    recipe = _section("recipe")
    if recipe is not None:
        for sub in ("methodology", "config"):
            if sub not in recipe:
                errors.append(f"Missing required field: 'recipe.{sub}'")
        dataset = recipe.get("dataset")
        if "dataset" not in recipe:
            errors.append("Missing required field: 'recipe.dataset'")
        elif not isinstance(dataset, dict):
            errors.append(
                f"Field 'recipe.dataset' must be a mapping, got {type(dataset).__name__}"
            )
        else:
            for sub in ("reference", "size"):
                if sub not in dataset:
                    errors.append(f"Missing required field: 'recipe.dataset.{sub}'")

    # results.{test_cases_run, metrics}
    results = _section("results")
    if results is not None:
        if "test_cases_run" not in results:
            errors.append("Missing required field: 'results.test_cases_run'")
        metrics = results.get("metrics")
        if "metrics" not in results:
            errors.append("Missing required field: 'results.metrics'")
        elif not isinstance(metrics, list) or not metrics:
            errors.append("Field 'results.metrics' must be a non-empty list")
        else:
            for i, metric in enumerate(metrics):
                if not isinstance(metric, dict):
                    errors.append(f"Field 'results.metrics[{i}]' must be a mapping")
                    continue
                for sub in ("name", "value"):
                    if sub not in metric:
                        errors.append(
                            f"Missing required field: 'results.metrics[{i}].{sub}'"
                        )

    # aggregate.{name, formula, value}
    aggregate = _section("aggregate")
    if aggregate is not None:
        for sub in ("name", "formula", "value"):
            if sub not in aggregate:
                errors.append(f"Missing required field: 'aggregate.{sub}'")
        # The gate compares aggregate.value numerically; a non-finite value
        # (NaN/inf) silently passes every `<` regression check, so reject it here.
        if "value" in aggregate:
            value = aggregate["value"]
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                errors.append(
                    f"Field 'aggregate.value' must be a finite number, got {value!r}"
                )

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


def carry_forward(prev_scorecard_path: Path, new_version: str) -> ResultPayload:
    """Carry forward a prior SCORECARD.md's results to a new patch version.

    Reads the single ``SCORECARD.md`` (the agent's one scorecard file, updated
    in place per release), copies all results verbatim, and sets
    ``inherited_from`` to the prior version string recorded in the front matter.

    Only patch bumps are allowed: if the prior scorecard's ``agent.version``
    differs in major or minor from ``new_version``, the caller must re-run the
    eval to generate fresh results.

    Args:
        prev_scorecard_path: Path to the prior ``SCORECARD.md`` file.
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
    prev_scorecard_path = Path(prev_scorecard_path)

    prev_text = prev_scorecard_path.read_text(encoding="utf-8")
    parsed = parse_scorecard(prev_text)

    # Extract prior version from front matter (agent.version)
    agent = parsed.get("agent", {})
    prev_version = str(agent.get("version", ""))
    if not prev_version:
        raise ValueError(
            f"Cannot read prior version from {prev_scorecard_path}: "
            "missing 'agent.version' field in front matter."
        )

    prev_tuple = _semver_tuple(prev_version)
    new_tuple = _semver_tuple(new_version)

    # Only patch bumps are allowed for carry-forward.
    if prev_tuple[0] != new_tuple[0] or prev_tuple[1] != new_tuple[1]:
        raise ValueError(
            f"Cannot carry forward from {prev_version} to {new_version}: "
            f"major or minor version changed. Please re-run the eval to "
            f"generate fresh results for this release."
        )

    # Extract fields from the parsed front matter
    recipe = parsed.get("recipe", {})
    dataset = recipe.get("dataset", {})
    results = parsed.get("results", {})
    metrics_raw = results.get("metrics", [])
    # Carry the optional blocks verbatim too — a patch release must not silently
    # shed the per-category breakdown or the run environment (the docstring/docs
    # promise "carried forward verbatim").
    breakdown = results.get("breakdown")
    environment = recipe.get("environment")
    # The notes block lives in the body (not front matter); recover it from the
    # prior render so a patch release keeps the dataset/example docs verbatim.
    notes = _extract_notes(prev_text)

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
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=prev_version,
        breakdown=breakdown,
        environment=environment,
        notes=notes,
    )
