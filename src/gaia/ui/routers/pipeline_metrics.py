# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pipeline metrics API endpoints for GAIA Agent UI.

Provides REST API endpoints for querying pipeline metrics:
- GET /api/v1/pipeline/{id}/metrics - Real-time metrics snapshot
- GET /api/v1/pipeline/{id}/metrics/history - Historical metrics data
- GET /api/v1/pipeline/metrics/aggregate - Aggregated metrics across pipelines
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ..schemas.metrics import (
    MetricHistoryPointSchema,
    MetricsListResponse,
    PipelineAggregateMetricsSchema,
    PipelineMetricsHistorySchema,
    PipelineMetricsResponseSchema,
)
from ..services.metrics_service import MetricsService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pipeline-metrics"])


def get_metrics_service() -> MetricsService:
    """Get metrics service instance."""
    return MetricsService()


@router.get("/api/v1/pipeline/{pipeline_id}/metrics")
async def get_pipeline_metrics(
    pipeline_id: str,
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get real-time metrics snapshot for a pipeline.

    Returns comprehensive metrics including:
    - Phase timings (duration, TPS, TTFT)
    - Loop metrics (iterations, quality scores, defects)
    - State transitions
    - Agent selection decisions
    - Hook execution times

    ### Example Response:
    ```json
    {
        "success": true,
        "pipeline_id": "pipeline-001",
        "summary": {
            "total_duration_seconds": 120.5,
            "total_tokens": 5000,
            "avg_tps": 41.5,
            "avg_ttft": 0.35,
            "total_loops": 4,
            "total_iterations": 12,
            "avg_quality_score": 0.88
        },
        "phase_breakdown": {
            "PLANNING": {
                "phase_name": "PLANNING",
                "duration_seconds": 25.3,
                "tps": 35.2,
                "ttft": 0.4
            }
        }
    }
    ```
    """
    try:
        # Get metrics summary
        summary = service.get_metrics_summary(pipeline_id)

        if not summary:
            raise HTTPException(
                status_code=404,
                detail=f"Pipeline metrics not found: {pipeline_id}",
            )

        # Build response
        response = PipelineMetricsResponseSchema(
            success=True,
            pipeline_id=pipeline_id,
            summary=summary.get("summary", {}),
            phase_breakdown=summary.get("phase_breakdown", {}),
            loop_metrics=summary.get("loop_metrics", {}),
            state_transitions=summary.get("state_transitions", []),
            defects_by_type=summary.get("defects_by_type", {}),
            agent_selections=summary.get("agent_selections", []),
        )

        return response

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "Failed to get pipeline metrics for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get pipeline metrics. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/history")
async def get_pipeline_metrics_history(
    pipeline_id: str,
    metric_type: Optional[str] = Query(None, description="Filter by metric type"),
    start_time: Optional[datetime] = Query(None, description="Start of time range"),
    end_time: Optional[datetime] = Query(None, description="End of time range"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum records to return"),
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get historical metrics data for a pipeline.

    Returns time-series data for metrics with optional filtering:
    - Filter by specific metric type (TPS, TTFT, etc.)
    - Filter by time range
    - Pagination via limit

    ### Query Parameters:
    - `metric_type`: Filter by specific metric (e.g., "TPS", "TTFT")
    - `start_time`: ISO 8601 datetime for range start
    - `end_time`: ISO 8601 datetime for range end
    - `limit`: Maximum records to return (default: 100, max: 1000)

    ### Example:
    ```
    GET /api/v1/pipeline/pipeline-001/metrics/history?metric_type=TPS&limit=50
    ```
    """
    try:
        history = service.get_metrics_history(
            pipeline_id=pipeline_id,
            metric_type=metric_type,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
        )

        response = PipelineMetricsHistorySchema(
            pipeline_id=pipeline_id,
            metric_type=metric_type,
            start_time=start_time,
            end_time=end_time,
            total_points=len(history),
            history=[MetricHistoryPointSchema(**h) for h in history],
        )

        return response

    except Exception as e:
        logger.error(
            "Failed to get pipeline metrics history for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get metrics history. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/metrics/aggregate")
async def get_aggregate_metrics(
    pipeline_ids: Optional[str] = Query(
        None,
        description="Comma-separated list of pipeline IDs (default: all)",
    ),
    start_time: Optional[datetime] = Query(None, description="Start of time range"),
    end_time: Optional[datetime] = Query(None, description="End of time range"),
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get aggregate metrics across multiple pipelines.

    Returns statistical analysis across pipelines:
    - Mean, median, standard deviation per metric
    - Trend analysis (increasing/decreasing/stable)
    - Percentile distributions
    - Overall health score
    - Improvement recommendations

    ### Query Parameters:
    - `pipeline_ids`: Comma-separated list (default: all pipelines)
    - `start_time`: ISO 8601 datetime for range start
    - `end_time`: ISO 8601 datetime for range end

    ### Example:
    ```
    GET /api/v1/pipeline/metrics/aggregate?pipeline_ids=pipeline-001,pipeline-002
    ```
    """
    try:
        # Parse pipeline IDs
        pipeline_id_list = None
        if pipeline_ids:
            pipeline_id_list = [pid.strip() for pid in pipeline_ids.split(",")]

        aggregate = service.get_aggregate_metrics(
            pipeline_ids=pipeline_id_list,
            start_time=start_time,
            end_time=end_time,
        )

        response = PipelineAggregateMetricsSchema(
            success=True,
            total_pipelines=aggregate.get("total_pipelines", 0),
            time_range=aggregate.get("time_range", {}),
            metric_statistics=aggregate.get("metric_statistics", {}),
            overall_health=aggregate.get("overall_health", 0.0),
            recommendations=aggregate.get("recommendations", []),
        )

        return response

    except Exception as e:
        logger.error(
            "Failed to get aggregate metrics: %s",
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get aggregate metrics. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/metrics/types")
async def list_metrics_types(
    service: MetricsService = Depends(get_metrics_service),
):
    """
    List available metric types and categories.

    Returns all metric types that can be queried, grouped by category:
    - **Efficiency**: TOKEN_EFFICIENCY, CONTEXT_UTILIZATION
    - **Quality**: QUALITY_VELOCITY, DEFECT_DENSITY
    - **Reliability**: MTTR, AUDIT_COMPLETENESS
    - **Performance**: TPS, TTFT, PHASE_DURATION, LOOP_ITERATION_COUNT, etc.

    ### Example Response:
    ```json
    {
        "metric_types": ["TPS", "TTFT", "PHASE_DURATION", ...],
        "categories": {
            "efficiency": ["TOKEN_EFFICIENCY", "CONTEXT_UTILIZATION"],
            "performance": ["TPS", "TTFT", "PHASE_DURATION"]
        }
    }
    ```
    """
    try:
        metrics = service.list_available_metrics()
        return MetricsListResponse(**metrics)

    except Exception as e:
        logger.error(
            "Failed to list metrics types: %s",
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to list metrics types. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/phases")
async def get_phase_metrics(
    pipeline_id: str,
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get phase-specific metrics for a pipeline.

    Returns detailed timing and performance metrics for each phase:
    - PLANNING
    - DEVELOPMENT
    - QUALITY
    - DECISION

    ### Example Response:
    ```json
    {
        "PLANNING": {
            "phase_name": "PLANNING",
            "started_at": "2025-01-01T10:00:00Z",
            "ended_at": "2025-01-01T10:00:25Z",
            "duration_seconds": 25.3,
            "token_count": 1500,
            "ttft": 0.4,
            "tps": 35.2
        }
    }
    ```
    """
    try:
        phase_timings = service.get_phase_timings(pipeline_id)

        if not phase_timings:
            # Return empty dict if pipeline not found (no metrics available)
            return {}

        return phase_timings

    except Exception as e:
        logger.error(
            "Failed to get phase metrics for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get phase metrics. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/loops")
async def get_loop_metrics(
    pipeline_id: str,
    loop_id: Optional[str] = Query(None, description="Specific loop ID"),
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get loop iteration metrics for a pipeline.

    Returns metrics for each loop iteration:
    - Iteration count
    - Quality score history
    - Defects by type
    - Start/end timestamps

    ### Query Parameters:
    - `loop_id`: Optional specific loop ID to filter

    ### Example Response:
    ```json
    {
        "loop-001": {
            "loop_id": "loop-001",
            "phase_name": "DEVELOPMENT",
            "iteration_count": 3,
            "quality_scores": [0.65, 0.78, 0.92],
            "average_quality": 0.78,
            "max_quality": 0.92,
            "defects_by_type": {"testing": 2, "documentation": 1}
        }
    }
    ```
    """
    try:
        loop_metrics = service.get_loop_metrics(pipeline_id, loop_id)

        return loop_metrics

    except Exception as e:
        logger.error(
            "Failed to get loop metrics for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get loop metrics. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/quality")
async def get_quality_history(
    pipeline_id: str,
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get quality score history for a pipeline.

    Returns a list of quality scores recorded during pipeline execution,
    showing the progression of quality across iterations.

    ### Example Response:
    ```json
    [
        {"loop_id": "loop-001", "phase": "DEVELOPMENT", "score": 0.65},
        {"loop_id": "loop-001", "phase": "DEVELOPMENT", "score": 0.78},
        {"loop_id": "loop-001", "phase": "DEVELOPMENT", "score": 0.92}
    ]
    ```
    """
    try:
        quality_history = service.get_quality_history(pipeline_id)

        # Format as list of dicts
        return [
            {"loop_id": loop_id, "phase": phase, "score": score}
            for loop_id, phase, score in quality_history
        ]

    except Exception as e:
        logger.error(
            "Failed to get quality history for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get quality history. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/defects")
async def get_defect_metrics(
    pipeline_id: str,
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get defect metrics for a pipeline.

    Returns defect counts categorized by type:
    - security
    - testing
    - documentation
    - code_quality
    - performance
    - etc.

    ### Example Response:
    ```json
    {
        "testing": 3,
        "documentation": 2,
        "code_quality": 1
    }
    ```
    """
    try:
        defects = service.get_defects_by_type(pipeline_id)
        return defects

    except Exception as e:
        logger.error(
            "Failed to get defect metrics for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get defect metrics. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/transitions")
async def get_state_transitions(
    pipeline_id: str,
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get state transition history for a pipeline.

    Returns the sequence of state transitions that occurred during
    pipeline execution, with timestamps and reasons.

    ### Example Response:
    ```json
    [
        {
            "from_state": "INIT",
            "to_state": "PLANNING",
            "timestamp": "2025-01-01T10:00:00Z",
            "reason": "Phase transition"
        },
        {
            "from_state": "PLANNING",
            "to_state": "DEVELOPMENT",
            "timestamp": "2025-01-01T10:00:25Z",
            "reason": "Phase exit"
        }
    ]
    ```
    """
    try:
        transitions = service.get_state_transitions(pipeline_id)
        return transitions

    except Exception as e:
        logger.error(
            "Failed to get state transitions for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get state transitions. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/{pipeline_id}/metrics/agents")
async def get_agent_selections(
    pipeline_id: str,
    service: MetricsService = Depends(get_metrics_service),
):
    """
    Get agent selection decisions for a pipeline.

    Returns records of which agents were selected for each phase,
    along with the rationale and alternative agents considered.

    ### Example Response:
    ```json
    [
        {
            "phase": "PLANNING",
            "agent_id": "senior-developer",
            "reason": "Best match for requirements analysis",
            "alternatives": ["architect", "tech-lead"],
            "timestamp": "2025-01-01T10:00:00Z"
        }
    ]
    ```
    """
    try:
        selections = service.get_agent_selections(pipeline_id)
        return selections

    except Exception as e:
        logger.error(
            "Failed to get agent selections for %s: %s",
            pipeline_id,
            e,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get agent selections. Check server logs for details.",
        )
