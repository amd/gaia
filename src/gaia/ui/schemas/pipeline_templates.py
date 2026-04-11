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


class TemplateUpdateRequest(BaseModel):
    """Request to update an existing pipeline template."""

    description: Optional[str] = None
    quality_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    max_iterations: Optional[int] = Field(None, ge=1)
    agent_categories: Optional[Dict[str, List[str]]] = None
    routing_rules: Optional[List[Dict[str, Any]]] = None
    quality_weights: Optional[Dict[str, float]] = None


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
