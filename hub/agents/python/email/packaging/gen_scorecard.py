#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Email-agent adapter: generate a release scorecard from a ``gaia eval benchmark`` run.

Reads the benchmark ``--output-dir`` (looks for a JSON file containing a
``scenarios`` key — ``scorecard.json`` in a real run, or any ``*scorecard*.json``
fixture) and the ground-truth JSON, builds a :class:`ResultPayload`, and writes the
scorecard to ``hub/agents/npm/agent-email/SCORECARD.md`` (a single file, updated
in place — versioned via the publish snapshot, the same way README.md works).

This adapter imports ``gaia.eval.release_scorecard`` (core generator) but never
imports the eval harness (``gaia.eval.benchmark``) or the email-agent package —
the loose-coupling spine is preserved.

Usage::

    PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \\
    GAIA_AGENT_TOOL_TIMEOUT=120 \\
    PYTHONPATH="$(pwd)" \\
    python hub/agents/python/email/packaging/gen_scorecard.py \\
        --benchmark-dir /tmp/email-eval \\
        [--ground-truth tests/fixtures/email/ground_truth.json] \\
        [--limit 25]

The ``--ground-truth`` path defaults to the canonical fixture in the repository.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Derive repo root the same way stamp_version.py does:
# packaging/ -> email/ -> python/ -> agents/ -> hub/ -> repo root
_PACKAGING_DIR = Path(__file__).resolve().parent
_EMAIL_ROOT = _PACKAGING_DIR.parent
_REPO_ROOT = _EMAIL_ROOT.parent.parent.parent.parent
_NPM_ROOT = _REPO_ROOT / "hub" / "agents" / "npm" / "agent-email"

# Default ground-truth path
_DEFAULT_GT = _REPO_ROOT / "tests" / "fixtures" / "email" / "ground_truth.json"

# Canonical benchmark scorecard filename (written by gaia eval benchmark)
_SCORECARD_FILENAME = "scorecard.json"

# Output filename: single SCORECARD.md per agent package, updated in place.
_OUTPUT_FILENAME = "SCORECARD.md"


def _find_benchmark_scorecard(benchmark_dir: Path) -> Path:
    """Locate the benchmark scorecard JSON in ``benchmark_dir``.

    Looks first for the canonical ``scorecard.json``, then for any ``*.json``
    file whose parsed content contains a ``scenarios`` key. Raises loudly if
    none is found or if multiple ambiguous files match.

    Args:
        benchmark_dir: Directory written by ``gaia eval benchmark --output-dir``.

    Returns:
        Path to the benchmark scorecard JSON file.

    Raises:
        FileNotFoundError: If ``benchmark_dir`` does not exist.
        ValueError: If no suitable scorecard JSON is found in the directory.
    """
    if not benchmark_dir.is_dir():
        raise FileNotFoundError(
            f"Benchmark directory not found: {benchmark_dir}\n"
            f"Run 'gaia eval benchmark --output-dir <dir>' first."
        )

    # Try the canonical name first
    canonical = benchmark_dir / _SCORECARD_FILENAME
    if canonical.exists():
        return canonical

    # Scan for any JSON containing a 'scenarios' key
    matches: list[Path] = []
    for p in sorted(benchmark_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "scenarios" in data:
                matches.append(p)
        except (json.JSONDecodeError, OSError):
            continue

    if not matches:
        raise ValueError(
            f"No benchmark scorecard JSON found in {benchmark_dir}.\n"
            f"Expected '{_SCORECARD_FILENAME}' (written by 'gaia eval benchmark'), "
            f"or any JSON file with a 'scenarios' key.\n"
            f"Run 'gaia eval benchmark --output-dir {benchmark_dir}' to generate it."
        )

    if len(matches) > 1:
        paths = ", ".join(str(p) for p in matches)
        raise ValueError(
            f"Ambiguous benchmark scorecard: multiple JSON files with a 'scenarios' "
            f"key found in {benchmark_dir}: {paths}.\n"
            f"Remove all but '{_SCORECARD_FILENAME}' and retry."
        )

    return matches[0]


def _is_judged(scenario: dict) -> bool:
    """Return True if a scenario has a valid category_accuracy in [0,1]."""
    quality = scenario.get("quality")
    if not isinstance(quality, dict):
        return False
    acc = quality.get("category_accuracy")
    if acc is None:
        return False
    try:
        import math

        f = float(acc)
    except (TypeError, ValueError):
        return False
    return 0.0 <= f <= 1.0 and math.isfinite(f)


def build_payload(benchmark_dir: Path, ground_truth_path: Path, limit=None):
    """Build a :class:`~gaia.eval.release_scorecard.ResultPayload` from benchmark output.

    A scenario is **judged** iff it has a ``quality`` dict AND
    ``quality.category_accuracy`` is a finite float in [0, 1]. Non-judged
    scenarios (missing ``quality`` or invalid accuracy) are skipped.

    Args:
        benchmark_dir: Directory written by ``gaia eval benchmark --output-dir``.
        ground_truth_path: Path to ``ground_truth.json`` (the labeled corpus).
        limit: The ``--limit`` value used for the eval run, recorded in
            ``config["limit"]`` for cross-version comparability. The benchmark
            ``scorecard.json`` does not persist this, so it must be passed in.

    Returns:
        Populated :class:`~gaia.eval.release_scorecard.ResultPayload`.

    Raises:
        ValueError: If zero scenarios are judged (likely missing ``--ground-truth``
            or a benchmark run that produced no quality metrics).
        FileNotFoundError: If required files are not found.
    """
    # Import here (not at module top) so tests that import build_payload before
    # gaia is installed in the test environment fail at call time, not import time.
    from gaia.eval.release_scorecard import ResultPayload, compute_aggregate

    scorecard_path = _find_benchmark_scorecard(benchmark_dir)
    data = json.loads(scorecard_path.read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", [])

    # Separate judged from non-judged scenarios
    judged = [s for s in scenarios if _is_judged(s)]

    if not judged:
        raise ValueError(
            f"Zero judged scenarios in {scorecard_path}.\n"
            f"Possible causes: benchmark ran without '--ground-truth', "
            f"or no scenario produced a category_accuracy metric.\n"
            f"Benchmark dir: {benchmark_dir}"
        )

    # Aggregate metrics from judged scenarios
    category_accuracy = sum(
        s["quality"]["category_accuracy"] for s in judged
    ) / len(judged)

    test_cases_run = sum(int(s.get("total_emails", 0)) for s in judged)

    # Dataset size = labeled entries in ground_truth.json (excluding _meta key)
    if not ground_truth_path.exists():
        raise FileNotFoundError(
            f"Ground truth not found: {ground_truth_path}\n"
            f"Pass --ground-truth <path> pointing to the labeled corpus JSON."
        )
    ground_truth = json.loads(ground_truth_path.read_text(encoding="utf-8"))
    dataset_size = len(ground_truth) - (1 if "_meta" in ground_truth else 0)

    # Read version from gaia-agent.yaml
    agent_yaml_path = _EMAIL_ROOT / "gaia-agent.yaml"
    try:
        import yaml  # noqa: PLC0415  (local import; PyYAML already a dep)

        agent_data = yaml.safe_load(agent_yaml_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ValueError(
            f"Cannot read agent version from {agent_yaml_path}: {exc}"
        ) from exc

    version = str(agent_data.get("version", ""))
    if not version:
        raise ValueError(
            f"No 'version:' field found in {agent_yaml_path}."
        )

    # Model id: benchmark output records it as the per-scenario `category`.
    # Fall back to the manifest's first declared model.
    scenario_model = scenarios[0].get("category") if scenarios else None
    manifest_models = agent_data.get("models") or [None]
    model = scenario_model or manifest_models[0]

    metrics = [
        {"name": "category_accuracy", "value": float(category_accuracy), "weight": 1.0}
    ]
    compute_aggregate(metrics)  # validate metrics; aggregate embedded in render_scorecard

    import datetime

    # Construct a portable, exact reproduction command so any reader can reproduce
    # this scorecard from scratch. Use repo-relative paths and a generic output dir
    # only — never a local absolute path (this ships in a published artifact).
    limit_flag = f" \\\n    --limit {limit}" if limit is not None else ""
    ground_truth_rel = (
        str(ground_truth_path.relative_to(_REPO_ROOT))
        if str(ground_truth_path).startswith(str(_REPO_ROOT))
        else ground_truth_path.name
    )
    reproduction_command = (
        "# Step 1: run the benchmark (requires a Lemonade Server with the model "
        "loaded; AMD Ryzen AI / Strix Halo recommended)\n"
        "PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \\\n"
        "GAIA_AGENT_TOOL_TIMEOUT=900 \\\n"
        'PYTHONPATH="$(pwd)" \\\n'
        "gaia eval benchmark \\\n"
        f"    --model {model} \\\n"
        "    --mbox-path tests/fixtures/email/synthetic_inbox.mbox \\\n"
        f"    --ground-truth {ground_truth_rel}{limit_flag} \\\n"
        "    --output-dir /tmp/email-eval\n\n"
        "# Step 2: generate this scorecard from the benchmark output\n"
        'PYTHONPATH="$(pwd)" \\\n'
        "python hub/agents/python/email/packaging/gen_scorecard.py \\\n"
        "    --benchmark-dir /tmp/email-eval \\\n"
        f"    --ground-truth {ground_truth_rel}"
        + (f"{limit_flag}" if limit is not None else "")
    )

    return ResultPayload(
        agent_name="Email Triage",
        agent_version=version,
        dataset_reference="tests/fixtures/email/ground_truth.json",
        dataset_description=(
            "Synthetic email corpus for GAIA email-triage evaluation "
            "(FakeGmailBackend, schema-2.0 triage taxonomy: "
            "fyi / needs_response / promotional / urgent / personal)"
        ),
        dataset_size=dataset_size,
        methodology=(
            "gaia eval benchmark — category classification accuracy "
            "(case-insensitive exact match of the agent's triage label vs the "
            "ground-truth label) over a synthetic labeled corpus via "
            "FakeGmailBackend; no LLM judge. The corpus uses the schema-2.0 "
            "triage taxonomy, aligned with the agent's output labels (#1874)"
        ),
        config={
            "harness": "gaia eval benchmark",
            "model": model,
            "corpus": "tests/fixtures/email/synthetic_inbox.mbox",
            # Store a repo-relative path — never leak a local absolute path into
            # a committed/published artifact.
            "ground_truth": ground_truth_rel,
            "limit": limit,
        },
        test_cases_run=test_cases_run,
        metrics=metrics,
        aggregate_name="weighted_accuracy",
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=None,
        reproduction_command=reproduction_command,
    )


def main(argv=None) -> int:
    """Generate and write the email-agent scorecard."""
    parser = argparse.ArgumentParser(
        description="Generate a release scorecard for the email-triage agent.",
        prog="gen_scorecard.py",
    )
    parser.add_argument(
        "--benchmark-dir",
        required=True,
        help=(
            "Directory written by 'gaia eval benchmark --output-dir <dir>' "
            "(must contain scorecard.json)."
        ),
    )
    parser.add_argument(
        "--ground-truth",
        default=str(_DEFAULT_GT),
        help=(
            f"Path to ground_truth.json (default: {_DEFAULT_GT.relative_to(_REPO_ROOT)})"
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Override the scorecard output directory "
            f"(default: hub/agents/npm/agent-email/, writes {_OUTPUT_FILENAME})."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help=(
            "The --limit value passed to 'gaia eval benchmark' for this run. "
            "Recorded in config.limit for cross-version comparability "
            "(the benchmark output does not persist it)."
        ),
    )

    args = parser.parse_args(argv)

    benchmark_dir = Path(args.benchmark_dir).resolve()
    gt_path = Path(args.ground_truth).resolve()

    try:
        payload = build_payload(benchmark_dir, gt_path, limit=args.limit)
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    from gaia.eval.release_scorecard import write_scorecard

    if args.output_dir:
        out_dir = Path(args.output_dir)
    else:
        out_dir = _NPM_ROOT

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _OUTPUT_FILENAME
    write_scorecard(payload, out_path)

    print(
        f"Scorecard written: {out_path}\n"
        f"  Version: {payload.agent_version}\n"
        f"  Aggregate: {payload.metrics[0]['value']:.4f} category_accuracy "
        f"({payload.test_cases_run} emails judged)\n"
        f"  Dataset size: {payload.dataset_size} labeled examples"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
