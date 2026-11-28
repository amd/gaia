# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Run modes for the Agent UI: Local or Cloud, and each mode's chat model.

The rules live in :mod:`gaia.run_modes`. Switching a mode goes through
:func:`gaia.ui.routers.providers.select_model`, so the availability check, the
Local only refusal, the key replay and the model warm-up are the model
picker's own. A switch that can't run is refused and the mode stays put.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gaia import run_modes
from gaia.config import GaiaConfig, GaiaConfigError
from gaia.llm.hybrid import HybridConfigError
from gaia.logger import get_logger

from ..database import ChatDatabase
from ..dependencies import get_db
from ..security import require_ui_header
from . import providers

logger = get_logger(__name__)

router = APIRouter(tags=["run-modes"])

_NAMES = {run_modes.LOCAL: "Local", run_modes.CLOUD: "Cloud"}


def _config() -> GaiaConfig:
    try:
        return GaiaConfig.load()
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _listed_id(model_id: str) -> str:
    """*model_id* as the model server lists it, when it lists it under another id.

    Fireworks lists ``fireworks.x`` as ``fireworks.accounts/fireworks/models/x``
    on some keys, and the pick has to name the listed one. An unreadable list
    leaves the id as is: the switch then reports why the model can't run.
    """
    provider = providers.provider_of(model_id)
    if provider == providers.LOCAL:
        return model_id
    try:
        models = providers._list_models(provider)
    except (providers.ProviderError, requests.RequestException) as e:
        logger.info("Could not look %s up in the model list: %s", model_id, e)
        return model_id
    return next(
        (m["id"] for m in models if run_modes.same_model(m["id"], model_id)),
        model_id,
    )


def _is_cloud(model_id: str) -> bool:
    try:
        return providers._is_cloud_model(model_id)
    except providers.ProviderError as e:
        raise HTTPException(status_code=e.status, detail=str(e)) from e


def _view(db: ChatDatabase) -> Dict[str, Any]:
    active = providers._active_view(db)
    if active["config_error"]:
        raise HTTPException(status_code=500, detail=active["config_error"])
    config = _config()
    mode = run_modes.mode_for(active["remote"])
    modes = []
    try:
        for candidate in run_modes.MODES:
            default = run_modes.default_chat(candidate)
            chat = (
                active["model"]
                if candidate == mode
                else run_modes.chat_for(config.run_profiles, candidate)
            )
            modes.append(
                {
                    "id": candidate,
                    "active": candidate == mode,
                    "chat": {
                        "model": chat,
                        "display_name": providers.display_name(chat),
                        "provider": providers.provider_of(chat),
                        "provider_name": providers.cloud_provider_name(chat),
                        "default_model": default,
                        "default_display_name": providers.display_name(default),
                        "customized": not run_modes.same_model(chat, default),
                    },
                    "capabilities": [
                        {
                            **row,
                            "display_name": row["model"]
                            and providers.display_name(row["model"]),
                        }
                        for row in run_modes.capability_rows(candidate, chat)
                    ],
                }
            )
    except (GaiaConfigError, HybridConfigError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return {"active": mode, "modes": modes}


def _mode(mode: str) -> str:
    try:
        return run_modes.check_mode(mode)
    except run_modes.RunModeError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _save_profile(mode: str, model: Optional[str]) -> None:
    def _apply(doc: dict) -> None:
        profiles = run_modes.with_chat(doc.get("run_profiles") or {}, mode, model)
        if profiles:
            doc["run_profiles"] = profiles
        else:
            doc.pop("run_profiles", None)

    try:
        GaiaConfig.edit_doc(_apply)
    except (GaiaConfigError, HybridConfigError) as e:
        raise HTTPException(
            status_code=500, detail=f"The {_NAMES[mode]} mode was not saved: {e}"
        ) from e


@router.get("/api/run-modes")
def get_run_modes(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """Both modes, which one is in use, and what serves each capability."""
    return _view(db)


class SelectModeRequest(BaseModel):
    mode: str
    #: A chat model to set for the mode on the way in; None keeps the mode's own.
    chat: Optional[str] = None


def _require_kind(mode: str, model: str) -> None:
    """Refuse *model* as *mode*'s chat model when it runs in the other place."""
    cloud = _is_cloud(model)
    if cloud != (mode == run_modes.CLOUD):
        place, other = ("in the cloud", "Cloud") if cloud else ("on this PC", "Local")
        raise HTTPException(
            status_code=422,
            detail=f"{providers.display_name(model)} runs {place}, so it can't be "
            f"{_NAMES[mode]}'s chat model. Set it under {other} instead.",
        )


@router.post("/api/run-modes/select", dependencies=[Depends(require_ui_header)])
def select_run_mode(
    body: SelectModeRequest, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Switch to *mode* by running its chat model; refused when that can't run.

    With *chat*, that model becomes the mode's chat model as part of the switch.
    """
    target = _mode(body.mode)
    chat = (body.chat or "").strip() or None
    if chat:
        _require_kind(target, chat)
    view = _view(db)
    leaving = next(m for m in view["modes"] if m["active"])
    arriving = next(m for m in view["modes"] if m["id"] == target)
    model = _listed_id(chat or arriving["chat"]["model"])
    if run_modes.same_model(leaving["chat"]["model"], model):
        return view
    providers.select_model(providers.SelectModelRequest(model=model), db)
    return _view(db)


class ModeUpdate(BaseModel):
    chat: str


@router.put("/api/run-modes/{mode}", dependencies=[Depends(require_ui_header)])
def update_run_mode(
    mode: str, body: ModeUpdate, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Set *mode*'s chat model. In the mode in use, that switches the model now."""
    mode = _mode(mode)
    model = body.chat.strip()
    if not model:
        raise HTTPException(status_code=422, detail="No model given.")
    _require_kind(mode, model)
    if _view(db)["active"] == mode:
        providers.select_model(providers.SelectModelRequest(model=model), db)
    else:
        providers.require_runnable(model)
        _save_profile(mode, model)
    return _view(db)


@router.post("/api/run-modes/{mode}/reset", dependencies=[Depends(require_ui_header)])
def reset_run_mode(mode: str, db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """Put *mode* back on its default chat model.

    In the mode in use the default has to be able to run, or nothing changes.
    """
    mode = _mode(mode)
    view = _view(db)
    entry = next(m for m in view["modes"] if m["id"] == mode)
    if entry["active"] and entry["chat"]["customized"]:
        default = _listed_id(entry["chat"]["default_model"])
        providers.select_model(providers.SelectModelRequest(model=default), db)
    _save_profile(mode, None)
    return _view(db)
