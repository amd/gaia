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

import json
import threading
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from gaia.logger import get_logger

from ..database import ChatDatabase
from ..dependencies import get_db

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
    from gaia.llm.lemonade_client import resolve_lemonade_base_url

    return resolve_lemonade_base_url().rstrip("/")


def _require_loopback(base_url: str) -> None:
    host = (urlparse(base_url).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise ProviderError(
            f"Lemonade is configured at {base_url}, which is not on this PC, so "
            "GAIA will not send it a provider key. Point LEMONADE_BASE_URL at a "
            "local Lemonade, or configure the provider on that server directly.",
            status=409,
        )


def _lemonade(
    method: str, path: str, payload: Optional[Dict[str, Any]] = None
) -> Tuple[int, Dict[str, Any]]:
    from gaia.llm.lemonade_client import lemonade_auth_headers, resolve_lemonade_api_key

    base = _base_url()
    try:
        resp = requests.request(
            method,
            f"{base}/{path}",
            json=payload,
            headers={
                "Content-Type": "application/json",
                **lemonade_auth_headers(resolve_lemonade_api_key()),
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
    return not labels & _NON_CHAT_LABELS


def _list_models(provider: str) -> List[Dict[str, Any]]:
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME
    from gaia.llm.recommended_models import rank

    status, body = _lemonade("GET", "models?show_all=true")
    if status >= 400:
        raise ProviderError(f"Lemonade answered /models with HTTP {status}.")
    out: List[Dict[str, Any]] = []
    for m in body.get("data") or []:
        model_id = str(m.get("id") or "")
        if not model_id or not _is_chat_model(m):
            continue
        is_cloud = m.get("recipe") == "cloud" or bool(m.get("cloud_provider"))
        if provider == LOCAL:
            if is_cloud or not (m.get("downloaded") or model_id == DEFAULT_MODEL_NAME):
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
                "rank": ranked[0] if ranked else None,
                "note": ranked[1].note if ranked else None,
                "evidence": ranked[1].evidence if ranked else None,
            }
        )
    out.sort(key=lambda x: (x["rank"] is None, x["rank"] or 0, x["id"].lower()))
    return out


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


def _active_view(db: ChatDatabase) -> Dict[str, Any]:
    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    chosen = (db.get_setting("custom_model") or "").strip()
    model = chosen or DEFAULT_MODEL_NAME
    provider = provider_of(model)
    return {
        "provider": provider,
        "model": model,
        "label": model_label(model),
        "remote": provider != LOCAL,
        "is_default": not chosen,
    }


# ── Last-model memory (#4497) ──────────────────────────────────────────────


def _write_config_keys(values: Dict[str, Any]) -> None:
    """Read-modify-write ``config.json``, keeping keys other clients wrote."""
    from gaia.config import GaiaConfig

    path = GaiaConfig.config_path()
    data: Dict[str, Any] = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as e:
            raise ProviderError(
                f"{path} is not valid JSON, so the model choice was not saved: {e}. "
                "Fix or delete the file, then pick the model again.",
                500,
            ) from e
    data.update(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


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
            db.set_setting("custom_model", last)
            _restore_result = {
                "status": "restored",
                "message": "Restored your last model.",
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


@router.get("/api/providers")
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


@router.get("/api/providers/active")
def active_model(db: ChatDatabase = Depends(get_db)) -> Dict[str, Any]:
    """The model new turns run, plus the once-per-run restore notice."""
    restore = restore_last_model(db)
    return {**_active_view(db), "restore": restore}


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


@router.delete("/api/providers/{provider}/key")
def forget_provider_key(provider: str) -> Dict[str, Any]:
    """Clear the key from Lemonade and from the OS credential store."""
    from gaia.connectors.errors import ConnectorsError
    from gaia.llm.cloud_keys import forget_key

    _known(provider)
    if provider == LOCAL:
        raise HTTPException(status_code=422, detail="Local models have no key.")
    try:
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
def provider_models(provider: str) -> Dict[str, Any]:
    """Chat models for the provider, recommended ones first with their evidence."""
    _known(provider)
    try:
        return {"provider": provider, "models": _list_models(provider)}
    except ProviderError as e:
        _raise(e)


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
