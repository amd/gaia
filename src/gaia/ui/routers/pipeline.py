# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Pipeline template management and execution endpoints for GAIA Agent UI.

Provides REST API endpoints for pipeline template CRUD operations:
- List all templates
- Get template by name
- Create new template
- Update existing template
- Delete template
- Validate template YAML
- Execute pipeline with SSE streaming (POST /api/v1/pipeline/run)
"""

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from ..database import ChatDatabase
from ..dependencies import get_db

from ..schemas.pipeline_templates import (
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineTemplateSchema,
    TemplateCreateRequest,
    TemplateListResponse,
    TemplateUpdateRequest,
    TemplateValidateResponse,
)
from ..services.template_service import TemplateService, TemplateValidationError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pipeline"])


# Session locks and semaphore for pipeline execution (matches chat.py pattern)
_pipeline_session_locks: dict[str, asyncio.Lock] = {}
_pipeline_semaphore = asyncio.Semaphore(5)  # Max 5 concurrent pipeline runs


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


@router.post("/api/v1/pipeline/run")
async def run_pipeline_endpoint(
    request: PipelineRunRequest,
    http_request: Request,
    db: ChatDatabase = Depends(get_db),
):
    """
    Execute a pipeline task with SSE streaming.

    Runs the full 5-stage pipeline orchestration:
    1. Domain Analysis
    2. Workflow Modeling
    3. Loom Building
    4. Gap Detection (with optional auto-spawn)
    5. Pipeline Execution

    When ``stream=True`` (default), returns a Server-Sent Events stream
    with typed events: ``status``, ``step``, ``thinking``, ``tool_start``,
    ``tool_end``, ``tool_result``, ``done``, ``error``.

    When ``stream=False``, returns a single JSON response after completion.
    """
    # Verify session exists
    session = db.get_session(request.session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    pipeline_id = str(uuid.uuid4())

    # Acquire session lock (prevent duplicate runs for same session)
    session_lock = _pipeline_session_locks.setdefault(
        request.session_id, asyncio.Lock()
    )
    try:
        await asyncio.wait_for(session_lock.acquire(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning(
            "Force-releasing stuck pipeline session lock for %s",
            request.session_id,
        )
        try:
            session_lock.release()
        except RuntimeError:
            pass
        await session_lock.acquire()

    # Acquire semaphore (limit concurrent pipeline runs)
    try:
        await asyncio.wait_for(_pipeline_semaphore.acquire(), timeout=60.0)
    except asyncio.TimeoutError:
        session_lock.release()
        raise HTTPException(
            status_code=429,
            detail=(
                "The server is busy processing other pipeline runs. "
                "Please try again."
            ),
        )

    # Create an SSE output handler for this run
    output_handler = _PipelineSSEHandler()

    try:
        if request.stream:
            async def _release_locks():
                try:
                    session_lock.release()
                except RuntimeError:
                    pass
                try:
                    _pipeline_semaphore.release()
                except ValueError:
                    pass

            async def _stream_pipeline_events() -> AsyncGenerator[str, None]:
                """Stream pipeline events as SSE."""
                try:
                    # Emit start event
                    try:
                        start_event = json.dumps({
                            'type': 'status',
                            'status': 'starting',
                            'message': 'Initializing pipeline...',
                            'pipeline_id': pipeline_id,
                        })
                    except (TypeError, ValueError):
                        start_event = '{"type": "status", "status": "starting", "message": "Initializing pipeline..."}'
                    yield f"data: {start_event}\n\n"

                    # Execute pipeline in a background thread
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        _execute_pipeline_sync,
                        request.task_description,
                        request.auto_spawn,
                        request.template_name,
                    )

                    # Stream any buffered output handler events
                    output_handler.drain(output_handler.event_queue)

                    # Emit completion event
                    try:
                        done_event = json.dumps({
                            'type': 'done',
                            'pipeline_id': pipeline_id,
                            'status': result.get('pipeline_status', 'unknown'),
                            'result': result,
                        })
                    except (TypeError, ValueError):
                        done_event = json.dumps({
                            'type': 'done',
                            'pipeline_id': pipeline_id,
                            'status': 'unknown',
                            'result': {'pipeline_status': 'unknown', 'error': 'Serialization error'},
                        })
                    yield f"data: {done_event}\n\n"
                except Exception as e:
                    logger.error(f"Pipeline streaming error: {e}", exc_info=True)
                    try:
                        error_event = json.dumps({
                            'type': 'error',
                            'content': str(e),
                            'pipeline_id': pipeline_id,
                        })
                    except (TypeError, ValueError):
                        error_event = '{"type": "error", "content": "Internal error"}'
                    yield f"data: {error_event}\n\n"

            return StreamingResponse(
                _stream_pipeline_events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "X-Accel-Buffering": "no",
                },
                background=BackgroundTask(_release_locks),
            )
        else:
            # Non-streaming: execute and return result
            try:
                result = _execute_pipeline_sync(
                    request.task_description,
                    request.auto_spawn,
                    request.template_name,
                )
                return PipelineRunResponse(
                    pipeline_id=pipeline_id,
                    status=result.get("pipeline_status", "unknown"),
                    message="Pipeline execution completed",
                )
            finally:
                session_lock.release()
                _pipeline_semaphore.release()

    finally:
        # For streaming path, locks are released in BackgroundTask after response
        # For non-streaming path, this is a no-op (already released above)
        pass


def _execute_pipeline_sync(
    task_description: str,
    auto_spawn: bool,
    template_name: str | None = None,
) -> dict:
    """Execute pipeline synchronously (for executor thread)."""
    try:
        from gaia.pipeline.orchestrator import run_pipeline as _run_pipeline

        return _run_pipeline(
            task_description=task_description,
            auto_spawn=auto_spawn,
        )
    except Exception as e:
        logger.error(f"Pipeline execution failed: {e}", exc_info=True)
        return {
            "pipeline_status": "failed",
            "error": str(e),
        }


class _PipelineSSEHandler:
    """Simple event queue for pipeline SSE streaming."""

    def __init__(self):
        self.event_queue: queue.Queue = queue.Queue()

    def drain(self, q: queue.Queue):
        """Yield all queued events."""
        while not q.empty():
            try:
                event = q.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
            except queue.Empty:
                break
