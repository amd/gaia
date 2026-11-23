# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Image creation settings: the model, size and steps GAIA creates images with.

The choices come from Lemonade's catalog (:mod:`gaia.sd.settings`). The
choice is saved in ``~/.gaia/config.json``, so the TUI uses the same one.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from gaia.config import IMAGE_MAX_STEPS, GaiaConfig, GaiaConfigError

from ..database import ChatDatabase
from ..dependencies import get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["images"])

#: The model the flagship creates images with when none is saved.
DEFAULT_IMAGE_MODEL = "SDXL-Turbo"


class ImageModel(BaseModel):
    id: str
    recipe: Optional[str] = None
    downloaded: bool
    downloads_on_first_use: bool
    size_gb: Optional[float] = None
    default_size: str
    default_steps: int
    sizes: List[str]


class ImageSettings(BaseModel):
    model: str
    #: None means the model's own default.
    size: Optional[str]
    steps: Optional[int]
    max_steps: int = IMAGE_MAX_STEPS
    models: List[ImageModel]
    #: False when the saved model is no longer on this Lemonade.
    model_available: bool
    chat_model: str
    #: False when the chat model is too small to be offered image creation.
    chat_model_creates_images: bool


class ImageSettingsUpdate(BaseModel):
    """Fields left out keep their value; ``null`` size or steps means automatic."""

    model: Optional[str] = None
    size: Optional[str] = None
    steps: Optional[int] = None


def _load_config() -> GaiaConfig:
    try:
        return GaiaConfig.load()
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _choices() -> List[Dict[str, Any]]:
    from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError
    from gaia.sd.settings import image_model_choices

    client = LemonadeClient(verbose=False)
    try:
        catalog = client.list_models(show_all=True)
    except LemonadeClientError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not read the image models from Lemonade at "
            f"{client.base_url}: {e}",
        ) from e
    return image_model_choices(catalog.get("data", []))


def _chat_model(db: ChatDatabase) -> str:
    from gaia.llm.lemonade_client import resolve_default_chat_model

    return db.get_setting("custom_model") or resolve_default_chat_model()


def _settings(
    config: GaiaConfig, choices: List[Dict[str, Any]], db: ChatDatabase
) -> ImageSettings:
    from gaia.agents.base.model_features import features_for_model

    model = config.image_model or DEFAULT_IMAGE_MODEL
    chat_model = _chat_model(db)
    return ImageSettings(
        model=model,
        size=config.image_size,
        steps=config.image_steps,
        models=[ImageModel(**c) for c in choices],
        model_available=any(c["id"] == model for c in choices),
        chat_model=chat_model,
        chat_model_creates_images=features_for_model(chat_model).image_generation,
    )


@router.get("/api/images/settings", response_model=ImageSettings)
def get_image_settings(db: ChatDatabase = Depends(get_db)) -> ImageSettings:
    """The saved image model, size and steps, and the models this Lemonade offers."""
    return _settings(_load_config(), _choices(), db)


@router.put("/api/images/settings", response_model=ImageSettings)
def update_image_settings(
    body: ImageSettingsUpdate, db: ChatDatabase = Depends(get_db)
) -> ImageSettings:
    """Save the image model, size or steps.

    A new model resets size and steps to automatic unless the request sets
    them, since sizes differ per model.
    """
    from gaia.sd.settings import check_image_choice

    config = _load_config()
    choices = _choices()
    sent = body.model_fields_set
    model = body.model if "model" in sent and body.model else None
    new_model = model is not None and model != (
        config.image_model or DEFAULT_IMAGE_MODEL
    )
    model = model or config.image_model or DEFAULT_IMAGE_MODEL
    size = body.size if "size" in sent else (None if new_model else config.image_size)
    steps = (
        body.steps if "steps" in sent else (None if new_model else config.image_steps)
    )
    try:
        check_image_choice(choices, model, size, steps)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    try:
        saved = GaiaConfig.save_keys(
            {"image_model": model, "image_size": size, "image_steps": steps}
        )
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    return _settings(saved, choices, db)
