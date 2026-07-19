# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""NPU-aware default model selection for the email triage agent (#1439).

Auto-selects the FLM-native triage model (``gemma4-it-e2b-FLM``) when the
Lemonade Server the agent will talk to reports an available AMD NPU AND
already serves that model; falls back to the GGUF default
(``DEFAULT_MODEL_NAME``, currently ``Gemma-4-E4B-it-GGUF``) in every other
case — no NPU, NPU present but the FLM model not downloaded, or the probe
itself failing/timing out.

This module is a LEAF: it must never import ``gaia_agent_email.api_routes``
(that would drag the whole FastAPI router into every CLI/agent
construction). ``api_routes.py`` imports the shared probe helpers back FROM
here instead — this module used to live inline in ``api_routes.py``; the
probe helpers (:func:`_resolve_probe_base`, :func:`_probe_model_present`,
the two timeout constants) were moved here verbatim so this module and
``api_routes.py`` share one implementation.

Timeout trap (#1677): NEVER call ``LemonadeClient.get_system_info()`` here
— it has no timeout knob of its own and inherits
``DEFAULT_REQUEST_TIMEOUT`` (900s). This module always uses a raw
``requests.get`` with the same short probe-timeout tuple ``api_routes.py``
already uses for its ``/health`` and ``/models`` probes.
"""

from __future__ import annotations

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Fast pre-flight timeouts (mirrors the api_routes.py probe timeouts,
# #1677): a short connect timeout turns "server down" into a prompt
# fallback instead of hanging on the OS SYN timeout.
_LEMONADE_PROBE_CONNECT_TIMEOUT = 2.0
_LEMONADE_PROBE_READ_TIMEOUT = 3.0

# The two-literal candidate set (security-panel-mandated): the value
# resolve_default_email_model() returns is ALWAYS one of these two hardcoded
# strings — never anything interpolated from a server response.
NPU_EMAIL_MODEL_ID = "gemma4-it-e2b-FLM"

# Module-level SUCCESS-ONLY memo, keyed by the resolved probe_base string. A
# failed/timeout probe is NEVER cached here — a cold-start caller (e.g. the
# import-time construction of a service singleton) must not bake in a
# "Lemonade wasn't up yet" failure for the life of the process. See
# _reset_model_select_cache() for the test-only reset hook.
_resolution_cache: Dict[str, str] = {}


def _resolve_probe_base(base_url: Optional[str]) -> str:
    """Resolve the Lemonade ``/api/v1`` base URL for a health/model probe.

    An explicit ``base_url`` is normalised to end in ``/api/v1`` (callers
    often omit it); ``None`` falls back to the env-derived default via
    ``_get_lemonade_config``. Shared by every probe in this module (and, via
    the re-export in ``api_routes.py``, its reachability/model-presence
    probes) so they all target the exact same server.
    """
    from gaia.llm.lemonade_client import _get_lemonade_config

    if base_url:
        probe_base = base_url.rstrip("/")
        if not probe_base.endswith("/api/v1"):
            probe_base = f"{probe_base}/api/v1"
        return probe_base
    _, _, probe_base = _get_lemonade_config()
    return probe_base


def _probe_model_present(probe_base: str, model_id: str) -> bool:
    """Cheaply check whether ``model_id`` is downloaded on the Lemonade server.

    Queries the model list (downloaded models only) with the short probe
    timeout and matches on the ``id`` field using the core tolerant
    comparison (``user.``-prefixed registrations are listed under the
    stripped id). Sends the resolved Lemonade auth header so an
    authenticated server answers instead of 401-ing. Raises
    ``requests.RequestException`` on a transport failure — the caller turns
    that into an actionable readiness hint (or, here, a fallback decision)
    rather than silently reporting "absent".

    "Present" is intentionally cheap (a list lookup, no model load).
    """
    import requests

    from gaia.llm.lemonade_client import (
        _model_ids_match,
        lemonade_auth_headers,
        resolve_lemonade_api_key,
    )

    resp = requests.get(
        f"{probe_base}/models",
        headers=lemonade_auth_headers(resolve_lemonade_api_key()),
        timeout=(_LEMONADE_PROBE_CONNECT_TIMEOUT, _LEMONADE_PROBE_READ_TIMEOUT),
    )
    resp.raise_for_status()
    payload = resp.json()
    data = payload.get("data", []) if isinstance(payload, dict) else []
    return any(
        isinstance(m, dict) and _model_ids_match(m.get("id"), model_id) for m in data
    )


def _probe_npu_available(probe_base: str) -> bool:
    """Raw ``/system-info`` probe for ``devices.amd_npu.available`` (#1677).

    Never uses ``LemonadeClient.get_system_info()`` — that has no timeout
    knob and inherits the 900s ``DEFAULT_REQUEST_TIMEOUT``. Raises
    ``requests.RequestException`` on a transport failure/timeout; the caller
    treats that as "can't tell, fall back to E4B" and does NOT cache it.
    """
    import requests

    from gaia.llm.lemonade_client import lemonade_auth_headers, resolve_lemonade_api_key

    resp = requests.get(
        f"{probe_base}/system-info",
        headers=lemonade_auth_headers(resolve_lemonade_api_key()),
        timeout=(_LEMONADE_PROBE_CONNECT_TIMEOUT, _LEMONADE_PROBE_READ_TIMEOUT),
    )
    resp.raise_for_status()
    payload = resp.json()
    devices = payload.get("devices", {}) if isinstance(payload, dict) else {}
    npu = devices.get("amd_npu", {}) if isinstance(devices, dict) else {}
    return bool(npu.get("available")) if isinstance(npu, dict) else False


def resolve_default_email_model(base_url: Optional[str] = None) -> str:
    """Resolve the default Lemonade model id for email triage (#1439).

    NPU available AND the FLM triage model already servable on that
    server -> ``NPU_EMAIL_MODEL_ID``. Every other case (no NPU, NPU present
    but the model isn't downloaded there, or either probe
    failing/timing out) -> ``DEFAULT_MODEL_NAME`` (GGUF). Callers that
    already have an explicit model id (``EmailAgentConfig.model_id`` or an
    end-user-supplied model) must not call this — it is the "no explicit
    preference" fallback path only.

    Successful resolutions are cached per resolved ``probe_base`` so a hot
    path (e.g. a REST route probing on every request) doesn't re-probe
    Lemonade every call. A failed/timed-out probe is NEVER cached — see the
    module docstring.
    """
    import requests

    from gaia.llm.lemonade_client import DEFAULT_MODEL_NAME

    probe_base = _resolve_probe_base(base_url)

    cached = _resolution_cache.get(probe_base)
    if cached is not None:
        return cached

    try:
        npu_available = _probe_npu_available(probe_base)
    except requests.exceptions.RequestException as exc:
        logger.info(
            "NPU auto-select: /system-info probe failed at %s (%s: %s) — "
            "defaulting to %s.",
            probe_base,
            type(exc).__name__,
            exc,
            DEFAULT_MODEL_NAME,
        )
        return DEFAULT_MODEL_NAME  # NOT cached — retry on the next call.

    if not npu_available:
        _resolution_cache[probe_base] = DEFAULT_MODEL_NAME
        return DEFAULT_MODEL_NAME

    try:
        servable = _probe_model_present(probe_base, NPU_EMAIL_MODEL_ID)
    except requests.exceptions.RequestException as exc:
        logger.info(
            "NPU auto-select: could not read the model list at %s/models "
            "(%s: %s) — defaulting to %s.",
            probe_base,
            type(exc).__name__,
            exc,
            DEFAULT_MODEL_NAME,
        )
        return DEFAULT_MODEL_NAME  # NOT cached — retry on the next call.

    if not servable:
        logger.info(
            "NPU detected at %s but %s is not downloaded there — defaulting "
            "to %s. Run `gaia init --profile npu` to pull it.",
            probe_base,
            NPU_EMAIL_MODEL_ID,
            DEFAULT_MODEL_NAME,
        )
        _resolution_cache[probe_base] = DEFAULT_MODEL_NAME
        return DEFAULT_MODEL_NAME

    _resolution_cache[probe_base] = NPU_EMAIL_MODEL_ID
    return NPU_EMAIL_MODEL_ID


def _reset_model_select_cache() -> None:
    """Test-only: clear the success-only resolution memo.

    Tests that monkeypatch ``requests.get`` across multiple probe_base
    values (or re-probe the same one under a different fake) must call this
    between cases — otherwise a cached success from an earlier case silently
    short-circuits a later one instead of re-probing.
    """
    _resolution_cache.clear()
