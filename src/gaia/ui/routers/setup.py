# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup endpoints: check readiness, run ``gaia init``, follow it.

See :mod:`gaia.ui.setup_runner`. The web UI and the TUI answer "is GAIA set up?"
with the same ``gaia init --check``, so they can never disagree.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..setup_runner import SetupError, check, runner, step_labels

router = APIRouter(tags=["setup"])


@router.get("/api/setup/check")
def setup_check(skip_chat_model: bool = False, load: bool = False) -> Dict[str, Any]:
    """``gaia init --check --json``: ready, or the stage and reasons it is not."""
    try:
        status = check(skip_chat_model=skip_chat_model, load=load)
    except SetupError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {**status, "steps": step_labels(skip_chat_model)}


class SetupRunRequest(BaseModel):
    """``skip_chat_model`` when the chat runs on a cloud provider."""

    skip_chat_model: bool = False


@router.post("/api/setup/run")
def setup_run(body: SetupRunRequest) -> Dict[str, Any]:
    """Start ``gaia init --profile gaia --yes``; follow it with ``/api/setup/status``."""
    try:
        return runner.start(body.skip_chat_model)
    except SetupError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.get("/api/setup/status")
def setup_status() -> Dict[str, Any]:
    """The running (or last) setup's steps and progress; ``state: idle`` if none."""
    return runner.status() or {"state": "idle"}


@router.post("/api/setup/cancel")
def setup_cancel() -> Dict[str, Any]:
    """Stop a running setup. Anything already downloaded is kept."""
    return {"cancelled": runner.cancel()}
