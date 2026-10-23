# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Nightly learning REST API for the Agent UI: settings, adapters, learned
items and runs."""

import asyncio
import logging
from typing import Any, Dict, Optional

import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from gaia.learning.errors import LearningError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["learning"])


class LearningConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    window_start: Optional[str] = None
    window_end: Optional[str] = None
    min_idle_minutes: Optional[int] = None
    require_ac_power: Optional[bool] = None
    base_model: Optional[str] = None
    keep_adapters: Optional[int] = None


def _bad(e: LearningError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(e))


def _model() -> str:
    from gaia.learning.config import load_config
    from gaia.llm.lemonade_client import resolve_default_chat_model

    return load_config().base_model or resolve_default_chat_model()


def _live(run: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """A run still marked running whose process is gone ended without saying
    so; report that instead of a run that never finishes."""
    if run and run.get("status") == "running":
        pid = run.get("pid")
        if pid and not psutil.pid_exists(int(pid)):
            run = {
                **run,
                "status": "failed",
                "summary": "The learning process exited before finishing. "
                "See ~/.gaia/learning/cycle.log.",
            }
    return run


def _status() -> Dict[str, Any]:
    from gaia.learning.config import load_config
    from gaia.learning.environment import cached_status
    from gaia.learning.models import TRAINABLE_BASES
    from gaia.learning.registry import AdapterRegistry
    from gaia.learning.runs import list_runs

    cfg = load_config()
    model = cfg.base_model or _model()
    runs = [_live(r) for r in list_runs(limit=1)]
    return {
        "config": cfg.to_dict(),
        "base_model": model,
        "base_model_trainable": model in TRAINABLE_BASES,
        "trainable_models": sorted(TRAINABLE_BASES),
        "toolchain": cached_status(),
        "active_adapter": AdapterRegistry().active(model),
        "latest_run": runs[0] if runs else None,
    }


@router.get("/api/learning/status")
async def learning_status():
    try:
        return await asyncio.to_thread(_status)
    except LearningError as e:
        raise _bad(e) from e


@router.put("/api/learning/config")
async def update_learning_config(body: LearningConfigUpdate):
    from gaia.learning.config import update_config

    changes = body.model_dump(exclude_unset=True)
    try:
        cfg = await asyncio.to_thread(update_config, **changes)
        if changes.get("enabled"):
            from gaia.learning.launcher import register_nightly_job

            await asyncio.to_thread(register_nightly_job)
    except LearningError as e:
        raise _bad(e) from e
    return cfg.to_dict()


@router.get("/api/learning/adapters")
async def list_adapters():
    from gaia.learning.registry import AdapterRegistry

    try:
        return await asyncio.to_thread(AdapterRegistry().list)
    except LearningError as e:
        raise _bad(e) from e


@router.get("/api/learning/adapters/{adapter_id}")
async def get_adapter(adapter_id: str):
    from gaia.learning.registry import AdapterRegistry

    try:
        return await asyncio.to_thread(AdapterRegistry().get, adapter_id)
    except LearningError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _reload(model: str) -> None:
    from gaia.learning.serving import reload_with_adapter
    from gaia.llm.lemonade_client import LemonadeClient

    reload_with_adapter(LemonadeClient(verbose=False), model)


@router.post("/api/learning/adapters/{adapter_id}/activate")
async def activate_adapter(adapter_id: str):
    from gaia.learning.registry import AdapterRegistry

    try:
        manifest = await asyncio.to_thread(AdapterRegistry().activate, adapter_id)
        await asyncio.to_thread(_reload, manifest["base_model"])
    except LearningError as e:
        raise _bad(e) from e
    return manifest


@router.post("/api/learning/deactivate")
async def deactivate_adapter():
    from gaia.learning.registry import AdapterRegistry

    model = _model()
    try:
        previous = await asyncio.to_thread(AdapterRegistry().deactivate, model)
        await asyncio.to_thread(_reload, model)
    except LearningError as e:
        raise _bad(e) from e
    return {"base_model": model, "deactivated": previous}


@router.delete("/api/learning/adapters/{adapter_id}")
async def delete_adapter(adapter_id: str):
    from gaia.learning.registry import AdapterRegistry

    try:
        await asyncio.to_thread(AdapterRegistry().delete, adapter_id)
    except LearningError as e:
        raise _bad(e) from e
    return {"deleted": adapter_id}


@router.get("/api/learning/items")
async def list_items():
    from gaia.learning.dataset import Corpus

    try:
        items = await asyncio.to_thread(Corpus().items)
    except LearningError as e:
        raise _bad(e) from e
    return [
        {
            "id": i["id"],
            "kind": i["kind"],
            "title": i["title"],
            "evidence": i["evidence"],
            "examples": len(i["examples"]),
            "probes": len(i.get("probes", [])),
            "first_learned": i["first_learned"],
            "updated_at": i["updated_at"],
        }
        for i in items
    ]


@router.delete("/api/learning/items/{item_id}")
async def forget_item(item_id: str):
    from gaia.learning.dataset import Corpus
    from gaia.learning.registry import AdapterRegistry

    try:
        await asyncio.to_thread(Corpus().remove, item_id)
    except LearningError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    model = _model()
    try:
        retired = await asyncio.to_thread(
            AdapterRegistry().retire_holding, model, [item_id]
        )
        if retired:
            await asyncio.to_thread(_reload, model)
    except LearningError as e:
        raise _bad(e) from e
    return {"removed": item_id, "adapter_retired": retired}


@router.get("/api/learning/runs")
async def list_learning_runs(limit: int = 20):
    from gaia.learning.runs import list_runs

    runs = await asyncio.to_thread(list_runs, max(1, min(limit, 100)))
    return [_live(r) for r in runs]


@router.get("/api/learning/runs/{run_id}/report")
async def run_report(run_id: str):
    from gaia.learning.runs import read_run_file

    try:
        report = await asyncio.to_thread(read_run_file, run_id, "report.json")
    except LearningError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    if report is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} has no report yet.")
    return report


@router.post("/api/learning/run")
async def start_learning_run():
    from gaia.learning.launcher import spawn_cycle
    from gaia.learning.runs import list_runs

    runs = await asyncio.to_thread(list_runs, 1)
    latest = _live(runs[0] if runs else None)
    if latest and latest.get("status") == "running":
        raise HTTPException(
            status_code=409, detail="A learning cycle is already running."
        )
    pid = await asyncio.to_thread(spawn_cycle, "manual")
    return {"started": True, "pid": pid}


@router.post("/api/learning/stop")
async def stop_learning_run():
    from gaia.learning.cycle import request_stop

    await asyncio.to_thread(request_stop)
    return {"stopping": True}
