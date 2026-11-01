# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""First-run setup endpoints: check readiness, run ``gaia init``, follow it.

See :mod:`gaia.ui.setup_runner`. The web UI and the TUI answer "is GAIA set up?"
with the same ``gaia init --check``, so they can never disagree.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gaia.logger import get_logger

from .. import setup_plan as plan_api
from ..security import require_ui_header
from ..setup_plan import (
    ServerNotRunning,
    SetupPlanError,
    UnknownModel,
    build_plan,
    choose_chat_model,
)
from ..setup_runner import (
    LemonadeOverridden,
    SetupBusy,
    SetupError,
    check,
    runner,
    step_labels,
    users_lemonade_url,
)

logger = get_logger(__name__)
router = APIRouter(tags=["setup"])

# Fixed words for each failure: what failed, what to do, where the details are.
# Exception text stays in the server log, never in a response.
_LOG = "Details are in the Agent UI server log."
_SERVER_DOWN = (
    "The model server isn't running yet. Start setup first, then try again. " + _LOG
)
_PLAN_FAILED = (
    "GAIA couldn't work out the setup plan. Check that the model server is "
    "running and that ~/.gaia/config.json is valid, then try again. " + _LOG
)
_CHECK_FAILED = (
    "GAIA couldn't check whether setup is done. Run `gaia init --check` in a "
    "terminal to see why. " + _LOG
)
_BUSY = (
    "Setup or that model's download is already running. Wait for it or cancel "
    "it, then try again."
)
_OVERRIDDEN = (
    "LEMONADE_BASE_URL points GAIA at another server, so setup can't switch to "
    "GAIA's own. Unset it and restart GAIA."
)
_START_FAILED = (
    "Setup couldn't start. Run `gaia init --profile gaia` in a terminal to see "
    "why. " + _LOG
)


def _plan_error(e: SetupPlanError, unknown_model: str) -> HTTPException:
    """The response for a failed plan call; *unknown_model* is the 422 wording."""
    logger.exception("Setup plan request failed")
    if isinstance(e, ServerNotRunning):
        return HTTPException(status_code=503, detail=_SERVER_DOWN)
    if isinstance(e, UnknownModel):
        return HTTPException(status_code=422, detail=unknown_model)
    return HTTPException(status_code=503, detail=_PLAN_FAILED)


def _run_error(e: SetupError) -> HTTPException:
    logger.exception("Setup could not start")
    if isinstance(e, SetupBusy):
        return HTTPException(status_code=409, detail=_BUSY)
    if isinstance(e, LemonadeOverridden):
        return HTTPException(status_code=409, detail=_OVERRIDDEN)
    return HTTPException(status_code=500, detail=_START_FAILED)


# Starts a subprocess that can load models, so a cross-site GET must not reach it.
@router.get("/api/setup/check", dependencies=[Depends(require_ui_header)])
def setup_check(skip_chat_model: bool = False, load: bool = False) -> Dict[str, Any]:
    """``gaia init --check --json``: ready, or the stage and reasons it is not."""
    try:
        status = check(skip_chat_model=skip_chat_model, load=load)
        users_server = users_lemonade_url()
    except SetupError as e:
        logger.exception("Setup check failed")
        raise HTTPException(status_code=503, detail=_CHECK_FAILED) from e
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
        raise _plan_error(e, _PLAN_FAILED) from e


def _confirm(warning: Dict[str, Any]) -> HTTPException:
    """409 whose detail says what to confirm and which field confirms it."""
    return HTTPException(status_code=409, detail=warning)


class ChatModelRequest(BaseModel):
    """A chat model GAIA knows, or one already downloaded in Lemonade."""

    model_id: str
    acknowledge_no_fit: bool = False


@router.post("/api/setup/chat-model", dependencies=[Depends(require_ui_header)])
def setup_chat_model(body: ChatModelRequest) -> Dict[str, Any]:
    """Pick the chat model, saved as ``default_model``; returns the new plan.

    One that may not fit this PC answers 409 ``no_fit`` until it is confirmed.
    """
    try:
        warning = plan_api.chat_model_warning(body.model_id)
        if warning and not body.acknowledge_no_fit:
            raise _confirm(warning)
        choose_chat_model(body.model_id, acknowledge_no_fit=True)
        return build_plan()
    except SetupPlanError as e:
        raise _plan_error(
            e,
            f"{body.model_id} isn't a chat model GAIA knows or one this Lemonade "
            "Server has downloaded. Download it first, then pick it.",
        ) from e


class SkipModelRequest(BaseModel):
    model_id: str
    skipped: bool = True


@router.post("/api/setup/models/skip", dependencies=[Depends(require_ui_header)])
def setup_skip_model(body: SkipModelRequest) -> Dict[str, Any]:
    """Skip one of setup's models, or take it back; returns the new plan."""
    try:
        plan_api.set_model_skipped(body.model_id, body.skipped)
        return build_plan()
    except SetupPlanError as e:
        raise _plan_error(
            e,
            f"{body.model_id} isn't one of the models setup downloads, so there "
            "is nothing to skip.",
        ) from e


class DownloadModelRequest(BaseModel):
    model_id: str
    acknowledge_low_disk: bool = False
    keep_trying: bool = False


@router.post(
    "/api/setup/models/download",
    status_code=202,
    dependencies=[Depends(require_ui_header)],
)
def setup_download_model(body: DownloadModelRequest) -> Dict[str, Any]:
    """Download one model now: Retry, Resume, or one skipped at setup.

    Takes it off ``skipped_models``. Follow it with ``/api/setup/status``.
    """
    try:
        warning = plan_api.download_disk_warning(body.model_id)
        if warning and not body.acknowledge_low_disk:
            raise _confirm(warning)
        client, model_id = plan_api.prepare_download(
            body.model_id, acknowledge_low_disk=True
        )
        return runner.download_model(client, model_id, body.keep_trying)
    except SetupError as e:
        raise _run_error(e) from e
    except SetupPlanError as e:
        raise _plan_error(
            e,
            f"{body.model_id} isn't a model GAIA knows or one in this Lemonade "
            "Server. Add it in Settings, then try again.",
        ) from e


class RemoveModelRequest(BaseModel):
    model_id: str


@router.post("/api/setup/models/remove", dependencies=[Depends(require_ui_header)])
def setup_remove_model(body: RemoveModelRequest) -> Dict[str, Any]:
    """Delete one model from Lemonade at the user's request; it joins
    ``skipped_models``. Returns the new plan and ``freed_gb``."""
    if runner.busy_with(body.model_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "Setup or that model's download is running, so the model can't be "
                "removed now. Wait for it or cancel it, then try again."
            ),
        )
    try:
        freed = plan_api.remove_model(body.model_id)
        return {**build_plan(), "freed_gb": freed}
    except SetupPlanError as e:
        raise _plan_error(
            e, f"{body.model_id} isn't downloaded, so there is nothing to remove."
        ) from e


class SetupRunRequest(BaseModel):
    """``skip_chat_model`` when the chat runs on a cloud provider;
    ``gaia_lemonade`` to set up GAIA's own server instead of the user's;
    ``acknowledge_low_disk`` to download past a too-small model drive;
    ``keep_trying`` to wait up to two minutes out a dropped connection."""

    skip_chat_model: bool = False
    gaia_lemonade: bool = False
    acknowledge_low_disk: bool = False
    keep_trying: bool = False


@router.post("/api/setup/run")
def setup_run(body: SetupRunRequest) -> Dict[str, Any]:
    """Start ``gaia init --profile gaia --yes``; follow it with ``/api/setup/status``.

    When the selected models need more disk than is free, answers 409
    ``low_disk`` until ``acknowledge_low_disk`` confirms it.
    """
    try:
        # Switching to GAIA's own server: the user's server's disk is moot.
        if not body.gaia_lemonade and not body.acknowledge_low_disk:
            warning = plan_api.run_disk_warning(skip_chat_model=body.skip_chat_model)
            if warning:
                raise _confirm(warning)
        return runner.start(
            body.skip_chat_model,
            body.gaia_lemonade,
            allow_low_disk=body.acknowledge_low_disk,
            keep_trying=body.keep_trying,
        )
    except SetupPlanError as e:
        raise _plan_error(e, _PLAN_FAILED) from e
    except SetupError as e:
        raise _run_error(e) from e


@router.get("/api/setup/status")
def setup_status() -> Dict[str, Any]:
    """The running (or last) setup's steps and progress; ``state: idle`` if none."""
    return runner.status() or {"state": "idle"}


class CancelRequest(BaseModel):
    model_id: Optional[str] = None


@router.post("/api/setup/cancel")
def setup_cancel(body: Optional[CancelRequest] = None) -> Dict[str, Any]:
    """Stop the running setup, or one model's download. Finished models and
    partial files are kept, so a later run or download resumes."""
    return {"cancelled": runner.cancel(body.model_id if body else None)}
