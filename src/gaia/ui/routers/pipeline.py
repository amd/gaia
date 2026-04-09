# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pipeline template management endpoints for GAIA Agent UI.

Provides REST API endpoints for pipeline template CRUD operations:
- List all templates
- Get template by name
- Create new template
- Update existing template
- Delete template
- Validate template YAML
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..schemas.pipeline_templates import (
    PipelineTemplateSchema,
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateUpdateRequest,
    TemplateValidateResponse,
)
from ..services.template_service import TemplateService, TemplateValidationError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pipeline"])


def get_template_service() -> TemplateService:
    """Get template service instance."""
    return TemplateService()


@router.get("/api/v1/pipeline/templates", response_model=TemplateListResponse)
async def list_templates(service: TemplateService = Depends(get_template_service)):
    """
    List all available pipeline templates.

    Returns a list of all pipeline templates stored in the templates directory.
    Templates that fail to load are skipped with a warning logged.
    """
    try:
        templates = service.list_templates()
        return TemplateListResponse(templates=templates, total=len(templates))
    except Exception as e:
        logger.error("Failed to list templates: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to list templates. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/templates/{template_name}")
async def get_template(
    template_name: str,
    service: TemplateService = Depends(get_template_service),
):
    """
    Get a specific pipeline template.

    Returns the template configuration as JSON.
    """
    try:
        template = service.get_template(template_name)
        return template.model_dump()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TemplateValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid template", "errors": e.errors},
        )
    except Exception as e:
        logger.error("Failed to get template %s: %s", template_name, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get template. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/templates/{template_name}/raw")
async def get_template_raw(
    template_name: str,
    service: TemplateService = Depends(get_template_service),
):
    """
    Get raw YAML content for a pipeline template.

    Returns the template as raw YAML text.
    """
    try:
        yaml_content = service.get_template_raw(template_name)
        from fastapi.responses import PlainTextResponse

        return PlainTextResponse(
            content=yaml_content,
            media_type="text/yaml",
            headers={"Content-Disposition": f'inline; filename="{template_name}.yaml"'},
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to get raw template %s: %s", template_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to get template. Check server logs for details.",
        )


@router.post("/api/v1/pipeline/templates", status_code=201)
async def create_template(
    request: TemplateCreateRequest,
    service: TemplateService = Depends(get_template_service),
):
    """
    Create a new pipeline template.

    Creates a new template with the specified configuration.
    The template name must be unique and contain only alphanumeric characters,
    underscores, and hyphens.
    """
    try:
        schema = service.create_template(
            name=request.name,
            description=request.description,
            quality_threshold=request.quality_threshold,
            max_iterations=request.max_iterations,
            agent_categories=request.agent_categories,
            routing_rules=request.routing_rules,
            quality_weights=request.quality_weights,
        )
        return schema.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TemplateValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid template", "errors": e.errors},
        )
    except Exception as e:
        logger.error("Failed to create template: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to create template. Check server logs for details.",
        )


@router.put("/api/v1/pipeline/templates/{template_name}")
async def update_template(
    template_name: str,
    request: TemplateUpdateRequest,
    service: TemplateService = Depends(get_template_service),
):
    """
    Update an existing pipeline template.

    Updates only the fields provided in the request.
    Fields not specified will retain their current values.
    """
    try:
        schema = service.update_template(
            name=template_name,
            description=request.description,
            quality_threshold=request.quality_threshold,
            max_iterations=request.max_iterations,
            agent_categories=request.agent_categories,
            routing_rules=request.routing_rules,
            quality_weights=request.quality_weights,
        )
        return schema.model_dump()
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except TemplateValidationError as e:
        raise HTTPException(
            status_code=400,
            detail={"error": "Invalid template", "errors": e.errors},
        )
    except Exception as e:
        logger.error(
            "Failed to update template %s: %s", template_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to update template. Check server logs for details.",
        )


@router.delete("/api/v1/pipeline/templates/{template_name}")
async def delete_template(
    template_name: str,
    service: TemplateService = Depends(get_template_service),
):
    """
    Delete a pipeline template.

    Permanently removes the template from the templates directory.
    This action cannot be undone.
    """
    try:
        deleted = service.delete_template(template_name)
        if not deleted:
            raise HTTPException(status_code=404, detail="Template not found")
        return {"deleted": True, "template": template_name}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to delete template %s: %s", template_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to delete template. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/templates/{template_name}/validate")
async def validate_template(
    template_name: str,
    service: TemplateService = Depends(get_template_service),
):
    """
    Validate a pipeline template YAML.

    Checks the template for structural and semantic validity.
    Returns validation status with any errors or warnings.
    """
    try:
        is_valid, errors, warnings = service.validate_template(template_name)
        return TemplateValidateResponse(
            valid=is_valid,
            errors=errors,
            warnings=warnings,
        )
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(
            "Failed to validate template %s: %s", template_name, e, exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail="Failed to validate template. Check server logs for details.",
        )
