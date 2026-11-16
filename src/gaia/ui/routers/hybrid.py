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
    DEFAULT_CLASSIFIER_TIMEOUT_S,
    DEFAULT_POLICY,
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


def _lemonade_payloads():
    """The model server's ``/models?show_all=true`` and ``/system-info`` answers."""
    from .providers import ProviderError, _lemonade

    answers = []
    for path in ("models?show_all=true", "system-info"):
        status, body = _lemonade("GET", path)
        if status >= 400:
            raise ProviderError(
                f"The model server answered /{path} with HTTP {status}."
            )
        answers.append(body)
    return answers[0], answers[1]


def inventory():
    """What every provider offers here; ProviderError when the model server can't say."""
    from gaia.env import credential, load_env
    from gaia.llm.hybrid_templates import build_inventory
    from gaia.llm.local_only import cloud_blocked

    load_env()
    models, info = _lemonade_payloads()
    try:
        return build_inventory(
            models,
            info,
            anthropic_key=bool(credential("ANTHROPIC_API_KEY")),
            local_only=cloud_blocked(),
        )
    except HybridConfigError as e:
        from .providers import ProviderError

        raise ProviderError(str(e)) from e


def _inventory_or_503():
    from .providers import ProviderError

    try:
        return inventory()
    except ProviderError as e:
        raise HTTPException(
            status_code=503,
            detail=surface_wording(
                f"Couldn't list the models hybrid can use: {e}", "agent_ui"
            ),
        ) from e


def _models(inv, configured: List[str]) -> Dict[str, Any]:
    """Every model a set-up provider offers, plus configured ones none offers.

    Grouped by ``provider``; prices are null when unknown, never $0.
    """
    from gaia.llm.hybrid_templates import PROVIDERS, provider_of

    from .providers import ProviderError, display_name

    if inv is None:
        return {"models": [], "models_error": None}
    rows = []
    for model_id in dict.fromkeys([*inv.offers, *configured]):
        offer = inv.offers.get(model_id)
        reason = inv.check(model_id)
        try:
            name = display_name(model_id)
        except ProviderError:
            name = model_id
        rows.append(
            {
                "id": model_id,
                "display_name": name,
                "provider": offer.provider if offer else provider_of(model_id),
                **_price(model_id),
                "available": reason is None,
                "reason": surface_wording(reason, "agent_ui") if reason else None,
            }
        )
    order = {p: i for i, p in enumerate(PROVIDERS)}
    rows.sort(key=lambda r: (order.get(r["provider"], len(order)), r["id"].lower()))
    return {"models": rows, "models_error": None}


def _is_active(db: ChatDatabase) -> Optional[bool]:
    """True when a new chat starts on ``hybrid``; None while config.json is invalid."""
    from gaia.config import GaiaConfigError

    from ..database import resolved_default_model

    chosen = (db.get_setting("custom_model") or "").strip()
    if chosen:
        return is_hybrid_model(chosen)
    try:
        return is_hybrid_model(resolved_default_model())
    except GaiaConfigError:
        return None


def _raw_doc() -> Dict[str, Any]:
    """config.json as saved, unvalidated; 500 only when it isn't JSON at all."""
    from gaia.config import GaiaConfig, GaiaConfigError

    path = GaiaConfig.config_path()
    try:
        text = GaiaConfig._read_text(path)
        return {} if text is None else GaiaConfig._parse_doc(text, path)
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


def _settings_from(doc: Dict[str, Any]) -> HybridSettings:
    """The hybrid settings in a raw config document; HybridConfigError says what's wrong."""
    cheap = doc.get("hybrid_cheap_model") or DEFAULT_CHEAP_MODEL
    policy = doc.get("hybrid_policy") or DEFAULT_POLICY
    return HybridSettings(
        cheap_model=cheap,
        strong_model=doc.get("hybrid_strong_model") or DEFAULT_STRONG_MODEL,
        classifier_model=doc.get("hybrid_classifier_model") or cheap,
        classifier_timeout_s=doc.get(
            "hybrid_classifier_timeout", DEFAULT_CLASSIFIER_TIMEOUT_S
        ),
        policy=policy.strip().lower() if isinstance(policy, str) else policy,
        rules=doc.get("hybrid_rules"),
    )


def _view(db: ChatDatabase) -> Dict[str, Any]:
    from gaia.config import GaiaConfig, GaiaConfigError

    try:
        cfg = GaiaConfig.load()
    except GaiaConfigError as invalid:
        # Shown as saved, with what's wrong, so this screen can repair it.
        cfg = None
        doc = _raw_doc()
        try:
            _settings_from(doc)
            error = surface_wording(str(invalid), "agent_ui")
        except HybridConfigError as e:
            error = surface_wording(str(e), "agent_ui")
        settings = None
    else:
        try:
            settings = HybridSettings.load(cfg)
            error = None
        except HybridConfigError as e:
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
        saved = (
            {k: getattr(cfg, k) for k in FIELDS.values()} if cfg is not None else doc
        )
        cheap = saved.get("hybrid_cheap_model") or DEFAULT_CHEAP_MODEL
        policy = saved.get("hybrid_policy") or DEFAULT_POLICY
        values = {
            "cheap_model": cheap,
            "strong_model": saved.get("hybrid_strong_model") or DEFAULT_STRONG_MODEL,
            "classifier_model": saved.get("hybrid_classifier_model") or cheap,
            "classifier_timeout": saved.get(
                "hybrid_classifier_timeout", DEFAULT_CLASSIFIER_TIMEOUT_S
            ),
            "policy": policy,
            "rules": (
                preset_rules(policy) if policy in PRESETS else saved.get("hybrid_rules")
            ),
        }
    from gaia.llm.hybrid_templates import notes

    from .providers import ProviderError

    configured = [values[f] for f in _MODEL_FIELDS]
    prices = _gateway_prices()
    try:
        inv, models_error = inventory(), None
    except ProviderError as e:
        logger.warning("Hybrid settings without the model list: %s", e)
        inv, models_error = None, surface_wording(str(e), "agent_ui")
    return {
        **values,
        "presets": {p: preset_rules(p) for p in PRESETS},
        "kinds": list(KIND_ORDER),
        **_models(inv, configured),
        "models_error": models_error,
        **prices,
        **notes(
            values["cheap_model"],
            values["strong_model"],
            values["classifier_model"],
            values["policy"],
        ),
        "entry": _entry(inv, settings, error),
        "is_active": _is_active(db),
        "config_error": error,
    }


def _entry(inv, settings, error) -> Optional[Dict[str, Any]]:
    from gaia.config import GaiaConfigError
    from gaia.llm.hybrid_templates import picker_entry

    if inv is None:
        return None
    try:
        return picker_entry(inv, settings, error)
    except GaiaConfigError:
        # config.json is invalid; config_error already says why.
        return None


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
                "model a provider offers."
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
    """Change any of the hybrid settings; the rest keep their saved values.

    Validated against config.json as saved, so a hand-broken file can be fixed.
    """
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
    for field in _MODEL_FIELDS:
        key = FIELDS[field]
        if key in values:
            values[key] = GaiaConfig._check_hybrid_model(key, values[key], field)
    doc = {**_raw_doc(), **values}
    if (doc.get("hybrid_policy") or DEFAULT_POLICY) == CUSTOM_POLICY and doc.get(
        "hybrid_rules"
    ) is None:
        raise _unprocessable(
            "The custom policy needs rules: send rules with a rule for each of "
            f"the {len(KIND_ORDER)} kinds, or pick a preset policy."
        )
    try:
        _settings_from(doc)
        GaiaConfig._from_doc(doc, GaiaConfig.config_path())
    except (GaiaConfigError, HybridConfigError) as e:
        raise _unprocessable(str(e)) from e
    chosen = [f for f in _MODEL_FIELDS if body.get(f)]
    if chosen:
        inv = _inventory_or_503()
        for f in chosen:
            reason = inv.check(body[f])
            if reason:
                raise _unprocessable(f"{f}: {reason}. {inv.checked()}")
    _save(values)
    return _view(db)


def _save(values: Dict[str, Any]) -> None:
    """Write *values* into config.json; the result is validated before it is."""
    from gaia.config import GaiaConfig, GaiaConfigError

    if not values:
        return
    try:
        GaiaConfig.edit_doc(lambda doc: doc.update(values))
    except GaiaConfigError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/hybrid/entry")
def hybrid_picker_entry() -> Dict[str, Any]:
    """The ``hybrid`` choice for a model picker: its own entry, named from the pair.

    ``shown`` is true when the configured pair or any template can run here.
    """
    from gaia.config import GaiaConfigError
    from gaia.llm.hybrid_templates import picker_entry

    inv = _inventory_or_503()
    try:
        settings, error = HybridSettings.load(), None
    except (GaiaConfigError, HybridConfigError) as e:
        settings, error = None, surface_wording(str(e), "agent_ui")
    entry = picker_entry(inv, settings, error)
    if entry["reason"]:
        entry["reason"] = surface_wording(entry["reason"], "agent_ui")
    return entry


@router.get("/api/hybrid/templates")
def hybrid_templates() -> List[Dict[str, Any]]:
    """The built-in pairings, resolved on this PC, with whether each can be used."""
    from gaia.llm.hybrid_templates import list_templates

    templates = list_templates(_inventory_or_503())
    for t in templates:
        if t["reason"]:
            t["reason"] = surface_wording(t["reason"], "agent_ui")
    return templates


@router.post("/api/hybrid/templates/{template_id}/apply")
def apply_hybrid_template(
    template_id: str, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Set the models and policy *template_id* names; 409 with why when it can't run here."""
    from gaia.llm.hybrid_templates import template_settings

    inv = _inventory_or_503()
    try:
        values = template_settings(template_id, inv)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=e.args[0]) from e
    except HybridConfigError as e:
        raise HTTPException(
            status_code=409, detail=surface_wording(str(e), "agent_ui")
        ) from e
    _save(values)
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
