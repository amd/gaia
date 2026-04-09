# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Eval metrics API endpoints for GAIA Agent UI.

Provides REST API endpoints for querying eval run metrics:
- GET /api/eval/runs/{run_id}/metrics - Aggregate metrics for an eval run
- GET /api/eval/runs/{run_id}/scenarios/{scenario_id}/metrics - Per-scenario metrics

These endpoints provide access to performance metrics collected during
eval scenario execution (duration, cost, tokens, etc.).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["eval-metrics"])

# Eval results directory
REPO_ROOT = Path(__file__).parent.parent.parent.parent
EVAL_DIR = REPO_ROOT / "eval"
RESULTS_DIR = EVAL_DIR / "results"


def get_run_dir(run_id: str) -> Path:
    """Get the directory for a specific eval run."""
    return RESULTS_DIR / run_id


def load_metrics_summary(run_id: str) -> Dict[str, Any]:
    """Load metrics summary from file."""
    run_dir = get_run_dir(run_id)
    metrics_path = run_dir / "metrics_summary.json"

    if not metrics_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Metrics summary not found for run: {run_id}",
        )

    try:
        return json.loads(metrics_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load metrics for %s: %s", run_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load metrics: {e}",
        )


def load_scorecard(run_id: str) -> Dict[str, Any]:
    """Load scorecard from file."""
    run_dir = get_run_dir(run_id)
    scorecard_path = run_dir / "scorecard.json"

    if not scorecard_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Scorecard not found for run: {run_id}",
        )

    try:
        return json.loads(scorecard_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load scorecard for %s: %s", run_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load scorecard: {e}",
        )


def load_scenario_trace(run_id: str, scenario_id: str) -> Dict[str, Any]:
    """Load individual scenario trace from file."""
    run_dir = get_run_dir(run_id)
    trace_path = run_dir / "traces" / f"{scenario_id}.json"

    if not trace_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Scenario trace not found: {scenario_id} in run {run_id}",
        )

    try:
        return json.loads(trace_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error("Failed to load trace for %s/%s: %s", run_id, scenario_id, e)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load scenario trace: {e}",
        )


@router.get("/api/eval/runs")
async def list_eval_runs():
    """
    List all available eval runs.

    Returns a list of run IDs found in the results directory.

    ### Example Response:
    ```json
    {
        "success": true,
        "runs": [
            {"run_id": "eval-20250101-120000", "has_metrics": true},
            {"run_id": "eval-20250101-140000", "has_metrics": false}
        ]
    }
    ```
    """
    try:
        if not RESULTS_DIR.exists():
            return JSONResponse(content={"success": True, "runs": []})

        runs = []
        for run_dir in sorted(RESULTS_DIR.iterdir()):
            if run_dir.is_dir() and run_dir.name.startswith("eval-"):
                has_metrics = (run_dir / "metrics_summary.json").exists()
                runs.append({
                    "run_id": run_dir.name,
                    "has_metrics": has_metrics,
                    "has_scorecard": (run_dir / "scorecard.json").exists(),
                })

        return JSONResponse(content={"success": True, "runs": runs})

    except Exception as e:
        logger.error("Failed to list eval runs: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to list eval runs. Check server logs for details.",
        )


@router.get("/api/eval/runs/{run_id}/metrics")
async def get_eval_run_metrics(run_id: str):
    """
    Get aggregate metrics for an eval run.

    Returns comprehensive metrics including:
    - Total scenarios executed
    - Total and average duration
    - Total cost estimate
    - Total tokens generated
    - Run start/end timestamps

    ### Example Response:
    ```json
    {
        "success": true,
        "run_id": "eval-20250101-120000",
        "summary": {
            "total_scenarios": 23,
            "total_duration_seconds": 1845.5,
            "avg_duration_seconds": 80.2,
            "total_cost_usd": 0.45,
            "total_tokens": 45000
        }
    }
    ```
    """
    try:
        metrics = load_metrics_summary(run_id)
        return JSONResponse(
            content={
                "success": True,
                "run_id": run_id,
                "summary": metrics,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get eval metrics for %s: %s", run_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get eval metrics. Check server logs for details.",
        )


@router.get("/api/eval/runs/{run_id}/scorecard")
async def get_eval_scorecard(run_id: str):
    """
    Get the full scorecard for an eval run.

    Returns the complete scorecard including:
    - Summary statistics (pass rate, avg score)
    - Per-category breakdown
    - All scenario results with performance data

    ### Example:
    ```
    GET /api/eval/runs/eval-20250101-120000/scorecard
    ```
    """
    try:
        scorecard = load_scorecard(run_id)
        return JSONResponse(
            content={
                "success": True,
                "scorecard": scorecard,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get scorecard for %s: %s", run_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get scorecard. Check server logs for details.",
        )


@router.get("/api/eval/runs/{run_id}/scenarios/{scenario_id}/metrics")
async def get_eval_scenario_metrics(run_id: str, scenario_id: str):
    """
    Get metrics for a specific scenario.

    Returns detailed performance metrics for a single scenario:
    - Duration in seconds
    - Cost estimate in USD
    - Token count estimate
    - Status
    - Timing timestamps

    ### Example Response:
    ```json
    {
        "success": true,
        "run_id": "eval-20250101-120000",
        "scenario_id": "knowledge_qa_001",
        "metrics": {
            "scenario_id": "knowledge_qa_001",
            "duration_seconds": 85.3,
            "cost_estimate_usd": 0.02,
            "tokens_generated": 1500,
            "status": "PASS"
        }
    }
    ```
    """
    try:
        # Try to load from metrics summary first
        metrics = load_metrics_summary(run_id)
        scenarios = metrics.get("scenarios", {})

        if scenario_id in scenarios:
            scenario_metrics = scenarios[scenario_id]
            return JSONResponse(
                content={
                    "success": True,
                    "run_id": run_id,
                    "scenario_id": scenario_id,
                    "metrics": scenario_metrics,
                }
            )

        # Fall back to trace file
        trace = load_scenario_trace(run_id, scenario_id)
        performance = trace.get("performance", {
            "duration_seconds": trace.get("elapsed_s", 0.0),
            "cost_estimate_usd": trace.get("cost_estimate", {}).get("estimated_usd", 0.0),
        })

        return JSONResponse(
            content={
                "success": True,
                "run_id": run_id,
                "scenario_id": scenario_id,
                "metrics": {
                    "scenario_id": scenario_id,
                    "duration_seconds": performance.get("duration_seconds", 0.0),
                    "cost_estimate_usd": performance.get("cost_estimate_usd", 0.0),
                    "status": trace.get("status", "UNKNOWN"),
                },
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get scenario metrics for %s/%s: %s",
            run_id,
            scenario_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get scenario metrics. Check server logs for details.",
        )


@router.get("/api/eval/runs/{run_id}/scenarios/{scenario_id}/trace")
async def get_eval_scenario_trace(run_id: str, scenario_id: str):
    """
    Get the full trace for a specific scenario.

    Returns the complete scenario trace including:
    - All turn details with scores
    - Tool usage
    - Root cause analysis (if failed)
    - Performance metrics

    This is useful for debugging individual scenario failures.
    """
    try:
        trace = load_scenario_trace(run_id, scenario_id)
        return JSONResponse(
            content={
                "success": True,
                "trace": trace,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get scenario trace for %s/%s: %s",
            run_id,
            scenario_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get scenario trace. Check server logs for details.",
        )


@router.get("/api/eval/runs/{run_id}/slowest")
async def get_slowest_scenarios(
    run_id: str,
    n: int = 5,
):
    """
    Get the N slowest scenarios from an eval run.

    Args:
        run_id: Eval run identifier
        n: Number of scenarios to return (default: 5, max: 20)

    Returns scenarios sorted by duration in descending order.

    ### Example Response:
    ```json
    {
        "success": true,
        "run_id": "eval-20250101-120000",
        "slowest": [
            {"scenario_id": "complex_reasoning_003", "duration_seconds": 245.5, "status": "PASS"},
            {"scenario_id": "multi_tool_002", "duration_seconds": 198.2, "status": "FAIL"}
        ]
    }
    ```
    """
    try:
        # Cap n to prevent abuse
        n = min(n, 20)

        scorecard = load_scorecard(run_id)
        scenarios = scorecard.get("scenarios", [])

        # Sort by duration (from performance field or elapsed_s)
        sorted_scenarios = sorted(
            scenarios,
            key=lambda s: s.get("performance", {}).get("duration_seconds", s.get("elapsed_s", 0.0)),
            reverse=True,
        )[:n]

        slowest = [
            {
                "scenario_id": s.get("scenario_id", "unknown"),
                "duration_seconds": s.get("performance", {}).get("duration_seconds", s.get("elapsed_s", 0.0)),
                "status": s.get("status", "UNKNOWN"),
                "score": s.get("overall_score"),
            }
            for s in sorted_scenarios
        ]

        return JSONResponse(
            content={
                "success": True,
                "run_id": run_id,
                "slowest": slowest,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get slowest scenarios for %s: %s", run_id, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get slowest scenarios. Check server logs for details.",
        )
