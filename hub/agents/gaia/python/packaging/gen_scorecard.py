#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Flagship-agent adapter: release scorecard from a ``gaia eval agent`` run.

Reads the scenario harness's ``scorecard.json``, keeps only the scenarios that
actually ran the flagship (each scenario's own ``agent_type`` field — never the
run config, never the filename), and writes ``hub/agents/gaia/npm/SCORECARD.md``.

This module imports ONLY ``gaia.eval.release_scorecard`` — never the eval
harness and never the flagship agent package, so the harness → file → adapter
coupling stays loose.

The metric is an **LLM judge score**, not an accuracy: the harness scores each
scenario 0-10, so a category's value is ``avg_score / 10`` and the aggregate is
named ``weighted_judge_score``. Calling it accuracy would misdescribe it.

Public-artifact hygiene: SCORECARD.md ships in the published npm tarball and
renders on the public hub page, so nothing host-specific may reach it. The
harness ``scorecard.json`` carries a ``backend_url`` (host:port) and full
``agent_response`` transcripts; neither is read here. Hardware is recorded as a
class descriptor, never a hostname, and every path is repo-relative.

Usage::

    PYTHONPATH="$(pwd)/src" \\
    python hub/agents/gaia/python/packaging/gen_scorecard.py \\
        --scorecard-json eval/results/<run-id>/scorecard.json \\
        [--output-dir hub/agents/gaia/npm] \\
        [--ctx-size 65536] [--agent-model Gemma-4-E4B-it-GGUF]
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Optional

# packaging/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root
_PACKAGING_DIR = Path(__file__).resolve().parent
_GAIA_AGENT_ROOT = _PACKAGING_DIR.parent
_REPO_ROOT = _GAIA_AGENT_ROOT.parent.parent.parent.parent
_NPM_ROOT = _REPO_ROOT / "hub" / "agents" / "gaia" / "npm"

_AGENT_MANIFEST = _GAIA_AGENT_ROOT / "gaia-agent.yaml"

# Output filename: one SCORECARD.md per agent package, updated in place.
_OUTPUT_FILENAME = "SCORECARD.md"

# The per-scenario agent id the flagship runs under (gaia-agent.yaml `id:`).
FLAGSHIP_AGENT_TYPE = "gaia"

# Repo-relative dataset pointer — the scenario corpus this run drew from.
_DATASET_REFERENCE = "eval/scenarios/"

# Harness statuses that mean "no measurement happened". A scenario in any of
# these never produced a judge score, so counting it would dilute the card with
# infrastructure noise. Any status starting with SKIPPED is included too
# (SKIPPED_NO_DOCUMENT today; the prefix is the contract).
_NO_MEASUREMENT_STATUSES = frozenset(
    {"INFRA_ERROR", "TIMEOUT", "BUDGET_EXCEEDED", "ERRORED"}
)

# The harness scores each scenario on a 0-10 judge scale.
_JUDGE_SCALE_MAX = 10.0


def _load_run(scorecard_json: Path) -> dict:
    """Read and shape-check the harness ``scorecard.json``.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If it is not valid JSON or carries no ``scenarios`` list.
    """
    if not scorecard_json.is_file():
        raise FileNotFoundError(
            f"Harness scorecard not found: {scorecard_json}\n"
            "Run 'gaia eval agent --category <category> --agent-type gaia' first; "
            "it prints the run directory, and writes <run-dir>/scorecard.json."
        )
    try:
        data = json.loads(scorecard_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(
            f"Cannot read harness scorecard {scorecard_json}: {exc}\n"
            "Expected the JSON written by 'gaia eval agent'. Re-run the eval and "
            "pass the run directory's scorecard.json."
        ) from exc

    scenarios = data.get("scenarios") if isinstance(data, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError(
            f"No 'scenarios' list in {scorecard_json}.\n"
            "This does not look like a 'gaia eval agent' scorecard.json. "
            "Point --scorecard-json at <run-dir>/scorecard.json from a real run."
        )
    return data


def _select_flagship(scenarios: list, scorecard_json: Path) -> list:
    """Return the scenarios whose own ``agent_type`` is the flagship.

    Provenance comes from each scenario entry, never from the run config (which
    records the CLI-level ``--agent-type``, not the resolved per-scenario one)
    and never from the filename. A file where **no** scenario carries an
    ``agent_type`` predates that field and cannot be attributed at all, so it is
    rejected rather than silently treated as flagship.

    Raises:
        ValueError: If the run carries no per-scenario provenance at all.
    """
    with_provenance = [
        s for s in scenarios if isinstance(s, dict) and s.get("agent_type")
    ]
    if not with_provenance:
        raise ValueError(
            f"No scenario in {scorecard_json} carries an 'agent_type' field, so "
            "there is no way to tell which agent produced these results.\n"
            "This is an old-format run, recorded before the harness stamped the "
            "resolved per-scenario agent. Re-run the eval on the current tree "
            "('gaia eval agent --category <category> --agent-type gaia') and use "
            "that run's scorecard.json — a scorecard built from an unattributed "
            "run would claim to measure the flagship without evidence.\n"
            "See src/gaia/eval/runner.py (_resolve_scenario_agent_type)."
        )
    return [s for s in with_provenance if s.get("agent_type") == FLAGSHIP_AGENT_TYPE]


def _is_judged(scenario: dict) -> bool:
    """True iff the scenario produced a real judge score.

    Judged means both: a finite numeric ``overall_score``, and a status that is
    not one of the no-measurement statuses.
    """
    status = str(scenario.get("status", "")).upper()
    if status in _NO_MEASUREMENT_STATUSES or status.startswith("SKIPPED"):
        return False
    score = scenario.get("overall_score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        return False
    return math.isfinite(float(score))


def _category_metrics(judged: list) -> list:
    """One metric per category present among the judged flagship scenarios.

    ``value`` is the category's mean judge score normalised onto [0,1]
    (``avg_score / 10``, clamped); ``weight`` is the number of judged scenarios
    in that category, so a category with more evidence counts for more.

    The means are computed from the filtered scenarios rather than read from
    ``summary.by_category`` — that block pools every agent in the run, so using
    it would fold doc-profile results into a card labelled flagship.
    """
    buckets: dict[str, list] = {}
    for scenario in judged:
        category = str(scenario.get("category") or "uncategorized")
        buckets.setdefault(category, []).append(float(scenario["overall_score"]))

    metrics = []
    for category in sorted(buckets):
        scores = buckets[category]
        value = (sum(scores) / len(scores)) / _JUDGE_SCALE_MAX
        metrics.append(
            {
                "name": category,
                "value": round(min(1.0, max(0.0, value)), 4),
                "weight": float(len(scores)),
            }
        )
    return metrics


def _read_agent_version() -> str:
    """Read ``version:`` from the flagship's gaia-agent.yaml — the one scheme."""
    try:
        import yaml  # noqa: PLC0415  (local import; PyYAML already a dep)

        manifest = yaml.safe_load(_AGENT_MANIFEST.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        raise ValueError(
            f"Cannot read the flagship agent version from {_AGENT_MANIFEST}: {exc}\n"
            "The scorecard version must come from the agent manifest; fix or "
            "restore that file rather than passing a version in by hand."
        ) from exc

    version = str(manifest.get("version", "") or "")
    if not version:
        raise ValueError(
            f"No 'version:' field in {_AGENT_MANIFEST}.\n"
            "Add the agent's semver there — the scorecard never carries a "
            "parallel version scheme."
        )
    return version


def _build_reproduction_command(category_names: list, ctx_size, agent_model) -> str:
    """The exact commands that reproduce this card, repo-relative only."""
    categories = " ".join(f"--category {c}" for c in category_names) or "--category all"
    model_flag = f"    --agent-model {agent_model} \\\n" if agent_model else ""
    return (
        "# Prerequisites: eval extras installed, a running Lemonade Server with\n"
        "# the agent model loaded, and a judge credential for the eval harness.\n"
        'uv pip install -e ".[dev,eval,api]"\n\n'
        "# Terminal 1 - the Agent UI backend the harness drives, on loopback.\n"
        "# PYTHONPATH must point at THIS checkout, or an editable install can\n"
        "# silently serve a different worktree's source.\n"
        'PYTHONPATH="$(pwd)/src" \\\n'
        "python -m gaia.ui.server\n\n"
        "# Terminal 2 - run the eval against the flagship. Run evals SERIALLY:\n"
        "# two concurrent runs race-evict each other's models.\n"
        "export ANTHROPIC_API_KEY=...   # judge credential\n"
        f"# ctx_size was {ctx_size} for this run. It is pinned per device\n"
        "# profile machine-wide (profile_ctx_size in\n"
        "# src/gaia/llm/lemonade_client.py), not by a flag on this command.\n"
        'PYTHONPATH="$(pwd)/src" \\\n'
        f"gaia eval agent {categories} --agent-type gaia\n"
        "# -> prints the run directory; its scorecard.json is the input below.\n\n"
        "# Terminal 2 - regenerate this scorecard from that run.\n"
        'PYTHONPATH="$(pwd)/src" \\\n'
        "python hub/agents/gaia/python/packaging/gen_scorecard.py \\\n"
        f"{model_flag}"
        f"    --ctx-size {ctx_size} \\\n"
        "    --scorecard-json <run-dir>/scorecard.json"
    )


def build_payload(
    scorecard_json: Path,
    ctx_size=None,
    agent_model=None,
    hardware: str = "AMD Ryzen AI (class descriptor, not a host)",
    gaia_commit=None,
):
    """Build a :class:`~gaia.eval.release_scorecard.ResultPayload` from a run.

    Args:
        scorecard_json: ``<run-dir>/scorecard.json`` from ``gaia eval agent``.
        ctx_size: Context window the run was measured under. Read from the run
            config when it records one; otherwise this must be supplied — the
            release gate's ``--require-ctx-match`` reads
            ``recipe.environment.ctx_size`` and skips the regression comparison
            when it is absent.
        agent_model: Model id of the agent under test, if known. The harness
            config records the driver/judge model, not the served one.
        hardware: Hardware **class** descriptor. Never a hostname.
        gaia_commit: Short git commit of the tree the run measured.

    Returns:
        Populated :class:`~gaia.eval.release_scorecard.ResultPayload`.

    Raises:
        ValueError: If the run has no per-scenario provenance, no flagship
            scenarios, zero judged flagship scenarios, or no ctx size.
        FileNotFoundError: If ``scorecard_json`` does not exist.
    """
    from gaia.eval.release_scorecard import ResultPayload

    data = _load_run(scorecard_json)
    scenarios = data["scenarios"]
    run_config = data.get("config") if isinstance(data.get("config"), dict) else {}

    flagship = _select_flagship(scenarios, scorecard_json)
    if not flagship:
        raise ValueError(
            f"No scenario in {scorecard_json} ran agent_type='{FLAGSHIP_AGENT_TYPE}' "
            f"({len(scenarios)} scenario(s) present, all other agents).\n"
            "A per-scenario 'agent_type:' in the scenario YAML overrides the CLI "
            "--agent-type, so a run invoked with --agent-type gaia can still have "
            "executed a different profile end to end. Point the eval at scenarios "
            "that target the flagship, then re-run.\n"
            "See src/gaia/eval/runner.py (_resolve_scenario_agent_type)."
        )

    judged = [s for s in flagship if _is_judged(s)]
    if not judged:
        statuses = sorted({str(s.get("status", "?")) for s in flagship})
        raise ValueError(
            f"Zero judged flagship scenarios in {scorecard_json} — refusing to "
            f"emit a scorecard.\n"
            f"All {len(flagship)} flagship scenario(s) came back with statuses "
            f"{statuses}, which record no measurement (infra error, timeout, "
            "budget exhaustion, skip, or a missing overall_score). A card built "
            "from this run would show a healthy-looking 0.0 that measured "
            "nothing.\n"
            "Fix the run first: check the backend is reachable, the judge "
            "credential is set, and the model is loaded, then re-run "
            "'gaia eval agent --agent-type gaia'."
        )

    metrics = _category_metrics(judged)

    # ctx_size: the flag wins, else whatever the run recorded. Never guessed —
    # the release gate silently skips its regression comparison when the stamp
    # is absent, so a missing value would turn the gate into a no-op.
    resolved_ctx = ctx_size if ctx_size is not None else run_config.get("ctx_size")
    if not isinstance(resolved_ctx, int) or isinstance(resolved_ctx, bool):
        raise ValueError(
            f"No ctx_size for this run: {scorecard_json} records none and no "
            "--ctx-size was passed.\n"
            "Pass --ctx-size <int> with the context window the eval actually ran "
            "under (GPU_CTX_SIZE / NPU_CTX_SIZE in src/gaia/llm/lemonade_client.py). "
            "The release gate reads recipe.environment.ctx_size and skips its "
            "regression comparison when the stamp is missing, so an unstamped "
            "card silently disarms the gate."
        )

    category_names = [m["name"] for m in metrics]
    environment: dict = {
        **({"gaia_commit": gaia_commit} if gaia_commit else {}),
        **({"model": agent_model} if agent_model else {}),
        "ctx_size": resolved_ctx,
        "hardware": hardware,
    }

    # Only host-free provenance from the run config reaches the card: the
    # harness records backend_url (host:port), which must never ship.
    config = {
        "harness": "gaia eval agent",
        "agent_type": FLAGSHIP_AGENT_TYPE,
        "judge_model": run_config.get("model"),
        **({"agent_model": agent_model} if agent_model else {}),
        "categories": category_names,
        "scenario_ids": sorted(str(s.get("scenario_id", "?")) for s in judged),
        "ctx_size": resolved_ctx,
        "run_id": data.get("run_id"),
    }

    return ResultPayload(
        agent_name="GAIA",
        agent_version=_read_agent_version(),
        dataset_reference=_DATASET_REFERENCE,
        dataset_description=(
            "Multi-turn scenario corpus for the GAIA agent eval harness. Each "
            "scenario drives the agent through a scripted conversation and an "
            "LLM judge scores the transcript 0-10 across correctness, tool "
            "selection, context retention, completeness, efficiency, personality "
            "and error recovery."
        ),
        dataset_size=len(flagship),
        methodology=(
            "gaia eval agent over the scenario corpus, filtered to the scenarios "
            "that resolved to agent_type='gaia' (the flagship) — a scenario's own "
            "agent_type overrides the CLI flag, so provenance is read per "
            "scenario, never from the run config. A scenario counts as judged "
            "when it has a finite overall_score and a status that records a real "
            "measurement (INFRA_ERROR / TIMEOUT / BUDGET_EXCEEDED / ERRORED / "
            "SKIPPED* are excluded). Each metric is a category's mean judge score "
            "normalised onto [0,1] (avg_score / 10), weighted by its judged "
            "scenario count. The aggregate is a weighted JUDGE SCORE, not an "
            "accuracy: there is no labelled ground truth here, only a judge's "
            "rubric."
        ),
        config=config,
        test_cases_run=len(judged),
        metrics=metrics,
        aggregate_name="weighted_judge_score",
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=None,
        reproduction_command=_build_reproduction_command(
            category_names, resolved_ctx, agent_model
        ),
        environment=environment,
    )


def _capture_gaia_commit() -> Optional[str]:
    """Short git commit at repo root, or None when git cannot answer."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or None


def main(argv=None) -> int:
    """Generate and write the flagship agent's release scorecard."""
    parser = argparse.ArgumentParser(
        description="Generate a release scorecard for the flagship GAIA agent.",
        prog="gen_scorecard.py",
    )
    parser.add_argument(
        "--scorecard-json",
        required=True,
        help=(
            "Path to <run-dir>/scorecard.json written by 'gaia eval agent' "
            "(the run directory is printed at the end of the eval)."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Override the scorecard output directory "
            f"(default: hub/agents/gaia/npm/, always writes {_OUTPUT_FILENAME})."
        ),
    )
    parser.add_argument(
        "--ctx-size",
        type=int,
        default=None,
        help=(
            "Context window the eval ran under. Required unless the run's config "
            "records one; stamped into recipe.environment.ctx_size, which the "
            "release gate's --require-ctx-match reads."
        ),
    )
    parser.add_argument(
        "--agent-model",
        default=None,
        help=(
            "Model id served to the agent under test (e.g. Gemma-4-E4B-it-GGUF). "
            "The harness config records the driver/judge model, not this one."
        ),
    )
    parser.add_argument(
        "--hardware",
        default="AMD Ryzen AI MAX+ (Strix Halo)",
        help=(
            "Hardware CLASS descriptor for the environment block. Never a "
            "hostname — this file ships in a published npm tarball."
        ),
    )

    args = parser.parse_args(argv)

    try:
        payload = build_payload(
            Path(args.scorecard_json),
            ctx_size=args.ctx_size,
            agent_model=args.agent_model,
            hardware=args.hardware,
            gaia_commit=_capture_gaia_commit(),
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    from gaia.eval.release_scorecard import compute_aggregate, write_scorecard

    out_dir = Path(args.output_dir) if args.output_dir else _NPM_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _OUTPUT_FILENAME
    write_scorecard(payload, out_path)

    _components, aggregate = compute_aggregate(payload.metrics)
    print(
        f"Scorecard written: {out_path}\n"
        f"  Version: {payload.agent_version}\n"
        f"  Aggregate ({payload.aggregate_name}): {aggregate}\n"
        f"  Judged: {payload.test_cases_run} of {payload.dataset_size} flagship "
        f"scenario(s) across {len(payload.metrics)} categor(ies)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
