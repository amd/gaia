# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pydantic schemas for pipeline template management."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RoutingRuleSchema(BaseModel):
    """Schema for a routing rule in pipeline template."""

    condition: str = Field(..., description="Condition expression for routing")
    route_to: str = Field(..., description="Target category or agent ID")
    priority: int = Field(default=0, description="Rule priority (lower = higher)")
    loop_back: bool = Field(
        default=False, description="Whether to loop back to previous phase"
    )
    guidance: Optional[str] = Field(
        None, description="Optional guidance message for the agent"
    )


class AgentCategorySchema(BaseModel):
    """Schema for agent category configuration."""

    name: str = Field(..., description="Category name")
    selection_mode: str = Field(default="auto", description="auto|sequential|parallel")
    agents: List[str] = Field(default_factory=list, description="List of agent IDs")


# ── Canvas Loop & Supervisor Configuration Schemas ────────────────────


class CanvasLoopConfigSchema(BaseModel):
    """Canvas-driven loop configuration with per-loop agent selection."""

    loop_id: str = Field(..., description="Unique loop identifier")
    label: str = Field(default="", description="Display label")
    agent_ids: List[str] = Field(
        default_factory=list, description="Explicit agent IDs for this loop"
    )
    max_iterations: int = Field(default=10, ge=1, description="Per-loop max iterations")
    quality_threshold: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Per-loop quality threshold"
    )
    source_stage: Optional[str] = Field(None, description="Source stage key")
    target_stage: Optional[str] = Field(None, description="Target stage key (loop destination)")
    condition: str = Field(
        default="quality_below_threshold", description="Loop trigger condition"
    )
    position: Dict[str, float] = Field(
        default_factory=dict, description="Canvas position {x, y}"
    )


class CanvasSupervisorConfigSchema(BaseModel):
    """Canvas-driven supervisor agent configuration."""

    supervisor_id: str = Field(..., description="Unique supervisor identifier")
    label: str = Field(default="", description="Display label")
    agent_id: Optional[str] = Field(None, description="Agent ID assigned to this supervisor")
    position: Dict[str, float] = Field(
        default_factory=dict, description="Canvas position {x, y}"
    )
    decision_condition: str = Field(
        default="quality_below_threshold", description="Condition triggering decision"
    )
    decision_type: str = Field(
        default="CONTINUE", description="Decision type: CONTINUE, LOOP_BACK, PAUSE, COMPLETE, FAIL"
    )
    monitoring_targets: List[str] = Field(
        default_factory=list, description="Node IDs this supervisor monitors"
    )


class PipelineTemplateSchema(BaseModel):
    """Schema for pipeline template."""

    name: str = Field(..., description="Template name")
    description: str = Field(default="", description="Template description")
    quality_threshold: float = Field(
        default=0.90, ge=0.0, le=1.0, description="Required quality score"
    )
    max_iterations: int = Field(
        default=10, ge=1, description="Maximum recursive iterations"
    )
    agent_categories: Dict[str, List[str]] = Field(
        default_factory=dict, description="Map of categories to agent lists"
    )
    routing_rules: List[RoutingRuleSchema] = Field(
        default_factory=list, description="Conditional routing rules"
    )
    quality_weights: Dict[str, float] = Field(
        default_factory=dict, description="Weights for quality scoring dimensions"
    )
    canvas_loops: List[CanvasLoopConfigSchema] = Field(
        default_factory=list, description="Canvas-defined loop configurations"
    )
    canvas_supervisors: List[CanvasSupervisorConfigSchema] = Field(
        default_factory=list, description="Canvas-defined supervisor configurations"
    )
    version_count: int = Field(
        default=0, description="Number of version snapshots stored"
    )

    @field_validator("quality_threshold")
    @classmethod
    def validate_quality_threshold(cls, v: float) -> float:
        """Validate quality threshold is between 0 and 1."""
        if not 0 <= v <= 1:
            raise ValueError("quality_threshold must be between 0 and 1")
        return v

    @field_validator("max_iterations")
    @classmethod
    def validate_max_iterations(cls, v: int) -> int:
        """Validate max iterations is at least 1."""
        if v < 1:
            raise ValueError("max_iterations must be at least 1")
        return v

    @field_validator("quality_weights")
    @classmethod
    def validate_quality_weights(cls, v: Dict[str, float]) -> Dict[str, float]:
        """Validate quality weights sum to approximately 1.0."""
        if v:
            total = sum(v.values())
            if abs(total - 1.0) > 0.05:  # 5% tolerance
                raise ValueError(f"Quality weights must sum to 1.0, got {total}")
        return v


class TemplateListResponse(BaseModel):
    """Response for listing pipeline templates."""

    templates: List[PipelineTemplateSchema]
    total: int
    version_counts: Dict[str, int] = Field(
        default_factory=dict,
        description="Map of template name to version snapshot count",
    )


class TemplateValidateResponse(BaseModel):
    """Response for template validation."""

    valid: bool
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class TemplateCreateRequest(BaseModel):
    """Request to create a new pipeline template."""

    name: str = Field(..., description="Template name")
    description: str = Field(default="", description="Template description")
    quality_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    max_iterations: int = Field(default=10, ge=1)
    agent_categories: Dict[str, List[str]] = Field(default_factory=dict)
    routing_rules: List[Dict[str, Any]] = Field(default_factory=list)
    quality_weights: Dict[str, float] = Field(default_factory=dict)
    canvas_loops: List[Dict[str, Any]] = Field(default_factory=list)
    canvas_supervisors: List[Dict[str, Any]] = Field(default_factory=list)


class TemplateUpdateRequest(BaseModel):
    """Request to update an existing pipeline template."""

    description: Optional[str] = None
    quality_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_iterations: Optional[int] = Field(None, ge=1)
    agent_categories: Optional[Dict[str, List[str]]] = None
    routing_rules: Optional[List[Dict[str, Any]]] = None
    quality_weights: Optional[Dict[str, float]] = None
    canvas_loops: Optional[List[Dict[str, Any]]] = None
    canvas_supervisors: Optional[List[Dict[str, Any]]] = None


class PipelineRunRequest(BaseModel):
    """Request to execute a pipeline from the Agent UI."""

    session_id: str = Field(..., description="Session ID for tracking")
    task_description: str = Field(..., description="Task/objective to execute")
    template_name: Optional[str] = Field(
        None, description="Optional pipeline template name"
    )
    auto_spawn: bool = Field(default=True, description="Auto-generate missing agents")
    stream: bool = Field(default=True, description="Enable SSE streaming")


class PipelineRunResponse(BaseModel):
    """Response from pipeline execution (non-streaming mode)."""

    pipeline_id: str = Field(..., description="Unique pipeline execution ID")
    status: str = Field(..., description="running|completed|failed|blocked")
    message: str = Field(default="", description="Status message")


# Component Framework schemas


class ComponentInfoSchema(BaseModel):
    """Schema for component metadata in list response."""

    category: str = Field(..., description="Component category (directory)")
    name: str = Field(..., description="Component name (filename without extension)")
    title: str = Field(..., description="Component title from frontmatter")
    description: str = Field(..., description="Component description from frontmatter")
    path: str = Field(..., description="Relative path to component file")
    version: Optional[str] = Field(None, description="Version from frontmatter")
    template_id: Optional[str] = Field(None, description="Template ID from frontmatter")


class ComponentListResponse(BaseModel):
    """Response for listing component framework files."""

    components: List[ComponentInfoSchema]
    total: int


class ComponentRawResponse(BaseModel):
    """Response for getting raw component content."""

    content: str = Field(..., description="Raw markdown content")
    path: str = Field(..., description="Relative path to component file")
    frontmatter: Dict[str, Any] = Field(..., description="Parsed YAML frontmatter")


class ComponentUpdateRequest(BaseModel):
    """Request to update component content."""

    content: str = Field(
        ..., description="New markdown content (including frontmatter)"
    )


# ── Template Export / Import Schemas ──────────────────────────────────────


class TemplateVersionSnapshot(BaseModel):
    """A single version snapshot of a template."""

    version: int = Field(..., description="Version number")
    snapshot: PipelineTemplateSchema = Field(
        ..., description="Template state at this version"
    )
    created_at: float = Field(
        ..., description="Unix timestamp when snapshot was created"
    )
    description: str = Field(default="", description="Description of this version")


class TemplateExportResponse(BaseModel):
    """Response for template export endpoint."""

    template: PipelineTemplateSchema = Field(
        ..., description="Current template configuration"
    )
    versions: List[TemplateVersionSnapshot] = Field(
        default_factory=list, description="Version history snapshots"
    )
    exported_at: float = Field(..., description="Unix timestamp of export")
    export_format: str = Field(
        default="gaia-pipeline-template/v1",
        description="Export format identifier",
    )


class TemplateImportRequest(BaseModel):
    """Request to import a pipeline template."""

    template: PipelineTemplateSchema = Field(..., description="Template to import")
    name_conflict_strategy: str = Field(
        default="rename",
        description="Strategy for handling name conflicts: rename, overwrite, skip",
    )
    versions: List[TemplateVersionSnapshot] = Field(
        default_factory=list, description="Version snapshots to restore"
    )


class TemplateImportResponse(BaseModel):
    """Response for template import endpoint."""

    imported: bool = Field(..., description="Whether the import succeeded")
    template_name: str = Field(..., description="Final name of the imported template")
    versions_restored: int = Field(
        ..., description="Number of version snapshots restored"
    )
    conflict_resolved: Optional[str] = Field(
        None, description="How the name conflict was resolved (rename|overwrite|skip)"
    )


# ── Tier 3: Performance Metrics Schemas ──────────────────────────────


class PhaseTimingSchema(BaseModel):
    """Timing data for a single pipeline stage."""

    stage_name: str = Field(..., description="Stage identifier")
    duration_seconds: float = Field(..., description="Stage execution time")
    agent_count: int = Field(default=0, description="Number of agents in stage")
    quality_score: Optional[float] = Field(None, description="Quality score if available")


class LoopMetricsSchema(BaseModel):
    """Metrics for a single loop iteration."""

    loop_id: str = Field(..., description="Loop identifier")
    iteration_count: int = Field(default=0, description="Number of iterations")
    total_duration_seconds: float = Field(default=0.0)


class StateTransitionSchema(BaseModel):
    """A state transition event in pipeline execution."""

    from_state: str = Field(..., description="Source state")
    to_state: str = Field(..., description="Target state")
    timestamp: float = Field(default=0.0)


class AgentSelectionSchema(BaseModel):
    """An agent selection event."""

    phase: str = Field(..., description="Pipeline phase")
    agent_id: str = Field(..., description="Selected agent ID")
    timestamp: float = Field(default=0.0)


class PipelineMetricsSummarySchema(BaseModel):
    """Summary metrics for a pipeline execution."""

    pipeline_id: str = Field(..., description="Pipeline execution ID")
    total_duration_seconds: float = Field(..., description="Total execution time")
    total_tokens: int = Field(default=0, description="Total tokens processed")
    avg_tps: float = Field(default=0.0, description="Average tokens per second")
    avg_ttft: float = Field(default=0.0, description="Average time to first token")
    total_loops: int = Field(default=0, description="Total loop count")
    total_iterations: int = Field(default=0, description="Total iteration count")
    total_defects: int = Field(default=0, description="Total defects found")
    avg_quality_score: float = Field(default=0.0, description="Average quality score")
    max_quality_score: float = Field(default=0.0, description="Max quality score")
    min_quality_score: float = Field(default=0.0, description="Min quality score")


class PipelineMetricsResponseSchema(BaseModel):
    """Full metrics response for a pipeline execution."""

    success: bool = Field(default=True)
    pipeline_id: str = Field(..., description="Pipeline execution ID")
    summary: PipelineMetricsSummarySchema = Field(..., description="Summary metrics")
    phase_breakdown: Dict[str, PhaseTimingSchema] = Field(
        default_factory=dict, description="Per-stage timing"
    )
    loop_metrics: Dict[str, LoopMetricsSchema] = Field(
        default_factory=dict, description="Loop iteration metrics"
    )
    state_transitions: List[StateTransitionSchema] = Field(
        default_factory=list, description="State transition history"
    )
    defects_by_type: Dict[str, int] = Field(
        default_factory=dict, description="Defects grouped by type"
    )
    agent_selections: List[AgentSelectionSchema] = Field(
        default_factory=list, description="Agent selection history"
    )


class MetricHistoryPointSchema(BaseModel):
    """Single point in metrics history."""

    timestamp: str = Field(..., description="ISO timestamp")
    loop_id: str = Field(default="", description="Loop identifier")
    phase: str = Field(default="", description="Pipeline phase")
    metric_type: str = Field(..., description="Metric type name")
    value: float = Field(..., description="Metric value")
    metadata: Dict[str, Any] = Field(default_factory=dict)


class PipelineMetricsHistorySchema(BaseModel):
    """Metrics history for a pipeline."""

    pipeline_id: str = Field(..., description="Pipeline execution ID")
    metric_type: Optional[str] = Field(None, description="Filter by metric type")
    start_time: Optional[str] = Field(None, description="Start time filter")
    end_time: Optional[str] = Field(None, description="End time filter")
    total_points: int = Field(..., description="Number of history points")
    history: List[MetricHistoryPointSchema] = Field(default_factory=list)


class AggregateMetricStatisticsSchema(BaseModel):
    """Statistical summary for a single metric type."""

    metric_type: str = Field(..., description="Metric identifier")
    count: int = Field(..., description="Sample count")
    mean: float = Field(..., description="Mean value")
    median: float = Field(default=0.0, description="Median value")
    std_dev: float = Field(default=0.0, description="Standard deviation")
    min_value: float = Field(..., description="Minimum observed value")
    max_value: float = Field(..., description="Maximum observed value")
    trend: str = Field(default="stable", description="trend: improving|stable|declining")
    percentiles: Dict[str, float] = Field(
        default_factory=dict, description="p50, p90, p95 percentiles"
    )


class PipelineAggregateMetricsSchema(BaseModel):
    """Aggregate metrics across multiple pipeline executions."""

    success: bool = Field(default=True)
    total_pipelines: int = Field(..., description="Total pipeline count")
    time_range: Dict[str, str] = Field(
        default_factory=dict, description="Start and end timestamps"
    )
    metric_statistics: Dict[str, AggregateMetricStatisticsSchema] = Field(
        default_factory=dict, description="Per-metric statistics"
    )
    overall_health: float = Field(default=0.0, description="Overall health score 0-1")
    recommendations: List[str] = Field(
        default_factory=list, description="Optimization recommendations"
    )
