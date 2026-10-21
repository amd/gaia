# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""AI provider and model selection for the Agent UI — the TUI's ``/provider``.

Three providers, all served through Lemonade: **Local** (models on this PC),
**Fireworks AI** and the **AMD LLM Gateway** (cloud, registered in Lemonade with
a key). The calls mirror ``tui/internal/lemonade/cloud.go`` and
``tui/internal/ui/providers`` so both clients configure Lemonade identically:

- Keys go to the local Lemonade only (``POST /cloud/auth``) and, once they
  discover models, into the OS credential store via :mod:`gaia.llm.cloud_keys`,
  the same slot the TUI uses — a key saved in one client works in the other.
  A key is never echoed back, logged, or accepted through the mobile tunnel.
- The chosen model is written to ``last_provider`` / ``last_model`` in
  ``~/.gaia/config.json`` (shared with the TUI, #4497) and to the Agent UI's
  ``custom_model`` setting, which is what a chat turn runs.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gaia.logger import get_logger

from ..database import ChatDatabase
from ..dependencies import get_db
from ..security import require_ui_header

logger = get_logger(__name__)

router = APIRouter(tags=["providers"])

LOCAL = "local"
FIREWORKS = "fireworks"
AMD = "amd"
PROVIDER_IDS = (LOCAL, FIREWORKS, AMD)

_NAMES = {LOCAL: "Local", FIREWORKS: "Fireworks AI", AMD: "AMD LLM Gateway"}

#: Shown before a cloud provider is used; same wording as the TUI.
_PRIVACY = {
    LOCAL: "Runs on this PC. Nothing leaves your machine.",
    FIREWORKS: "Chat history is sent to Fireworks AI. Usage may incur charges.",
    AMD: "Chat history is sent to your configured AMD gateway.",
}

#: Model labels that are not chat models (the TUI's set, plus speech-to-text).
_NON_CHAT_LABELS = {
    "embeddings",
    "embedding",
    "image",
    "reranker",
    "audio",
    "tts",
    "stt",
    "transcription",
    "realtime-transcription",
}

_TIMEOUT = 60


class ProviderError(Exception):
    """A provider call failed, with the remedy."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# ── Lemonade access ────────────────────────────────────────────────────────


def _base_url() -> str:
    from gaia.llm.lemonade_client import LemonadeClient, LemonadeClientError

    try:
        return LemonadeClient(verbose=False).credential_base_url().rstrip("/")
    except LemonadeClientError as e:
        raise ProviderError(str(e), status=503) from e


def _is_local_url(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1")


def _server_owner(base_url: str) -> str:
    """``gaia`` for GAIA's own Lemonade, ``user`` for another local one, ``remote``."""
    from gaia.llm.lemonade_client import _read_embedded_lemonade_state
    from gaia.llm.model_server_info import server_owner

    state = _read_embedded_lemonade_state()
    return server_owner(base_url, state.get("port") if state else None)


def _require_loopback(base_url: str) -> None:
    if not _is_local_url(base_url):
        raise ProviderError(
            f"Lemonade is configured at {base_url}, which is not on this PC, so "
            "GAIA will not send it a provider key. Point LEMONADE_BASE_URL at a "
            "local Lemonade, or configure the provider on that server directly.",
            status=409,
        )


def _lemonade(
    method: str, path: str, payload: Optional[Dict[str, Any]] = None
) -> Tuple[int, Dict[str, Any]]:
    from gaia.llm.lemonade_choice import setup_change_refusal
    from gaia.llm.lemonade_client import lemonade_auth_headers, resolve_lemonade_api_key

    base = _base_url()
    refusal = setup_change_refusal(f"{base}/{path}", method, payload)
    if refusal:
        raise ProviderError(refusal, status=409)
    try:
        resp = requests.request(
            method,
            f"{base}/{path}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                **lemonade_auth_headers(resolve_lemonade_api_key(base_url=base)),
            },
            timeout=_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        raise ProviderError(
            f"The local model server is not reachable at {base} ({e}). Finish "
            "setup, or start it with `gaia daemon start`.",
            status=503,
        ) from e
    try:
        body = resp.json() if resp.content else {}
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {"data": body}


def _cloud_error(
    provider: str, status: int, body: Dict[str, Any], echo_body: bool = True
) -> ProviderError:
    """*echo_body* False for calls that carried a key: Lemonade may echo the request."""
    name = _NAMES[provider]
    if status in (401, 403):
        return ProviderError(f"{name} rejected the key. Check it and try again.", 401)
    if status == 404:
        return ProviderError(
            "This Lemonade cannot register cloud providers. Update Lemonade to "
            "11.8.1 or newer (`gaia init` installs it).",
            409,
        )
    if status == 409:
        from gaia.llm.cloud_keys import env_var_for

        return ProviderError(
            f"An environment key ({env_var_for(provider)}) is already active for "
            f"{name} and takes precedence, so the pasted key was not used. Leave "
            "the key blank to keep using it.",
            409,
        )
    detail = (body.get("error") or {}) if isinstance(body.get("error"), dict) else {}
    message = (detail.get("message") if echo_body else None) or f"HTTP {status}"
    return ProviderError(f"Lemonade could not configure {name}: {message}.", 502)


def _cloud_entry(provider: str) -> Optional[Dict[str, Any]]:
    status, info = _lemonade("GET", "system-info")
    if status >= 400:
        raise ProviderError(f"Lemonade answered /system-info with HTTP {status}.")
    for entry in (info.get("cloud") or {}).get("providers") or []:
        if entry.get("name") == provider:
            return entry
    return None


def _registration(provider: str, body: "ConnectRequest") -> Dict[str, Any]:
    from gaia.llm.gateway import (
        DEFAULT_AUTH_HEADER_NAME,
        DEFAULT_AUTH_HEADER_PREFIX,
        DEFAULT_GATEWAY_BASE_URL,
    )
    from gaia.llm.providers.fireworks import FIREWORKS_BASE_URL

    if provider == FIREWORKS:
        return {
            "base_url": FIREWORKS_BASE_URL,
            "auth_header_name": "Authorization",
            "auth_header_prefix": "Bearer ",
        }
    return {
        "base_url": (body.base_url or DEFAULT_GATEWAY_BASE_URL).strip(),
        "auth_header_name": (body.auth_header_name or DEFAULT_AUTH_HEADER_NAME).strip(),
        "auth_header_prefix": (
            body.auth_header_prefix
            if body.auth_header_prefix is not None
            else DEFAULT_AUTH_HEADER_PREFIX
        ),
    }


# ── Views ──────────────────────────────────────────────────────────────────


def _key_source(entry: Optional[Dict[str, Any]], provider: str) -> Optional[str]:
    from gaia.llm.cloud_keys import recall_key

    if entry and entry.get("env_var_set"):
        return "environment"
    if entry and entry.get("runtime_key_set"):
        return "lemonade"
    if recall_key(provider):
        return "stored"
    return None


def _provider_view(provider: str, entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    from gaia.llm.cloud_keys import env_var_for

    view: Dict[str, Any] = {
        "id": provider,
        "name": _NAMES[provider],
        "remote": provider != LOCAL,
        "privacy_notice": _PRIVACY[provider],
    }
    if provider == LOCAL:
        return view
    view.update(
        {
            "registered": entry is not None,
            "key_source": _key_source(entry, provider),
            "models_discovered": bool(entry and entry.get("models_discovered")),
            "env_var": env_var_for(provider),
            "base_url": entry.get("base_url") if entry else None,
        }
    )
    return view


def _is_chat_model(model: Dict[str, Any]) -> bool:
    labels = {str(label).lower() for label in model.get("labels") or []}
    # ``chat`` wins: FLM omni models are labeled both ``chat`` and ``audio``.
    return "chat" in labels or not labels & _NON_CHAT_LABELS


def _suggested_local_models() -> set:
    """Not-yet-downloaded local models still worth offering: the defaults."""
    from gaia.config import GaiaConfigError
    from gaia.llm.lemonade_client import DEFAULT_MODEL_LADDER

    from ..database import resolved_default_model

    try:
        default = resolved_default_model()
    except GaiaConfigError as e:
        # The active view reports the broken config; the list still shows the ladder.
        logger.warning("Listing models without the configured default: %s", e)
        default = DEFAULT_MODEL_LADDER[-1]
    return {m.lower() for m in (*DEFAULT_MODEL_LADDER, default)}


def _list_models(provider: str, *, offer_optional: bool = True) -> List[Dict[str, Any]]:
    """Chat models for *provider*; *offer_optional* adds the optional ones that fit."""
    from gaia.llm.lemonade_client import _model_ids_match, find_model_requirement
    from gaia.llm.recommended_models import rank

    from ..spending import price_card

    status, body = _lemonade("GET", "models?show_all=true")
    if status >= 400:
        raise ProviderError(f"Lemonade answered /models with HTTP {status}.")
    offered: List[str] = []
    if provider == LOCAL:
        offered = [*_suggested_local_models()]
        if offer_optional:
            offered += _optional_models_that_fit()
    out: List[Dict[str, Any]] = []
    for m in body.get("data") or []:
        model_id = str(m.get("id") or "")
        if not model_id or not _is_chat_model(m):
            continue
        is_cloud = m.get("recipe") == "cloud" or bool(m.get("cloud_provider"))
        if provider == LOCAL:
            if is_cloud or not (
                m.get("downloaded")
                or any(_model_ids_match(model_id, o) for o in offered)
            ):
                continue
        elif not model_id.startswith(f"{provider}."):
            continue
        ranked = rank(model_id)
        out.append(
            {
                "id": model_id,
                "context_length": m.get("context_length"),
                "downloaded": bool(m.get("downloaded")) or is_cloud,
                "labels": list(m.get("labels") or []),
                "recipe": m.get("recipe"),
                "size_gb": m.get("size"),
                "rank": ranked[0] if ranked else None,
                "note": ranked[1].note if ranked else None,
                "evidence": ranked[1].evidence if ranked else None,
                # List price per million tokens; None when unknown, never $0.
                "price": None if provider == LOCAL else price_card(model_id),
            }
        )
    # A custom model Lemonade has not registered yet is not in its list; a
    # missing built-in is one this Lemonade cannot pull, so it stays out.
    for model_id in offered:
        if model_id.startswith("user.") and not any(
            _model_ids_match(model_id, m["id"]) for m in out
        ):
            req = find_model_requirement(model_id)
            out.append(
                {
                    "id": model_id,
                    "context_length": None,
                    "downloaded": False,
                    "labels": [],
                    "recipe": req.recipe if req else None,
                    "size_gb": req.size_gb if req else None,
                    "rank": None,
                    "note": None,
                    "evidence": None,
                    "price": None,
                }
            )
    if provider == AMD:
        _rank_gateway_models(out)
    out.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0, x["id"].lower()))
    return out


def _optional_models_that_fit() -> List[str]:
    """Optional local models to offer for download: only those this PC holds."""
    from gaia.llm.lemonade_client import optional_chat_models_that_fit
    from gaia.llm.model_fit import ModelFitError, capacity_from_system_info

    status, info = _lemonade("GET", "system-info")
    try:
        if status >= 400:
            raise ModelFitError(f"Lemonade answered /system-info with HTTP {status}")
        capacity = capacity_from_system_info(info)
    except ModelFitError as e:
        # Not offering what may not fit; downloaded models still list.
        logger.warning("Not offering optional local models: %s", e)
        return []
    return optional_chat_models_that_fit(capacity)


def _rank_gateway_models(models: List[Dict[str, Any]]) -> None:
    """Rank AMD gateway models by ``gateway.PREFERRED_MODEL_HINTS``.

    The recommendation table covers Fireworks only, so without this every
    gateway model is unranked and setup auto-picks whichever sorts first.
    """
    from gaia.llm.gateway import PREFERRED_MODEL_HINTS, preference_rank

    preferred = sorted(
        (m for m in models if preference_rank(m["id"]) < len(PREFERRED_MODEL_HINTS)),
        key=lambda m: (preference_rank(m["id"]), m["id"].lower()),
    )
    for i, m in enumerate(preferred, start=1):
        m["rank"] = i
        m["note"] = "recommended"


def provider_of(model_id: str) -> str:
    """The provider a model id belongs to; its prefix wins, as in the TUI."""
    prefix = model_id.split(".", 1)[0] if "." in model_id else ""
    return prefix if prefix in (FIREWORKS, AMD) else LOCAL


def model_label(model_id: str) -> str:
    provider = provider_of(model_id)
    name = (
        model_id.split(".", 1)[1].rsplit("/", 1)[-1] if provider != LOCAL else model_id
    )
    return f"{name} · {'local' if provider == LOCAL else _NAMES[provider]}"


def display_name(model_id: str) -> str:
    """The name a person reads: friendly for local models, bare for cloud ones."""
    from ..local_models import friendly_name

    if provider_of(model_id) == LOCAL:
        return friendly_name(model_id)
    return model_id.split(".", 1)[1].rsplit("/", 1)[-1]


def _active_view(db: ChatDatabase) -> Dict[str, Any]:
    from gaia.config import GaiaConfigError

    from ..database import resolved_default_model

    chosen = (db.get_setting("custom_model") or "").strip()
    try:
        # The model a new chat starts on, so the chip never names another.
        model = chosen or resolved_default_model()
    except GaiaConfigError as e:
        # A new chat cannot start either; say why rather than name a model.
        return {
            "provider": LOCAL,
            "model": None,
            "display_name": None,
            "label": None,
            "remote": False,
            "is_default": True,
            "config_error": str(e),
        }
    provider = provider_of(model)
    return {
        "provider": provider,
        "model": model,
        "display_name": display_name(model),
        "label": model_label(model),
        "remote": provider != LOCAL,
        "is_default": not chosen,
        "config_error": None,
        **_new_chat_location(model, chosen or None),
    }


def _new_chat_location(model: str, custom_model: Optional[str]) -> Dict[str, Any]:
    """Where a new chat would be answered, by the rule session locations use.

    Lets the chip say "Local" before the first chat exists, from the backend.
    """
    from .._chat_helpers import session_inference_location
    from ..warmup import NEW_CHAT_AGENT

    loc = session_inference_location(
        {"agent_type": NEW_CHAT_AGENT, "model": model}, custom_model
    )
    if loc is None:
        return {"inference_remote": None, "inference_description": None}
    return {
        "inference_remote": loc.remote,
        "inference_description": loc.notice() if loc.model else None,
    }


# ── Last-model memory (#4497) ──────────────────────────────────────────────


def _write_config_keys(values: Dict[str, Any]) -> None:
    """Save *values* to ``config.json`` under its lock, keeping every other key."""
    from gaia.config import GaiaConfig, GaiaConfigError

    try:
        GaiaConfig.save_keys(values)
    except GaiaConfigError as e:
        raise ProviderError(
            f"The model choice was not saved: {e} Then pick the model again.",
            500,
        ) from e


_restore_lock = threading.Lock()
_restore_result: Optional[Dict[str, Any]] = None


class _ServerNotReady(Exception):
    """The model server is not answering yet; the question can be asked again."""


def _check_model_available(model_id: str) -> Optional[str]:
    """None when *model_id* can run now, else why not.

    Raises :class:`_ServerNotReady` while the local model server is still
    starting, which is not an answer about the model.
    """
    from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated

    provider = provider_of(model_id)
    try:
        if provider != LOCAL:
            _require_loopback(_base_url())
            if not ensure_authenticated(provider):
                return f"{_NAMES[provider]} has no working key"
        models = {m["id"]: m for m in _list_models(provider)}
    except ProviderError as e:
        if e.status == 503:
            raise _ServerNotReady(str(e)) from e
        return str(e)
    except CloudKeyError as e:
        if "not reachable" in str(e):
            raise _ServerNotReady(str(e)) from e
        return str(e)
    except requests.RequestException as e:
        return str(e)
    found = models.get(model_id)
    if found is None:
        return "the model is no longer listed"
    if provider == LOCAL and not found["downloaded"]:
        return "the model is not downloaded"
    return None


def restore_last_model(db: ChatDatabase) -> Dict[str, Any]:
    """Apply ``last_model`` from config.json once per backend run.

    Never runs a different model quietly: when the saved one cannot run, the
    result says why and the UI asks the user to pick.
    """
    global _restore_result
    with _restore_lock:
        if _restore_result is not None:
            return _restore_result
        from gaia.config import GaiaConfig, GaiaConfigError

        try:
            last = (GaiaConfig.load().last_model or "").strip()
        except GaiaConfigError as e:
            _restore_result = {
                "status": "failed",
                "message": f"Your last model was not restored: {e}. Pick a model.",
            }
            return _restore_result
        if not last:
            _restore_result = {"status": "none", "message": None}
            return _restore_result
        try:
            reason = _check_model_available(last)
        except _ServerNotReady:
            # Not cached: the UI asks again once the server is up.
            return {"status": "pending", "message": None}
        if reason is None:
            from gaia.llm.lemonade_client import resolve_default_chat_model

            current = (db.get_setting("custom_model") or "").strip()
            unchanged = last == (current or resolve_default_chat_model())
            db.set_setting("custom_model", last)
            _restore_result = {
                "status": "restored",
                # Nothing to announce when the model was going to run anyway.
                "message": None if unchanged else "Restored your last model.",
            }
        else:
            _restore_result = {
                "status": "failed",
                "message": (
                    f"Couldn't restore your last model, {model_label(last)}: {reason}. "
                    "Pick a model to continue."
                ),
            }
        return _restore_result


def reset_restore_state() -> None:
    """Forget the once-per-run restore result. Tests only."""
    global _restore_result
    with _restore_lock:
        _restore_result = None


# ── Routes ─────────────────────────────────────────────────────────────────


def _raise(e: ProviderError) -> None:
    raise HTTPException(status_code=e.status, detail=str(e)) from e


def _known(provider: str) -> None:
    if provider not in PROVIDER_IDS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown provider {provider!r}. Known: {', '.join(PROVIDER_IDS)}.",
        )


def _via_tunnel(request: Request) -> bool:
    return any(
        h in request.headers
        for h in ("x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")
    )


# Re-sends a stored key to Lemonade, so a cross-site GET must not reach it.
@router.get("/api/providers", dependencies=[Depends(require_ui_header)])
def list_providers(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """Every provider with its connection state, and the active model."""
    from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated

    providers = [_provider_view(LOCAL, None)]
    lemonade_error = None
    try:
        for provider in (FIREWORKS, AMD):
            entry = _cloud_entry(provider)
            view = _provider_view(provider, entry)
            # Hand a kept key back to a Lemonade that lost it, as the TUI does.
            if entry is not None and view["key_source"] == "stored":
                try:
                    _require_loopback(_base_url())
                    ensure_authenticated(provider)
                    view = _provider_view(provider, _cloud_entry(provider))
                except (ProviderError, CloudKeyError, requests.RequestException) as e:
                    view["error"] = str(e)
            providers.append(view)
    except ProviderError as e:
        lemonade_error = str(e)
        listed = {view["id"] for view in providers}
        providers += [
            _provider_view(p, None) for p in (FIREWORKS, AMD) if p not in listed
        ]
    return {
        "providers": providers,
        "active": _active_view(db),
        "lemonade_error": lemonade_error,
    }


# Can restore and persist the last model, so a cross-site GET must not reach it.
@router.get("/api/providers/active", dependencies=[Depends(require_ui_header)])
def active_model(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """The model new turns run, plus the once-per-run restore notice."""
    restore = restore_last_model(db)
    view = _active_view(db)
    return {**view, "downloaded": _local_downloaded(view), "restore": restore}


def _local_downloaded(view: Dict[str, Any]) -> Optional[bool]:
    """Whether the active local model is on disk; None for cloud or while the server starts."""
    if view["remote"] or not view["model"]:
        return None
    try:
        models = {m["id"].lower(): m for m in _list_models(LOCAL, offer_optional=False)}
    except ProviderError as e:
        # Unknown, not an error: the chip simply makes no download claim.
        logger.info("Could not tell whether %s is downloaded: %s", view["model"], e)
        return None
    found = models.get(view["model"].lower())
    return bool(found and found["downloaded"])


class ConnectRequest(BaseModel):
    """Register a cloud provider in Lemonade; ``api_key`` blank keeps the current key."""

    api_key: Optional[str] = None
    base_url: Optional[str] = None
    auth_header_name: Optional[str] = None
    auth_header_prefix: Optional[str] = None


@router.post("/api/providers/{provider}/connect")
def connect_provider(
    provider: str, body: ConnectRequest, request: Request
) -> Dict[str, Any]:
    """Register the provider and hand Lemonade the key; keep a key that works."""
    from gaia.llm.cloud_keys import CloudKeyError, ensure_authenticated, remember_key

    _known(provider)
    if provider == LOCAL:
        raise HTTPException(status_code=422, detail="Local models need no connection.")
    key = (body.api_key or "").strip()
    if key and _via_tunnel(request):
        raise HTTPException(
            status_code=403,
            detail="Enter provider keys on the PC running GAIA, not over mobile access.",
        )
    if _uses_users_server():
        return _connect_on_users_server(provider)
    try:
        _require_loopback(_base_url())
        status, result = _lemonade(
            "POST",
            "install",
            {
                "backend": "cloud",
                "provider": provider,
                "wire_format": "openai",
                **_registration(provider, body),
            },
        )
        if status >= 400:
            raise _cloud_error(provider, status, result)
        remembered, remember_error = False, None
        if key:
            status, result = _lemonade(
                "POST", "cloud/auth", {"provider": provider, "api_key": key}
            )
            if status >= 400:
                raise _cloud_error(provider, status, result, echo_body=False)
            if not result.get("models_discovered"):
                # Lemonade stores a key without checking it; don't leave a dud behind.
                _lemonade("DELETE", f"cloud/auth/{provider}")
                raise ProviderError(
                    f"{_NAMES[provider]} did not accept that key (no models were "
                    "discovered). Check the key and try again.",
                    401,
                )
            try:
                remember_key(provider, key)
                remembered = True
            except CloudKeyError as e:
                remember_error = str(e)
        else:
            try:
                ensure_authenticated(provider)
            except (CloudKeyError, requests.RequestException) as e:
                raise ProviderError(str(e), 502) from e
        del key
        entry = _cloud_entry(provider)
    except ProviderError as e:
        _raise(e)
    view = _provider_view(provider, entry)
    if not view["models_discovered"]:
        raise HTTPException(
            status_code=401,
            detail=(
                f"{_NAMES[provider]} is registered but discovered no models. "
                "Paste a valid API key."
            ),
        )
    return {**view, "remembered": remembered, "remember_error": remember_error}


def _uses_users_server() -> bool:
    from gaia.llm.lemonade_choice import is_users_server

    try:
        return is_users_server(_base_url())
    except ProviderError as e:
        _raise(e)


def _connect_on_users_server(provider: str) -> Dict[str, Any]:
    """Use the provider as the user's own server already has it; change nothing.

    A key typed here is not sent anywhere: handing it to the user's server
    would change that server's setup.
    """
    from gaia.llm.cloud_keys import provider_usable, users_server_provider_message

    try:
        entry = _cloud_entry(provider)
    except ProviderError as e:
        _raise(e)
    if not provider_usable(entry):
        raise HTTPException(
            status_code=409,
            detail=users_server_provider_message(_base_url(), provider),
        )
    return {
        **_provider_view(provider, entry),
        "remembered": False,
        "remember_error": None,
    }


@router.delete("/api/providers/{provider}/key")
def forget_provider_key(provider: str) -> Dict[str, Any]:
    """Clear the key from Lemonade and from the OS credential store."""
    from gaia.connectors.errors import ConnectorsError
    from gaia.llm.cloud_keys import forget_key

    _known(provider)
    if provider == LOCAL:
        raise HTTPException(status_code=422, detail="Local models have no key.")
    try:
        # The user's own server keeps its keys; only GAIA's copy is removed.
        if not _uses_users_server():
            status, body = _lemonade("DELETE", f"cloud/auth/{provider}")
            if status >= 400 and status != 404:
                raise _cloud_error(provider, status, body)
        removed = forget_key(provider)
    except ProviderError as e:
        _raise(e)
    except ConnectorsError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    return {"removed": removed}


@router.get("/api/providers/{provider}/models")
def provider_models(
    provider: str, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Chat models for the provider, recommended ones first with their evidence.

    Local models also carry their state, device, fit, size and measured speed.
    """
    _known(provider)
    try:
        models = _list_models(provider)
        if provider != LOCAL:
            return {"provider": provider, "models": models}
        return {"provider": provider, **_local_report(models, db)}
    except ProviderError as e:
        _raise(e)


def _read_system_info() -> Dict[str, Any]:
    status, info = _lemonade("GET", "system-info")
    if status >= 400:
        raise ProviderError(f"Lemonade answered /system-info with HTTP {status}.")
    return info


def _read_health() -> Dict[str, Any]:
    status, health = _lemonade("GET", "health")
    if status >= 400:
        raise ProviderError(f"Lemonade answered /health with HTTP {status}.")
    return health


def _preferred_backend() -> Optional[str]:
    from gaia.config import GaiaConfig, GaiaConfigError

    try:
        return GaiaConfig.load().llamacpp_backend
    except GaiaConfigError as e:
        raise ProviderError(str(e), 500) from e


def _local_report(models: List[Dict[str, Any]], db: ChatDatabase) -> Dict[str, Any]:
    from gaia.llm.model_fit import ModelFitError, capacity_from_system_info

    from ..local_models import annotate_local_models, measured_speeds

    system_info = _read_system_info()
    health = _read_health()
    capacity, capacity_error = None, None
    try:
        capacity = capacity_from_system_info(system_info)
    except ModelFitError as e:
        capacity_error = str(e)
    from .system import _get_download_progress

    annotated = annotate_local_models(
        models,
        health=health,
        system_info=system_info,
        capacity=capacity,
        preferred_backend=_preferred_backend(),
        speeds=measured_speeds(db.recent_inference_stats()),
    )
    for m in annotated:
        m["download_progress"] = _get_download_progress(m["id"])
    return {
        "models": annotated,
        "memory_gb": round(capacity.memory_gb, 1) if capacity else None,
        "memory_source": capacity.memory_source if capacity else None,
        "capacity_error": capacity_error,
    }


class SelectModelRequest(BaseModel):
    model: str


@router.post("/api/providers/select")
def select_model(
    body: SelectModelRequest, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Make *model* the one new turns run, and remember it across restarts."""
    model_id = body.model.strip()
    if not model_id:
        raise HTTPException(status_code=422, detail="No model given.")
    try:
        reason = _check_model_available(model_id)
    except _ServerNotReady as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if reason is not None:
        raise HTTPException(
            status_code=409,
            detail=f"Can't switch to {model_label(model_id)}: {reason}.",
        )
    try:
        _write_config_keys(
            {"last_provider": provider_of(model_id), "last_model": model_id}
        )
    except ProviderError as e:
        _raise(e)
    db.set_setting("custom_model", model_id)
    # A choice made now supersedes this run's restore notice.
    global _restore_result
    with _restore_lock:
        _restore_result = {"status": "none", "message": None}
    return _active_view(db)


# ── Context size (#5213) ───────────────────────────────────────────────────


def _loaded_ctx(health: Dict[str, Any], model_id: str) -> Optional[int]:
    from gaia.llm.model_device import find_loaded, loaded_entries

    entry = find_loaded(loaded_entries(health), model_id)
    if entry is None:
        return None
    ctx = (entry.get("recipe_options") or {}).get("ctx_size")
    return ctx if isinstance(ctx, int) and ctx > 0 else None


_CLOUD_CONTEXT_DETAIL = (
    "Cloud models run at their provider's context size; there is nothing to choose."
)


def _context_view(model_id: str) -> Dict[str, Any]:
    from gaia.config import GaiaConfigError
    from gaia.llm.context_choice import context_range, saved_context_size
    from gaia.llm.model_fit import ModelFitError, capacity_from_system_info

    if provider_of(model_id) != LOCAL:
        raise HTTPException(status_code=422, detail=_CLOUD_CONTEXT_DETAIL)
    try:
        capacity = capacity_from_system_info(_read_system_info())
        loaded_ctx = _loaded_ctx(_read_health(), model_id)
        chosen = saved_context_size(model_id)
    except ProviderError as e:
        _raise(e)
    except (ModelFitError, GaiaConfigError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    from gaia.llm.lemonade_client import GPU_CTX_SIZE, NPU_CTX_SIZE, runs_on_npu

    span = context_range(model_id, capacity)
    # A model GAIA has no entry for loads at its device's flat window.
    flat = NPU_CTX_SIZE if runs_on_npu(model_id) else GPU_CTX_SIZE
    return {
        "model": model_id,
        "display_name": display_name(model_id),
        "chosen": chosen,
        "automatic": span["automatic"] if span else flat,
        "min": span["min"] if span else None,
        "max_fit": span["max_fit"] if span else None,
        "options": span["options"] if span else [],
        "loaded_ctx": loaded_ctx,
        "memory_source": capacity.memory_source,
        "note": (
            None
            if span
            else "GAIA doesn't know how much memory this model needs per token, "
            "so it keeps the automatic size."
        ),
    }


@router.get("/api/providers/local/context")
def get_context(model: str) -> Dict[str, Any]:
    """The context sizes *model* may use on this PC, and the one in force."""
    return _context_view(model.strip())


class ContextRequest(BaseModel):
    """``ctx_size`` None returns the model to GAIA's automatic size."""

    model: str
    ctx_size: Optional[int] = None


@router.put("/api/providers/local/context")
def set_context(body: ContextRequest) -> Dict[str, Any]:
    """Remember a context size for a model; says whether the loaded model must reload."""
    from gaia.config import GaiaConfigError
    from gaia.llm.context_choice import ContextChoiceError, save_context_size
    from gaia.llm.model_fit import ModelFitError, capacity_from_system_info

    model_id = body.model.strip()
    if not model_id:
        raise HTTPException(status_code=422, detail="No model given.")
    if provider_of(model_id) != LOCAL:
        raise HTTPException(status_code=422, detail=_CLOUD_CONTEXT_DETAIL)
    try:
        capacity = capacity_from_system_info(_read_system_info())
        save_context_size(model_id, body.ctx_size, capacity)
    except ProviderError as e:
        _raise(e)
    except ContextChoiceError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except (ModelFitError, GaiaConfigError) as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    view = _context_view(model_id)
    reload = None
    if view["loaded_ctx"]:
        try:
            entry = _chat_entry(_read_health())
        except ProviderError as e:
            _raise(e)
        busy_reason = _busy_reason(entry, _warming_up())
        if view["loaded_ctx"] == (view["chosen"] or view["automatic"]):
            # Already loaded at that size: a reload would only stall chat.
            reload = {"started": False, "reason": "already_loaded"}
        elif _server_owner(_base_url()) != "gaia":
            reload = {"started": False, "reason": "not_gaias_server"}
        elif busy_reason == "warm_up":
            reload = {"started": False, "reason": "warm_up"}
        elif busy_reason == "request":
            reload = {"started": False, "reason": "busy"}
        elif _start_reload(model_id, server_up=True):
            reload = {"started": True, "reason": None}
        else:
            reload = {"started": False, "reason": "restarting"}
    return {**view, "reload": reload}


# ── Live status of the local model (sidebar popover) ──────────────────────


def _chat_entry(health: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    from gaia.llm.model_server_info import is_chat_model_entry

    loaded = [
        m
        for m in health.get("all_models_loaded") or []
        if is_chat_model_entry(m) and m.get("recipe") != "cloud"
    ]
    root = str(health.get("model_loaded") or "").lower()
    for m in loaded:
        if root and str(m.get("model_name") or "").lower() == root:
            return m
    return loaded[0] if loaded else None


def _memory_view(
    system_info: Dict[str, Any], model_id: Optional[str], ctx: Optional[int]
) -> Dict[str, Any]:
    """Total and free memory of the pool models load into, and this model's share."""
    from gaia.llm.lemonade_client import find_model_requirement
    from gaia.llm.model_fit import (
        capacity_from_system_info,
        free_memory,
        kv_cache_gb,
        read_os_memory,
        required_memory_gb,
    )

    capacity = capacity_from_system_info(system_info)
    # This PC's free memory says nothing about a Lemonade on another machine.
    free = (
        free_memory(system_info, read_os_memory())
        if _is_local_url(_base_url())
        else None
    )
    req = find_model_requirement(model_id) if model_id else None
    model_gb = None
    if req is not None and req.size_gb:
        model_gb = required_memory_gb(
            req.size_gb, kv_cache_gb(req.kv_bytes_per_token, ctx or req.min_ctx_size)
        )
    elif model_id:
        # Unknown KV cost: weights plus the shared margin, from Lemonade's size.
        size = next(
            (
                m.get("size_gb")
                for m in _list_models(LOCAL, offer_optional=False)
                if m["id"] == model_id
            ),
            None,
        )
        if isinstance(size, (int, float)) and size > 0:
            model_gb = required_memory_gb(size)
    return {
        "source": capacity.memory_source,
        "total_gb": round(capacity.memory_gb, 1),
        # The OS also counts RAM outside the GPU pool, so free is bounded by what
        # the pool has left once this model is in it.
        "free_gb": (
            round(
                min(free.free_gb, max(0.0, capacity.memory_gb - (model_gb or 0.0))), 1
            )
            if free
            else None
        ),
        "model_gb": round(model_gb, 1) if model_gb else None,
    }


#: Why Restart waits, worded for each cause of a busy model.
_BUSY_DETAIL = {
    "warm_up": "GAIA is getting the model ready for a new chat. Restart it once that finishes.",
    "request": "The model is answering a request. Restart it once that finishes.",
}


def _warming_up() -> bool:
    from .. import warmup

    return warmup.warm_up_status().get("state") == warmup.WARMING


def _busy_reason(entry: Optional[Dict[str, Any]], warming: bool) -> Optional[str]:
    """``warm_up`` while GAIA primes a new chat, ``request`` while it answers one."""
    # The warm-up's first load lists no model yet, so it counts with or without one.
    if warming:
        return "warm_up"
    return "request" if entry and entry.get("is_busy") else None


#: ``all_models_loaded[].status`` values for a model that cannot answer yet.
_LOADING_STATUSES = frozenset({"loading", "starting", "initializing", "warming"})


@router.get("/api/providers/local/status", dependencies=[Depends(require_ui_header)])
def local_status(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """Loaded / warming / idle / stopped, with device, memory and context window."""
    from gaia.llm.model_device import describe_placement
    from gaia.llm.model_fit import ModelFitError

    from .. import warmup

    active = _active_view(db)
    view: Dict[str, Any] = {
        "state": "stopped",
        "active_model": active["model"],
        "active_display_name": active["display_name"],
        "active_remote": active["remote"],
        "model": None,
        "display_name": None,
        "placement": None,
        "ctx_size": None,
        "memory": None,
        "memory_error": None,
        "error": None,
        "error_detail": None,
        "base_url": None,
        "owner": None,
        "busy": False,
        "busy_reason": None,
        "restarting": _restart_lock.locked(),
        "restart_error": _restart_error,
    }
    try:
        view["base_url"] = _base_url()
        view["owner"] = _server_owner(view["base_url"])
        health = _read_health()
        system_info = _read_system_info()
    except ProviderError as e:
        down = e.status == 503 and view["base_url"]
        view["error"] = (
            f"The model server isn't answering at {view['base_url']}. "
            + (
                "Restart starts it."
                if view["owner"] == "gaia"
                else "GAIA doesn't start a server it doesn't run; start it yourself."
            )
            if down
            else str(e)
        )
        view["error_detail"] = str(e)
        return view
    entry = _chat_entry(health)
    warming = warmup.warm_up_status().get("state") == warmup.WARMING
    if entry is None:
        view["state"] = "warming" if warming else "idle"
        view["busy"] = warming
        view["busy_reason"] = _busy_reason(None, warming)
    else:
        name = str(entry.get("model_name") or "")
        # "in_use" is a loaded model answering a request, not one still loading.
        loading = entry.get("status") in _LOADING_STATUSES
        view["state"] = "warming" if loading or warming else "loaded"
        busy_reason = _busy_reason(entry, warming)
        view["busy"] = busy_reason is not None
        view["busy_reason"] = busy_reason
        view["model"] = name
        view["display_name"] = display_name(name)
        try:
            backend = _preferred_backend()
        except ProviderError as e:
            view["error"] = str(e)
            backend = None
        view["placement"] = describe_placement(
            entry.get("recipe"), entry, system_info, backend
        )
        view["ctx_size"] = (entry.get("recipe_options") or {}).get("ctx_size")
    try:
        view["memory"] = _memory_view(system_info, view["model"], view["ctx_size"])
    except (ModelFitError, ProviderError) as e:
        view["memory_error"] = str(e)
    return view


_restart_lock = threading.Lock()
#: Why the last restart failed, shown in the status popover until the next one.
_restart_error: Optional[str] = None


def _restart_job(model_id: str, server_up: bool) -> None:
    """Reload *model_id* through GAIA's checked load path, then free ``_restart_lock``."""
    from gaia.llm.lemonade_client import LemonadeClient
    from gaia.llm.lemonade_manager import LemonadeManager

    global _restart_error
    try:
        if server_up:
            # pylint: disable=protected-access
            LemonadeClient(verbose=False)._ensure_model_loaded(model_id, force=True)
        elif not LemonadeManager.ensure_ready(quiet=True, model=model_id):
            raise RuntimeError(
                "the model server did not start; see ~/.gaia/gaia.log for "
                "why, then press Restart again"
            )
    except Exception as e:  # noqa: BLE001 - surfaced in /local/status and the log
        logger.error("Restarting the local model %s failed: %s", model_id, e)
        _restart_error = f"Restarting {display_name(model_id)} failed: {e}"
    finally:
        _restart_lock.release()


def _start_reload(model_id: str, server_up: bool) -> bool:
    """Start a reload in the background; False when one is already running."""
    global _restart_error
    if not _restart_lock.acquire(blocking=False):
        return False
    _restart_error = None
    threading.Thread(
        target=_restart_job,
        args=(model_id, server_up),
        daemon=True,
        name="gaia-model-restart",
    ).start()
    return True


class RestartRequest(BaseModel):
    """``confirm`` is required to reload a model on a server GAIA does not run."""

    confirm: bool = False


@router.post("/api/providers/local/restart", status_code=202)
def restart_local(
    body: Optional[RestartRequest] = None, db: ChatDatabase = Depends(get_db)
) -> Dict[str, Any]:
    """Start the model server if it is stopped, then reload the chat model.

    Reloads the model already loaded (or the active one), so nothing another
    session relies on is swapped out.
    """
    active = _active_view(db)
    try:
        health = _read_health()
        entry = _chat_entry(health)
        busy_reason = _busy_reason(entry, _warming_up())
        if busy_reason is not None:
            raise HTTPException(status_code=409, detail=_BUSY_DETAIL[busy_reason])
        model_id = str(entry.get("model_name")) if entry else active["model"]
        server_up = True
    except ProviderError as e:
        if e.status != 503:
            _raise(e)
        model_id, server_up = active["model"], False
    if not model_id:
        raise HTTPException(status_code=409, detail=active["config_error"])
    try:
        base = _base_url()
    except ProviderError as e:
        _raise(e)
    owner = _server_owner(base)
    if owner != "gaia" and not server_up:
        raise HTTPException(
            status_code=409,
            detail=(
                f"The model server at {base} isn't GAIA's, so GAIA doesn't start "
                "it. Start it the way you usually do."
            ),
        )
    if owner != "gaia" and not (body and body.confirm):
        raise HTTPException(
            status_code=409,
            detail=(
                f"This reloads {display_name(model_id)} on your Lemonade at {base}. "
                "Other apps using it are interrupted while it reloads. Confirm to "
                "go ahead."
            ),
        )
    if provider_of(model_id) != LOCAL:
        raise HTTPException(
            status_code=422,
            detail="The active model runs in the cloud; nothing to restart.",
        )
    if not _start_reload(model_id, server_up):
        raise HTTPException(status_code=409, detail="A restart is already running.")
    return {"status": "restarting", "model": model_id, "starting_server": not server_up}
