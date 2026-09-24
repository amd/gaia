#!/usr/bin/env python3
# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Gaia-agent adapter: generate a release scorecard from a ``gaia eval agent`` run.

Reads the run directory the eval runner printed (``Output: <run-dir>`` — it
contains ``scorecard.json``) — or a collection directory holding several
per-category run scorecards (the CI ``eval-out/<category>/scorecard.json``
layout), which are merged into one combined run — builds a
:class:`~gaia.eval.release_scorecard.ResultPayload`, and writes the scorecard to
``hub/agents/gaia/npm/SCORECARD.md`` (a single file, updated in place —
versioned via the publish snapshot, the same way README.md works).

Aggregate = ``judged_pass_rate`` (weight 1.0) across every judged scenario
(PASS / FAIL / BLOCKED_BY_ARCHITECTURE). The judge's 0–10 ``avg_score``
(normalized to [0,1]) and the per-category pass rates are DISPLAYED at weight
0.0 — on the card, excluded from the aggregate, so ``aggregate.value`` stays
recomputable as 100 × judged_pass_rate.

This adapter imports ``gaia.eval.release_scorecard`` (core generator) but never
imports the eval harness (``gaia.eval.runner``) or the gaia-agent package — the
loose-coupling spine is preserved (same rule as the email adapter).

Usage::

    python hub/agents/gaia/python/packaging/gen_scorecard.py \\
        --run-dir <run-dir-printed-by-the-eval> \\
        --model Gemma-4-E4B-it-GGUF \\
        --ctx-size 65536 \\
        --hardware "AMD Ryzen AI MAX+ (Strix Halo)"

``--model`` is the AGENT's model (the model under test). It cannot be derived
from the run scorecard: the scorecard's ``config.model`` is the eval
driver/judge model (``claude -p``), which is stamped as ``eval_model``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Derive repo root the same way stamp_version.py does:
# packaging/ -> python/ -> gaia/ -> agents/ -> hub/ -> repo root
_PACKAGING_DIR = Path(__file__).resolve().parent
_GAIA_ROOT = _PACKAGING_DIR.parent
_REPO_ROOT = _GAIA_ROOT.parent.parent.parent.parent
_NPM_ROOT = _REPO_ROOT / "hub" / "agents" / "gaia" / "npm"

# Canonical run scorecard filename (written by gaia eval agent)
_SCORECARD_FILENAME = "scorecard.json"

# Output filename: single SCORECARD.md per agent package, updated in place.
_OUTPUT_FILENAME = "SCORECARD.md"

# Scenario corpus glob (dataset_size = every committed gaia_* scenario).
_SCENARIO_GLOB = "eval/scenarios/gaia_*/*.yaml"

# Statuses the eval agent actually judged — mirrors
# gaia.eval.scorecard._JUDGED_STATUSES (kept as a literal so this adapter never
# imports the harness; test_gaia_scorecard_adapter.py pins the two in sync).
_JUDGED_KEYS = ("passed", "failed", "blocked")


def _load_run_scorecard(run_dir: Path) -> dict:
    """Read ``scorecard.json`` file(s) under ``run_dir`` — merged if several.

    A single-run dir (one ``scorecard.json``) is returned as-is. A collection
    dir (the CI ``eval-out/<category>/scorecard.json`` layout from serial
    per-category runs) is merged into one scorecard: counts sum, categories
    union (a category present twice is ambiguous and fails), avg_score is
    re-pooled weighted by each card's judged count.

    Raises:
        FileNotFoundError: If the run dir has no scorecard.json anywhere.
        ValueError: If a JSON lacks the ``gaia eval agent`` scorecard shape,
            the cards disagree on their run config, or a category repeats.
    """
    if not run_dir.is_dir():
        raise FileNotFoundError(
            f"Eval run directory not found: {run_dir}\n"
            f"Run 'gaia eval agent --agent-type gaia ...' first and pass the "
            f"absolute run dir it prints on its 'Output:' line."
        )
    paths = sorted(run_dir.rglob(_SCORECARD_FILENAME))
    if not paths:
        raise FileNotFoundError(
            f"No {_SCORECARD_FILENAME} under {run_dir}.\n"
            f"The eval runner writes it at the end of a run; an interrupted run "
            f"has none. Re-run 'gaia eval agent --agent-type gaia ...'."
        )
    cards = []
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or "summary" not in data
            or "scenarios" not in data
        ):
            raise ValueError(
                f"{path} is not a 'gaia eval agent' scorecard (missing "
                f"'summary'/'scenarios'). Pass the run dir of an agent-eval "
                f"run, not a 'gaia eval benchmark' output."
            )
        cards.append(data)
    if len(cards) == 1:
        return cards[0]
    return _merge_scorecards(cards, run_dir)


# Count keys summed verbatim when merging per-category run scorecards.
_SUM_KEYS = (
    "total_scenarios",
    "passed",
    "failed",
    "blocked",
    "timeout",
    "budget_exceeded",
    "infra_error",
    "skipped",
    "errored",
)


def _merge_scorecards(cards: list, run_dir: Path) -> dict:
    """Merge serial per-category run scorecards into one combined scorecard."""
    ref_config = {
        k: cards[0].get("config", {}).get(k)
        for k in ("model", "agent_type", "budget_per_scenario_usd")
    }
    for card in cards[1:]:
        got = {k: card.get("config", {}).get(k) for k in ref_config}
        if got != ref_config:
            raise ValueError(
                f"Cannot merge scorecards under {run_dir}: run configs "
                f"disagree ({ref_config} vs {got}). One card comes from a "
                f"different eval setup — remove it and retry."
            )

    summary = {k: 0 for k in _SUM_KEYS}
    by_category: dict = {}
    scenarios: list = []
    score_mass = 0.0
    score_count = 0
    perf_in = perf_out = perf_scen = 0
    tps_mass = ttft_mass = 0.0
    tps_weight = ttft_weight = 0
    for card in cards:
        s = card["summary"]
        for k in _SUM_KEYS:
            summary[k] += int(s.get(k, 0) or 0)
        for cat, block in (s.get("by_category") or {}).items():
            if cat in by_category:
                raise ValueError(
                    f"Cannot merge scorecards under {run_dir}: category "
                    f"{cat!r} appears in more than one scorecard — ambiguous. "
                    f"Keep exactly one run per category."
                )
            by_category[cat] = block
        card_judged = sum(int(s.get(k, 0) or 0) for k in _JUDGED_KEYS)
        avg = s.get("avg_score")
        if isinstance(avg, (int, float)) and not isinstance(avg, bool) and card_judged:
            score_mass += float(avg) * card_judged
            score_count += card_judged
        scenarios.extend(card.get("scenarios") or [])
        perf = card.get("performance") or {}
        perf_in += int(perf.get("total_input_tokens", 0) or 0)
        perf_out += int(perf.get("total_output_tokens", 0) or 0)
        card_perf_scen = int(perf.get("scenarios_with_data", 0) or 0)
        perf_scen += card_perf_scen
        if card_perf_scen:
            tps = perf.get("avg_tokens_per_second")
            if isinstance(tps, (int, float)) and not isinstance(tps, bool):
                tps_mass += float(tps) * card_perf_scen
                tps_weight += card_perf_scen
            ttft = perf.get("avg_time_to_first_token")
            if isinstance(ttft, (int, float)) and not isinstance(ttft, bool):
                ttft_mass += float(ttft) * card_perf_scen
                ttft_weight += card_perf_scen

    judged = sum(summary[k] for k in _JUDGED_KEYS)
    summary["judged_pass_rate"] = summary["passed"] / judged if judged else 0.0
    summary["avg_score"] = (score_mass / score_count) if score_count else 0.0
    summary["by_category"] = by_category
    return {
        "run_id": "+".join(str(c.get("run_id")) for c in cards),
        "config": cards[0].get("config", {}),
        "summary": summary,
        "scenarios": scenarios,
        "performance": {
            "avg_tokens_per_second": (
                round(tps_mass / tps_weight, 1) if tps_weight else None
            ),
            "avg_time_to_first_token": (
                round(ttft_mass / ttft_weight, 3) if ttft_weight else None
            ),
            "total_input_tokens": perf_in,
            "total_output_tokens": perf_out,
            "scenarios_with_data": perf_scen,
        },
    }


def _judged_counts(block: dict) -> tuple[int, int]:
    """Return ``(passed, judged)`` from a summary or by_category block."""
    judged = sum(int(block.get(k, 0) or 0) for k in _JUDGED_KEYS)
    return int(block.get("passed", 0) or 0), judged


def _compute_performance(scorecard: dict):
    """Surface the run's Lemonade perf counters (report-only), or ``None``.

    A run without counters (e.g. a Claude-backed harness-validation run) yields
    ``None`` — the section is omitted, never faked with zeros.
    """
    perf_in = scorecard.get("performance")
    if not isinstance(perf_in, dict):
        return None
    perf = {
        "ttft_s": perf_in.get("avg_time_to_first_token"),
        "throughput_tps": perf_in.get("avg_tokens_per_second"),
        "total_input_tokens": perf_in.get("total_input_tokens"),
        "total_output_tokens": perf_in.get("total_output_tokens"),
        "scenarios_with_data": perf_in.get("scenarios_with_data"),
    }
    # Drop absent (None) AND zero values: 0 here means "not measured", and
    # rendering "0.0" on the hub would read as an impossibly fast measurement.
    perf = {k: v for k, v in perf.items() if v}
    return perf or None


def _build_reproduction_command(model: str, ctx_size: int) -> str:
    """Exact, portable shell recipe that reproduces this scorecard."""
    return (
        "# Prerequisites: install the eval extras + this repo's chat/gaia hub\n"
        "# packages, start a Lemonade Server with the model on AMD Ryzen AI\n"
        "# hardware, and have the Claude Code CLI on PATH (the eval driver).\n"
        'uv pip install -e ".[dev,eval,ui,api]" '
        "-e hub/agents/chat/python -e hub/agents/gaia/python\n"
        "lemonade-server serve   # in a separate shell; must stay running\n\n"
        "# Step 0: stage fixtures + start the fixture server (see\n"
        "# tests/fixtures/gaia/README.md for the staging contract)\n"
        "python tests/fixtures/gaia/prepare_fixture_hub.py --skills-root <skills-root>\n"
        "python tests/fixtures/gaia/serve_fixtures.py --port 8765   # separate shell\n\n"
        "# Step 1: start the Agent UI backend (separate shell; NOT port 4001)\n"
        "python -m gaia.ui.server --port 4200 --host 127.0.0.1\n\n"
        "# Step 2: run every gaia category serially (never in parallel)\n"
        "for c in gaia_core gaia_memory gaia_rag gaia_files gaia_data gaia_web \\\n"
        "         gaia_shell gaia_skills_lifecycle gaia_skills_tasks \\\n"
        "         gaia_skills_capture gaia_honesty gaia_tool_selection \\\n"
        "         gaia_code; do\n"
        "  gaia eval agent --agent-type gaia --category $c \\\n"
        "    --backend http://127.0.0.1:4200 --budget 5.00 --exclude-tag live\n"
        "done\n\n"
        "# Step 3: generate this scorecard from ONE combined run's output dir\n"
        "python hub/agents/gaia/python/packaging/gen_scorecard.py \\\n"
        "    --run-dir <run-dir-printed-by-the-eval> \\\n"
        f"    --model {model} \\\n"
        f"    --ctx-size {ctx_size} \\\n"
        '    --hardware "<hardware class>"'
    )


def build_payload(
    run_dir: Path,
    model: str,
    ctx_size: int,
    hardware: str,
    environment=None,
):
    """Build a :class:`~gaia.eval.release_scorecard.ResultPayload` from a run.

    Args:
        run_dir: Directory written by ``gaia eval agent`` (contains
            ``scorecard.json``).
        model: The AGENT's model id (the model under test — NOT the eval
            driver recorded in the scorecard's ``config.model``).
        ctx_size: Context window the agent ran under (#1892 envelope).
        hardware: Hardware class descriptor (never a hostname).
        environment: Optional dict of environment metadata, embedded verbatim
            (assembled by ``main()``: gaia_commit, model, ctx_size, hardware,
            eval_model).

    Raises:
        ValueError: Zero judged scenarios, an agent_type other than ``gaia``,
            or a scorecard whose recorded rates don't reconcile with its counts.
        FileNotFoundError: Missing run dir / scorecard.json.
    """
    # Import here (not at module top) so tests that import build_payload before
    # gaia is installed in the test environment fail at call time, not import time.
    from gaia.eval.release_scorecard import ResultPayload, compute_aggregate

    scorecard = _load_run_scorecard(run_dir)
    summary = scorecard.get("summary", {})
    config = scorecard.get("config", {})

    agent_type = config.get("agent_type")
    if agent_type != "gaia":
        raise ValueError(
            f"Run {run_dir} has config.agent_type={agent_type!r}, expected "
            f"'gaia'. This adapter builds the FLAGSHIP agent's card; re-run "
            f"with 'gaia eval agent --agent-type gaia ...'."
        )

    eval_model = config.get("model")
    if not eval_model:
        raise ValueError(
            f"Run {run_dir} records no config.model (the eval driver/judge). "
            f"The card must state which judge scored it — re-run with an "
            f"explicit '--model <claude model>'."
        )

    passed, judged = _judged_counts(summary)
    if judged <= 0:
        raise ValueError(
            f"Zero judged scenarios in {run_dir}/scorecard.json "
            f"(summary: {json.dumps({k: summary.get(k) for k in ('total_scenarios', 'passed', 'failed', 'blocked', 'infra_error', 'errored', 'timeout')})}).\n"
            f"Every scenario ended in an infra/timeout/budget state — the run "
            f"measured nothing. Fix the harness failure and re-run; refusing "
            f"to build a card from an unjudged run."
        )

    judged_pass_rate = summary.get("judged_pass_rate")
    if not isinstance(judged_pass_rate, (int, float)) or isinstance(
        judged_pass_rate, bool
    ):
        raise ValueError(
            f"{run_dir}/scorecard.json has no numeric summary.judged_pass_rate."
        )
    if abs(float(judged_pass_rate) - passed / judged) > 1e-6:
        raise ValueError(
            f"Corrupt scorecard: summary.judged_pass_rate={judged_pass_rate} "
            f"does not equal passed/judged = {passed}/{judged}. Re-run the eval."
        )

    avg_score = summary.get("avg_score")
    if not isinstance(avg_score, (int, float)) or isinstance(avg_score, bool):
        raise ValueError(f"{run_dir}/scorecard.json has no numeric summary.avg_score.")

    # judged_pass_rate is the gated aggregate (weight 1.0). avg_score
    # (normalized: the judge rubric is 0-10) and the per-category pass rates
    # are DISPLAYED at weight 0.0 so aggregate.value = 100 x judged_pass_rate
    # stays recomputable from the displayed metrics alone.
    metrics = [
        {
            "name": "judged_pass_rate",
            "value": float(judged_pass_rate),
            "weight": 1.0,
        },
        {
            "name": "avg_score_normalized",
            "value": round(float(avg_score) / 10.0, 4),
            "weight": 0.0,
        },
    ]

    by_category = summary.get("by_category", {})
    per_category = []
    for cat in sorted(by_category):
        cat_passed, cat_judged = _judged_counts(by_category[cat])
        if cat_judged <= 0:
            raise ValueError(
                f"Category {cat!r} produced zero judged scenarios "
                f"({json.dumps(by_category[cat])}). A category that measured "
                f"nothing must not appear on the card as a rate — fix the "
                f"harness failure (or exclude the category) and re-run."
            )
        rate = round(cat_passed / cat_judged, 4)
        metrics.append({"name": f"{cat}_pass_rate", "value": rate, "weight": 0.0})
        per_category.append(
            {
                "category": cat,
                "total": cat_judged,
                "correct": cat_passed,
                "accuracy": rate,
            }
        )

    compute_aggregate(metrics)  # validate; aggregate embedded in render_scorecard

    dataset_size = len(list(_REPO_ROOT.glob(_SCENARIO_GLOB)))
    if dataset_size <= 0:
        raise ValueError(
            f"No scenarios matched {_SCENARIO_GLOB} under {_REPO_ROOT} — the "
            f"committed corpus is missing. Run from a full checkout."
        )

    # Read version from gaia-agent.yaml
    agent_yaml_path = _GAIA_ROOT / "gaia-agent.yaml"
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

    import datetime

    return ResultPayload(
        agent_name="GAIA (flagship agent)",
        agent_version=version,
        dataset_reference="eval/scenarios/",
        dataset_description=(
            "Judged multi-turn scenario corpus for the flagship gaia agent "
            "(12 gaia_* categories: core conversation/memory tiers through "
            "adversarial, RAG, files, data, web, shell gate, skill lifecycle "
            "+ all 12 skills, honesty floor, tool selection, code index); "
            "deterministic fixtures under tests/fixtures/gaia/, planted-fact "
            "ground truth per eval/scenarios/GAIA_FIXTURE_VALUES.md"
        ),
        dataset_size=dataset_size,
        methodology=(
            "gaia eval agent --agent-type gaia: each scenario is driven "
            "against the Agent UI backend (REST/SSE) by the eval driver and "
            "scored by the Claude judge on planted-fact ground truth + "
            "success criteria. Aggregate = judged_pass_rate over judged "
            "scenarios (PASS/FAIL/BLOCKED_BY_ARCHITECTURE; infra failures "
            "are excluded from the rate and fail the run's integrity gate "
            "instead). Reported secondaries (weight 0): the judge's 0-10 "
            "avg_score normalized to [0,1], and per-category pass rates. "
            "Thresholds/enforcement: tests/fixtures/gaia/"
            "quality_gate_thresholds.json"
        ),
        config={
            "harness": "gaia eval agent",
            "run_id": scorecard.get("run_id"),
            "agent_type": agent_type,
            "eval_model": eval_model,
            "budget_per_scenario_usd": config.get("budget_per_scenario_usd"),
            "categories": sorted(by_category),
        },
        test_cases_run=int(summary.get("total_scenarios", 0) or 0),
        metrics=metrics,
        aggregate_name="judged_pass_rate",
        generated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        inherited_from=None,
        reproduction_command=_build_reproduction_command(model, ctx_size),
        breakdown={"per_category": per_category},
        environment=environment,
        performance=_compute_performance(scorecard),
        capability_quality=None,
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


def main(argv=None) -> int:
    """Generate and write the gaia-agent scorecard."""
    parser = argparse.ArgumentParser(
        description="Generate a release scorecard for the flagship gaia agent.",
        prog="gen_scorecard.py",
    )
    parser.add_argument(
        "--run-dir",
        required=True,
        help=(
            "Run directory written by 'gaia eval agent --agent-type gaia' "
            "(the absolute path on its 'Output:' line; contains "
            "scorecard.json), or a directory holding several per-category run "
            "scorecards (eval-out/<category>/scorecard.json) to merge."
        ),
    )
    parser.add_argument(
        "--model",
        required=True,
        help=(
            "The AGENT's model id (the model under test, e.g. "
            "Gemma-4-E4B-it-GGUF). Required: the run scorecard's config.model "
            "is the eval driver/judge, not the agent."
        ),
    )
    parser.add_argument(
        "--ctx-size",
        required=True,
        type=int,
        help=(
            "Context window the agent ran under (#1892 envelope; e.g. 65536 "
            "for the GPU profile, 32768 for NPU). Required — a card that "
            "cannot state its measurement window must not be built."
        ),
    )
    parser.add_argument(
        "--hardware",
        required=True,
        help=(
            "Hardware class descriptor recorded in the environment block "
            "(e.g. 'AMD Ryzen AI MAX+ (Strix Halo)'). Required — never "
            "defaulted, so a local harness-validation card cannot silently "
            "claim runner hardware. Use a class description, never a hostname."
        ),
    )
    parser.add_argument(
        "--lemonade-version",
        default=None,
        help=(
            "Lemonade Server version used for the run, stamped when given. "
            "Omit for runs that never touched Lemonade (e.g. a Claude-backed "
            "harness validation) — the field is then absent, never guessed."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Override the scorecard output directory "
            f"(default: hub/agents/gaia/npm/, writes {_OUTPUT_FILENAME})."
        ),
    )
    parser.add_argument(
        "--note",
        default=None,
        help=(
            "Optional provenance note stamped into the environment block "
            "(e.g. 'harness validation on claude-haiku-4-5 — pending first "
            "runner baseline'). Shown on the card verbatim."
        ),
    )
    args = parser.parse_args(argv)

    run_dir = Path(args.run_dir).resolve()

    try:
        gaia_commit = _capture_gaia_commit()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # eval_model is read from the run scorecard inside build_payload; replicate
    # the lightweight read for the environment stamp without splitting the
    # pure build_payload interface.
    try:
        _eval_model = _load_run_scorecard(run_dir).get("config", {}).get("model")
    except (FileNotFoundError, ValueError, json.JSONDecodeError):
        _eval_model = None

    environment: dict = {
        "gaia_commit": gaia_commit,
        "model": args.model,
        "ctx_size": args.ctx_size,
        "hardware": args.hardware,
        **({"eval_model": _eval_model} if _eval_model else {}),
    }
    if args.lemonade_version:
        environment["lemonade_version"] = args.lemonade_version
    if args.note:
        environment["note"] = args.note

    try:
        payload = build_payload(
            run_dir,
            model=args.model,
            ctx_size=args.ctx_size,
            hardware=args.hardware,
            environment=environment,
        )
    except (ValueError, FileNotFoundError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    from gaia.eval.release_scorecard import write_scorecard

    out_dir = Path(args.output_dir) if args.output_dir else _NPM_ROOT
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / _OUTPUT_FILENAME
    write_scorecard(payload, out_path)

    print(
        f"Scorecard written: {out_path}\n"
        f"  Version: {payload.agent_version}\n"
        f"  Aggregate: {payload.metrics[0]['value']:.4f} {payload.metrics[0]['name']} "
        f"({payload.test_cases_run} scenarios run)\n"
        f"  Dataset size: {payload.dataset_size} committed scenarios"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
