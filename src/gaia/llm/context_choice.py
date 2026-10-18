# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""The context window a user chose for a local model, and what they may choose.

GAIA picks a window automatically (``lemonade_client.context_for_capacity``).
A user may pick another in Settings › Model, from the model's floor up to the
largest window that fits this PC (``model_fit.largest_context``). The choice is
kept per model in ``config.json``'s ``context_sizes`` and used for every load of
that model. A saved size that no longer fits — another PC, less memory — or
that can't be checked because the PC's memory can't be read, is refused at load
with how to fix it, never loaded into an out-of-memory crash.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

#: Sizes offered between a model's floor and the most that fits.
CONTEXT_STEPS = (32768, 65536, 131072, 262144, 524288, 1048576)


class ContextChoiceError(ValueError):
    """A context size cannot be chosen or used for a model, with the remedy."""


def _k(tokens: int) -> str:
    return f"{tokens // 1024}K"


def context_range(model_id: str, capacity) -> Optional[Dict[str, Any]]:
    """``{min, max_fit, automatic, options}`` for *model_id* on *capacity*.

    None when GAIA does not know the model's memory needs: no size can be
    judged to fit, so only the automatic one is offered.
    """
    from gaia.llm.lemonade_client import (
        co_resident_reserve_gb,
        context_for_capacity,
        find_model_requirement,
    )
    from gaia.llm.model_fit import largest_context

    req = find_model_requirement(model_id)
    if req is None:
        return None
    floor = req.min_ctx_size
    if req.size_gb and req.kv_bytes_per_token and req.max_ctx_size:
        top = largest_context(
            size_gb=req.size_gb,
            kv_bytes_per_token=req.kv_bytes_per_token,
            min_ctx=floor,
            max_ctx=req.max_ctx_size,
            capacity=capacity,
            reserve_gb=co_resident_reserve_gb(capacity),
        )
    else:
        top = floor
    options = sorted({s for s in CONTEXT_STEPS if floor <= s <= top} | {floor, top})
    return {
        "min": floor,
        "max_fit": top,
        "automatic": context_for_capacity(req, capacity),
        "options": options,
    }


def saved_context_size(model_id: Optional[str]) -> Optional[int]:
    """The size the user chose for *model_id*, or None for automatic."""
    if not model_id:
        return None
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import _model_ids_match

    for name, tokens in GaiaConfig.load().context_sizes.items():
        if _model_ids_match(name, model_id):
            return tokens
    return None


def check_saved_size(model_id: str, tokens: int, capacity) -> None:
    """Raise :class:`ContextChoiceError` when *tokens* does not fit *model_id* here."""
    span = context_range(model_id, capacity)
    if span is None:
        return
    if tokens > span["max_fit"]:
        raise ContextChoiceError(
            f"The context size you chose for {model_id} ({_k(tokens)} tokens) does "
            f"not fit this PC, which holds at most {_k(span['max_fit'])} for it. "
            "Choose a smaller size, or Automatic, in Settings › Model."
        )
    if tokens < span["min"]:
        raise ContextChoiceError(
            f"The context size you chose for {model_id} ({_k(tokens)} tokens) is "
            f"below its {_k(span['min'])} minimum. Choose another size, or "
            "Automatic, in Settings › Model."
        )


def save_context_size(model_id: str, tokens: Optional[int], capacity) -> None:
    """Remember *tokens* for *model_id*; None returns it to automatic."""
    from gaia.config import GaiaConfig
    from gaia.llm.lemonade_client import _model_ids_match

    if tokens is not None:
        span = context_range(model_id, capacity)
        if span is None:
            raise ContextChoiceError(
                f"GAIA does not know how much memory {model_id} needs per token, "
                "so it cannot tell which context sizes fit; it keeps the "
                "automatic size."
            )
        if tokens not in span["options"]:
            choices = ", ".join(_k(o) for o in span["options"])
            raise ContextChoiceError(
                f"{_k(tokens)} is not offered for {model_id} on this PC. "
                f"Choose one of: {choices}."
            )
    sizes = {
        k: v
        for k, v in GaiaConfig.load().context_sizes.items()
        if not _model_ids_match(k, model_id)
    }
    if tokens is not None:
        sizes[model_id] = tokens
    _write_context_sizes(sizes)


def _write_context_sizes(sizes: Dict[str, int]) -> None:
    """Write ``context_sizes`` into config.json, keeping every other key."""
    import json

    from gaia.config import GaiaConfig

    path = GaiaConfig.config_path()
    doc: Dict[str, Any] = {}
    if path.exists():
        doc = json.loads(path.read_text(encoding="utf-8") or "{}")
    if sizes:
        doc["context_sizes"] = sizes
    else:
        doc.pop("context_sizes", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
