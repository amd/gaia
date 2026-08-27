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

# AMD's internal gateway. Unverified from this repo (the host is SSO-gated), so
# it is a starting suggestion the user confirms, never a silent default: every
# entry point makes it editable and `check_reachable` reports the real HTTP
# status when it is wrong.
DEFAULT_GATEWAY_BASE_URL = os.getenv(
    "GAIA_GATEWAY_BASE_URL", "https://llm.amd.com/api/v1"
)

# Substrings that float a discovered model to the top of the picker and
# pre-select it. Hints for ordering only — the real ids come from the gateway,
# which names things its own way (e.g. "Claude-Opus-5", not Anthropic's
# "claude-opus-4-8").
RECOMMENDED_HINTS = (
    "gemma-4-31b",
    "gemma4-31b",
    "claude-opus",
    "claude-sonnet",
)

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

    def check_reachable(self, base_url: str) -> int:
        """Probe the gateway's own ``/models`` and return how many it lists.

        Runs before registration so a wrong URL or a dead token fails here,
        with the real HTTP status, instead of surfacing later as an empty
        model list.
        """
        url = f"{base_url.rstrip('/')}/models"
        token = os.getenv(GATEWAY_API_KEY_ENV)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = requests.get(url, headers=headers, timeout=_PROBE_TIMEOUT)
        except requests.exceptions.RequestException as e:
            raise GatewayError(
                f"Could not reach the gateway at {url}: {e}. "
                f"Check the base URL and that you are on the network/VPN that "
                f"can see it."
            ) from e
        if response.status_code in (401, 403):
            raise GatewayError(
                f"The gateway at {url} rejected the request (HTTP "
                f"{response.status_code}). Supply a token with "
                f"`gaia gateway auth`, or set {GATEWAY_API_KEY_ENV} in "
                f"Lemonade's environment."
            )
        if response.status_code >= 400:
            raise GatewayError(
                f"The gateway at {url} returned HTTP {response.status_code}. "
                f"Check that the base URL includes the API path "
                f"(e.g. .../api/v1 or .../v1)."
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
    ) -> Dict[str, Any]:
        """Register the gateway with Lemonade as a cloud provider.

        ``auth_header_name`` / ``auth_header_prefix`` exist because some
        gateways front an OpenAI-shaped API behind a non-Bearer header; leave
        them unset for the standard ``Authorization: Bearer`` scheme.
        """
        payload: Dict[str, Any] = {
            "backend": "cloud",
            "provider": GATEWAY_PROVIDER,
            "base_url": base_url.rstrip("/"),
            # GAIA's agents speak OpenAI chat completions end to end; the
            # anthropic wire format serves only /v1/messages.
            "wire_format": "openai",
        }
        if auth_header_name is not None:
            payload["auth_header_name"] = auth_header_name
        if auth_header_prefix is not None:
            payload["auth_header_prefix"] = auth_header_prefix
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
        state.enabled_models = []
        state.active_model = None
        state.save()
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
        return self._request(
            "POST",
            "cloud/auth",
            payload={"provider": GATEWAY_PROVIDER, "api_key": api_key.strip()},
            timeout=_DISCOVERY_TIMEOUT,
            redact=True,
        )

    def clear_token(self) -> Dict[str, Any]:
        """Drop the session token. Does not affect the env var."""
        return self._request("DELETE", f"cloud/auth/{GATEWAY_PROVIDER}")

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
        models.sort(key=lambda m: (not m.recommended, m.id.lower()))
        return models

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
        if state.active_model == model_id:
            state.active_model = (
                state.enabled_models[0] if state.enabled_models else None
            )
        state.save()
        return state

    def set_active(self, model_id: str) -> GatewayState:
        """Make *model_id* the model GAIA commands default to."""
        state = GatewayState.load()
        if model_id not in state.enabled_models:
            state.enabled_models.append(model_id)
        state.active_model = model_id
        state.save()
        return state
