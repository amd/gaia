# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Live CPU, memory, GPU, NPU and network usage for the chat's resource strip."""

from __future__ import annotations

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import resource_monitor
from ..security import require_ui_header

router = APIRouter(tags=["system"])

_HISTORY = "Percent per sample, oldest first, one per interval_ms."


class CpuUsage(BaseModel):
    percent: float
    ghz: Optional[float] = Field(
        description="Current clock speed, when the OS reports it."
    )
    history: List[float] = Field(description=_HISTORY)


class MemoryUsage(BaseModel):
    used_bytes: int
    total_bytes: int
    percent: float
    history: List[float] = Field(description=_HISTORY)


class DeviceUsage(BaseModel):
    id: str
    name: str
    kind: Literal["gpu", "npu"]
    percent: float = Field(description="Load of the device's busiest engine.")
    memory_used_bytes: Optional[int] = Field(
        description="Dedicated plus shared memory in use, when the OS reports it."
    )
    history: List[float] = Field(description=_HISTORY)


class NetworkUsage(BaseModel):
    id: str
    name: str = Field(description='The connection\'s name, such as "Ethernet 3".')
    description: str = Field(description="The adapter's hardware name.")
    kind: Literal["ethernet", "wifi", "network"]
    send_bps: int = Field(description="Bits per second sent.")
    recv_bps: int = Field(description="Bits per second received.")
    send_history: List[int] = Field(description="Bits per second, oldest first.")
    recv_history: List[int] = Field(description="Bits per second, oldest first.")


class ResourceUsage(BaseModel):
    interval_ms: int
    ready: bool = Field(description="False until the first sample is in.")
    cpu: Optional[CpuUsage] = None
    memory: Optional[MemoryUsage] = None
    devices: List[DeviceUsage] = Field(default_factory=list)
    networks: List[NetworkUsage] = Field(default_factory=list)


# Reads this PC's hardware load, so a cross-site GET must not reach it.
@router.get(
    "/api/system/resources",
    response_model=ResourceUsage,
    dependencies=[Depends(require_ui_header)],
)
def get_resources() -> ResourceUsage:
    """Latest usage plus about a minute of history, sampled once a second."""
    try:
        return ResourceUsage(**resource_monitor.get_sampler().snapshot())
    except resource_monitor.ResourceProbeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
