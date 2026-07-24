# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``/host/v1/*`` custody routes — the reverse contract (design §0.31).

Built as a factory (``build_custody_router``) and mounted from inside
``create_app``, mirroring the agents/relay routers so no state is smuggled
through ``app.state``. Every route resolves the calling agent from the per-spawn
custody secret (``CustodyAuth.resolve``) and scopes its work to that agent — a
request never carries a claimed agent id.

Errors are typed and loud (§0.31, no-silent-fallbacks): the store/auth layers
raise :class:`gaia.daemon.custody.errors.CustodyError` subclasses carrying the
HTTP status, and one exception handler maps them to a response whose ``detail``
is the actionable message verbatim. Unknown/missing secret → 403; cross-agent
access → 403; unknown session → 404; audit conflict → 409; store down → 503.
"""

from __future__ import annotations

# Module-level (mirrors sidecars/routes.py): this module is imported lazily from
# create_app, so its endpoint annotations must resolve from module globals under
# PEP 563.
from fastapi import APIRouter, Body, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from gaia.daemon.custody.constants import (
    CUSTODY_AUTH_SCHEME,
    HOST_API_PREFIX,
    HOST_API_VERSION,
    MEMORY_SCOPE_AGENT,
    RAG_QUERY_DEFAULT_K,
    RAG_QUERY_MAX_K,
)
from gaia.daemon.custody.errors import CustodyError, UnknownSecretError


def _resolve_agent(auth, authorization: "str | None") -> str:
    """Resolve the calling agent id from the Authorization header, or raise a
    403 :class:`UnknownSecretError`. Bearer scheme, same shape as the client
    token so callers implement one convention."""
    if not authorization:
        raise UnknownSecretError(
            "Missing custody secret. Send "
            f"'Authorization: {CUSTODY_AUTH_SCHEME} <secret>' using the value "
            "the daemon injected at spawn (GAIA_HOST_CUSTODY_SECRET)."
        )
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() != CUSTODY_AUTH_SCHEME.lower() or not credential:
        raise UnknownSecretError(
            f"Malformed Authorization header. Expected "
            f"'{CUSTODY_AUTH_SCHEME} <secret>'."
        )
    return auth.resolve(credential)


def _parse_rag_k(raw) -> int:
    """Coerce the request's ``k`` to a bounded positive int, or raise a 400.

    Guards the SQL ``LIMIT``: a negative/zero ``k`` becomes ``LIMIT -1`` (full
    corpus dump) and a null/non-integer ``k`` raises ``TypeError`` (500). Both
    are caller-input errors, so reject them loudly with a 400 instead of passing
    them through to the query.
    """
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise HTTPException(
            status_code=400,
            detail=(
                f"'k' must be an integer between 1 and {RAG_QUERY_MAX_K}; "
                f"got {raw!r}."
            ),
        )
    if raw < 1 or raw > RAG_QUERY_MAX_K:
        raise HTTPException(
            status_code=400,
            detail=f"'k' must be between 1 and {RAG_QUERY_MAX_K}; got {raw}.",
        )
    return raw


def build_custody_router(auth, store) -> APIRouter:
    """Build the ``/host/v1/*`` router over *auth* and *store*.

    *auth* is a :class:`gaia.daemon.custody.auth.CustodyAuth`; *store* a
    :class:`gaia.daemon.custody.store.CustodyStore`.
    """
    router = APIRouter()

    @router.get(f"{HOST_API_PREFIX}/version")
    def custody_version() -> dict:
        # Unauthenticated: lets a sidecar negotiate the custody MAJOR before it
        # holds a resolvable secret. Carries no per-agent data.
        return {"apiVersion": HOST_API_VERSION}

    @router.post(f"{HOST_API_PREFIX}/rag/query")
    def rag_query(
        payload: dict = Body(...),
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        query = payload.get("query")
        if not query:
            raise HTTPException(
                status_code=400,
                detail="POST /host/v1/rag/query requires a non-empty 'query'.",
            )
        k = _parse_rag_k(payload.get("k", RAG_QUERY_DEFAULT_K))
        chunks = store.query_rag(agent_id, query, k=k)
        return {"chunks": chunks}

    @router.get(f"{HOST_API_PREFIX}/memory")
    def get_memory(
        scope: "str | None" = Query(default=None),
        query: "str | None" = Query(default=None),
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        items = store.get_memory(agent_id, scope=scope, query=query)
        return {"items": items}

    @router.post(f"{HOST_API_PREFIX}/memory")
    def post_memory(
        payload: dict = Body(...),
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        # Accept {item} or {content}; item is the §0.31 field name.
        content = payload.get("item", payload.get("content"))
        if not content:
            raise HTTPException(
                status_code=400,
                detail="POST /host/v1/memory requires a non-empty 'item'.",
            )
        scope = payload.get("scope") or MEMORY_SCOPE_AGENT
        row_id = store.add_memory(agent_id, str(content), scope=scope)
        return {"id": row_id}

    @router.post(f"{HOST_API_PREFIX}/sessions")
    def create_session(
        payload: dict = Body(default=None),
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        # Host-minted id (§0.30): the caller may propose one, else the daemon
        # mints it. Either way the daemon owns the binding.
        import secrets as _secrets

        session_id = (payload or {}).get("session_id") or _secrets.token_urlsafe(16)
        store.create_session(agent_id, session_id)
        return {"session_id": session_id}

    @router.post(f"{HOST_API_PREFIX}/sessions/{{session_id}}/messages")
    def append_message(
        session_id: str,
        payload: dict = Body(...),
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        role = payload.get("role")
        content = payload.get("content")
        if not role or content is None:
            raise HTTPException(
                status_code=400,
                detail="append message requires 'role' and 'content'.",
            )
        seq = store.append_session_message(agent_id, session_id, role, str(content))
        return {"seq": seq}

    @router.get(f"{HOST_API_PREFIX}/sessions/{{session_id}}")
    def get_session(
        session_id: str,
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        transcript = store.get_session(agent_id, session_id)
        return {"session_id": session_id, "transcript": transcript}

    @router.post(f"{HOST_API_PREFIX}/audit")
    def post_audit(
        payload: dict = Body(...),
        authorization: "str | None" = Header(default=None),
    ) -> dict:
        agent_id = _resolve_agent(auth, authorization)
        action_id = payload.get("action_id")
        action = payload.get("action")
        if not action_id or not action:
            raise HTTPException(
                status_code=400,
                detail="POST /host/v1/audit requires 'action_id' and 'action'.",
            )
        summary = str(payload.get("summary", ""))
        ts = float(payload.get("ts") or _now())
        seq = store.append_audit(agent_id, action_id, action, summary, ts)
        return {"seq": seq}

    return router


def _now() -> float:  # pragma: no cover - trivial seam
    import time

    return time.time()


def install_custody_exception_handler(app) -> None:
    """Map :class:`CustodyError` to its carried HTTP status app-wide.

    One handler so every custody route's typed error becomes a loud JSON
    ``{"detail": ...}`` at the boundary — never a 500 stack trace, never a
    silent empty body.
    """

    @app.exception_handler(CustodyError)
    async def _handle_custody_error(_request, exc: CustodyError):  # noqa: ANN001
        return JSONResponse(
            status_code=getattr(exc, "http_status", 500),
            content={"detail": str(exc)},
        )
