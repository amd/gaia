# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/daemon/v1/gpu-memory`` — the GPU memory step for the TUI.

The same :mod:`gaia.llm.gpu_memory` calls the Agent UI makes under
``/api/gpu-memory``, so the two apps can't disagree about the split.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

_SETTING_GB = "GPU memory to set, in decimal GB (10**9 bytes)."


class GpuMemoryApplyRequest(BaseModel):
    setting_gb: float = Field(description=_SETTING_GB)
    confirm: bool = False


class GpuMemoryRevertRequest(BaseModel):
    confirm: bool = False


class GpuMemoryPendingRequest(BaseModel):
    setting_gb: float = Field(description=_SETTING_GB)


def build_gpu_memory_router(token: str) -> APIRouter:
    """Read, apply, undo and track a change to the integrated GPU's memory."""
    from gaia.daemon.app import build_require_token
    from gaia.llm import gpu_memory

    router = APIRouter(
        prefix="/daemon/v1/gpu-memory",
        tags=["gpu-memory"],
        dependencies=[Depends(build_require_token(token))],
    )

    def answer(call: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
        try:
            return call()
        except gpu_memory.GpuMemoryError as e:
            raise HTTPException(status_code=e.status, detail=str(e)) from e

    @router.get("")
    def read(setting_gb: Optional[float] = None) -> Dict[str, Any]:
        return answer(lambda: gpu_memory.status(setting_gb))

    @router.post("/apply")
    def apply(body: GpuMemoryApplyRequest) -> Dict[str, Any]:
        return answer(lambda: gpu_memory.apply(body.setting_gb, body.confirm))

    @router.post("/revert")
    def revert(body: GpuMemoryRevertRequest) -> Dict[str, Any]:
        return answer(lambda: gpu_memory.revert(body.confirm))

    @router.post("/pending")
    def record_pending(body: GpuMemoryPendingRequest) -> Dict[str, Any]:
        return answer(lambda: gpu_memory.record_pending(body.setting_gb))

    @router.delete("/pending")
    def dismiss_pending() -> Dict[str, Any]:
        return answer(gpu_memory.dismiss_pending)

    return router
