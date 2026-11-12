# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The image model, size and steps a user can pick, read off Lemonade's catalog.

Each Lemonade image model publishes ``image_defaults``: its default width,
height and steps, and a ``soft_pixel_cap`` past which it degrades. The sizes
offered here stay under that cap, so a saved setting is one the model can run.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from gaia.config import IMAGE_MAX_STEPS, parse_image_size

#: Sizes offered beside a model's own default, kept only when under its cap.
CANDIDATE_SIZES = (
    (512, 512),
    (768, 768),
    (1024, 1024),
    (1440, 1440),
    (1024, 768),
    (768, 1024),
    (1280, 720),
    (720, 1280),
    (1536, 1024),
    (1024, 1536),
    (1920, 1080),
    (1080, 1920),
)

#: Recipes that create images from a prompt.
IMAGE_RECIPES = frozenset({"sd-cpp", "thenoise"})


def _model_choice(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from gaia.llm.lemonade_client import LemonadeClient

    defaults = entry.get("image_defaults")
    labels = entry.get("labels") or []
    if not isinstance(defaults, dict) or "image" not in labels:
        return None
    if entry.get("recipe") not in IMAGE_RECIPES or "upscaling" in labels:
        return None
    width = int(defaults.get("width") or 512)
    height = int(defaults.get("height") or 512)
    default_size = f"{width}x{height}"
    legacy = entry.get("id") in LemonadeClient.SD_MODELS
    if legacy:
        # The SD client accepts only these sizes for the legacy models.
        sizes = list(LemonadeClient.SD_SIZES)
    else:
        cap = int(defaults.get("soft_pixel_cap") or width * height)
        sizes = [f"{w}x{h}" for w, h in CANDIDATE_SIZES if w * h <= cap]
    if default_size not in sizes:
        sizes.insert(0, default_size)
    downloaded = bool(entry.get("downloaded"))
    return {
        "id": entry["id"],
        "recipe": entry.get("recipe"),
        "downloaded": downloaded,
        "downloads_on_first_use": legacy and not downloaded,
        "size_gb": entry.get("size"),
        "default_size": default_size,
        "default_steps": int(defaults.get("steps") or 20),
        "sizes": sizes,
    }


def image_model_choices(catalog: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The models a user may pick to create images.

    A model qualifies when it is downloaded, or is a legacy SD model the SD
    client downloads on first use. Upscalers are left out. Downloaded ones
    come first.
    """
    choices = [c for c in map(_model_choice, catalog) if c]
    usable = [c for c in choices if c["downloaded"] or c["downloads_on_first_use"]]
    return sorted(usable, key=lambda c: (not c["downloaded"], c["id"].lower()))


def check_image_choice(
    choices: List[Dict[str, Any]],
    model: str,
    size: Optional[str],
    steps: Optional[int],
) -> Dict[str, Any]:
    """The choice for *model*, after checking *size* and *steps* suit it.

    Raises:
        ValueError: naming what this Lemonade cannot create.
    """
    choice = next((c for c in choices if c["id"] == model), None)
    if choice is None:
        offered = ", ".join(c["id"] for c in choices) or "none"
        raise ValueError(
            f"{model} is not an image model on this Lemonade, or it is not "
            f"downloaded. Models you can use: {offered}."
        )
    if size is not None:
        parse_image_size(size)
        if size not in choice["sizes"]:
            raise ValueError(
                f"{model} takes the sizes {', '.join(choice['sizes'])}, not {size}."
            )
    if steps is not None and not 1 <= steps <= IMAGE_MAX_STEPS:
        raise ValueError(
            f"Image steps must be from 1 to {IMAGE_MAX_STEPS}, got {steps}."
        )
    return choice
