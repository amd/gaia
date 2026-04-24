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
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator

import re
import yaml

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
    ComponentListResponse,
    ComponentRawResponse,
    ComponentUpdateRequest,
    ComponentInfoSchema,
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


@router.get("/api/v1/pipeline/agents")
async def list_agents():
    """
    List all available agents from config/agents/ and pipeline templates.

    Returns a unified agent registry showing:
    - All agent definitions (YAML + MD format)
    - Category grouping
    - Capabilities, triggers, model assignments
    - Which pipeline templates reference each agent
    - Pipeline stage agents (domain-analyzer, etc.)
    - Template specialist agents (planning-analysis-strategist, etc.)
    """
    try:
        agents_dir = Path(os.getcwd()) / "config" / "agents"
        if not agents_dir.is_dir():
            return {"agents": [], "categories": {}, "total": 0}

        # Parse all YAML agents
        yaml_agents = {}
        try:
            import yaml as yaml_lib
        except ImportError:
            yaml_lib = None

        if yaml_lib:
            for yaml_file in sorted(agents_dir.glob("*.yaml")):
                try:
                    with open(yaml_file, "r", encoding="utf-8") as f:
                        data = yaml_lib.safe_load(f)
                    if not data:
                        continue
                    agent_data = data.get("agent", data)
                    agent_id = agent_data.get("id", yaml_file.stem)
                    triggers = agent_data.get("triggers", {})
                    complexity = triggers.get("complexity_range", {})
                    if isinstance(complexity, dict):
                        complexity_range = f"{complexity.get('min', 0)}-{complexity.get('max', 1)}"
                    elif isinstance(complexity, list) and len(complexity) == 2:
                        complexity_range = f"{complexity[0]}-{complexity[1]}"
                    else:
                        complexity_range = "0-1"

                    yaml_agents[agent_id] = {
                        "id": agent_id,
                        "name": agent_data.get("name", agent_id),
                        "category": agent_data.get("category", "unknown"),
                        "description": (agent_data.get("description", "") or "").strip(),
                        "model_id": agent_data.get("model_id", None),
                        "capabilities": agent_data.get("capabilities", []),
                        "keywords": triggers.get("keywords", []),
                        "phases": triggers.get("phases", []),
                        "complexity_range": complexity_range,
                        "tools": agent_data.get("tools", []),
                        "enabled": agent_data.get("enabled", True),
                        "version": agent_data.get("version", "1.0.0"),
                        "source": "yaml",
                        "templates_using": [],
                    }
                except Exception as e:
                    logger.warning(f"Failed to parse agent YAML {yaml_file}: {e}")

        # Parse MD pipeline stage agents
        md_agents = {}
        for md_file in sorted(agents_dir.glob("*.md")):
            try:
                with open(md_file, "r", encoding="utf-8") as f:
                    content = f.read()
                # Extract YAML frontmatter
                if not content.startswith("---"):
                    continue
                parts = content.split("---", 2)
                if len(parts) < 3:
                    continue
                frontmatter = parts[1].strip()
                if yaml_lib:
                    fm_data = yaml_lib.safe_load(frontmatter)
                else:
                    # Minimal fallback parse
                    fm_data = {}
                    for line in frontmatter.split("\n"):
                        if ":" in line:
                            key, _, val = line.partition(":")
                            fm_data[key.strip()] = val.strip().strip('"')

                agent_id = fm_data.get("id", md_file.stem)
                category = fm_data.get("category", "pipeline_stage")
                md_agents[agent_id] = {
                    "id": agent_id,
                    "name": fm_data.get("name", agent_id),
                    "category": category,
                    "description": (fm_data.get("description", "") or "").strip(),
                    "model_id": fm_data.get("model_id", None),
                    "capabilities": fm_data.get("capabilities", []),
                    "keywords": fm_data.get("triggers", {}).get("keywords", []) if isinstance(fm_data.get("triggers"), dict) else [],
                    "phases": fm_data.get("triggers", {}).get("phases", []) if isinstance(fm_data.get("triggers"), dict) else [],
                    "complexity_range": "0-1",
                    "tools": fm_data.get("tools", []),
                    "enabled": fm_data.get("enabled", True),
                    "version": fm_data.get("version", "1.0.0"),
                    "source": "pipeline_stage",
                    "entrypoint": fm_data.get("pipeline.entrypoint", None),
                    "templates_using": [],
                }
            except Exception as e:
                logger.warning(f"Failed to parse agent MD {md_file}: {e}")

        # Cross-reference with pipeline templates
        templates_dir = Path(os.getcwd()) / "config" / "pipeline_templates"
        template_agent_map = {}  # agent_id -> [template_names]
        if templates_dir.is_dir() and yaml_lib:
            for tmpl_file in sorted(templates_dir.glob("*.yaml")):
                try:
                    with open(tmpl_file, "r", encoding="utf-8") as f:
                        tmpl_data = yaml_lib.safe_load(f)
                    if not tmpl_data:
                        continue
                    tmpl_name = tmpl_data.get("name", tmpl_file.stem)
                    agent_cats = tmpl_data.get("agent_categories", {})
                    for cat_agents in agent_cats.values():
                        if isinstance(cat_agents, list):
                            for aid in cat_agents:
                                template_agent_map.setdefault(aid, []).append(tmpl_name)
                except Exception as e:
                    logger.warning(f"Failed to parse template {tmpl_file}: {e}")

        # Apply template references to agents
        for aid, tmpl_names in template_agent_map.items():
            if aid in yaml_agents:
                yaml_agents[aid]["templates_using"] = tmpl_names
            elif aid in md_agents:
                md_agents[aid]["templates_using"] = tmpl_names

        # Build category index
        all_agents = {**yaml_agents, **md_agents}
        categories = {}
        for aid, agent in all_agents.items():
            cat = agent["category"]
            categories.setdefault(cat, []).append(aid)

        return {
            "agents": list(all_agents.values()),
            "categories": categories,
            "total": len(all_agents),
        }
    except Exception as e:
        logger.error("Failed to list agents: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to list agents. Check server logs for details.",
        )


# Regex pattern for path traversal protection - only allow safe characters
_AGENT_ID_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


class AgentFileUpdate(BaseModel):
    """Request body for updating an agent file."""
    content: str = Field(..., description="Raw YAML/MD content to save")


@router.get("/api/v1/pipeline/agents/{agent_id}/raw")
async def get_agent_raw(agent_id: str):
    """
    Get raw YAML/MD content for an agent file.

    Returns the agent file content as JSON with agent_id, source filename, and content.
    Path traversal protection: agent_id must match alphanumeric, underscore, hyphen only.
    """
    # Security: validate agent_id to prevent path traversal
    if not _AGENT_ID_PATTERN.match(agent_id):
        raise HTTPException(status_code=400, detail="Invalid agent_id format")

    agents_dir = Path(os.getcwd()) / "config" / "agents"
    if not agents_dir.is_dir():
        raise HTTPException(status_code=404, detail="Agents directory not found")

    # Try .yaml first, then .md
    for ext in [".yaml", ".md"]:
        filepath = agents_dir / f"{agent_id}{ext}"
        if filepath.exists():
            content = filepath.read_text(encoding="utf-8")
            return {"agent_id": agent_id, "source": filepath.name, "content": content}

    raise HTTPException(status_code=404, detail=f"Agent file not found for: {agent_id}")


@router.put("/api/v1/pipeline/agents/{agent_id}/raw")
async def update_agent_raw(agent_id: str, update: AgentFileUpdate):
    """
    Update raw YAML/MD content for an agent file.

    Saves the provided content back to the existing agent file.
    Path traversal protection: agent_id must match alphanumeric, underscore, hyphen only.
    """
    # Security: validate agent_id to prevent path traversal
    if not _AGENT_ID_PATTERN.match(agent_id):
        raise HTTPException(status_code=400, detail="Invalid agent_id format")

    agents_dir = Path(os.getcwd()) / "config" / "agents"
    if not agents_dir.is_dir():
        raise HTTPException(status_code=404, detail="Agents directory not found")

    # Find existing file
    for ext in [".yaml", ".md"]:
        filepath = agents_dir / f"{agent_id}{ext}"
        if filepath.exists():
            filepath.write_text(update.content, encoding="utf-8")
            return {"agent_id": agent_id, "source": filepath.name, "updated": True}

    raise HTTPException(status_code=404, detail=f"Agent file not found for: {agent_id}")


# =============================================================================
# Component Framework Endpoints
# =============================================================================

# Valid component categories (whitelist)
VALID_COMPONENT_CATEGORIES = [
    "memory",
    "knowledge",
    "tasks",
    "commands",
    "documents",
    "checklists",
    "personas",
    "workflows",
    "templates"
]

# Regex pattern for component name validation - only allow safe characters
_COMPONENT_NAME_PATTERN = re.compile(r'^[a-zA-Z0-9_-]+$')


@router.get("/api/v1/pipeline/components/list", response_model=ComponentListResponse)
async def list_components():
    """
    List all component framework files grouped by category.

    Returns metadata for all 44 component files across 9 categories:
    - memory, knowledge, tasks, commands, documents
    - checklists, personas, workflows, templates

    Each component includes title, description, version from frontmatter.
    """
    try:
        from gaia.utils.component_loader import ComponentLoader

        loader = ComponentLoader()
        all_components = loader.list_components()

        components = []
        for comp_path in all_components:
            try:
                # Parse category from path (e.g., "memory/working-memory.md")
                path_parts = comp_path.split("/")
                if len(path_parts) < 2:
                    continue

                category = path_parts[0]
                name = path_parts[-1].replace(".md", "")

                # Load frontmatter for metadata
                component = loader.load_component(comp_path)
                frontmatter = component["frontmatter"]

                components.append(ComponentInfoSchema(
                    category=category,
                    name=name,
                    title=frontmatter.get("title", frontmatter.get("template_id", name)),
                    description=frontmatter.get("description", ""),
                    path=comp_path,
                    version=frontmatter.get("version", None),
                    template_id=frontmatter.get("template_id", None),
                ))
            except Exception as e:
                logger.warning(f"Failed to load component {comp_path}: {e}")
                continue

        return ComponentListResponse(components=components, total=len(components))

    except ImportError as e:
        logger.error("ComponentLoader not available: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Component framework not available. Check server logs for details.",
        )
    except Exception as e:
        logger.error("Failed to list components: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to list components. Check server logs for details.",
        )


@router.get("/api/v1/pipeline/components/{category}/{component_name}/raw")
async def get_component_raw(category: str, component_name: str):
    """
    Get raw markdown content for a component framework file.

    Returns the component file content as JSON with path, frontmatter, and content.

    Path traversal protection:
    - Category must be in whitelist (9 valid categories)
    - Component name must match alphanumeric, underscore, hyphen only
    """
    # Security: validate category against whitelist
    if category not in VALID_COMPONENT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: {', '.join(VALID_COMPONENT_CATEGORIES)}"
        )

    # Security: validate component_name to prevent path traversal
    if not _COMPONENT_NAME_PATTERN.match(component_name):
        raise HTTPException(status_code=400, detail="Invalid component_name format")

    try:
        from gaia.utils.component_loader import ComponentLoader

        loader = ComponentLoader()
        component_path = f"{category}/{component_name}.md"

        # Load component using ComponentLoader (includes SEC-003 path traversal protection)
        component = loader.load_component(component_path)

        return ComponentRawResponse(
            content=f"---\n{yaml.dump(component['frontmatter'], default_flow_style=False, allow_unicode=True, sort_keys=False)}---\n{component['content']}",
            path=component_path,
            frontmatter=component["frontmatter"],
        )

    except ImportError as e:
        logger.error("ComponentLoader not available: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Component framework not available. Check server logs for details.",
        )
    except Exception as e:
        if "Component not found" in str(e) or "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=f"Component not found: {category}/{component_name}.md")
        logger.error("Failed to get component %s/%s: %s", category, component_name, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to get component. Check server logs for details.",
        )


@router.put("/api/v1/pipeline/components/{category}/{component_name}/raw")
async def update_component_raw(category: str, component_name: str, update: ComponentUpdateRequest):
    """
    Update raw markdown content for a component framework file.

    Saves the provided content back to the component file.
    Content should include both frontmatter (---YAML---) and markdown body.

    Path traversal protection:
    - Category must be in whitelist (9 valid categories)
    - Component name must match alphanumeric, underscore, hyphen only
    - ComponentLoader.save_component() provides SEC-003 path traversal protection
    """
    # Security: validate category against whitelist
    if category not in VALID_COMPONENT_CATEGORIES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid category. Must be one of: {', '.join(VALID_COMPONENT_CATEGORIES)}"
        )

    # Security: validate component_name to prevent path traversal
    if not _COMPONENT_NAME_PATTERN.match(component_name):
        raise HTTPException(status_code=400, detail="Invalid component_name format")

    try:
        from gaia.utils.component_loader import ComponentLoader

        loader = ComponentLoader()
        component_path = f"{category}/{component_name}.md"

        # Parse frontmatter from content if present
        content = update.content
        frontmatter = None

        if content.startswith("---\n"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_text = parts[1].strip()
                frontmatter = yaml.safe_load(frontmatter_text)
                content = parts[2].strip()  # Everything after the closing ---

        # Save component using ComponentLoader (includes SEC-003 path traversal protection)
        saved_path = loader.save_component(component_path, content, frontmatter)

        return {
            "success": True,
            "path": component_path,
            "full_path": saved_path,
        }

    except ImportError as e:
        logger.error("ComponentLoader not available: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Component framework not available. Check server logs for details.",
        )
    except HTTPException:
        raise
    except Exception as e:
        if "Component not found" in str(e) or "not found" in str(e).lower():
            raise HTTPException(status_code=404, detail=f"Component not found: {category}/{component_name}.md")
        if "Path traversal" in str(e):
            raise HTTPException(status_code=400, detail="Invalid path: path traversal detected")
        logger.error("Failed to update component %s/%s: %s", category, component_name, e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="Failed to update component. Check server logs for details.",
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

                    # Execute pipeline in a background thread (recursive mode with SSE events)
                    loop = asyncio.get_event_loop()
                    result = await loop.run_in_executor(
                        None,
                        _execute_and_record,
                        pipeline_id,
                        request.session_id,
                        request.task_description,
                        request.auto_spawn,
                        request.template_name,
                        True,  # recursive=True
                        output_handler,
                    )

                    # Stream any buffered output handler events
                    output_handler.drain(output_handler.event_queue)

                    # Emit completion event (include recursive pipeline metadata)
                    try:
                        done_payload = {
                            'type': 'done',
                            'pipeline_id': pipeline_id,
                            'status': result.get('pipeline_status', 'unknown'),
                            'result': result,
                        }
                        # Add recursive pipeline metadata if present
                        if 'loop_count' in result:
                            done_payload['loop_count'] = result['loop_count']
                        if 'quality_scores' in result:
                            done_payload['quality_scores'] = result['quality_scores']
                        if 'decisions' in result:
                            done_payload['decisions'] = result['decisions']
                        done_event = json.dumps(done_payload)
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
                result = _execute_and_record(
                    pipeline_id=pipeline_id,
                    session_id=request.session_id,
                    task_description=request.task_description,
                    auto_spawn=request.auto_spawn,
                    template_name=request.template_name,
                    recursive=False,
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


def _execute_and_record(
    pipeline_id: str,
    session_id: str,
    task_description: str,
    auto_spawn: bool,
    template_name: str | None = None,
    recursive: bool = False,
    sse_handler=None,
) -> dict:
    """Execute pipeline and record the result in execution history."""
    start_time = time.time()
    try:
        result = _execute_pipeline_sync(
            task_description=task_description,
            auto_spawn=auto_spawn,
            template_name=template_name,
            recursive=recursive,
            sse_handler=sse_handler,
        )
        end_time = time.time()
        record_execution(
            pipeline_id=pipeline_id,
            session_id=session_id,
            task_description=task_description,
            status=result.get("pipeline_status", "unknown"),
            start_time=start_time,
            end_time=end_time,
            quality_scores=result.get("quality_scores", []),
            loop_count=result.get("loop_count", 0),
            decisions=result.get("decisions", []),
            agents_used=result.get("agents_used", []),
        )
        return result
    except Exception as e:
        end_time = time.time()
        record_execution(
            pipeline_id=pipeline_id,
            session_id=session_id,
            task_description=task_description,
            status="failed",
            start_time=start_time,
            end_time=end_time,
            agents_used=[],
        )
        raise


def _execute_pipeline_sync(
    task_description: str,
    auto_spawn: bool,
    template_name: str | None = None,
    recursive: bool = False,
    sse_handler=None,
) -> dict:
    """Execute pipeline synchronously (for executor thread).

    Args:
        task_description: Task description for the pipeline.
        auto_spawn: Whether to auto-spawn missing agents.
        template_name: Optional pipeline template name.
        recursive: If True, use PipelineEngine with recursive loop support.
        sse_handler: Optional SSE handler for event emission during execution.
    """
    if recursive:
        from gaia.pipeline.orchestrator import _execute_recursive_pipeline

        return _execute_recursive_pipeline(
            task_description=task_description,
            sse_handler=sse_handler,
            template_name=template_name or "generic",
        )

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

    def emit(self, event_type: str, data: dict):
        """Emit an SSE event to the stream."""
        event = {"type": event_type, **data}
        try:
            self.event_queue.put(event)
        except Exception:
            pass

    def drain(self, q: queue.Queue):
        """Yield all queued events."""
        while not q.empty():
            try:
                event = q.get_nowait()
                yield f"data: {json.dumps(event)}\n\n"
            except queue.Empty:
                break


# ── Tier 3: Execution History ────────────────────────────────────────────

# In-memory execution history (last 100 runs)
_execution_history: list[dict] = []
_MAX_HISTORY = 100


def record_execution(pipeline_id: str, session_id: str, task_description: str,
                     status: str, start_time: float, end_time: float | None = None,
                     quality_scores: list[float] | None = None,
                     loop_count: int = 0, decisions: list[dict] | None = None,
                     agents_used: list[str] | None = None):
    """Record a pipeline execution in the history log."""
    entry = {
        "pipeline_id": pipeline_id,
        "session_id": session_id,
        "task_description": task_description,
        "status": status,
        "start_time": start_time,
        "end_time": end_time or time.time(),
        "duration_seconds": round((end_time or time.time()) - start_time, 2),
        "quality_scores": quality_scores or [],
        "loop_count": loop_count,
        "decisions": decisions or [],
        "agents_used": agents_used or [],
        "avg_quality": round(sum(quality_scores) / len(quality_scores), 3) if quality_scores else None,
    }
    _execution_history.append(entry)
    # Trim to max
    while len(_execution_history) > _MAX_HISTORY:
        _execution_history.pop(0)


@router.get("/api/v1/pipeline/executions")
async def list_executions(limit: int = 20, offset: int = 0):
    """
    List past pipeline executions, most recent first.

    Returns summary info for each execution including status, duration,
    quality scores, and loop count.
    """
    reversed_history = list(reversed(_execution_history))
    total = len(reversed_history)
    page = reversed_history[offset:offset + limit]
    return {
        "executions": page,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/api/v1/pipeline/executions/{pipeline_id}")
async def get_execution(pipeline_id: str):
    """Get detailed info for a specific pipeline execution."""
    for entry in _execution_history:
        if entry["pipeline_id"] == pipeline_id:
            return {"execution": entry}
    raise HTTPException(status_code=404, detail="Execution not found")


@router.delete("/api/v1/pipeline/executions/{pipeline_id}")
async def delete_execution(pipeline_id: str):
    """Delete a specific pipeline execution from history."""
    before = len(_execution_history)
    _execution_history[:] = [e for e in _execution_history if e["pipeline_id"] != pipeline_id]
    deleted = before - len(_execution_history)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Execution not found")
    return {"deleted": True, "pipeline_id": pipeline_id}


@router.post("/api/v1/pipeline/executions/{pipeline_id}/replay")
async def replay_execution(pipeline_id: str):
    """
    Re-execute a pipeline using the same configuration as a past run.

    Returns the task description and agents used for manual replay.
    The caller should invoke /api/v1/pipeline/run with this configuration.
    """
    for entry in _execution_history:
        if entry["pipeline_id"] == pipeline_id:
            return {
                "pipeline_id": pipeline_id,
                "task_description": entry["task_description"],
                "agents_used": entry["agents_used"],
                "quality_scores": entry["quality_scores"],
                "loop_count": entry["loop_count"],
                "replay_suggestion": f"Re-run with {len(entry['agents_used'])} agents from previous execution",
            }
    raise HTTPException(status_code=404, detail="Execution not found")


# ── Tier 3: Template Versioning ─────────────────────────────────────────

_template_versions: dict[str, list[dict]] = {}


@router.get("/api/v1/pipeline/templates/{template_name}/versions")
async def list_template_versions(template_name: str, service: TemplateService = Depends(get_template_service)):
    """List version history for a template."""
    versions = _template_versions.get(template_name, [])
    # Also check if template exists
    template = service.get_template(template_name)
    if not template and not versions:
        raise HTTPException(status_code=404, detail="Template not found")
    return {
        "template_name": template_name,
        "versions": versions,
        "current": template.get("version", 1) if template else None,
    }


@router.post("/api/v1/pipeline/templates/{template_name}/version")
async def version_template(template_name: str,
                           service: TemplateService = Depends(get_template_service)):
    """Create a new version snapshot of a template."""
    template = service.get_template(template_name)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    current_version = template.get("version", 1)
    new_version = current_version + 1

    version_entry = {
        "version": current_version,
        "snapshot": template.copy(),
        "created_at": time.time(),
        "description": template.get("description", ""),
    }

    if template_name not in _template_versions:
        _template_versions[template_name] = []
    _template_versions[template_name].append(version_entry)

    # Update template version
    template["version"] = new_version

    return {
        "template_name": template_name,
        "previous_version": current_version,
        "new_version": new_version,
        "versioned": True,
    }
