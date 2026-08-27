# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""AMD LLM gateway support, via Lemonade's cloud offload.

Lemonade >= 11.8 can register any OpenAI-compatible endpoint as a *cloud
provider* and expose its models in its own ``/api/v1/models`` under a
``<provider>.`` namespace. Registering the AMD gateway that way means every
GAIA agent can address a gateway model with no new LLM client — see
``docs/guides/llm-gateway.mdx``.

**No API token is ever written to disk by GAIA.** Tokens go straight to
Lemonade's ``POST /api/v1/cloud/auth``, which holds them in process memory only.
For a token that survives a Lemonade restart, set ``LEMONADE_AMD_API_KEY`` in
Lemonade's environment. The file this module *does* write
(``~/.gaia/gateway.json``) records only the base URL and which models the user
enabled; it has no field for a secret.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from gaia.config import GAIA_CONFIG_DIR
from gaia.llm.lemonade_client import (
    CLOUD_RECIPE,
    LemonadeClient,
    lemonade_auth_headers,
    resolve_lemonade_api_key,
)
from gaia.logger import get_logger
from gaia.version import LEMONADE_GATEWAY_MIN_VERSION

log = get_logger(__name__)

# Lemonade namespaces discovered models as "<provider>.<id>", so this is also
# the prefix of every gateway model id GAIA will see.
GATEWAY_PROVIDER = "amd"

# The env var Lemonade resolves for this provider. Set it in *Lemonade's*
# environment (not your shell) for a token that survives a restart.
GATEWAY_API_KEY_ENV = f"LEMONADE_{GATEWAY_PROVIDER.upper()}_API_KEY"

# AMD's gateway. `llm.amd.com` is the SSO-gated portal, not the API — every
# path there redirects to Okta. The OpenAI-compatible surface is the Unified
# API on a separate host, verified live: `<base>/models` lists 76 models and
# `<base>/chat/completions` returns real completions.
DEFAULT_GATEWAY_BASE_URL = os.getenv(
    "GAIA_GATEWAY_BASE_URL", "https://llm-api.amd.com/Unified/v1"
)

# The gateway is Azure API Management, which authenticates on its own
# subscription-key header rather than a bearer token. AMD's sample sends the key
# in `Authorization: Bearer` as well, but only because the OpenAI SDK requires
# an `api_key`; the gateway ignores it. Verified: the APIM header alone returns
# 200, and `Authorization: Bearer` alone returns 401 "missing subscription key".
# That matters because Lemonade can carry exactly one auth header.
DEFAULT_AUTH_HEADER_NAME = "Ocp-Apim-Subscription-Key"
DEFAULT_AUTH_HEADER_PREFIX = ""

# Ordered preference for the picker, best first. The first match is what GAIA
# selects automatically when a gateway is connected with nothing chosen yet.
#
# `Gemma-4-31B` leads deliberately: it is currently the ONLY gateway model that
# streams. The others return zero tokens on a streaming request while
# non-streaming works, and GAIA's agent path streams by default — so any other
# default hands a new user an agent that produces nothing. It is also on-prem,
# so it carries no per-token cost.
#
# Matching is lowercase-substring: the gateway mixes casing across its
# catalogue (`Claude-Opus-5` sits next to `claude-opus-4.8`).
PREFERRED_MODEL_HINTS = (
    "gemma-4-31b",
    "claude-opus-5",
    "claude-sonnet-5",
)

# Older name, kept so existing imports keep working.
RECOMMENDED_HINTS = PREFERRED_MODEL_HINTS


def preference_rank(model_id: str) -> int:
    """Position in ``PREFERRED_MODEL_HINTS``; unlisted models sort last.

    Explicit ranking rather than alphabetical: sorting the recommended models
    by name put `Claude-Opus-5` ahead of `Gemma-4-31B`, which made the one
    model that cannot stream the default.
    """
    lowered = model_id.lower()
    for i, hint in enumerate(PREFERRED_MODEL_HINTS):
        if hint in lowered:
            return i
    return len(PREFERRED_MODEL_HINTS)


GATEWAY_STATE_FILE = Path(
    os.getenv("GAIA_GATEWAY_FILE", str(GAIA_CONFIG_DIR / "gateway.json"))
)

_REQUEST_TIMEOUT = 30
# Registration and auth trigger upstream model discovery, which is slower.
_DISCOVERY_TIMEOUT = 60
# Bounds the wait on a mistyped host: a DNS failure to a non-existent domain
# can otherwise take ~30s, which reads as a hang rather than an answer.
_PROBE_TIMEOUT = 12


class GatewayError(Exception):
    """Raised when a gateway operation fails.

    Messages name what failed, what to do about it, and where to look.
    """


@dataclass
class GatewayModel:
    """A model discovered from the gateway."""

    id: str
    labels: List[str] = field(default_factory=list)
    ctx_size: Optional[int] = None

    @property
    def upstream_id(self) -> str:
        """The gateway's own name for this model, without GAIA's namespace."""
        prefix = f"{GATEWAY_PROVIDER}."
        return self.id[len(prefix) :] if self.id.startswith(prefix) else self.id

    @property
    def recommended(self) -> bool:
        lowered = self.id.lower()
        return any(hint in lowered for hint in RECOMMENDED_HINTS)

    @property
    def tool_calling(self) -> bool:
        return "tool-calling" in self.labels


@dataclass
class GatewayStatus:
    """Provider registration and auth state, from Lemonade's system-info."""

    installed: bool
    base_url: Optional[str] = None
    env_var_set: bool = False
    runtime_key_set: bool = False
    models_discovered: int = 0
    warnings: List[str] = field(default_factory=list)

    @property
    def authenticated(self) -> bool:
        return self.env_var_set or self.runtime_key_set


@dataclass
class GatewayState:
    """Non-secret gateway preferences persisted to ``~/.gaia/gateway.json``.

    There is deliberately no token field. Adding one would put a secret on
    disk, which the whole design exists to avoid.
    """

    base_url: str = DEFAULT_GATEWAY_BASE_URL
    enabled_models: List[str] = field(default_factory=list)
    active_model: Optional[str] = None

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "GatewayState":
        target = path or GATEWAY_STATE_FILE
        if not target.exists():
            return cls()
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            raise GatewayError(
                f"Gateway settings at {target} could not be read: {e}. "
                f"Delete the file to start fresh, then re-run `gaia gateway install`."
            ) from e
        return cls(
            base_url=raw.get("base_url") or DEFAULT_GATEWAY_BASE_URL,
            enabled_models=list(raw.get("enabled_models") or []),
            active_model=raw.get("active_model"),
        )

    def save(self, path: Optional[Path] = None) -> None:
        target = path or GATEWAY_STATE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "base_url": self.base_url,
            "enabled_models": self.enabled_models,
            "active_model": self.active_model,
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        # Preferences, not secrets — but this sits next to config.json in a
        # user-private directory, so match its posture.
        try:
            os.chmod(target, 0o600)
        except OSError as e:
            log.debug(f"Could not tighten permissions on {target}: {e}")


def clear_default_model_if(model_id: Optional[str]) -> bool:
    """Drop GAIA's persistent ``default_model`` when it names *model_id*.

    ``gaia gateway use`` writes the choice to two places: this module's state
    file and ``GaiaConfig.default_model``. Removing a model from only the first
    left ``gaia chat`` / ``llm`` / ``prompt`` resolving a gateway id that no
    longer exists, with no way to clear it short of editing config.json.

    Returns True when a value was actually cleared.
    """
    if not model_id:
        return False
    try:
        from gaia.config import GaiaConfig, GaiaConfigError

        cfg = GaiaConfig.load()
        if cfg.get("default_model") != model_id:
            return False
        cfg.set("default_model", "")
        cfg.save()
        return True
    except (GaiaConfigError, OSError) as e:
        # Surfaced rather than swallowed: the caller reports it alongside the
        # operation that triggered it.
        raise GatewayError(
            f"Removed the gateway model but could not clear GAIA's default "
            f"model ({e}). Run `gaia config set default_model <id>` to fix it."
        ) from e


# Slot name in the OS credential store. Namespaced by provider so a second
# gateway would not collide.
GATEWAY_SECRET_NAME = f"gateway:{GATEWAY_PROVIDER}"


def remember_token(token: str) -> None:
    """Keep the token in the OS credential store so it survives a restart.

    Lemonade holds a token in memory only, which means re-entering it every
    time Lemonade restarts — several times a day for anyone running an agent
    harness. This is the one place GAIA persists it, and it is encrypted at
    rest by the OS (DPAPI on Windows, Keychain on macOS, SecretService on
    Linux). ``verify_keyring_backend`` refuses plaintext backends, so it cannot
    quietly become a readable file.

    Still never written to ``gateway.json`` or any other GAIA file.
    """
    from gaia.connectors.store import save_secret

    save_secret(GATEWAY_SECRET_NAME, token)


def recall_token() -> Optional[str]:
    """The remembered token, or None when there is none.

    A keyring that cannot be opened is reported as "no stored token" rather
    than raised: the caller's next step is to prompt, which is the right
    outcome either way. The reason is logged.
    """
    try:
        from gaia.connectors.store import peek_secret

        return peek_secret(GATEWAY_SECRET_NAME)
    except Exception as e:  # noqa: BLE001 - degraded to "prompt me", never fatal
        log.debug(f"Could not read the stored gateway token: {e}")
        return None


def forget_token() -> bool:
    """Remove the remembered token. True when one was actually stored."""
    from gaia.connectors.store import delete_secret

    had = recall_token() is not None
    delete_secret(GATEWAY_SECRET_NAME)
    return had


class GatewayManager:
    """Talks to Lemonade's cloud-provider API on behalf of GAIA."""

    def __init__(self, client: Optional[LemonadeClient] = None):
        self.client = client or LemonadeClient(verbose=False)
        self.log = log

    # -- plumbing ---------------------------------------------------------

    @property
    def base_url(self) -> str:
        """Lemonade's own base URL (already ends in ``/api/v1``)."""
        return self.client.base_url

    def _headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            **lemonade_auth_headers(resolve_lemonade_api_key()),
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Optional[Dict[str, Any]] = None,
        timeout: int = _REQUEST_TIMEOUT,
        redact: bool = False,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        self.log.debug(f"{method} {url}" + ("" if redact else f" {payload or ''}"))
        try:
            response = requests.request(
                method, url, json=payload, headers=self._headers(), timeout=timeout
            )
        except requests.exceptions.RequestException as e:
            raise GatewayError(
                f"Lemonade Server is not reachable at {self.base_url}: {e}. "
                f"Start it with `lemonade-server serve`, or point "
                f"LEMONADE_BASE_URL at a running server."
            ) from e

        if response.status_code >= 400:
            raise self._http_error(response, method, url)
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError as e:
            raise GatewayError(
                f"Lemonade returned a non-JSON response to {method} {url}: {e}"
            ) from e

    def _http_error(
        self, response: requests.Response, method: str, url: str
    ) -> GatewayError:
        """Translate a Lemonade error body into an actionable GatewayError."""
        detail = ""
        error_type = ""
        try:
            body = response.json().get("error", {})
            detail = body.get("message", "")
            error_type = body.get("type", "")
        except ValueError:
            detail = response.text.strip()[:400]

        if response.status_code == 409 and error_type == "auth_conflict":
            return GatewayError(
                f"{GATEWAY_API_KEY_ENV} is already set in Lemonade's environment, "
                f"so Lemonade refuses to accept a different token at runtime. "
                f"Either keep using that key, or unset {GATEWAY_API_KEY_ENV} and "
                f"restart Lemonade before setting one here."
            )
        if response.status_code == 404 and "cloud" in url:
            return GatewayError(
                f"Lemonade at {self.base_url} has no cloud-provider API "
                f"(needs >= {LEMONADE_GATEWAY_MIN_VERSION}). "
                f"Upgrade with `gaia init` and re-run this command."
            )
        return GatewayError(
            f"{method} {url} failed with HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )

    # -- provider lifecycle ----------------------------------------------

    def check_reachable(self, base_url: str) -> Optional[int]:
        """Probe the gateway's own ``/models`` before registering it.

        Returns the number of models it lists, or ``None`` when the endpoint is
        real but wants credentials — which is the normal state before a token
        has been supplied, since a token cannot be set until the provider is
        registered.

        Redirects are deliberately NOT followed. AMD's gateway answers an
        unauthenticated request with ``302 -> /login``, and following that
        lands on an Okta HTML page: a ``200`` that parses as neither JSON nor a
        model list, which would report a correct URL as "not an
        OpenAI-compatible endpoint" and block registration entirely.
        """
        url = f"{base_url.rstrip('/')}/models"
        token = os.getenv(GATEWAY_API_KEY_ENV) or os.getenv("GAIA_GATEWAY_TOKEN")
        # Same header Lemonade will use, so the probe tests the real thing.
        headers = (
            {DEFAULT_AUTH_HEADER_NAME: f"{DEFAULT_AUTH_HEADER_PREFIX}{token}"}
            if token
            else {}
        )
        try:
            response = requests.get(
                url, headers=headers, timeout=_PROBE_TIMEOUT, allow_redirects=False
            )
        except requests.exceptions.RequestException as e:
            raise GatewayError(
                f"Could not reach the gateway at {url}: {e}. "
                f"Check the base URL and that you are on the network/VPN that "
                f"can see it."
            ) from e
        # A redirect to a login page and a 401 mean the same thing: the route
        # exists and wants credentials.
        if response.status_code in (301, 302, 303, 307, 308, 401, 403):
            return None
        if response.status_code >= 400:
            raise GatewayError(
                f"The gateway at {url} returned HTTP {response.status_code}. "
                f"Check that the base URL includes the API path "
                f"(e.g. .../v1 — AMD's gateway serves /v1, not /api/v1)."
            )
        try:
            body = response.json()
        except ValueError as e:
            raise GatewayError(
                f"{url} did not return JSON, so it is not an OpenAI-compatible "
                f"models endpoint: {e}"
            ) from e
        entries = body.get("data") if isinstance(body, dict) else body
        return len(entries) if isinstance(entries, list) else 0

    def install(
        self,
        base_url: str,
        *,
        api_key: Optional[str] = None,
        auth_header_name: Optional[str] = None,
        auth_header_prefix: Optional[str] = None,
        allow_insecure_http: bool = False,
    ) -> Dict[str, Any]:
        """Register the gateway with Lemonade as a cloud provider.

        ``auth_header_name`` / ``auth_header_prefix`` override the APIM
        subscription header GAIA sends by default. Pass
        ``("Authorization", "Bearer ")`` for a bearer-token gateway.

        ``allow_insecure_http`` is required before Lemonade will hold a token
        for an ``http://`` endpoint — it refuses by default rather than send a
        credential in the clear. Needed only for an on-prem gateway without
        TLS; AMD's is https and does not.
        """
        payload: Dict[str, Any] = {
            "backend": "cloud",
            "provider": GATEWAY_PROVIDER,
            "base_url": base_url.rstrip("/"),
            # GAIA's agents speak OpenAI chat completions end to end; the
            # anthropic wire format serves only /v1/messages.
            "wire_format": "openai",
            # Default to the APIM subscription header the gateway actually
            # checks. Sending a bearer token instead returns 401 and, because
            # Lemonade stores a key without validating it, that surfaces only
            # as an empty model list much later.
            "auth_header_name": DEFAULT_AUTH_HEADER_NAME,
            "auth_header_prefix": DEFAULT_AUTH_HEADER_PREFIX,
        }
        if auth_header_name is not None:
            payload["auth_header_name"] = auth_header_name
        if auth_header_prefix is not None:
            payload["auth_header_prefix"] = auth_header_prefix
        if allow_insecure_http:
            payload["allow_insecure_http"] = True
        if api_key:
            payload["api_key"] = api_key

        result = self._request(
            "POST", "install", payload=payload, timeout=_DISCOVERY_TIMEOUT, redact=True
        )
        state = GatewayState.load()
        state.base_url = payload["base_url"]
        state.save()
        return result

    def uninstall(self) -> Dict[str, Any]:
        """Remove the provider and forget the enabled-model selection."""
        result = self._request(
            "POST",
            "uninstall",
            payload={"backend": "cloud", "provider": GATEWAY_PROVIDER},
        )
        state = GatewayState.load()
        previously_active = state.active_model
        state.enabled_models = []
        state.active_model = None
        state.save()
        clear_default_model_if(previously_active)
        return result

    def set_token(self, api_key: str) -> Dict[str, Any]:
        """Hand a token to Lemonade for this session.

        Lemonade keeps it in process memory and never writes it to disk, so it
        is gone on restart. Set ``LEMONADE_AMD_API_KEY`` in Lemonade's
        environment for one that persists.
        """
        if not api_key or not api_key.strip():
            raise GatewayError(
                "No token supplied. Pass one to `gaia gateway auth`, or set "
                f"{GATEWAY_API_KEY_ENV} in Lemonade's environment."
            )
        payload: Dict[str, Any] = {
            "provider": GATEWAY_PROVIDER,
            "api_key": api_key.strip(),
        }
        # Lemonade refuses to hold a token for an http:// endpoint unless the
        # caller has opted in. Carry the opt-in already recorded at install so
        # `auth` does not fail on a provider registration that succeeded.
        if GatewayState.load().base_url.lower().startswith("http://"):
            payload["allow_insecure_http"] = True
        return self._request(
            "POST",
            "cloud/auth",
            payload=payload,
            timeout=_DISCOVERY_TIMEOUT,
            redact=True,
        )

    def clear_token(self) -> Dict[str, Any]:
        """Drop the session token. Does not affect the env var."""
        return self._request("DELETE", f"cloud/auth/{GATEWAY_PROVIDER}")

    def ensure_authenticated(self) -> bool:
        """Give Lemonade the remembered token if it has none.

        Lemonade forgets its token on restart, so without this every restart
        means re-entering it. Returns True when the gateway is usable.
        """
        status = self.status()
        if not status.installed or status.authenticated:
            return status.authenticated
        token = recall_token()
        if not token:
            return False
        try:
            self.set_token(token)
        except GatewayError as e:
            # A remembered token that the gateway now rejects is worth saying
            # out loud — silently prompting again hides a revoked key.
            self.log.warning(f"The remembered gateway token was not accepted: {e}")
            return False
        del token
        return True

    def status(self) -> GatewayStatus:
        """Registration and auth state for the gateway provider."""
        info = self._request("GET", "system-info")
        providers = (info.get("cloud") or {}).get("providers") or []
        for entry in providers:
            if entry.get("name") != GATEWAY_PROVIDER:
                continue
            return GatewayStatus(
                installed=True,
                base_url=entry.get("base_url"),
                env_var_set=bool(entry.get("env_var_set")),
                runtime_key_set=bool(entry.get("runtime_key_set")),
                models_discovered=int(entry.get("models_discovered") or 0),
                warnings=list(entry.get("warnings") or []),
            )
        return GatewayStatus(installed=False)

    # -- models -----------------------------------------------------------

    def list_models(self) -> List[GatewayModel]:
        """Gateway models Lemonade has discovered, recommended ones first."""
        payload = self.client.list_models()
        models = [
            GatewayModel(
                id=entry["id"],
                labels=list(entry.get("labels") or []),
                ctx_size=entry.get("context_length"),
            )
            for entry in payload.get("data", [])
            if isinstance(entry, dict)
            and entry.get("recipe") == CLOUD_RECIPE
            and str(entry.get("id", "")).startswith(f"{GATEWAY_PROVIDER}.")
        ]
        models.sort(key=lambda m: (preference_rank(m.id), m.id.lower()))
        return models

    def default_model(self) -> Optional[str]:
        """The model GAIA should pick when the user has not chosen one.

        The top-ranked discovered model, so a fresh connection lands on one
        that actually streams rather than the first name alphabetically.
        """
        models = self.list_models()
        return models[0].id if models else None

    def ensure_active_model(self) -> Optional[str]:
        """Select a sensible default if nothing is active yet.

        Returns the active model id, or None when the gateway has discovered
        nothing. Existing choices are never overridden.
        """
        state = GatewayState.load()
        if state.active_model:
            return state.active_model
        chosen = self.default_model()
        if chosen:
            self.set_active(chosen)
        return chosen

    def enable(self, model_id: str) -> GatewayState:
        """Enable a model, making it active if nothing else is."""
        state = GatewayState.load()
        if model_id not in state.enabled_models:
            state.enabled_models.append(model_id)
        if state.active_model is None:
            state.active_model = model_id
        state.save()
        return state

    def disable(self, model_id: str) -> GatewayState:
        state = GatewayState.load()
        state.enabled_models = [m for m in state.enabled_models if m != model_id]
        was_active = state.active_model == model_id
        if was_active:
            state.active_model = (
                state.enabled_models[0] if state.enabled_models else None
            )
        state.save()
        if was_active:
            if state.active_model:
                # Hand the global default to whatever took over.
                from gaia.config import GaiaConfig

                cfg = GaiaConfig.load()
                if cfg.get("default_model") == model_id:
                    cfg.set("default_model", state.active_model)
                    cfg.save()
            else:
                clear_default_model_if(model_id)
        return state

    def set_active(self, model_id: str) -> GatewayState:
        """Make *model_id* the model GAIA commands default to."""
        state = GatewayState.load()
        if model_id not in state.enabled_models:
            state.enabled_models.append(model_id)
        state.active_model = model_id
        state.save()
        return state
