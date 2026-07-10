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
    GAIA_AGENT_TOOL_TIMEOUT=1800 \\
    PYTHONPATH="$(pwd)" \\
    python hub/agents/python/email/packaging/gen_scorecard.py \\
        --benchmark-dir /tmp/email-eval \\
        [--ground-truth tests/fixtures/email/ground_truth.json] \\
        [--limit N]   # the --limit passed to the benchmark for this run

The ``--ground-truth`` path defaults to the canonical fixture in the repository.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

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


def _load_quality_aggregate(benchmark_dir: Path) -> Optional[dict]:
    """Read the harness's aggregate acceptance block (``quality.json``) if present.

    ``gaia eval benchmark`` writes ``quality.json`` (the aggregate within-one /
    urgent-vs-not / urgent-recall metrics + run-to-run variance/CI) alongside
    ``scorecard.json``. The adapter consumes this file — never the harness module —
    so the harness→file→adapter loose coupling holds. Returns ``None`` when the
    file is absent (older runs); the caller then derives the metrics from the
    per-scenario quality blocks.
    """
    p = benchmark_dir / "quality.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"quality.json present in {benchmark_dir} but unreadable: {exc}. "
            f"Re-run 'gaia eval benchmark --output-dir {benchmark_dir}'."
        ) from exc


def _mean_scenario_metric(judged: list, key: str) -> Optional[float]:
    """Mean of a per-scenario quality metric across judged runs (None if absent).

    Each run scores the same corpus, so the cross-run mean is the aggregate — the
    same value :func:`gaia.eval.benchmark._aggregate_quality` records.
    """
    vals = [
        float(s["quality"][key])
        for s in judged
        if isinstance(s.get("quality"), dict)
        and isinstance(s["quality"].get(key), (int, float))
    ]
    return round(sum(vals) / len(vals), 4) if vals else None


def _compute_breakdown(judged: list) -> Optional[dict]:
    """Aggregate per-category accuracy and top confusion pairs across judged scenarios.

    Only scenarios carrying ``quality.categorization.rows`` contribute. Returns
    ``None`` if no scenario has rows (e.g. older benchmark runs without this field).

    Args:
        judged: List of judged scenario dicts (already filtered by :func:`_is_judged`).

    Returns:
        Dict with ``per_category`` (sorted by name) and ``top_confusions`` (top 5),
        or ``None`` when no rows are present.
    """
    # Aggregate (expected, predicted) pairs across all scenarios with rows.
    cat_totals: dict[str, int] = {}
    cat_correct: dict[str, int] = {}
    confusion_counts: dict[tuple[str, str], int] = {}

    has_rows = False
    for scenario in judged:
        rows = scenario.get("quality", {}).get("categorization", {}).get("rows", [])
        if not rows:
            continue
        has_rows = True
        for row in rows:
            expected = str(row.get("expected", "")).strip().lower()
            predicted = str(row.get("predicted", "")).strip().lower()
            if not expected:
                continue
            cat_totals[expected] = cat_totals.get(expected, 0) + 1
            if row.get("category_correct"):
                cat_correct[expected] = cat_correct.get(expected, 0) + 1
            if predicted != expected:
                pair = (expected, predicted)
                confusion_counts[pair] = confusion_counts.get(pair, 0) + 1

    if not has_rows:
        return None

    per_category = sorted(
        [
            {
                "category": cat,
                "total": cat_totals[cat],
                "correct": cat_correct.get(cat, 0),
                "accuracy": round(cat_correct.get(cat, 0) / cat_totals[cat], 4),
            }
            for cat in cat_totals
        ],
        key=lambda r: r["category"],
    )

    top_confusions = sorted(
        [
            {"expected": exp, "predicted": pred, "count": cnt}
            for (exp, pred), cnt in confusion_counts.items()
        ],
        key=lambda c: -c["count"],
    )[:5]

    return {"per_category": per_category, "top_confusions": top_confusions}


def _build_reproduction_command(model, ground_truth_rel: str, limit=None) -> str:
    """Build the exact, portable shell recipe that reproduces this scorecard.

    Repo-relative paths and a generic output dir only — never a local absolute
    path (this ships in a published artifact).
    """
    limit_flag = f" \\\n    --limit {limit}" if limit is not None else ""
    return (
        "# Prerequisites: install the eval extras and start a Lemonade Server\n"
        "# with the model on AMD Ryzen AI hardware (Strix Halo recommended).\n"
        'uv pip install -e ".[dev,eval,api]"\n'
        "lemonade-server serve   # in a separate shell; must stay running\n\n"
        "# Step 0: build the corpus from the committed seed. The mbox +\n"
        "# ground_truth are GENERATED artifacts (gitignored), so a fresh\n"
        "# checkout must materialise them before the benchmark can read them.\n"
        "python tests/fixtures/email/generate_mbox.py\n\n"
        "# Step 1: run the benchmark (requires the Lemonade Server above with the\n"
        "# model loaded; AMD Ryzen AI / Strix Halo recommended)\n"
        "PYTHON_KEYRING_BACKEND=keyring.backends.null.Keyring \\\n"
        # Full-corpus triage is one tool call (~17 min on a 4B local model); a
        # lower timeout abandons it mid-run and scores 0 emails.
        "GAIA_AGENT_TOOL_TIMEOUT=1800 \\\n"
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
        + "\n\n# Background, dataset details, a worked example, and metric\n"
        "# definitions: see EVALUATION.md (next to this scorecard)."
    )


def _load_draft_approval_rate(drafting_report: Path) -> float:
    """Read ``summary.drafting.draft_approval_rate`` from a drafting gate report.

    A drafting report passed here must be a real judged run — ``eval_drafting_report.py``
    hard-fails (exit 1) rather than emitting a skip report, so there is no silent
    "skipped" path (CLAUDE.md: No Silent Fallbacks). Fails loud on a legacy
    ``skipped`` marker or any report missing the rate — never silently omits the
    metric.
    """
    data = json.loads(drafting_report.read_text(encoding="utf-8"))
    if data.get("skipped"):
        raise ValueError(
            f"Drafting report {drafting_report} is marked skipped, but the judged "
            f"drafting eval must not skip (ANTHROPIC_API_KEY is required and "
            f"eval_drafting_report.py exits 1 when it is absent). Re-run "
            f"eval_drafting_report.py with ANTHROPIC_API_KEY set."
        )
    rate = data.get("summary", {}).get("drafting", {}).get("draft_approval_rate")
    if rate is None:
        raise ValueError(
            f"No summary.drafting.draft_approval_rate in {drafting_report} "
            f"(judged run expected). Re-run eval_drafting_report.py with "
            f"ANTHROPIC_API_KEY set."
        )
    return float(rate)


def build_payload(
    benchmark_dir: Path,
    ground_truth_path: Path,
    limit=None,
    environment=None,
    drafting_report=None,
):
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
        environment: Optional dict of environment metadata (commit, version, model,
            hardware, …). Embedded verbatim in the payload; assembled by ``main()``.

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

    # Each judged scenario is one experiment over the SAME corpus, so test cases
    # run = the per-run email count (NOT summed across experiments — that would
    # conflate runs with cases and inflate the count n_runs-fold). n_runs is
    # recorded distinctly so a reader sees both.
    per_run_emails = [int(s.get("total_emails", 0)) for s in judged]
    test_cases_run = max(per_run_emails) if per_run_emails else 0
    if test_cases_run <= 0:
        raise ValueError(
            f"Judged scenarios in {scorecard_path} report no emails "
            f"(total_emails={per_run_emails}); cannot compute a per-email "
            f"accuracy. Check the benchmark output."
        )
    n_runs = len(judged)

    # Acceptance metrics (#1437): within-one-bucket is the GATED aggregate;
    # urgent-vs-not, urgent-recall, and exact category accuracy are reported.
    # Prefer the harness aggregate (quality.json — carries variance/CI #1894);
    # fall back to per-scenario means; fail loud if the metric is absent entirely
    # (an old benchmark run predating the acceptance metric — never silently
    # default to exact-only).
    quality_agg = _load_quality_aggregate(benchmark_dir)
    acceptance_variance = None
    if quality_agg is not None and "within_one_bucket_accuracy" in quality_agg:
        within_one_accuracy = float(quality_agg["within_one_bucket_accuracy"])
        urgent_vs_not_accuracy = float(quality_agg.get("urgent_vs_not_accuracy", 0.0))
        urgent_recall = float(quality_agg.get("urgent_recall", 0.0))
        personal_recall = float(quality_agg.get("personal_recall", 0.0))
        category_accuracy = float(quality_agg.get("category_accuracy", 0.0))
        acceptance_variance = quality_agg.get("acceptance_variance")
    else:
        within_one_accuracy = _mean_scenario_metric(
            judged, "within_one_bucket_accuracy"
        )
        if within_one_accuracy is None:
            raise ValueError(
                f"No acceptance metric (within_one_bucket_accuracy) in {benchmark_dir}.\n"
                f"This benchmark output predates the acceptance metric (#1437). "
                f"Re-run 'gaia eval benchmark --output-dir {benchmark_dir}' with the "
                f"current harness, which writes quality.json + per-scenario acceptance "
                f"fields."
            )
        urgent_vs_not_accuracy = (
            _mean_scenario_metric(judged, "urgent_vs_not_accuracy") or 0.0
        )
        urgent_recall = _mean_scenario_metric(judged, "urgent_recall") or 0.0
        personal_recall = _mean_scenario_metric(judged, "personal_recall") or 0.0
        category_accuracy = _mean_scenario_metric(judged, "category_accuracy") or 0.0

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
        raise ValueError(f"No 'version:' field found in {agent_yaml_path}.")

    # Model id: benchmark output records it as the per-scenario `category`.
    # Fall back to the manifest's first declared model.
    scenario_model = scenarios[0].get("category") if scenarios else None
    manifest_models = agent_data.get("models") or [None]
    model = scenario_model or manifest_models[0]

    # within_one_bucket_accuracy is the gated aggregate (weight 1.0). The rest are
    # DISPLAYED but weight 0.0 — shown on the card, excluded from the aggregate
    # formula so aggregate.value stays recomputable from the displayed metrics
    # (#1862) and equals 100 × within_one.
    metrics = [
        {
            "name": "within_one_bucket_accuracy",
            "value": float(within_one_accuracy),
            "weight": 1.0,
        },
        {
            "name": "urgent_vs_not_accuracy",
            "value": float(urgent_vs_not_accuracy),
            "weight": 0.0,
        },
        {"name": "urgent_recall", "value": float(urgent_recall), "weight": 0.0},
        {"name": "personal_recall", "value": float(personal_recall), "weight": 0.0},
        {
            "name": "category_accuracy",
            "value": float(category_accuracy),
            "weight": 0.0,
        },
    ]
    # Fold the judge-scored voice-drafting result in as a REPORTED metric
    # (weight 0) when a drafting report is supplied — visible on the card without
    # changing the aggregate (still 100 x within_one). Blocking on a drafting
    # regression is the drafting gate's job (enforce:true), not the aggregate's.
    if drafting_report is not None:
        # A judged run always yields a real rate; _load_draft_approval_rate raises
        # loudly otherwise (no silent omit).
        draft_rate = _load_draft_approval_rate(Path(drafting_report))
        metrics.append(
            {
                "name": "draft_approval_rate",
                "value": float(draft_rate),
                "weight": 0.0,
            }
        )
    compute_aggregate(
        metrics
    )  # validate metrics; aggregate embedded in render_scorecard

    import datetime

    # Construct a portable, exact reproduction command so any reader can reproduce
    # this scorecard from scratch.
    ground_truth_rel = (
        str(ground_truth_path.relative_to(_REPO_ROOT))
        if str(ground_truth_path).startswith(str(_REPO_ROOT))
        else ground_truth_path.name
    )
    reproduction_command = _build_reproduction_command(model, ground_truth_rel, limit)

    breakdown = _compute_breakdown(judged)

    return ResultPayload(
        agent_name="Email Triage",
        agent_version=version,
        dataset_reference="tests/fixtures/email/ground_truth.json",
        dataset_description=(
            "Vendor-derived labelled email corpus for GAIA email-triage "
            "evaluation (FakeGmailBackend, schema-2.0 triage taxonomy: "
            "urgent / needs_response / fyi / promotional / personal); a "
            "deterministic, category-balanced subset of the vendor mailbox dataset"
        ),
        dataset_size=dataset_size,
        methodology=(
            "gaia eval benchmark over the vendor-derived labelled corpus via "
            "FakeGmailBackend; no LLM judge. The full 249-email corpus is scored "
            "(GAIA_EMAIL_TRIAGE_MAX_MESSAGES lifts the interactive per-call scan "
            "cap for the eval so the whole balanced corpus is covered). Aggregate "
            "= within-one-bucket ACCEPTANCE accuracy (#1437): triage priority is "
            "ordinal (URGENT>NEEDS_RESPONSE>FYI>PROMOTIONAL), so a prediction is "
            "credited when it is exact or an adjacent bucket (|rank diff|<=1) — "
            "what users feel (nothing urgent buried). Reported secondaries (not in "
            "the aggregate): urgent-vs-not binary accuracy, urgent recall "
            "(anti-gaming floor), and exact 4-way category_accuracy. The corpus "
            "uses the schema-2.0 taxonomy aligned with the agent's output labels "
            f"(#1874); averaged over {n_runs} run(s) for run-to-run variance/CI "
            "(#1894)"
        ),
        config={
            "harness": "gaia eval benchmark",
            "model": model,
            "corpus": "tests/fixtures/email/synthetic_inbox.mbox",
            # Store a repo-relative path — never leak a local absolute path into
            # a committed/published artifact.
            "ground_truth": ground_truth_rel,
            "limit": limit,
            "n_runs": n_runs,
            # Run-to-run variance/CI on the acceptance metrics (#1894). Additive —
            # never affects aggregate.value (which is the within-one mean).
            "acceptance_variance": acceptance_variance,
        },
        test_cases_run=test_cases_run,
        metrics=metrics,
        aggregate_name="weighted_accuracy",
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=None,
        reproduction_command=reproduction_command,
        breakdown=breakdown,
        environment=environment,
    )


def _capture_gaia_commit() -> str:
    """Return the short git commit hash at repo root.

    Raises:
        RuntimeError: If git is unavailable or the repo root cannot be resolved.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"Cannot determine gaia_commit: git failed in {_REPO_ROOT}: {exc}. "
            "Ensure git is installed and the working tree is inside a git repository."
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Cannot determine gaia_commit: 'git rev-parse --short HEAD' exited "
            f"{result.returncode} in {_REPO_ROOT}: {result.stderr.strip()}. "
            "Ensure the working tree is inside a git repository."
        )
    return result.stdout.strip()


def _query_lemonade_version(base_url: str) -> str:
    """Query the Lemonade Server health endpoint and return its version string.

    Args:
        base_url: Base URL of the running Lemonade Server, e.g.
            ``http://localhost:13305``.

    Returns:
        Version string from ``/api/v1/health``.

    Raises:
        RuntimeError: If the endpoint is unreachable or the response lacks a version.
    """
    url = base_url.rstrip("/") + "/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Cannot determine lemonade_version: health endpoint unreachable at "
            f"{url}: {exc}. "
            "Start the Lemonade Server or pass --lemonade-version explicitly."
        ) from exc
    version = data.get("version") if isinstance(data, dict) else None
    if not version:
        raise RuntimeError(
            f"Cannot determine lemonade_version: health endpoint at {url} "
            f"returned no 'version' field (response: {data!r}). "
            "Pass --lemonade-version explicitly."
        )
    return str(version)


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
    parser.add_argument(
        "--lemonade-version",
        default=None,
        help=(
            "Lemonade Server version used for this run (e.g. '10.8.0'). "
            "If omitted, queried from the running server's /api/v1/health endpoint. "
            "The server URL defaults to LEMONADE_BASE_URL or http://localhost:13305."
        ),
    )
    parser.add_argument(
        "--hardware",
        default="AMD Ryzen AI MAX+ (Strix Halo)",
        help=(
            "Hardware class descriptor recorded in the environment block "
            "(default: 'AMD Ryzen AI MAX+ (Strix Halo)'). "
            "Use a class description, never a hostname."
        ),
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="Sampling temperature used for the eval run, if applicable.",
    )
    parser.add_argument(
        "--drafting-report",
        default=None,
        help=(
            "Path to eval-out/drafting_gate_report.json (from "
            "eval_drafting_report.py, a judged run). Folds draft_approval_rate "
            "into the scorecard as a reported metric (weight 0); a missing/"
            "unjudged report fails loudly, never silently omits. Blocking on a "
            "drafting regression is the drafting gate's job (enforce:true), not "
            "the aggregate's."
        ),
    )

    args = parser.parse_args(argv)

    benchmark_dir = Path(args.benchmark_dir).resolve()
    gt_path = Path(args.ground_truth).resolve()

    # Capture the model id early so we can include it in the environment block.
    # build_payload resolves the model from the benchmark output; we replicate the
    # same lightweight read here to avoid splitting build_payload's pure interface.
    try:
        _sc_path = _find_benchmark_scorecard(benchmark_dir)
        _sc_data = json.loads(_sc_path.read_text(encoding="utf-8"))
        _scenarios = _sc_data.get("scenarios", [])
        _model = _scenarios[0].get("category") if _scenarios else None
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        _model = None

    # Resolve lemonade_version: flag wins, then live query.
    lemonade_version: Optional[str] = args.lemonade_version
    if not lemonade_version:
        base_url = os.environ.get("LEMONADE_BASE_URL", "http://localhost:13305")
        try:
            lemonade_version = _query_lemonade_version(base_url)
        except RuntimeError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # Capture the git commit at repo root — always required.
    try:
        gaia_commit = _capture_gaia_commit()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    environment: dict = {
        "gaia_commit": gaia_commit,
        "lemonade_version": lemonade_version,
        **({"model": _model} if _model else {}),
        "hardware": args.hardware,
    }
    if args.temperature is not None:
        environment["temperature"] = args.temperature

    try:
        payload = build_payload(
            benchmark_dir,
            gt_path,
            limit=args.limit,
            environment=environment,
            drafting_report=args.drafting_report,
        )
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
        f"  Aggregate: {payload.metrics[0]['value']:.4f} {payload.metrics[0]['name']} "
        f"({payload.test_cases_run} emails judged)\n"
        f"  Dataset size: {payload.dataset_size} labeled examples"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
