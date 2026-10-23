# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup endpoints: check readiness, run ``gaia init``, follow it.

See :mod:`gaia.ui.setup_runner`. The web UI and the TUI answer "is GAIA set up?"
with the same ``gaia init --check``, so they can never disagree.
"""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..security import require_ui_header
from ..setup_plan import SetupPlanError, build_plan, choose_chat_model
from ..setup_runner import SetupError, check, runner, step_labels, users_lemonade_url

router = APIRouter(tags=["setup"])


# Starts a subprocess that can load models, so a cross-site GET must not reach it.
@router.get("/api/setup/check", dependencies=[Depends(require_ui_header)])
def setup_check(skip_chat_model: bool = False, load: bool = False) -> Dict[str, Any]:
    """``gaia init --check --json``: ready, or the stage and reasons it is not."""
    try:
        status = check(skip_chat_model=skip_chat_model, load=load)
        users_server = users_lemonade_url()
    except SetupError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    # cloud_steps lets the UI show a cloud choice's smaller download before it starts.
    return {
        **status,
        "steps": step_labels(skip_chat_model, users_server),
        "cloud_steps": step_labels(True, users_server),
    }


# Probes the Lemonade GAIA uses, so a cross-site GET must not reach it.
@router.get("/api/setup/plan", dependencies=[Depends(require_ui_header)])
def setup_plan() -> Dict[str, Any]:
    """The server, chat model and downloads setup will use; loads nothing."""
    try:
        return build_plan()
    except SetupPlanError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e


class ChatModelRequest(BaseModel):
    """One of setup's default chat models."""

    model_id: str


@router.post("/api/setup/chat-model", dependencies=[Depends(require_ui_header)])
def setup_chat_model(body: ChatModelRequest) -> Dict[str, Any]:
    """Pick the chat model setup downloads, before it starts; returns the new plan."""
    try:
        choose_chat_model(body.model_id)
        return build_plan()
    except SetupPlanError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


class SetupRunRequest(BaseModel):
    """``skip_chat_model`` when the chat runs on a cloud provider;
    ``server_only`` to start the model server and download nothing yet."""

    skip_chat_model: bool = False
    server_only: bool = False


@router.post("/api/setup/run")
def setup_run(body: SetupRunRequest) -> Dict[str, Any]:
    """Start ``gaia init --profile gaia --yes``; follow it with ``/api/setup/status``."""
    try:
        return runner.start(body.skip_chat_model, server_only=body.server_only)
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
