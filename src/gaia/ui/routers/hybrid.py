# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Settings for the ``hybrid`` model: its models, policy and rules, a try-it
preview, and how its turns have routed.

The values live in ``config.json`` (the ``hybrid_*`` keys), shared with the
CLI and the TUI, and are validated by the same rules as ``gaia config set``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from gaia.llm.hybrid import (
    CUSTOM_POLICY,
    DEFAULT_CHEAP_MODEL,
    DEFAULT_STRONG_MODEL,
    KIND_ORDER,
    MAX_CLASSIFIER_TIMEOUT_S,
    POLICIES,
    PRESETS,
    HybridConfigError,
    HybridRouter,
    HybridSettings,
    is_hybrid_model,
    preset_rules,
    validate_rules,
)
from gaia.llm.surface_hints import surface_wording
from gaia.logger import get_logger

from .. import spending
from ..database import ChatDatabase
from ..dependencies import get_db

logger = get_logger(__name__)

router = APIRouter(tags=["hybrid"])

#: Request field -> config key. Rules come before the policy that needs them.
FIELDS = {
    "rules": "hybrid_rules",
    "cheap_model": "hybrid_cheap_model",
    "strong_model": "hybrid_strong_model",
    "classifier_model": "hybrid_classifier_model",
    "classifier_timeout": "hybrid_classifier_timeout",
    "policy": "hybrid_policy",
}
_MODEL_FIELDS = ("cheap_model", "strong_model", "classifier_model")
#: Longest preview request accepted; the classifier reads only the start.
MAX_PREVIEW_CHARS = 100_000


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail=surface_wording(message, "agent_ui"))


def _load_config():
    from gaia.config import GaiaConfig, GaiaConfigError

    try:
        return GaiaConfig.load()
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _price(model_id: str) -> Dict[str, Any]:
    from gaia.llm.gateway_prices import GatewayPriceError

    try:
        card = spending.model_price(model_id) or {}
    except GatewayPriceError:
        # An unreadable price list is reported once, as prices_error.
        card = {}
    return {
        "input_per_million": card.get("input_per_million"),
        "output_per_million": card.get("output_per_million"),
        "cache_read_per_million": card.get("cache_read_per_million"),
        "price_source": card.get("source"),
    }


def _gateway_prices() -> Dict[str, Any]:
    """Read the gateway's prices again when stale; say when and whether it worked."""
    from gaia.llm.gateway_prices import GatewayPriceError, ensure_fresh, load

    try:
        doc = ensure_fresh()
        error = None
    except GatewayPriceError as e:
        logger.warning("Hybrid prices without a fresh gateway price list: %s", e)
        error = surface_wording(str(e), "agent_ui")
        try:
            doc = load()
        except GatewayPriceError as unreadable:
            doc, error = None, surface_wording(str(unreadable), "agent_ui")
    return {
        "prices_fetched_at": doc.get("fetched_at") if doc else None,
        "prices_error": error,
    }


def _models(configured: List[str]) -> Dict[str, Any]:
    """The gateway's chat models with list prices, plus any configured model it lacks.

    ``available`` is None when the catalog couldn't be read, never a guess.
    """
    from .providers import AMD, ProviderError, _list_models, display_name

    try:
        listed: Optional[List[str]] = [m["id"] for m in _list_models(AMD)]
        error = None
    except ProviderError as e:
        logger.warning("Hybrid settings without the gateway's model list: %s", e)
        listed, error = None, surface_wording(str(e), "agent_ui")
    ids = list(dict.fromkeys([*(listed or []), *configured]))
    return {
        "models": [
            {
                "id": model_id,
                "display_name": display_name(model_id),
                **_price(model_id),
                "available": None if listed is None else model_id in listed,
            }
            for model_id in ids
        ],
        "models_error": error,
    }


def _is_active(db: ChatDatabase) -> bool:
    """True when a new chat starts on ``hybrid``."""
    from gaia.config import GaiaConfigError

    from ..database import resolved_default_model

    chosen = (db.get_setting("custom_model") or "").strip()
    try:
        return is_hybrid_model(chosen or resolved_default_model())
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _view(db: ChatDatabase) -> Dict[str, Any]:
    cfg = _load_config()
    try:
        settings = HybridSettings.load(cfg)
        error = None
    except HybridConfigError as e:
        # Shown with the values as saved, so the screen can still fix them.
        settings, error = None, surface_wording(str(e), "agent_ui")
    if settings is not None:
        values = {
            "cheap_model": settings.cheap_model,
            "strong_model": settings.strong_model,
            "classifier_model": settings.classifier_model,
            "classifier_timeout": settings.classifier_timeout_s,
            "policy": settings.policy,
            "rules": settings.table,
        }
    else:
        cheap = cfg.hybrid_cheap_model or DEFAULT_CHEAP_MODEL
        values = {
            "cheap_model": cheap,
            "strong_model": cfg.hybrid_strong_model or DEFAULT_STRONG_MODEL,
            "classifier_model": cfg.hybrid_classifier_model or cheap,
            "classifier_timeout": cfg.hybrid_classifier_timeout,
            "policy": cfg.hybrid_policy,
            "rules": (
                preset_rules(cfg.hybrid_policy)
                if cfg.hybrid_policy in PRESETS
                else cfg.hybrid_rules
            ),
        }
    configured = [values[f] for f in _MODEL_FIELDS]
    prices = _gateway_prices()
    return {
        **values,
        "presets": {p: preset_rules(p) for p in PRESETS},
        "kinds": list(KIND_ORDER),
        **_models(configured),
        **prices,
        "is_active": _is_active(db),
        "config_error": error,
    }


@router.get("/api/hybrid/settings")
def get_hybrid_settings(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """The hybrid models, policy, effective rules, presets and gateway models."""
    return _view(db)


def _checked(field: str, value: Any) -> Any:
    """*value* for *field* if its type and range are right; 422 otherwise."""
    if field in _MODEL_FIELDS:
        if value is not None and not isinstance(value, str):
            raise _unprocessable(
                f"{field} must be a model id, or null for the default, but it is "
                f"{value!r}."
            )
        if is_hybrid_model(value):
            raise _unprocessable(
                f"{field} can't be 'hybrid', which would route to itself; pick a "
                "model the gateway offers."
            )
        return value
    if field == "classifier_timeout":
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= MAX_CLASSIFIER_TIMEOUT_S
        ):
            raise _unprocessable(
                f"classifier_timeout must be a whole number of seconds, "
                f"1-{MAX_CLASSIFIER_TIMEOUT_S}, but it is {value!r}."
            )
        return value
    if field == "policy":
        if value not in POLICIES:
            raise _unprocessable(
                f"policy must be one of {', '.join(POLICIES)}, but it is {value!r}."
            )
        return value
    if value is None:
        return None
    try:
        return validate_rules(value)
    except HybridConfigError as e:
        raise _unprocessable(str(e)) from e


@router.put("/api/hybrid/settings")
def put_hybrid_settings(
    body: Any = Body(...), db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Change any of the hybrid settings; the rest keep their saved values."""
    from gaia.config import GaiaConfig, GaiaConfigError

    if not isinstance(body, dict):
        raise _unprocessable("Send a JSON object of the settings to change.")
    unknown = sorted(str(k) for k in body if k not in FIELDS)
    if unknown:
        raise _unprocessable(
            f"Unknown settings {', '.join(unknown[:5])}; the hybrid settings are "
            f"{', '.join(sorted(FIELDS))}."
        )
    values = {FIELDS[f]: _checked(f, body[f]) for f in FIELDS if f in body}
    cfg = _load_config()
    policy = values.get("hybrid_policy", cfg.hybrid_policy)
    rules = values.get("hybrid_rules", cfg.hybrid_rules)
    if policy == CUSTOM_POLICY and rules is None:
        raise _unprocessable(
            "The custom policy needs rules: send rules with a rule for each of "
            f"the {len(KIND_ORDER)} kinds, or pick a preset policy."
        )
    if values.get("hybrid_rules", "") is None:
        # A cleared table goes after the policy that stops needing it.
        values = {k: v for k, v in values.items() if k != "hybrid_rules"}
        values["hybrid_rules"] = None
    try:
        for key, value in values.items():
            cfg.set(key, value)
        HybridSettings.load(cfg)
    except (GaiaConfigError, HybridConfigError) as e:
        raise _unprocessable(str(e)) from e
    if values:
        try:
            GaiaConfig.save_keys(values)
        except GaiaConfigError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    return _view(db)


@router.post("/api/hybrid/preview")
def preview_hybrid_route(body: Any = Body(...)) -> Dict[str, Any]:
    """Where *text* would run as a new chat's first turn; nothing else happens.

    Runs the real classifier and policy. No turn runs, no tool is offered, and
    no chat becomes sticky; a classifier failure answers like a turn's (strong
    model, ``classified`` false, ``reason`` and ``fix`` saying why).
    """
    from gaia.config import GaiaConfigError

    text = body.get("text") if isinstance(body, dict) else None
    if not isinstance(text, str):
        raise _unprocessable('Send {"text": "<a request to try>"}.')
    if len(text) > MAX_PREVIEW_CHARS:
        raise _unprocessable(
            f"That request is {len(text):,} characters; try one of at most "
            f"{MAX_PREVIEW_CHARS:,} (the classifier reads only the start anyway)."
        )
    try:
        settings = HybridSettings.load()
    except (GaiaConfigError, HybridConfigError) as e:
        raise HTTPException(
            status_code=409, detail=surface_wording(str(e), "agent_ui")
        ) from e
    try:
        decision = HybridRouter(settings).preview(text)
    except RuntimeError as e:
        # Local only is on and a hybrid model is in the cloud.
        raise HTTPException(
            status_code=409, detail=surface_wording(str(e), "agent_ui")
        ) from e
    return {
        "model": decision.model,
        "tier": decision.tier,
        "kind": decision.kind,
        "difficulty": decision.difficulty,
        "stakes": decision.stakes,
        "reason": surface_wording(decision.reason, "agent_ui"),
        "fix": surface_wording(decision.fix, "agent_ui"),
        "classified": decision.classified,
        "seconds": decision.seconds,
        "policy": decision.policy,
    }


@router.get("/api/hybrid/stats")
def hybrid_route_stats(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """How every recorded hybrid turn routed and what it cost; see ``spending.hybrid_stats``."""
    from gaia.config import GaiaConfigError

    try:
        settings = HybridSettings.load()
    except (GaiaConfigError, HybridConfigError) as e:
        raise HTTPException(
            status_code=409, detail=surface_wording(str(e), "agent_ui")
        ) from e
    prices = _gateway_prices()
    try:
        return {**spending.hybrid_stats(db, settings), **prices}
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
