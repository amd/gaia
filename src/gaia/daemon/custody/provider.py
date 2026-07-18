# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``CustodyProvider`` — the sidecar-side custody abstraction (design §0.37).

A sidecar depends on this interface and does **not** know which backend answers.
Three adapters, auto-selected at launch (:func:`select_custody_provider`):

- **Delegated** — the daemon injected a ``/host/v1`` URL + per-spawn secret at
  spawn; every call is an HTTP round-trip to the daemon's single writer (the
  §0.9 Agent-UI deployment model). This is the wire protocol *and* the
  third-party-implementable interface (§0.35 #1) — the daemon's routes are GAIA's
  reference implementation of it.
- **Embedded** — no host endpoint injected; the sidecar is its *own* single
  writer over a bundled :class:`gaia.daemon.custody.store.CustodyStore`. Rich,
  self-contained, works offline. Default for a standalone agent.
- **Ephemeral** — explicit stateless flag; nothing persists. Writes are defined
  no-ops and reads return empty — this is the *documented contract* of the
  stateless tier (the caller passes context per request), not a silent fallback.

The single-writer invariant holds in every mode (§0.37): embedded = one agent is
its own writer; delegated = the host is the one writer across N; ephemeral =
nothing persisted. There is never multi-writer.

Layering: this module is pure client code (httpx only, imported lazily). It does
NOT import fastapi/uvicorn, so a sidecar package can import it without pulling in
the daemon's server stack.
"""

from __future__ import annotations

import abc
import os
from typing import Optional

from gaia.daemon.custody.constants import (
    CUSTODY_EPHEMERAL_ENV_VAR,
    CUSTODY_SECRET_ENV_VAR,
    CUSTODY_URL_ENV_VAR,
    HOST_API_PREFIX,
    MEMORY_SCOPE_AGENT,
)


class CustodyProvider(abc.ABC):
    """The custody surface a sidecar depends on (mirrors ``/host/v1/*``)."""

    @abc.abstractmethod
    def add_memory(
        self, content: str, scope: str = MEMORY_SCOPE_AGENT
    ) -> Optional[int]: ...

    @abc.abstractmethod
    def get_memory(
        self, scope: Optional[str] = None, query: Optional[str] = None
    ) -> "list[dict]": ...

    @abc.abstractmethod
    def create_session(self, session_id: Optional[str] = None) -> str: ...

    @abc.abstractmethod
    def append_session_message(
        self, session_id: str, role: str, content: str
    ) -> Optional[int]: ...

    @abc.abstractmethod
    def get_session(self, session_id: str) -> "list[dict]": ...

    @abc.abstractmethod
    def query_rag(self, query: str, k: int = 4) -> "list[dict]": ...

    @abc.abstractmethod
    def append_audit(
        self, action_id: str, action: str, summary: str = "", ts: Optional[float] = None
    ) -> Optional[int]: ...


class EmbeddedCustodyProvider(CustodyProvider):
    """Self-custody: the sidecar owns its :class:`CustodyStore` (single writer)."""

    def __init__(self, store, agent_id: str):
        self._store = store
        self._agent_id = agent_id

    def add_memory(self, content, scope=MEMORY_SCOPE_AGENT):
        return self._store.add_memory(self._agent_id, content, scope=scope)

    def get_memory(self, scope=None, query=None):
        return self._store.get_memory(self._agent_id, scope=scope, query=query)

    def create_session(self, session_id=None):
        import secrets

        sid = session_id or secrets.token_urlsafe(16)
        return self._store.create_session(self._agent_id, sid)

    def append_session_message(self, session_id, role, content):
        return self._store.append_session_message(
            self._agent_id, session_id, role, content
        )

    def get_session(self, session_id):
        return self._store.get_session(self._agent_id, session_id)

    def query_rag(self, query, k=4):
        return self._store.query_rag(self._agent_id, query, k=k)

    def append_audit(self, action_id, action, summary="", ts=None):
        import time

        return self._store.append_audit(
            self._agent_id,
            action_id,
            action,
            summary,
            ts if ts is not None else time.time(),
        )


class DelegatedCustodyProvider(CustodyProvider):
    """Host custody: every call is an HTTP round-trip to the daemon's ``/host/v1``.

    The daemon resolves the agent id from *secret* (bound at mint), so this
    provider never sends an agent id — its identity IS the secret.
    """

    def __init__(self, base_url: str, secret: str, *, timeout: float = 30.0):
        self._base = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout

    # -- HTTP plumbing -------------------------------------------------------

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._secret}"}

    def _request(self, method: str, path: str, **kwargs):
        import httpx

        url = f"{self._base}{HOST_API_PREFIX}{path}"
        try:
            resp = httpx.request(
                method, url, headers=self._headers(), timeout=self._timeout, **kwargs
            )
        except httpx.HTTPError as e:
            raise CustodyUnavailableError(
                f"custody host at {self._base} did not answer "
                f"{method} {HOST_API_PREFIX}{path} ({e.__class__.__name__}: {e}). "
                "Is the daemon running? Re-ensure the agent, then retry."
            ) from e
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise CustodyRequestError(
                resp.status_code,
                f"custody host returned HTTP {resp.status_code} for "
                f"{method} {HOST_API_PREFIX}{path}: {detail}",
            )
        return resp.json()

    def add_memory(self, content, scope=MEMORY_SCOPE_AGENT):
        return self._request("POST", "/memory", json={"item": content, "scope": scope})[
            "id"
        ]

    def get_memory(self, scope=None, query=None):
        params = {}
        if scope is not None:
            params["scope"] = scope
        if query is not None:
            params["query"] = query
        return self._request("GET", "/memory", params=params or None)["items"]

    def create_session(self, session_id=None):
        body = {"session_id": session_id} if session_id else {}
        return self._request("POST", "/sessions", json=body)["session_id"]

    def append_session_message(self, session_id, role, content):
        return self._request(
            "POST",
            f"/sessions/{session_id}/messages",
            json={"role": role, "content": content},
        )["seq"]

    def get_session(self, session_id):
        return self._request("GET", f"/sessions/{session_id}")["transcript"]

    def query_rag(self, query, k=4):
        return self._request("POST", "/rag/query", json={"query": query, "k": k})[
            "chunks"
        ]

    def append_audit(self, action_id, action, summary="", ts=None):
        body = {"action_id": action_id, "action": action, "summary": summary}
        if ts is not None:
            body["ts"] = ts
        return self._request("POST", "/audit", json=body)["seq"]


class EphemeralCustodyProvider(CustodyProvider):
    """Stateless: nothing persists. Writes are defined no-ops, reads return empty.

    This is the documented contract of the stateless tier (§0.37) — the caller
    passes all context per request. It is NOT a silent degradation of a
    persistent backend: it is selected only by an explicit flag, so a caller
    that expected persistence never lands here by accident.
    """

    def add_memory(self, content, scope=MEMORY_SCOPE_AGENT):
        return None

    def get_memory(self, scope=None, query=None):
        return []

    def create_session(self, session_id=None):
        import secrets

        return session_id or secrets.token_urlsafe(16)

    def append_session_message(self, session_id, role, content):
        return None

    def get_session(self, session_id):
        return []

    def query_rag(self, query, k=4):
        return []

    def append_audit(self, action_id, action, summary="", ts=None):
        return None


class CustodyUnavailableError(Exception):
    """The delegated custody host could not be reached (transport-level)."""


class CustodyRequestError(Exception):
    """The delegated custody host answered with a non-2xx status."""

    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        super().__init__(message)


def select_custody_provider(
    agent_id: str,
    *,
    embedded_store=None,
    env: Optional[dict] = None,
) -> CustodyProvider:
    """Auto-select the provider per §0.37 from the spawn environment.

    Order: explicit stateless flag → Ephemeral; a host custody URL present →
    Delegated; else Embedded (requires *embedded_store*). A delegated selection
    with a URL but no secret fails loud — an un-authenticable custody wire is a
    misconfiguration, not a fall-back-to-embedded case.
    """
    env = os.environ if env is None else env

    if _truthy(env.get(CUSTODY_EPHEMERAL_ENV_VAR)):
        return EphemeralCustodyProvider()

    url = env.get(CUSTODY_URL_ENV_VAR)
    if url:
        secret = env.get(CUSTODY_SECRET_ENV_VAR)
        if not secret:
            raise ValueError(
                f"{CUSTODY_URL_ENV_VAR} is set ({url}) but "
                f"{CUSTODY_SECRET_ENV_VAR} is missing — a delegated custody "
                "endpoint cannot be used without its per-spawn secret. The "
                "daemon injects both together; if you set one by hand, set both."
            )
        return DelegatedCustodyProvider(url, secret)

    if embedded_store is None:
        raise ValueError(
            "no host custody endpoint was injected and no embedded store was "
            "provided — cannot build a custody provider. Inject "
            f"{CUSTODY_URL_ENV_VAR}+{CUSTODY_SECRET_ENV_VAR} for delegated "
            "custody, or pass embedded_store for self-custody."
        )
    return EmbeddedCustodyProvider(embedded_store, agent_id)


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on") if value else False
