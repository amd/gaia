# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pydantic schemas for pipeline metrics API."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field


class MetricTypeSchema(str):
    """Schema for metric type enumeration."""

    # Efficiency Metrics
    TOKEN_EFFICIENCY = "TOKEN_EFFICIENCY"
    CONTEXT_UTILIZATION = "CONTEXT_UTILIZATION"

    # Quality Metrics
    QUALITY_VELOCITY = "QUALITY_VELOCITY"
    DEFECT_DENSITY = "DEFECT_DENSITY"

    # Reliability Metrics
    MTTR = "MTTR"
    AUDIT_COMPLETENESS = "AUDIT_COMPLETENESS"

    # Performance Metrics (Phase 2)
    TPS = "TPS"
    TTFT = "TTFT"
    PHASE_DURATION = "PHASE_DURATION"
    LOOP_ITERATION_COUNT = "LOOP_ITERATION_COUNT"
    HOOK_EXECUTION_TIME = "HOOK_EXECUTION_TIME"
    STATE_TRANSITION = "STATE_TRANSITION"
    AGENT_SELECTION = "AGENT_SELECTION"
    RESOURCE_UTILIZATION = "RESOURCE_UTILIZATION"


class MetricSnapshotSchema(BaseModel):
    """Schema for a single metric snapshot."""

    timestamp: datetime = Field(..., description="When the snapshot was taken")
    loop_id: str = Field(..., description="Loop iteration identifier")
    phase: str = Field(..., description="Pipeline phase name")
    metrics: Dict[str, float] = Field(default_factory=dict, description="Metric values")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )

    model_config = ConfigDict(from_attributes=True)


class PhaseTimingSchema(BaseModel):
    """Schema for phase timing information."""

    phase_name: str = Field(..., description="Name of the phase")
    started_at: Optional[datetime] = Field(None, description="When the phase started")
    ended_at: Optional[datetime] = Field(None, description="When the phase ended")
    duration_seconds: float = Field(
        default=0.0, description="Total duration in seconds"
    )
    token_count: int = Field(default=0, description="Number of tokens generated")
    ttft: Optional[float] = Field(None, description="Time to first token in seconds")
    tps: float = Field(default=0.0, description="Tokens per second")

    model_config = ConfigDict(from_attributes=True)


class LoopMetricsSchema(BaseModel):
    """Schema for loop iteration metrics."""

    loop_id: str = Field(..., description="Loop identifier")
    phase_name: str = Field(..., description="Pipeline phase name")
    iteration_count: int = Field(default=0, description="Number of iterations")
    quality_scores: List[float] = Field(
        default_factory=list, description="Quality score history"
    )
    average_quality: Optional[float] = Field(None, description="Average quality score")
    max_quality: Optional[float] = Field(None, description="Maximum quality score")
    defects_by_type: Dict[str, int] = Field(
        default_factory=dict, description="Defects by type"
    )
    started_at: Optional[datetime] = Field(None, description="When the loop started")
    ended_at: Optional[datetime] = Field(None, description="When the loop ended")

    model_config = ConfigDict(from_attributes=True)


class StateTransitionSchema(BaseModel):
    """Schema for state transition record."""

    from_state: str = Field(..., description="Previous state")
    to_state: str = Field(..., description="New state")
    timestamp: datetime = Field(..., description="When the transition occurred")
    reason: str = Field(default="", description="Reason for transition")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional context"
    )

    model_config = ConfigDict(from_attributes=True)


class AgentSelectionSchema(BaseModel):
    """Schema for agent selection decision."""

    phase: str = Field(..., description="Phase where selection occurred")
    agent_id: str = Field(..., description="Selected agent ID")
    reason: str = Field(default="", description="Reason for selection")
    alternatives: List[str] = Field(
        default_factory=list, description="Alternative agents considered"
    )
    timestamp: datetime = Field(..., description="When the selection occurred")

    model_config = ConfigDict(from_attributes=True)


class HookExecutionSchema(BaseModel):
    """Schema for hook execution record."""

    hook_name: str = Field(..., description="Name of the hook")
    event: str = Field(..., description="Event that triggered the hook")
    duration_seconds: float = Field(..., description="Execution duration in seconds")
    success: bool = Field(default=True, description="Whether execution succeeded")
    timestamp: datetime = Field(..., description="When the execution occurred")

    model_config = ConfigDict(from_attributes=True)


class PipelineMetricsSnapshotSchema(BaseModel):
    """Schema for comprehensive pipeline metrics snapshot."""

    pipeline_id: str = Field(..., description="Pipeline identifier")
    phase_timings: Dict[str, PhaseTimingSchema] = Field(
        default_factory=dict, description="Timing information per phase"
    )
    loop_metrics: Dict[str, LoopMetricsSchema] = Field(
        default_factory=dict, description="Metrics per loop"
    )
    state_transitions: List[StateTransitionSchema] = Field(
        default_factory=list, description="State transition history"
    )
    agent_selections: List[AgentSelectionSchema] = Field(
        default_factory=list, description="Agent selection decisions"
    )
    hook_execution_times: List[HookExecutionSchema] = Field(
        default_factory=list, description="Hook execution records"
    )
    quality_scores: List[tuple] = Field(
        default_factory=list,
        description="Quality score history as (loop_id, phase, score)",
    )
    defects_by_type: Dict[str, int] = Field(
        default_factory=dict, description="Defect counts by type"
    )

    model_config = ConfigDict(from_attributes=True)


class PipelineMetricsSummarySchema(BaseModel):
    """Schema for pipeline metrics summary."""

    pipeline_id: str = Field(..., description="Pipeline identifier")
    total_duration_seconds: float = Field(
        default=0.0, description="Total pipeline duration"
    )
    total_tokens: int = Field(default=0, description="Total tokens generated")
    avg_tps: float = Field(default=0.0, description="Average tokens per second")
    avg_ttft: float = Field(default=0.0, description="Average time to first token")
    total_loops: int = Field(default=0, description="Total number of loops")
    total_iterations: int = Field(
        default=0, description="Total iterations across all loops"
    )
    total_defects: int = Field(default=0, description="Total defects discovered")
    avg_quality_score: float = Field(default=0.0, description="Average quality score")
    max_quality_score: float = Field(default=0.0, description="Maximum quality score")
    min_quality_score: float = Field(default=0.0, description="Minimum quality score")
    avg_hook_execution_time: float = Field(
        default=0.0, description="Average hook execution time"
    )

    model_config = ConfigDict(from_attributes=True)


class PipelineMetricsResponseSchema(BaseModel):
    """Response schema for pipeline metrics endpoint."""

    success: bool = Field(default=True, description="Whether the request succeeded")
    pipeline_id: str = Field(..., description="Pipeline identifier")
    summary: PipelineMetricsSummarySchema = Field(..., description="Metrics summary")
    phase_breakdown: Dict[str, PhaseTimingSchema] = Field(
        default_factory=dict, description="Phase timing breakdown"
    )
    loop_metrics: Dict[str, LoopMetricsSchema] = Field(
        default_factory=dict, description="Loop metrics"
    )
    state_transitions: List[StateTransitionSchema] = Field(
        default_factory=list, description="State transition history"
    )
    defects_by_type: Dict[str, int] = Field(
        default_factory=dict, description="Defects by type"
    )
    agent_selections: List[AgentSelectionSchema] = Field(
        default_factory=list, description="Agent selection decisions"
    )

    model_config = ConfigDict(from_attributes=True)


class MetricHistoryPointSchema(BaseModel):
    """Schema for a single point in metric history."""

    timestamp: datetime = Field(..., description="When the metric was recorded")
    loop_id: str = Field(..., description="Loop identifier")
    phase: str = Field(..., description="Pipeline phase")
    metric_type: str = Field(..., description="Type of metric")
    value: float = Field(..., description="Metric value")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )

    model_config = ConfigDict(from_attributes=True)


class PipelineMetricsHistorySchema(BaseModel):
    """Response schema for metrics history endpoint."""

    pipeline_id: str = Field(..., description="Pipeline identifier")
    metric_type: Optional[str] = Field(None, description="Filter by metric type")
    start_time: Optional[datetime] = Field(None, description="Start of time range")
    end_time: Optional[datetime] = Field(None, description="End of time range")
    total_points: int = Field(..., description="Total number of data points")
    history: List[MetricHistoryPointSchema] = Field(
        default_factory=list, description="Metric history data points"
    )

    model_config = ConfigDict(from_attributes=True)


class AggregateMetricStatisticsSchema(BaseModel):
    """Schema for aggregated metric statistics."""

    metric_type: str = Field(..., description="Type of metric")
    count: int = Field(..., description="Number of data points")
    mean: float = Field(..., description="Mean value")
    median: float = Field(..., description="Median value")
    std_dev: float = Field(default=0.0, description="Standard deviation")
    min_value: float = Field(..., description="Minimum value")
    max_value: float = Field(..., description="Maximum value")
    trend: str = Field(default="stable", description="Trend direction")
    percentiles: Dict[str, float] = Field(
        default_factory=dict, description="Percentile values"
    )

    model_config = ConfigDict(from_attributes=True)


class PipelineAggregateMetricsSchema(BaseModel):
    """Response schema for aggregate metrics endpoint."""

    success: bool = Field(default=True, description="Whether the request succeeded")
    total_pipelines: int = Field(..., description="Number of pipelines analyzed")
    time_range: Dict[str, Optional[datetime]] = Field(
        default_factory=lambda: {"start": None, "end": None},
        description="Time range of aggregation",
    )
    metric_statistics: Dict[str, AggregateMetricStatisticsSchema] = Field(
        default_factory=dict, description="Statistics per metric type"
    )
    overall_health: float = Field(
        default=0.0, ge=0.0, le=1.0, description="Overall health score"
    )
    recommendations: List[str] = Field(
        default_factory=list, description="Improvement recommendations"
    )

    model_config = ConfigDict(from_attributes=True)


class MetricsListResponse(BaseModel):
    """Response for listing available metrics."""

    metric_types: List[str] = Field(..., description="Available metric types")
    categories: Dict[str, List[str]] = Field(
        ..., description="Metric types grouped by category"
    )

    model_config = ConfigDict(from_attributes=True)
