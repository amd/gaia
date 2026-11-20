# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""GPU memory step of setup and Settings: see :mod:`gaia.llm.gpu_memory`.

The TUI reads the same functions through ``/daemon/v1/gpu-memory``.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Literal, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from gaia.llm import gpu_memory
from gaia.llm.gpu_memory import (
    METHOD_APPLY,
    METHOD_GUIDED,
    METHOD_UNSUPPORTED,
    PENDING_NOT_APPLIED,
    PENDING_RESTART,
    PENDING_TOOK_EFFECT,
)

from ..models import WireModel
from ..security import require_local_pc, require_ui_header

router = APIRouter(tags=["gpu-memory"])

# Starts a root step on this PC, so only someone at this PC may ask for it.
_AT_THIS_PC = [Depends(require_ui_header), Depends(require_local_pc)]


_SETTING_GB = "GPU memory to set, in decimal GB (10**9 bytes)."

_Number = Union[int, float]


class GpuMemorySetting(WireModel):
    """The knob the user changes, in decimal GB."""

    name: str
    current_gb: float
    min_gb: float
    max_gb: float


class GpuMemoryPreset(WireModel):
    """A share of memory to offer, and the models it unlocks."""

    setting_gb: float
    gpu_gb: float
    unlocks: List[str]


class GpuMemoryModel(WireModel):
    """A model GAIA could run, and the share it needs."""

    id: str
    size_gb: _Number
    need_gb: float
    default: bool
    fits_now: bool
    fits_at_setting_gb: Optional[float]


class GpuMemoryPending(WireModel):
    """A change waiting for a restart, or checked after one."""

    setting_gb: _Number
    previous_setting_gb: Optional[_Number]
    source: Optional[Literal["apply", "revert", "guided"]]
    recorded_at: Optional[str]
    state: Literal[PENDING_RESTART, PENDING_TOOK_EFFECT, PENDING_NOT_APPLIED]


class GpuMemoryRevert(WireModel):
    """Whether GAIA can undo its own change."""

    available: bool
    previous_setting_gb: Optional[_Number]


class GpuMemoryStatusResponse(WireModel):
    """The GPU memory split. A PC the step doesn't apply to sends only the first six fields."""

    applies: bool
    show_in_setup: bool
    hidden_reason: Optional[str]
    os: str
    method: Literal[METHOD_APPLY, METHOD_GUIDED, METHOD_UNSUPPORTED]
    method_reason: str
    total_gb: Optional[float] = None
    gpu_gb: Optional[float] = None
    cpu_gb: Optional[float] = None
    setting: Optional[GpuMemorySetting] = None
    reserve_gb: Optional[float] = None
    needs_admin: Optional[bool] = None
    needs_restart: Optional[bool] = None
    presets: Optional[List[GpuMemoryPreset]] = None
    recommended_setting_gb: Optional[float] = None
    models: Optional[List[GpuMemoryModel]] = None
    pending: Optional[GpuMemoryPending] = None
    revert: Optional[GpuMemoryRevert] = None
    guided_steps: Optional[List[str]] = None
    guided_value: Optional[str] = None


class GpuMemoryApplyRequest(BaseModel):
    setting_gb: float = Field(description=_SETTING_GB)
    confirm: bool = False


class GpuMemoryRevertRequest(BaseModel):
    confirm: bool = False


class GpuMemoryPendingRequest(BaseModel):
    setting_gb: float = Field(description=_SETTING_GB)


def _answer(call: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    try:
        return call()
    except gpu_memory.GpuMemoryError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


# Reads this PC's memory through Lemonade, so a cross-site GET must not reach it.
@router.get(
    "/api/gpu-memory",
    dependencies=[Depends(require_ui_header)],
    response_model=GpuMemoryStatusResponse,
    response_model_exclude_unset=True,
)
def get_gpu_memory(setting_gb: Optional[float] = None) -> Dict[str, Any]:
    """The split now, what a bigger share unlocks, and how to change it."""
    return _answer(lambda: gpu_memory.status(setting_gb))


@router.post(
    "/api/gpu-memory/apply",
    dependencies=_AT_THIS_PC,
    response_model=GpuMemoryStatusResponse,
    response_model_exclude_unset=True,
)
def apply_gpu_memory(body: GpuMemoryApplyRequest) -> Dict[str, Any]:
    """Set the GPU's share after admin permission; takes effect after a restart."""
    return _answer(lambda: gpu_memory.apply(body.setting_gb, body.confirm))


@router.post(
    "/api/gpu-memory/revert",
    dependencies=_AT_THIS_PC,
    response_model=GpuMemoryStatusResponse,
    response_model_exclude_unset=True,
)
def revert_gpu_memory(body: GpuMemoryRevertRequest) -> Dict[str, Any]:
    """Put back the setting GAIA replaced; takes effect after a restart."""
    return _answer(lambda: gpu_memory.revert(body.confirm))


@router.post(
    "/api/gpu-memory/pending",
    dependencies=[Depends(require_ui_header)],
    response_model=GpuMemoryStatusResponse,
    response_model_exclude_unset=True,
)
def record_gpu_memory_pending(body: GpuMemoryPendingRequest) -> Dict[str, Any]:
    """Remember a value the user sets by hand, to check it after the restart."""
    return _answer(lambda: gpu_memory.record_pending(body.setting_gb))


@router.delete(
    "/api/gpu-memory/pending",
    dependencies=[Depends(require_ui_header)],
    response_model=GpuMemoryStatusResponse,
    response_model_exclude_unset=True,
)
def dismiss_gpu_memory_pending() -> Dict[str, Any]:
    """Stop reporting the pending change ("Keep current"). Changes nothing."""
    return _answer(gpu_memory.dismiss_pending)
