# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Request bodies and error mapping shared by the two GPU memory APIs.

The Agent UI's ``/api/gpu-memory`` and the daemon's ``/daemon/v1/gpu-memory``
call the same :mod:`gaia.llm.gpu_memory` functions, so they take the same
bodies and answer a refusal the same way.
"""

from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import HTTPException
from pydantic import BaseModel, Field

_SETTING_GB = "GPU memory to set, in decimal GB (10**9 bytes)."


class GpuMemoryApplyRequest(BaseModel):
    setting_gb: float = Field(description=_SETTING_GB)
    confirm: bool = False


class GpuMemoryRevertRequest(BaseModel):
    confirm: bool = False


class GpuMemoryPendingRequest(BaseModel):
    setting_gb: float = Field(description=_SETTING_GB)


def answer(call: Callable[[], Dict[str, Any]]) -> Dict[str, Any]:
    """Run *call*, turning a :class:`GpuMemoryError` into its HTTP status."""
    from gaia.llm import gpu_memory

    try:
        return call()
    except gpu_memory.GpuMemoryError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e
