# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pydantic schemas for GAIA Agent UI API."""

from .pipeline_templates import (
    PipelineTemplateSchema,
    RoutingRuleSchema,
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateUpdateRequest,
    TemplateValidateResponse,
)

__all__ = [
    "PipelineTemplateSchema",
    "RoutingRuleSchema",
    "TemplateListResponse",
    "TemplateValidateResponse",
    "TemplateCreateRequest",
    "TemplateUpdateRequest",
]
