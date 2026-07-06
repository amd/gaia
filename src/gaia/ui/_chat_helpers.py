# Copyright(C) 2024-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Chat and document-indexing helper functions for GAIA Agent UI.

These functions are extracted into their own module so that both
``server.py`` (for backward-compatible ``@patch`` targets) and the
router modules can import from the same canonical location.

Tests may patch ``gaia.ui.server._get_chat_response`` etc. because
``server.py`` re-exports these names.  The router endpoints access
them through ``gaia.ui.server`` as well (via lazy import) so the
patches take effect.
"""

import asyncio
import copy
import json
import logging
import re as _re
import threading
import time as _time
from pathlib import Path
from typing import Optional

from .database import SESSION_DEFAULT_MODEL, ChatDatabase
from .models import ChatRequest
from .sse_handler import (
    _ANSWER_JSON_SUB_RE,
    _RAG_RESULT_JSON_SUB_RE,
    _THOUGHT_JSON_SUB_RE,
    _TOOL_CALL_JSON_SUB_RE,
    _clean_answer_json,
    _fix_double_escaped,
)

logger = logging.getLogger(__name__)


def _stamp_chat_identity(config) -> None:
    """Inject ``namespaced_agent_id="installed:chat"`` into a ``ChatAgentConfig``
    BEFORE ``ChatAgent(config)`` is constructed.

    Must be applied to the *config*, not to the instance after construction.
    The connectors activation filter (``Agent._active_mcp_servers`` →
    ``MCPClientManager.servers_for_agent``) is consulted inside
    ``ChatAgent.__init__`` → ``super().__init__`` → ``_register_tools``,
    so the agent must already know its namespaced id by the time
    ``_register_tools`` runs. A post-construction stamp on the instance
    is too late — ``_register_tools`` has already loaded the unfiltered
    MCP-tool set.

    ``ChatAgent.__init__`` reads ``config.namespaced_agent_id`` at its top
    and sets ``self._gaia_namespaced_agent_id`` from it before invoking
    ``super().__init__``. This helper just centralises the
    ``"installed:chat"`` literal so every direct-construction site in this
    module uses the same value the registry assigns the gaia-agent-chat wheel
    (#1102) and a fifth caller can't forget.

    Idempotent — only sets the field if it is still its default ``None``,
    so callers that already set a custom namespaced id (e.g. a future
    custom-Chat wrapper) are not clobbered.
    """
    if getattr(config, "namespaced_agent_id", None) is None:
        config.namespaced_agent_id = "installed:chat"


def _register_agent_memory_ops(agent) -> None:
    """Register LLM-powered memory operations from a ChatAgent with the memory router.

    Consolidation and reconciliation require a live LLM + FAISS index, so they
    cannot run from the router's standalone MemoryStore.  This function wires
    the agent's methods into module-level callables that the router can invoke.

    Safe to call on every agent construction — the router just overwrites the
    previous reference (all agents share the same DB, so any active agent works).
    """
    try:
        from gaia.ui.routers import memory as _mem_router

        if hasattr(agent, "consolidate_old_sessions"):
            _mem_router._consolidate_fn = agent.consolidate_old_sessions
        if hasattr(agent, "reconcile_memory"):
            _mem_router._reconcile_fn = agent.reconcile_memory
    except Exception:
        pass  # Non-fatal: dashboard ops degrade gracefully when not registered


# Active SSE handlers keyed by session_id.  The /api/chat/confirm-tool
# endpoint looks up the handler here to resolve a pending confirmation.
_active_sse_handlers: dict = {}  # session_id -> SSEOutputHandler

# ── Agent registry ───────────────────────────────────────────────────────────
# Set by server lifespan via set_agent_registry() once discovery completes.
_agent_registry = None


def set_agent_registry(registry) -> None:
    """Store the AgentRegistry for use by chat helpers."""
    global _agent_registry
    _agent_registry = registry


def get_agent_registry():
    """Return the current AgentRegistry instance, or None if not yet initialized."""
    return _agent_registry


# ── Per-session agent cache ───────────────────────────────────────────────────
# Constructing a fresh ChatAgent on every message is expensive: it initialises
# RAGSDK, MCPClientManager, runs LemonadeManager.ensure_ready() (HTTP calls),
# registers all tools, composes the system prompt, and re-indexes session docs
# even when nothing has changed.  Caching the agent per session_id lets us skip
# all of that on follow-up turns.
#
# Thread-safety: the global chat_semaphore(1) in server.py serialises all chat
# requests, and the per-session session_lock prevents concurrent turns within
# the same session.  Together they guarantee the cache dict and each agent are
# accessed by at most one thread at a time — no per-entry locking needed.
_agent_cache: dict[str, dict] = (
    {}
)  # session_id -> {"agent": Agent, "model_id": str, "agent_type": str, "document_ids": list}
_agent_cache_lock = threading.Lock()
_MAX_CACHED_AGENTS = 10

# Alias so call-sites read naturally; the canonical value lives in database.py.
_DB_DEFAULT_MODEL = SESSION_DEFAULT_MODEL

# Last known MCP runtime status — updated after each agent setup so
# GET /api/mcp/status can return it without needing a running chat.
_mcp_status_cache: list[dict] = []
_mcp_status_lock = threading.Lock()

# Lock preventing concurrent load_model() calls.  Shared between the per-request
# path (_maybe_load_expected_model) and the boot-time preload task in server.py.
# Public (no underscore) because it is intentionally accessed cross-module.
model_load_lock = threading.Lock()


# ── Lemonade error classification (chat-side helper) ───────────────────────
#
# AgentSDK + the agent loop wrap LLM errors in their own exception types,
# so a raw ``LemonadeError`` raised by the provider often arrives at the
# chat layer as ``ValueError("...")`` or ``RuntimeError("...")`` with the
# original message preserved as text.  We walk the exception chain and
# also pattern-match the message string so retry decisions don't depend
# on the exception type bubbling through unchanged.


def _classify_chat_exception(exc: BaseException):
    """Return a typed ``LemonadeError`` instance if *exc* (or anything in
    its ``__cause__`` chain) corresponds to a known Lemonade failure mode.

    Returns ``None`` when the exception is unrelated.  Used by the chat
    streaming/non-streaming paths to decide whether to auto-retry and
    what user-facing message to surface.
    """
    from gaia.llm.providers.lemonade import (  # local import to avoid cycle at import time
        LemonadeContextOverflowError,
        LemonadeError,
        LemonadeModelNotLoadedError,
        LemonadeNetworkError,
        LemonadeUpstreamTimeoutError,
    )

    # 1. Direct typed match anywhere in the cause chain.
    # Walk both ``__cause__`` (explicit ``raise ... from e``) and ``__context__``
    # (implicit ``raise ...`` inside an ``except`` block) so we don't lose the
    # typed-class metadata (e.g. ``LemonadeContextOverflowError.retryable``)
    # for handlers that re-raise without ``from``.
    #
    # Cycle protection: tracking visited ids defends against pathological
    # exception graphs where ``a.__cause__ = b`` and ``b.__cause__ = a``.
    # Without it the walker would loop forever and freeze the chat handler.
    cur: Optional[BaseException] = exc
    _seen: set = set()
    while cur is not None and id(cur) not in _seen:
        _seen.add(id(cur))
        if isinstance(cur, LemonadeError):
            return cur
        cur = cur.__cause__ or cur.__context__

    # 2. Substring match on the stringified exception — covers the case
    # where AgentSDK re-raises with ``str(original)`` as the message,
    # losing the typed-class info.
    text = str(exc).lower()
    if "no model loaded" in text or "model_not_loaded" in text:
        return LemonadeModelNotLoadedError()
    if "exceed_context_size" in text or "exceeds the available context size" in text:
        err = LemonadeContextOverflowError()
        # If the textual error mentions a small n_ctx, the model was
        # loaded with the wrong context size — reload via pre-flight
        # will fix it, so make the error retryable.
        m = _re.search(r"context size \((\d+) tokens?\)", text)
        if not m:
            m = _re.search(r"n_ctx['\"]?\s*[:=]\s*(\d+)", text)
        if m:
            try:
                n_ctx = int(m.group(1))
                # Threshold tracks the chat / rag profile default
                # (65536) — see lemonade.py:_classify_lemonade_response.
                if 0 < n_ctx < 65536:
                    err.retryable = True
            except ValueError:
                pass
        return err
    # Distinguish upstream model-call timeouts (Lemonade reachable, llama-server
    # hung) from real connectivity failures (#1030). The user-facing remediation
    # is very different.
    is_timeout = (
        "timeout was reached" in text
        or "timed out" in text
        or "operation_timeout" in text
    )
    is_unreachable = (
        "connection refused" in text
        or "could not resolve host" in text
        or "no route to host" in text
        or "couldn't connect" in text
    )
    # Lemonade HTTP 5xx — typical when llama-server is mid-swap between
    # models or hit an internal recovery state. ``LemonadeClient._send_request``
    # raises ``LemonadeClientError("Request failed with status 503: ...")`` /
    # 500/502/504 for these. Pre-iter2 these fell through to the generic
    # "trouble connecting" UI fallback and the chat layer never retried —
    # so a transient model-swap stall surfaced as a hard FAIL. Treat them
    # as the network-flavour transient: retryable=True kicks the chat
    # layer's auto-reload + one-retry path, which usually recovers.
    is_backend_5xx = bool(
        _re.search(r"failed with status 5\d\d", text)
        or "internal server error" in text
        or "service unavailable" in text
        or "bad gateway" in text
        or "gateway timeout" in text
    )
    if is_timeout and not is_unreachable:
        return LemonadeUpstreamTimeoutError()
    if (
        "network_error" in text
        or "curl error" in text
        or is_unreachable
        or is_backend_5xx
    ):
        return LemonadeNetworkError()
    return None


# ── Auto-titling ────────────────────────────────────────────────────────────
#
# After the agent finishes a turn, kick off a background task that asks the
# same LLM to generate a 3-6 word title summarising the conversation, then
# update the session title in the DB. Fires when:
#   * Title is still the default ("New Chat" / "New Task" / starts with
#     "Untitled") — first-response pass
#   * The new user message has low word-overlap with the existing title
#     (≤ 0.15 Jaccard on lowercase words) AND the message is substantive
#     (≥ 25 chars) — topic-shift pass
#
# Skipped when:
#   * Title starts with "Eval:" — those are owned by the eval framework
#   * Title was last updated < 30 s ago — prevents thrash mid-conversation
#   * No agent / no Lemonade base URL available
#
# Throttled by a per-session timestamp dict so concurrent fire-and-forget
# tasks don't pile up.
_AUTO_TITLE_DEFAULTS = {
    "new chat",
    "new task",
    "untitled",
    "untitled session",
    "chat",
}
_AUTO_TITLE_LOCK = threading.Lock()
_AUTO_TITLE_LAST_AT: dict[str, float] = {}  # session_id -> monotonic ts
_AUTO_TITLE_THROTTLE_S = 30.0
_AUTO_TITLE_RE_NONWORD = _re.compile(r"[^a-z0-9 ]+")


def _title_word_set(s: str) -> set[str]:
    """Lowercase word set for overlap comparison; strips punctuation,
    drops 1-letter tokens (mostly noise like "a" / "I")."""
    cleaned = _AUTO_TITLE_RE_NONWORD.sub(" ", (s or "").lower())
    return {w for w in cleaned.split() if len(w) > 1}


def _should_retitle(current_title: str, last_user_msg: str) -> bool:
    """Decide whether the session deserves a fresh title.

    See module-level comment for the rule set.  Returns True/False; pure
    function so it's easy to unit-test in isolation.
    """
    title = (current_title or "").strip()
    title_lower = title.lower()
    if title.startswith("Eval:"):
        return False  # eval framework owns these
    if not title or title_lower in _AUTO_TITLE_DEFAULTS:
        return True  # first-response pass: replace the default
    # Topic-shift pass: low overlap + substantive new message.
    user_msg = (last_user_msg or "").strip()
    if len(user_msg) < 25:
        return False
    title_words = _title_word_set(title)
    user_words = _title_word_set(user_msg)
    if not title_words:
        return True
    overlap = len(title_words & user_words) / len(title_words)
    return overlap <= 0.15


async def _generate_session_title(
    base_url: str,
    model_id: str,
    user_msg: str,
    assistant_msg: str,
) -> Optional[str]:
    """Call Lemonade chat completions to produce a short tab-style title.

    Returns the cleaned title (≤ 64 chars, no quotes, no trailing
    punctuation) or None on any failure.  Times out at 30 s so a hung
    LLM doesn't keep the background task alive forever.
    """
    import httpx  # pylint: disable=import-outside-toplevel

    from gaia.llm.lemonade_client import (
        lemonade_auth_headers,
        resolve_lemonade_api_key,
    )

    prompt = (
        "Summarise the user's task in 3 to 6 plain words for a tab/window "
        "title.  Reply with ONLY the title, no quotes, no trailing "
        "punctuation, no leading verbs like 'Task:' or 'Title:'.\n\n"
        f"User: {user_msg[:400]}\n"
        f"Assistant: {(assistant_msg or '')[:200]}\n"
        "Title:"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 24,
                    # Low temperature: titles should be deterministic-ish
                    # for the same conversation.
                    "temperature": 0.3,
                },
                headers=lemonade_auth_headers(resolve_lemonade_api_key()),
            )
            if resp.status_code != 200:
                logger.debug(
                    "Auto-title HTTP %d: %s", resp.status_code, resp.text[:200]
                )
                return None
            data = resp.json()
            content = (
                data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
            )
            title = content.strip().split("\n")[0].strip()
            # Strip wrapping quotes and trailing punctuation that some
            # models always add despite the instruction.
            title = title.strip("\"'`").rstrip(".!?;:,").strip()
            # Strip stock prefixes models sometimes emit anyway.
            for prefix in ("title:", "tab:", "summary:"):
                if title.lower().startswith(prefix):
                    title = title[len(prefix) :].strip()
            return title[:64] if title else None
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("Auto-title LLM call failed: %s", exc)
        return None


async def _maybe_update_session_title(
    db: ChatDatabase,
    session_id: str,
    user_msg: str,
    assistant_msg: str,
    model_id: Optional[str],
) -> None:
    """Best-effort background task: re-title the session if the rules say so.

    Called fire-and-forget after each assistant turn finishes — never
    blocks the user's response.  Failures are logged at debug level
    only because nothing user-visible breaks if the title doesn't update.
    """
    if not model_id:
        return
    # Lookup current session.  If it's gone (deleted while we were
    # generating), bail silently.
    session = db.get_session(session_id)
    if not session:
        return
    current_title = session.get("title") or ""
    if not _should_retitle(current_title, user_msg):
        return

    # Throttle: don't re-title if we updated within the last 30 s.
    now = _time.monotonic()
    with _AUTO_TITLE_LOCK:
        last = _AUTO_TITLE_LAST_AT.get(session_id, 0.0)
        if now - last < _AUTO_TITLE_THROTTLE_S:
            return
        _AUTO_TITLE_LAST_AT[session_id] = now

    # Use the same Lemonade endpoint the chat just used.
    from gaia.llm.lemonade_manager import LemonadeManager

    base_url = LemonadeManager.get_base_url() or "http://localhost:13305/api/v1"
    new_title = await _generate_session_title(
        base_url=base_url,
        model_id=model_id,
        user_msg=user_msg,
        assistant_msg=assistant_msg,
    )
    if not new_title or new_title.lower() == current_title.lower():
        return
    try:
        db.update_session(session_id, title=new_title)
        logger.info(
            "Auto-titled session %s: %r → %r",
            session_id[:8],
            current_title,
            new_title,
        )
    except Exception as exc:  # pylint: disable=broad-except
        logger.debug("Auto-title DB update failed: %s", exc)


def _build_create_kwargs(
    *,
    custom_model: str | None,
    model_id: str | None,
    streaming: bool = False,
    device: str | None = None,
    min_context_size: int | None = None,
) -> dict:
    """Return the kwargs dict for registry.create_agent().

    Precedence (high → low):
      1. custom_model setting (explicit user override from db)
      2. session-explicit model (differs from SESSION_DEFAULT_MODEL)
      3. omit model_id — lets the agent's kwargs.setdefault govern (fix #841)

    Note: if registry.resolve_model() already promoted model_id before this
    call, it is forwarded as-is via branch 2 (resolve_model result ≠ default).

    ``device``/``min_context_size`` flow through to the agent's config so the
    requested device is validated at runtime. Agent factories filter unknown
    kwargs via ``dataclasses.fields``, so this is safe for agents whose config
    doesn't declare them.
    """
    suffix = " (streaming)" if streaming else ""
    kwargs: dict = {"silent_mode": not streaming, "debug": False}
    if streaming:
        kwargs["streaming"] = True
    if device is not None:
        kwargs["device"] = device
    if min_context_size is not None:
        kwargs["min_context_size"] = min_context_size

    if custom_model:
        kwargs["model_id"] = custom_model
        logger.info("create_agent: custom_model override -> %s%s", custom_model, suffix)
    elif model_id and model_id != _DB_DEFAULT_MODEL:
        kwargs["model_id"] = model_id
        logger.info("create_agent: session-explicit model -> %s%s", model_id, suffix)
    else:
        # Omit model_id so kwargs.setdefault in the agent's __init__ fires.
        # setdefault only works when the key is ABSENT. Passing the DB default
        # (or None / empty) explicitly defeats it — this is the fix for #841.
        logger.info(
            "create_agent: omitting model_id kwarg (session at DB default %s); "
            "agent's kwargs.setdefault or AgentConfig fallback will govern%s",
            _DB_DEFAULT_MODEL,
            suffix,
        )
    return kwargs


def _effective_model(agent, fallback: str | None) -> str | None:
    """Return agent.model_id if set, else fallback.

    Uses explicit None check (not `or`) to avoid treating empty-string
    model_id as missing — which would silently load the wrong model.
    """
    effective = getattr(agent, "model_id", None)
    return effective if effective is not None else fallback


def resolve_device_model(
    agent_type: str, device: str | None, registry=None
) -> tuple[str | None, int | None]:
    """Resolve the (model, ctx_size) an agent should use on *device*.

    The Agent UI device dropdown writes ``session["device"]``; resolving the
    model here is what makes selecting "NPU" actually switch models instead of
    rebuilding on the GPU model. Public (no underscore) because the sessions
    router also calls it to rewrite the session model on a device switch.

    Looks up the registered agent's ``device_configs`` and returns the model
    and context size of the entry matching *device*.  Falls back to the
    built-in ``DEFAULT_DEVICE_CONFIGS`` when the agent isn't registered or
    declares none (e.g. the direct-construction ``chat`` path).  Returns
    ``(None, None)`` when *device* is falsy or no entry matches, so callers
    leave the existing model untouched.
    """
    if not device:
        return None, None
    registry = registry if registry is not None else _agent_registry
    device_configs = None
    if registry is not None:
        reg = registry.get(agent_type)
        if reg is not None:
            device_configs = getattr(reg, "device_configs", None)
    if not device_configs:
        from gaia.agents.registry import DEFAULT_DEVICE_CONFIGS

        device_configs = DEFAULT_DEVICE_CONFIGS
    for dc in device_configs:
        if getattr(dc, "device", None) == device:
            return dc.model, getattr(dc, "ctx_size", None)
    return None, None


def _apply_device_model(
    session: dict,
    agent_type: str,
    model_id: str | None,
    custom_model: str | None,
    registry=None,
) -> tuple[str | None, int | None]:
    """Apply the session's device choice to the model + context window.

    Returns ``(model_id, device_ctx)``. The device config drives the model when
    the current model is the generic default (so built-in Gemma agents switch
    correctly) OR the user picked a non-GPU device explicitly (a pinned model
    can't run there) — so an agent that declares its own model isn't clobbered
    on the default GPU. Skipped when the user pinned a ``custom_model``.
    """
    device = session.get("device")
    if custom_model or not device:
        return model_id, None
    dev_model, dev_ctx = resolve_device_model(agent_type, device, registry)
    if not dev_model:
        return model_id, None
    is_default_model = model_id in (None, _DB_DEFAULT_MODEL)
    device_is_explicit = device != "gpu"
    if dev_model == model_id or is_default_model or device_is_explicit:
        if dev_model != model_id:
            logger.info(
                "chat: device=%s -> model %s (was %s)", device, dev_model, model_id
            )
        return dev_model, dev_ctx
    return model_id, None


def get_cached_mcp_status() -> list[dict]:
    """Return the last known MCP server connection status from any cached agent."""
    with _mcp_status_lock:
        return copy.deepcopy(_mcp_status_cache)


def _disconnect_cached_agent(entry) -> None:
    """Best-effort disconnect of MCP subprocesses held by a cache entry."""
    if not isinstance(entry, dict):
        return
    agent = entry.get("agent")
    mcp_manager = getattr(agent, "_mcp_manager", None)
    if mcp_manager is not None:
        try:
            mcp_manager.disconnect_all()
        except Exception as exc:
            # Best-effort on cache eviction — a failed disconnect on a
            # previously-cached agent shouldn't block the new agent slot.
            # WARNING because a leaked MCP subprocess is observable
            # (orphaned process, stuck port) and worth surfacing.
            logger.warning("MCP disconnect failed during cache eviction: %s", exc)


def _agent_unavailable_message(requested: str, registry) -> str:
    """Return a user-friendly error message for an agent that could not be loaded.

    Mirrors the fail-loudly precedent at _chat_helpers.py around line 589 — no
    silent swap to chat, just a clear message the user can act on.
    """
    reason_suffix = ""
    if registry is not None:
        reason = registry.get_load_error(requested)
        if reason:
            reason_suffix = f": {reason}"

    return (
        f"I couldn't load the agent **'{requested}'**. "
        f"It may have failed to install or contains an error{reason_suffix}. "
        f"Try re-creating it, or pick another agent from the selector."
    )


def _canonical_agent_type(agent_type: str) -> str:
    """Resolve legacy agent-type aliases (e.g. ``chat-lite`` → ``gaia-lite``).

    Keeps the per-session agent cache from thrashing when a client mixes the
    old and new IDs within the same session — both resolve to the same
    canonical ID and therefore the same cache entry.

    Raises:
        AttributeError: If the registry doesn't expose ``canonical_id``.
            Fail loudly per CLAUDE.md "no silent fallbacks" — a registry
            that lost this method is a real bug, not something to paper
            over with a cache miss.
    """
    registry = _agent_registry
    if registry is None:
        return agent_type
    return registry.canonical_id(agent_type)


def _get_cached_agent(session_id: str, model_id: str, agent_type: str = "chat"):
    """Return the cached agent for *session_id* if model and agent_type match, else None.

    Evicts the entry when the model or agent type has changed. Legacy
    agent-type aliases (e.g. ``chat-lite`` → ``gaia-lite``) are normalised
    before comparison so stored sessions that pre-date a rename continue to
    hit the cache instead of reconstructing the agent on every turn.
    """
    canonical = _canonical_agent_type(agent_type)
    with _agent_cache_lock:
        entry = _agent_cache.get(session_id)
        if entry is None:
            return None
        if entry["model_id"] != model_id:
            old_entry = _agent_cache.pop(session_id)
            _disconnect_cached_agent(old_entry)
            logger.debug(
                "Agent cache miss (model change) for session %s", session_id[:8]
            )
            return None
        if _canonical_agent_type(entry.get("agent_type", "chat")) != canonical:
            old_entry = _agent_cache.pop(session_id)
            _disconnect_cached_agent(old_entry)
            logger.debug(
                "Agent cache miss (agent_type change) for session %s", session_id[:8]
            )
            return None
        return entry["agent"]


def _store_agent(
    session_id: str,
    model_id: str,
    document_ids: list,
    agent,
    agent_type: str = "chat",
) -> None:
    """Cache *agent* for *session_id*.  Evicts the oldest entry if over the limit.

    The agent_type is stored in canonical form so a later lookup with a
    legacy alias still matches (see ``_canonical_agent_type``).
    """
    canonical = _canonical_agent_type(agent_type)
    with _agent_cache_lock:
        if session_id not in _agent_cache and len(_agent_cache) >= _MAX_CACHED_AGENTS:
            oldest = next(iter(_agent_cache))
            old_entry = _agent_cache.pop(oldest)
            _disconnect_cached_agent(old_entry)
            logger.debug("Agent cache full; evicted session %s", oldest[:8])
        _agent_cache[session_id] = {
            "model_id": model_id,
            "agent_type": canonical,
            "document_ids": list(document_ids or []),
            "agent": agent,
        }
        logger.debug(
            "Cached agent for session %s agent_type=%s (cache size: %d)",
            session_id[:8],
            canonical,
            len(_agent_cache),
        )


def reload_all_session_agents_mcp() -> int:
    """
    Call ``reload()`` on every cached agent's ``MCPClientManager`` (#1004).

    Each `ChatAgent` instance owns its own per-instance `MCPClientManager`
    (see `MCPClientMixin.__init__`); there is no process-wide singleton.
    When a user toggles a connector via Settings → Connectors, the active
    chat sessions need to pick up the new ``mcp_servers.json`` state on
    their next turn — otherwise tools materialize/disappear only after
    GAIA restart.

    Wired as the ``McpServerHandler.reload_callback`` from the FastAPI
    lifespan. Returns the count of managers reloaded for diagnostics.
    """
    count = 0
    failed = 0
    with _agent_cache_lock:
        entries = list(_agent_cache.items())

    for session_id, entry in entries:
        agent = entry.get("agent")
        manager = getattr(agent, "_mcp_manager", None)
        if manager is None:
            continue
        try:
            manager.reload()
            count += 1
        except (
            Exception
        ) as e:  # noqa: BLE001 — defensive: one bad session must not break others
            failed += 1
            logger.warning(
                "reload_all_session_agents_mcp: reload() failed for session %s (%s)",
                session_id[:8],
                e,
            )

    logger.info(
        "reload_all_session_agents_mcp: reloaded %d session manager(s), %d failed",
        count,
        failed,
    )
    return count


def _index_rag_with_progress(
    agent, fpath_list, sse_handler, *, rebuild_per_doc=False, label="document(s)"
):
    """Index *fpath_list* with SSE progress events.

    Emits tool_start, per-doc status, and tool_result events.
    When *rebuild_per_doc* is True, calls agent.rebuild_system_prompt() after
    each successfully indexed document (used for cache-hit incremental updates).
    """
    n = len(fpath_list)
    sse_handler._emit(
        {
            "type": "tool_start",
            "tool": "index_documents",
            "detail": f"Indexing {n} {label} for RAG",
        }
    )
    idx_start = _time.time()
    doc_stats = []
    total_chunks = 0
    for i, fpath in enumerate(fpath_list, 1):
        doc_name = Path(fpath).name
        sse_handler._emit(
            {
                "type": "status",
                "status": "info",
                "message": f"Indexing [{i}/{n}]: {doc_name}",
            }
        )
        try:
            result = agent.rag.index_document(fpath)
            n_chunks = result.get("num_chunks", 0)
            error = result.get("error")
            if error:
                logger.warning("RAG error for %s: %s", fpath, error)
                doc_stats.append(f"  {doc_name} — ERROR: {error}")
                sse_handler._emit(
                    {
                        "type": "status",
                        "status": "warning",
                        "message": f"Error indexing {doc_name}: {error}",
                    }
                )
            else:
                agent.indexed_files.add(fpath)
                total_chunks += n_chunks
                size_mb = result.get("file_size_mb", 0) or 0
                file_size_bytes = int(size_mb * 1024 * 1024)
                if size_mb >= 1:
                    size_str = f"{size_mb:.1f} MB"
                elif file_size_bytes >= 1024:
                    size_str = f"{file_size_bytes // 1024} KB"
                else:
                    size_str = f"{file_size_bytes} B"
                from_cache = result.get("from_cache", False)
                doc_stats.append(
                    f"  {doc_name} — {n_chunks} chunks, {size_str}"
                    + (" (cached)" if from_cache else "")
                )
                if rebuild_per_doc:
                    agent.rebuild_system_prompt()
        except Exception as idx_err:
            logger.warning("Failed to index %s: %s", fpath, idx_err)
            doc_stats.append(f"  {doc_name} — FAILED: {idx_err}")
            sse_handler._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": f"Failed to index {doc_name}: {idx_err}",
                }
            )
    idx_elapsed = round(_time.time() - idx_start, 1)
    summary_lines = [
        f"Indexed {n} {label} in {idx_elapsed}s",
        f"Total: {total_chunks} chunks in index",
        "",
    ] + doc_stats
    sse_handler._emit(
        {
            "type": "tool_result",
            "title": "Index Documents",
            "summary": "\n".join(summary_lines),
            "success": True,
        }
    )


def evict_session_agent(session_id: str) -> None:
    """Remove a session's cached agent (call on session deletion or clear)."""
    with _agent_cache_lock:
        entry = _agent_cache.pop(session_id, None)
        if entry is not None:
            logger.debug("Evicted cached agent for session %s", session_id[:8])
            _disconnect_cached_agent(entry)


# ── Chat Helpers ─────────────────────────────────────────────────────────────


def _build_history_pairs(messages: list) -> list:
    """Build user/assistant conversation pairs from message history.

    Iterates messages sequentially and pairs adjacent user->assistant messages.
    Unpaired messages (e.g., a user message without a following assistant reply
    due to a prior streaming error) are safely skipped without misaligning
    subsequent pairs.

    Returns:
        List of (user_content, assistant_content) tuples.
    """
    pairs = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["role"] == "user" and i + 1 < len(messages):
            next_msg = messages[i + 1]
            if next_msg["role"] == "assistant":
                pairs.append((msg["content"], next_msg["content"]))
                i += 2
                continue
        # Skip unpaired or system messages
        i += 1
    return pairs


def _resolve_rag_paths(db: ChatDatabase, document_ids: list) -> tuple:
    """Resolve document IDs to file paths for RAG.

    If the session has specific documents attached (document_ids non-empty),
    resolves those IDs to file paths for auto-indexing.  Otherwise returns
    them as library documents (available but not auto-indexed) so the agent
    can index on demand based on the user's request.

    Returns:
        Tuple of (rag_file_paths, library_file_paths).
        - rag_file_paths: Docs to auto-index (session-specific attachments).
        - library_file_paths: Docs available for on-demand indexing (entire library).
    """
    if document_ids:
        # Session has specific documents attached -- auto-index these
        rag_file_paths = []
        for doc_id in document_ids:
            doc = db.get_document(doc_id)
            if doc and doc.get("filepath"):
                rag_file_paths.append(doc["filepath"])
            else:
                logger.warning("Document %s not found in database, skipping", doc_id)
        return rag_file_paths, []
    else:
        # No session-specific documents attached — return empty lists.
        # Previously this exposed ALL global library documents, causing
        # cross-session contamination: documents from unrelated sessions
        # would appear in the system prompt and list_indexed_documents,
        # confusing the agent about what's actually available in the
        # current session.  Users who want a document available must
        # explicitly index it and link it to their session via document_ids.
        return [], []


def _compute_allowed_paths(rag_file_paths: list) -> list:
    """Derive allowed filesystem paths from document locations.

    Collects the unique parent directories of all RAG document paths.
    Falls back to the current working directory when no document paths
    are provided, to avoid granting unnecessarily broad access across
    unrelated projects on the same machine.
    """
    dirs = set()
    for fp in rag_file_paths:
        dirs.add(str(Path(fp).parent))
    if not dirs:
        dirs.add(str(Path.cwd()))
    return list(dirs)


def _session_agent_kwargs(
    *,
    rag_file_paths: list,
    library_paths: list,
    allowed: list,
    session_id: str,
    dynamic_tools: bool = False,
) -> dict:
    """Build the session-scoped ChatAgentConfig fields.

    Every agent registered in ``AgentRegistry`` that is backed by ChatAgent
    (e.g. the built-in ``gaia-lite``) needs the same per-session context:
    which docs to auto-index, which are available on-demand, which paths
    the PathValidator should allow, and the session ID so ChatAgent can
    persist state. Collecting them here means both the direct-construction
    path and the ``registry.create_agent(...)`` call sites forward an
    identical bundle — this PR's blocker bug was one of them forgetting a
    field.

    ``dynamic_tools`` is the Beta tool-loader toggle (#1798): a valid
    ``ChatAgentConfig`` field whose effect is only observable on the ``doc``
    agent (``_maybe_build_tool_loader`` gates on ``prompt_profile == "doc"``).
    Threading it here is what makes the UI toggle actually apply on the
    loader-active path.

    Unknown kwargs are filtered by each factory (manifest agents via
    ``dataclasses.fields`` / ``AgentManifest``) so this stays safe to pass
    to agents that don't need RAG or a path validator.
    """
    return {
        "rag_documents": rag_file_paths,
        "library_documents": library_paths,
        "allowed_paths": allowed,
        "ui_session_id": session_id,
        "dynamic_tools": dynamic_tools,
    }


def _session_mail_provider(session: dict) -> str | None:
    """Session mailbox FILTER for the email agent (#1596 / #1603 Phase 2).

    ``None`` (unset, null, or empty string from the frontend) means "every
    connected mailbox" — the email agent scans all of them and fails loudly
    when none is connected. An explicit ``"google"`` / ``"microsoft"``
    restricts to that provider. Never coerce a missing pick to "google":
    that silently triaged Gmail for sessions that never chose a provider
    and ignored a connected Outlook.
    """
    return session.get("mail_provider") or None


def _build_email_proxy_agent(
    *,
    model_id: str,
    device: str | None,
    device_ctx: int,
    mail_provider: str | None,
    streaming: bool,
):
    """Construct the sidecar-backed email chat agent (#1767 cutover).

    ``agent_type=email`` is served by ``EmailProxyAgent``: the local-LLM
    tool-calling loop still runs here in the UI backend, but every tool forwards
    to the out-of-process email sidecar over HTTP — so the UI process no longer
    loads live Gmail/Outlook backends, and Agent UI dogfoods the shipped product.
    Kept out of the agent registry on purpose (the registry auto-discovers the
    in-process ``EmailTriageAgent`` wheel); this is the UI's deliberate override.
    """
    from gaia.ui.email_sidecar.proxy_agent import EmailProxyAgent

    return EmailProxyAgent(
        model_id=model_id,
        mail_provider=mail_provider,
        device=device,
        min_context_size=device_ctx,
        streaming=streaming,
        # Match the chat agent: a streaming session drives the SSE console; a
        # non-streaming one is silent (JSON-only).
        silent_mode=not streaming,
    )


def _find_last_tool_step(steps: list) -> dict | None:
    """Find the last tool step in captured_steps, searching backwards."""
    for i in range(len(steps) - 1, -1, -1):
        if steps[i].get("type") == "tool":
            return steps[i]
    return None


# Tight timeout for pre-flight load_model. The default Lemonade
# DEFAULT_MODEL_LOAD_TIMEOUT is 12000 s (200 min) — a hung Lemonade
# would block the chat thread that long. Cold-load of a 4B GGUF on
# consumer hardware fits comfortably in 120 s; if it hasn't completed
# in that window something is genuinely wrong and we'd rather surface
# the failure than hang.
_PREFLIGHT_LOAD_TIMEOUT_S = 120


def _maybe_load_expected_model(model_id: str, sse_handler=None) -> None:
    """Ensure a text-generation LLM is active *with the expected model
    name and a 32K+ context window* before issuing a chat completion.

    Handles four failure modes that the chat path would otherwise
    surface as cryptic errors or hangs:

      1. **No model loaded** (fresh Lemonade start, or post-eviction).
         Lemonade keeps the connection open producing zero tokens.
      2. **Embedding model active** (after document indexing).
         Chat completions silently hang.
      3. **Wrong chat model active** (e.g. an eval reloaded a
         different LLM into the slot mid-session).
      4. **Right model, wrong ctx_size**.  The ChatAgent system
         prompt is >7K tokens; loading at the legacy 4096 default
         truncates it and yields empty / context-overflow errors.
         Cases (3) and (4) used to slip through — the prior guard
         was ``active_ctx and active_ctx < N`` which short-circuited
         when ``active_ctx`` was 0 / missing, leaving a 0-ctx model
         in place.  The new guard treats missing ctx as "needs reload".

    On reload we use a tight ``_PREFLIGHT_LOAD_TIMEOUT_S`` (120 s)
    so a hung Lemonade fails fast instead of blocking the chat thread
    for the default 200-minute timeout.

    VLMs (``type="vlm"``) count as valid chat models for the model-name
    check, since the multimodal backbone serves text completions.

    Note: there is a small TOCTOU window between this check and the
    actual chat request.  An eviction in that window is handled by
    the one-shot retry in the streaming worker (see ``_run_agent``).
    """
    if not model_id:
        return
    try:
        import httpx

        from gaia.llm.lemonade_client import (
            lemonade_auth_headers,
            resolve_lemonade_api_key,
        )
        from gaia.llm.lemonade_manager import DEFAULT_CONTEXT_SIZE, LemonadeManager

        base_url = LemonadeManager.get_base_url() or "http://localhost:13305/api/v1"
        _auth = lemonade_auth_headers(resolve_lemonade_api_key())
        resp = httpx.get(f"{base_url}/health", timeout=5.0, headers=_auth)
        if resp.status_code != 200:
            return
        data = resp.json()
        all_models = data.get("all_models_loaded", [])

        expected_lower = model_id.lower()
        chat_models = [m for m in all_models if m.get("type") in ("llm", "vlm")]
        active_is_expected = any(
            (m.get("model_name") or "").lower() == expected_lower for m in chat_models
        )
        active_ctx = 0
        for m in chat_models:
            if (m.get("model_name") or "").lower() == expected_lower:
                active_ctx = m.get("recipe_options", {}).get("ctx_size") or 0
                break
        # Reload conditions:
        #   - no chat model active
        #   - active chat model isn't the one we want
        #   - active ctx is missing (== 0 from .get() fallback) OR < required
        # The previous guard ``active_ctx and active_ctx < DEFAULT_CONTEXT_SIZE``
        # let a 0-ctx state pass through, which is exactly the broken-model
        # state where reload is most needed.
        ctx_too_small = active_is_expected and (
            active_ctx == 0 or active_ctx < DEFAULT_CONTEXT_SIZE
        )
        needs_load = not chat_models or not active_is_expected or ctx_too_small
        if not needs_load:
            return

        reason = (
            "no chat model loaded"
            if not chat_models
            else (
                f"wrong model active ({[m.get('model_name') for m in chat_models]}); "
                f"need {model_id}"
                if not active_is_expected
                else f"ctx={active_ctx} < required {DEFAULT_CONTEXT_SIZE}"
            )
        )
        logger.info("Pre-flight load: %s → loading %s", reason, model_id)
        if sse_handler is not None:
            sse_handler._emit(
                {"type": "status", "status": "info", "message": "Loading LLM model..."}
            )

        from gaia.llm.lemonade_client import LemonadeClient

        with model_load_lock:
            # Re-check after acquiring the lock: another thread may have
            # already loaded the expected model with sufficient context.
            resident_chat_models = []
            resp2 = httpx.get(f"{base_url}/health", timeout=5.0, headers=_auth)
            if resp2.status_code == 200:
                resident_chat_models = [
                    m
                    for m in resp2.json().get("all_models_loaded", [])
                    if m.get("type") in ("llm", "vlm")
                ]
                if any(
                    (m.get("model_name") or "").lower() == expected_lower
                    and (m.get("recipe_options", {}).get("ctx_size") or 0)
                    >= DEFAULT_CONTEXT_SIZE
                    for m in resident_chat_models
                ):
                    logger.debug(
                        "Expected model loaded with sufficient ctx by concurrent "
                        "thread; skipping load"
                    )
                    return

            client = LemonadeClient(verbose=False)
            # Lemonade does not evict the resident model on a new /load, so
            # loading a different model (or the same model at a larger ctx)
            # leaves the previous one resident — a silent double-load that
            # wastes memory and can degrade output. Unload the wrong/stale chat
            # model first, mirroring gaia/rag/sdk.py before an embedder swap.
            if resident_chat_models:
                try:
                    client.unload_model()
                except Exception as unload_exc:
                    logger.debug(
                        "Pre-flight unload before reload failed: %s", unload_exc
                    )
            client.load_model(
                model_id,
                ctx_size=DEFAULT_CONTEXT_SIZE,
                prompt=False,
                timeout=_PREFLIGHT_LOAD_TIMEOUT_S,
            )
    except Exception as exc:
        logger.warning("Pre-flight model check failed: %s", exc)
        if sse_handler is not None:
            sse_handler._emit(
                {
                    "type": "status",
                    "status": "warning",
                    "message": "Could not auto-load LLM. Check that Lemonade is running.",
                }
            )


# ── Non-streaming Chat ───────────────────────────────────────────────────────


async def _get_chat_response(
    db: ChatDatabase, session: dict, request: ChatRequest
) -> str:
    """Get a non-streaming chat response from the ChatAgent.

    Uses the full ChatAgent (with tools) instead of plain AgentSDK
    so non-streaming mode also has agentic capabilities.

    Runs the synchronous agent in a thread pool executor
    to avoid blocking the async event loop.
    """

    def _do_chat():
        # Build conversation history from database
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = _build_history_pairs(messages)

        # Resolve document IDs to file paths.
        document_ids = session.get("document_ids", [])
        rag_file_paths, library_paths = _resolve_rag_paths(db, document_ids)

        all_doc_paths = rag_file_paths + library_paths
        if all_doc_paths:
            logger.info(
                "Chat: %d auto-index doc(s), %d library doc(s)",
                len(rag_file_paths),
                len(library_paths),
            )

        allowed = _compute_allowed_paths(all_doc_paths)

        model_id = session.get("model")
        custom_model = db.get_setting("custom_model")
        if custom_model:
            logger.info(
                "Using custom model override: %s (session default: %s)",
                custom_model,
                model_id,
            )
            model_id = custom_model

        # Beta dynamic tool loader (#1798). Effective only on the doc agent;
        # GAIA_DYNAMIC_TOOLS still overrides per ChatAgent's resolver.
        dynamic_tools = db.get_setting("dynamic_tools", "false") == "true"

        session_id = request.session_id
        stored_agent_type = session.get("agent_type") or "chat"
        agent_type = request.agent_type or stored_agent_type

        # Validate requested agent_type exists in the registry before persisting
        registry = _agent_registry
        if agent_type != "chat" and registry and not registry.get(agent_type):
            logger.warning(
                "chat: Session %s requested unknown agent_type '%s'; "
                "returning unavailable-agent error",
                session_id[:8],
                agent_type,
            )
            return _agent_unavailable_message(agent_type, registry)

        if agent_type != stored_agent_type:
            db.update_session(session_id, agent_type=agent_type)
            logger.info(
                "chat: Session %s agent_type changed: %s -> %s",
                session_id[:8],
                stored_agent_type,
                agent_type,
            )
        logger.info("chat: Session %s using agent type: %s", session_id[:8], agent_type)

        # Honour agent model preferences from the registry (skipped when the
        # user has set a custom model override, which always takes priority).
        if not custom_model and registry and agent_type != "chat":
            preferred = registry.resolve_model(agent_type)
            if preferred:
                logger.info(
                    "chat: Agent %s prefers model %s (was %s)",
                    agent_type,
                    preferred,
                    model_id,
                )
                model_id = preferred

        # The UI device dropdown drives the model + ctx window.
        device = session.get("device")
        model_id, device_ctx = _apply_device_model(
            session, agent_type, model_id, custom_model, registry
        )

        # ── Agent cache ──────────────────────────────────────────────────────
        cached_agent = _get_cached_agent(session_id, model_id, agent_type)

        if cached_agent is not None:
            agent = cached_agent
            agent._register_tools()
            if rag_file_paths and hasattr(agent, "rag") and agent.rag:
                new_paths = [p for p in rag_file_paths if p not in agent.indexed_files]
                for fpath in new_paths:
                    try:
                        result_idx = agent.rag.index_document(fpath)
                        if result_idx.get("success"):
                            agent.indexed_files.add(fpath)
                            agent.rebuild_system_prompt()
                    except Exception as _idx_err:
                        logger.warning("Failed to index %s: %s", fpath, _idx_err)
            logger.info(
                "chat: Agent cache hit for session %s (agent_type=%s)",
                session_id[:8],
                agent_type,
            )
        elif agent_type == "chat":
            try:
                from gaia_agent_chat.agent import ChatAgent, ChatAgentConfig
            except ImportError as e:
                raise RuntimeError(
                    "The chat agent is not installed. Run "
                    "`pip install gaia-agent-chat` (or `pip install "
                    '"amd-gaia[agents]"`), then restart the server.'
                ) from e

            logger.info(
                "chat: Creating new chat agent (ChatAgent) for session %s",
                session_id[:8],
            )
            _session_kwargs = _session_agent_kwargs(
                rag_file_paths=rag_file_paths,
                library_paths=library_paths,
                allowed=allowed,
                session_id=session_id,
                dynamic_tools=dynamic_tools,
            )
            config = ChatAgentConfig(
                model_id=model_id,
                silent_mode=True,
                debug=False,
                device=device,
                min_context_size=device_ctx,
                **_session_kwargs,
            )
            _stamp_chat_identity(config)
            agent = ChatAgent(config)
            _store_agent(session_id, model_id, document_ids, agent, agent_type)
            _register_agent_memory_ops(agent)
        elif agent_type == "email":
            # #1767 cutover: agent_type=email is the out-of-process sidecar, not
            # the in-process EmailTriageAgent. Same chat plumbing + pre_scan card.
            logger.info(
                "chat: Creating new email agent (sidecar) for session %s",
                session_id[:8],
            )
            agent = _build_email_proxy_agent(
                model_id=model_id,
                device=device,
                device_ctx=device_ctx,
                mail_provider=_session_mail_provider(session),
                streaming=False,
            )
            _store_agent(session_id, model_id, document_ids, agent, agent_type)
        else:
            # Non-chat agent: create via registry
            registry = _agent_registry
            if registry is None or registry.get(agent_type) is None:
                # Registry unavailable or agent_type unknown — return a user-friendly
                # error rather than silently falling back to a ChatAgent.
                if registry is None:
                    logger.warning(
                        "chat: Agent registry not initialized for session %s; "
                        "returning unavailable-agent error",
                        session_id[:8],
                    )
                else:
                    logger.warning(
                        "chat: Unknown agent_type '%s' for session %s; "
                        "returning unavailable-agent error",
                        agent_type,
                        session_id[:8],
                    )
                return _agent_unavailable_message(agent_type, registry)
            else:
                logger.info(
                    "chat: Creating new %s agent for session %s",
                    agent_type,
                    session_id[:8],
                )
                # Registered agents backed by ChatAgent (e.g. gaia-lite) need
                # the same session-scoped context as the built-in path above.
                # Agent factories that don't recognise a field filter it out
                # via ``dataclasses.fields``/``AgentManifest`` validation.
                #
                # Non-streaming vs streaming asymmetry — read before refactoring:
                # This path forwards ``rag_file_paths`` (the real list) because
                # non-streaming has no SSE handler, so ChatAgent's silent
                # ``_index_documents`` in __init__ is the only indexer and must
                # see the paths. The STREAMING registered-agent path, by
                # contrast, passes ``rag_file_paths=[]`` and does the indexing
                # in ``_index_rag_with_progress`` so the user sees progress
                # events; forwarding the real list there would double-index
                # (see the regression test in test_chat_helpers.py).
                agent = registry.create_agent(
                    agent_type,
                    **_build_create_kwargs(
                        custom_model=custom_model,
                        model_id=model_id,
                        device=device,
                        min_context_size=device_ctx,
                    ),
                    **_session_agent_kwargs(
                        rag_file_paths=rag_file_paths,
                        library_paths=library_paths,
                        allowed=allowed,
                        session_id=session_id,
                        dynamic_tools=dynamic_tools,
                    ),
                    # Forwarded only here (not via _session_agent_kwargs, which
                    # also feeds the strict ChatAgentConfig). Non-email factories
                    # drop it via dataclasses.fields filtering. None = scan every
                    # connected mailbox (#1596).
                    mail_provider=_session_mail_provider(session),
                )
                logger.info(
                    "chat: Invoking agent %s for session %s, model=%s",
                    agent_type,
                    session_id[:8],
                    _effective_model(agent, model_id),
                )
                _store_agent(
                    session_id,
                    model_id,
                    document_ids,
                    agent,
                    agent_type,
                )

        # Suppress memory writes when private session OR global memory is disabled.
        if hasattr(agent, "_incognito"):
            memory_globally_off = db.get_setting("memory_enabled", "false") == "false"
            agent._incognito = memory_globally_off or bool(session.get("private", 0))

        # Restore conversation history (limited to prevent context overflow).
        # Always re-inject from DB so the history is consistent with what was
        # persisted — regardless of whether the agent was cached or fresh.
        # 5 pairs × 2 msgs × ~500 tokens ≈ 5 000 tokens — well within 32K.
        # 2000-char truncation preserves enough assistant context for cross-turn
        # recall, pronoun resolution, and multi-step planning.
        _MAX_PAIRS = 5
        _MAX_CHARS = 2000
        agent.conversation_history = []
        for user_msg, assistant_msg in history_pairs[-_MAX_PAIRS:]:
            u = user_msg[:_MAX_CHARS]
            a = assistant_msg[:_MAX_CHARS]
            if len(assistant_msg) > _MAX_CHARS:
                a += "... (truncated)"
            agent.conversation_history.append({"role": "user", "content": u})
            agent.conversation_history.append({"role": "assistant", "content": a})

        # Pre-flight on agent's ACTUAL effective model. When model_id kwarg was
        # omitted, the agent's __init__ set model_id via kwargs.setdefault —
        # a value invisible pre-construction. Using _effective_model preserves
        # the existing 100-900s silent-hang protection for all code paths.
        effective = _effective_model(agent, model_id)
        _maybe_load_expected_model(effective)

        # One automatic retry on transient Lemonade failures (model
        # evicted between turns, network blip).  Mirror of the streaming
        # path's retry logic so non-streaming clients get the same
        # recovery behaviour. See _classify_chat_exception for the
        # detection rules.
        try:
            result = agent.process_query(request.message)
        except Exception as first_exc:  # pylint: disable=broad-except
            classified = _classify_chat_exception(first_exc)
            if classified is None or not classified.retryable:
                raise
            logger.warning(
                "Non-streaming chat hit retryable Lemonade error (%s) — "
                "reloading model and retrying once. Original: %s",
                type(classified).__name__,
                first_exc,
            )
            try:
                _maybe_load_expected_model(effective)
            except Exception:  # pylint: disable=broad-except
                pass
            try:
                result = agent.process_query(request.message)
            except Exception as second_exc:  # pylint: disable=broad-except
                second_classified = _classify_chat_exception(second_exc)
                if second_classified is not None:
                    raise type(second_exc)(
                        second_classified.user_message
                    ) from second_exc
                raise

        if isinstance(result, dict):
            # process_query returns {"result": "...", "status": "...", ...}
            # Use explicit None check so an intentional empty string isn't
            # overridden by fallback to "answer".
            val = result.get("result")
            return val if val is not None else result.get("answer", "")
        result_str = str(result) if result else ""
        # Strip JSON envelope (e.g. {"answer": "..."}) emitted by agents
        # whose system prompt requires JSON output format.
        return _clean_answer_json(result_str)

    try:
        loop = asyncio.get_running_loop()
        # Apply a 600-second timeout to prevent indefinite hangs when the
        # LLM gets stuck in a tool loop or Lemonade becomes unresponsive
        return await asyncio.wait_for(
            loop.run_in_executor(None, _do_chat),
            timeout=600.0,
        )
    except asyncio.TimeoutError:
        logger.error("Chat response timed out after 600 seconds")
        return "I took too long thinking about that one. Try breaking your question into simpler parts and I'll do my best."
    except Exception as e:
        logger.error("Chat error: %s", e, exc_info=True)
        # Surface the typed Lemonade error's friendly user_message
        # when we have one, instead of a stock "trouble connecting"
        # blob.  Falls back to the generic copy when we can't
        # classify the exception.
        classified = _classify_chat_exception(e)
        if classified is not None:
            return classified.user_message
        return (
            "I'm having trouble connecting to the language model right now. "
            "Please make sure Lemonade Server is running and try again."
        )


# ── Streaming Chat ───────────────────────────────────────────────────────────


async def _stream_chat_impl(run, db: ChatDatabase, session: dict, request: ChatRequest):
    """Produce chat-response SSE events for a single run.

    Uses ChatAgent with SSEOutputHandler to emit agent activity events
    (steps, tool calls, thinking) alongside text chunks, giving the
    frontend visibility into what the agent is doing.

    This is the run *producer*: it is driven by ``_run_chat_lifecycle``
    inside a detached task that owns the run for its full duration,
    independent of any HTTP/SSE client connection. Yielded ``data: ...``
    strings are buffered and fanned out to attached subscribers by the
    lifecycle; DB persistence happens here regardless of whether a client
    is still listening, so navigating away never loses the run (issue
    #1580 follow-up).
    """
    import queue

    from gaia.ui.sse_handler import SSEOutputHandler

    session_id = request.session_id
    sse_handler = None
    producer = None
    cleanup_done = False
    # Cooperative cancel signal for the producer's agent loop. Set on stream
    # timeout / client disconnect so the agent bails at its next step boundary
    # and the producer thread is actually reaped (see agent._cancel_event).
    cancel_event = threading.Event()

    def _cleanup_stream():
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        cancel_event.set()
        if sse_handler is not None:
            sse_handler.cancelled.set()
        _active_sse_handlers.pop(session_id, None)
        if producer is not None:
            producer.join(timeout=5.0)
            if producer.is_alive():
                logger.warning("Producer thread still running after stream ended")

    try:
        # Create SSE handler for streaming events
        sse_handler = SSEOutputHandler()
        # Expose the handler on the run so an external Stop can signal the
        # producer to bail even after every client has detached (#1580).
        run.handler = sse_handler
        # Register so /api/chat/confirm-tool can find this handler.
        _active_sse_handlers[session_id] = sse_handler

        # ── Immediate browser feedback ────────────────────────────────────
        # Yield "Connecting to LLM..." directly (not via the queue) so the
        # browser sees it *before* the producer thread starts — giving instant
        # visual feedback even if agent construction or LemonadeManager take
        # several seconds on first turn.
        #
        # The padding comment that follows forces Chromium / Electron to flush
        # its internal receive buffer.  With small SSE events (< ~512 bytes),
        # Chromium's fetch ReadableStream holds chunks until the buffer fills or
        # the stream closes.  Without this, the browser sees nothing for the
        # entire duration and then gets a batch-dump of all events at the end.
        yield (
            'data: {"type":"status","status":"info","message":"Connecting to LLM..."}\n\n'
            ": " + "x" * 512 + "\n\n"
        )

        # Build conversation history
        messages = db.get_messages(request.session_id, limit=20)
        history_pairs = _build_history_pairs(messages)

        # Resolve document IDs to file paths.
        # Session-specific docs get auto-indexed; library docs are available
        # for on-demand indexing by the agent based on user's query.
        document_ids = session.get("document_ids", [])
        rag_file_paths, library_paths = _resolve_rag_paths(db, document_ids)

        all_doc_paths = rag_file_paths + library_paths
        if all_doc_paths:
            logger.info(
                "Streaming chat: %d auto-index doc(s), %d library doc(s)",
                len(rag_file_paths),
                len(library_paths),
            )

        allowed = _compute_allowed_paths(all_doc_paths)
        model_id = session.get("model")

        # Use custom model override if set in user settings
        custom_model = db.get_setting("custom_model")
        if custom_model:
            logger.info(
                "Streaming: using custom model override: %s (session default: %s)",
                custom_model,
                model_id,
            )
            model_id = custom_model

        # Beta dynamic tool loader (#1798). Effective only on the doc agent;
        # GAIA_DYNAMIC_TOOLS still overrides per ChatAgent's resolver.
        dynamic_tools = db.get_setting("dynamic_tools", "false") == "true"
        # ``session_model`` is the model_id we expect to drive the
        # session with, captured here in the outer scope so the
        # auto-title background task at the bottom of this generator
        # can read it without needing access to the inner producer
        # thread's ``agent`` variable.
        session_model = model_id

        stored_agent_type = session.get("agent_type") or "chat"
        agent_type = request.agent_type or stored_agent_type

        # Validate requested agent_type exists in the registry before persisting
        registry = _agent_registry
        if agent_type != "chat" and registry and not registry.get(agent_type):
            logger.warning(
                "chat: Session %s requested unknown agent_type '%s' (streaming); "
                "returning unavailable-agent error",
                session_id[:8],
                agent_type,
            )
            error_msg = _agent_unavailable_message(agent_type, registry)
            yield f"data: {json.dumps({'type': 'answer', 'content': error_msg})}\n\n"
            return

        if agent_type != stored_agent_type:
            db.update_session(session_id, agent_type=agent_type)
            logger.info(
                "chat: Session %s agent_type changed: %s -> %s (streaming)",
                session_id[:8],
                stored_agent_type,
                agent_type,
            )
        logger.info(
            "chat: Session %s using agent type: %s (streaming)",
            session_id[:8],
            agent_type,
        )

        # Honour agent model preferences from the registry (skipped when the
        # user has set a custom model override, which always takes priority).
        if not custom_model and registry and agent_type != "chat":
            preferred = registry.resolve_model(agent_type)
            if preferred:
                logger.info(
                    "chat: Agent %s prefers model %s (was %s) (streaming)",
                    agent_type,
                    preferred,
                    model_id,
                )
                model_id = preferred

        # The UI device dropdown drives the model + ctx window.
        device = session.get("device")
        model_id, device_ctx = _apply_device_model(
            session, agent_type, model_id, custom_model, registry
        )

        # Move ALL slow work into the background thread so the SSE generator
        # can yield the thinking event immediately.
        result_holder = {"answer": "", "error": None}

        def _run_agent():
            try:
                t0 = _time.monotonic()

                # ── Agent cache check ─────────────────────────────────────────
                cached_agent = _get_cached_agent(session_id, model_id, agent_type)

                if cached_agent is not None:
                    # -- Cache hit --
                    agent = cached_agent
                    agent.console = sse_handler

                    # Re-register tools so _TOOL_REGISTRY points at this agent's self.
                    agent._register_tools()

                    # Early-exit if consumer disconnected
                    if sse_handler.cancelled.is_set():
                        return

                    # Index any session docs newly attached since last turn.
                    new_rag_paths = [
                        p for p in rag_file_paths if p not in agent.indexed_files
                    ]
                    if new_rag_paths and hasattr(agent, "rag") and agent.rag:
                        _index_rag_with_progress(
                            agent,
                            new_rag_paths,
                            sse_handler,
                            rebuild_per_doc=True,
                            label="new document(s)",
                        )

                    logger.info(
                        "chat: Agent cache hit for session %s (agent_type=%s) setup=%.3fs",
                        session_id[:8],
                        agent_type,
                        _time.monotonic() - t0,
                    )
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Sending to model...",
                        }
                    )

                elif agent_type == "chat":
                    # -- Cache miss: ChatAgent --
                    try:
                        from gaia_agent_chat.agent import (
                            ChatAgent,
                            ChatAgentConfig,
                        )
                    except ImportError as e:
                        raise RuntimeError(
                            "The chat agent is not installed. Run "
                            "`pip install gaia-agent-chat` (or `pip install "
                            '"amd-gaia[agents]"`), then restart the server.'
                        ) from e

                    logger.info(
                        "chat: Creating new chat agent (ChatAgent) for session %s",
                        session_id[:8],
                    )
                    _session_kwargs = _session_agent_kwargs(
                        rag_file_paths=[],  # streaming path indexes separately below
                        library_paths=library_paths,
                        allowed=allowed,
                        session_id=session_id,
                        dynamic_tools=dynamic_tools,
                    )
                    config = ChatAgentConfig(
                        model_id=model_id,
                        streaming=True,
                        silent_mode=False,
                        debug=False,
                        device=device,
                        min_context_size=device_ctx,
                        **_session_kwargs,
                    )

                    _stamp_chat_identity(config)
                    t_construct = _time.monotonic()
                    agent = ChatAgent(config)
                    logger.info(
                        "chat: Invoking agent chat for session %s, model=%s took=%.3fs",
                        session_id[:8],
                        model_id,
                        _time.monotonic() - t_construct,
                    )
                    _register_agent_memory_ops(agent)
                    agent.console = sse_handler  # Assign early so tool events flow

                    # Early-exit if consumer disconnected
                    if sse_handler.cancelled.is_set():
                        return

                    # -- Phase 3: RAG indexing --
                    # Session-attached docs are indexed with full SSE progress events.
                    # Library docs are silently pre-indexed from disk cache so the
                    # system prompt shows them as "already indexed" — preventing the
                    # LLM from calling index_document again on unchanged files.
                    # The hash-based cache (RAGSDK) guarantees no re-processing
                    # unless file content has actually changed.
                    if rag_file_paths and agent.rag:
                        t_rag = _time.monotonic()
                        _index_rag_with_progress(agent, rag_file_paths, sse_handler)
                        logger.info(
                            "PERF RAG indexing session=%s took=%.3fs",
                            session_id[:8],
                            _time.monotonic() - t_rag,
                        )

                    # -- Phase 3b: Silently pre-index library docs from cache --
                    # Library docs that are already on disk are loaded from the
                    # hash-based RAG cache (no LLM/embedding re-computation for
                    # unchanged files).  Adding them to agent.indexed_files causes
                    # rebuild_system_prompt() to emit the ANTI-RE-INDEX RULE, so
                    # the LLM will query them directly instead of re-indexing.
                    if library_paths and agent.rag:
                        preindexed = 0
                        for fpath in library_paths:
                            try:
                                result = agent.rag.index_document(fpath)
                                if result.get("success") and not result.get("error"):
                                    agent.indexed_files.add(fpath)
                                    preindexed += 1
                            except Exception as lib_err:
                                logger.debug(
                                    "Library pre-index skipped for %s: %s",
                                    fpath,
                                    lib_err,
                                )
                        if preindexed:
                            agent.rebuild_system_prompt()
                            logger.info(
                                "Pre-indexed %d library doc(s) from cache", preindexed
                            )

                    # Cache the agent for subsequent turns in this session.
                    _store_agent(session_id, model_id, document_ids, agent, agent_type)
                    logger.info(
                        "chat: Total setup (cache miss, chat) session=%s took=%.3fs",
                        session_id[:8],
                        _time.monotonic() - t0,
                    )
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Sending to model...",
                        }
                    )

                elif agent_type == "email":
                    # -- Cache miss: email agent backed by the sidecar (#1767) --
                    # Same chat plumbing as before; the pre_scan_inbox tool returns
                    # the identical email_pre_scan envelope so the card renders
                    # unchanged. Tools forward to the out-of-process sidecar.
                    logger.info(
                        "chat: Creating new email agent (sidecar) for session %s",
                        session_id[:8],
                    )
                    t_construct = _time.monotonic()
                    agent = _build_email_proxy_agent(
                        model_id=model_id,
                        device=device,
                        device_ctx=device_ctx,
                        mail_provider=_session_mail_provider(session),
                        streaming=True,
                    )
                    agent.console = sse_handler  # tool events flow to SSE
                    logger.info(
                        "chat: Invoking agent email for session %s, model=%s took=%.3fs",
                        session_id[:8],
                        _effective_model(agent, model_id),
                        _time.monotonic() - t_construct,
                    )
                    if sse_handler.cancelled.is_set():
                        return
                    _store_agent(session_id, model_id, document_ids, agent, agent_type)
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Sending to model...",
                        }
                    )

                else:
                    # -- Cache miss: non-chat agent via registry --
                    registry = _agent_registry
                    if registry is None or registry.get(agent_type) is None:
                        # Registry unavailable or agent_type unknown — emit a
                        # user-friendly error rather than silently falling back to chat.
                        if registry is None:
                            logger.warning(
                                "chat: Agent registry not initialized for session %s (streaming); "
                                "returning unavailable-agent error",
                                session_id[:8],
                            )
                        else:
                            logger.warning(
                                "chat: Unknown agent_type '%s' for session %s (streaming); "
                                "returning unavailable-agent error",
                                agent_type,
                                session_id[:8],
                            )
                        error_msg = _agent_unavailable_message(agent_type, registry)
                        sse_handler._emit({"type": "answer", "content": error_msg})
                        result_holder["answer"] = error_msg
                        return
                    else:
                        logger.info(
                            "chat: Creating new %s agent for session %s",
                            agent_type,
                            session_id[:8],
                        )
                        t_construct = _time.monotonic()
                        # Same session-scoped kwargs as the built-in Chat
                        # streaming path. Registered agents backed by ChatAgent
                        # (e.g. gaia-lite) need these so session docs are
                        # reachable and PathValidator accepts absolute paths.
                        #
                        # IMPORTANT: pass ``rag_file_paths=[]`` so ChatAgent
                        # doesn't silently auto-index in ``__init__`` — we do
                        # the indexing below via ``_index_rag_with_progress``
                        # so the user sees progress events. Otherwise the file
                        # would be indexed twice on every cache miss, surfacing
                        # a noisy "Used tool index_documents" card for a
                        # 0-work hash-cache hit on casual chat turns.
                        agent = registry.create_agent(
                            agent_type,
                            **_build_create_kwargs(
                                custom_model=custom_model,
                                model_id=model_id,
                                streaming=True,
                                device=device,
                                min_context_size=device_ctx,
                            ),
                            **_session_agent_kwargs(
                                rag_file_paths=[],
                                library_paths=library_paths,
                                allowed=allowed,
                                session_id=session_id,
                                dynamic_tools=dynamic_tools,
                            ),
                            # See the non-streaming path: email-only kwarg,
                            # filtered out by non-email factories. None = scan
                            # every connected mailbox (#1596).
                            mail_provider=_session_mail_provider(session),
                        )
                        agent.console = sse_handler
                        logger.info(
                            "chat: Invoking agent %s for session %s, model=%s took=%.3fs",
                            agent_type,
                            session_id[:8],
                            _effective_model(agent, model_id),
                            _time.monotonic() - t_construct,
                        )

                        if sse_handler.cancelled.is_set():
                            return

                        # Index session-attached RAG docs (single pass, with SSE progress).
                        if rag_file_paths and hasattr(agent, "rag") and agent.rag:
                            _index_rag_with_progress(agent, rag_file_paths, sse_handler)

                        _store_agent(
                            session_id,
                            model_id,
                            document_ids,
                            agent,
                            agent_type,
                        )

                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Sending to model...",
                        }
                    )

                # -- Emit MCP runtime status (once per request, after agent setup) --
                if hasattr(agent, "get_mcp_status_report"):
                    mcp_report = agent.get_mcp_status_report()
                    with _mcp_status_lock:
                        _mcp_status_cache[:] = mcp_report
                    if mcp_report:
                        sse_handler._emit({"type": "mcp_status", "servers": mcp_report})

                # Suppress memory writes when private session OR global memory is disabled.
                if hasattr(agent, "_incognito"):
                    memory_globally_off = (
                        db.get_setting("memory_enabled", "false") == "false"
                    )
                    agent._incognito = memory_globally_off or bool(
                        session.get("private", 0)
                    )

                # Early-exit if consumer disconnected
                if sse_handler.cancelled.is_set():
                    return

                # -- Phase 4: Conversation history --
                # Always re-inject from DB so history is consistent regardless of
                # whether the agent was cached or freshly constructed.  Clears any
                # stale history accumulated in prior turns of a cached agent.
                # 5 pairs × 2 msgs × ~500 tokens ≈ 5 000 tokens — well within 32K.
                _MAX_HISTORY_PAIRS = 5
                _MAX_MSG_CHARS = 2000
                agent.conversation_history = []
                if history_pairs:
                    recent = history_pairs[-_MAX_HISTORY_PAIRS:]
                    for user_msg, assistant_msg in recent:
                        # Truncate to keep context manageable
                        u = user_msg[:_MAX_MSG_CHARS]
                        a = assistant_msg[:_MAX_MSG_CHARS]
                        if len(assistant_msg) > _MAX_MSG_CHARS:
                            a += "... (truncated)"
                        agent.conversation_history.append(
                            {"role": "user", "content": u}
                        )
                        agent.conversation_history.append(
                            {"role": "assistant", "content": a}
                        )

                # Early-exit if consumer disconnected
                if sse_handler.cancelled.is_set():
                    return

                # Let the agent loop observe stream-timeout/disconnect so a hung
                # turn is torn down instead of leaking this producer thread.
                agent._cancel_event = cancel_event

                # Pre-flight on agent's ACTUAL effective model. When model_id kwarg was
                # omitted, the agent's __init__ set model_id via kwargs.setdefault — a value
                # invisible pre-construction. Using agent.model_id preserves the existing
                # 100-900s silent-hang protection for all code paths including setdefault.
                _maybe_load_expected_model(
                    _effective_model(agent, model_id), sse_handler
                )

                # -- Phase 5: Query processing.  One automatic retry on
                # known-transient Lemonade errors (model evicted between
                # turns, network blip, etc.). The retry forces a fresh
                # model reload at our 32K ctx_size before re-issuing the
                # query so we don't replay against a still-broken backend.
                t_query = _time.monotonic()
                try:
                    result = agent.process_query(request.message)
                except Exception as first_exc:  # pylint: disable=broad-except
                    classified = _classify_chat_exception(first_exc)
                    if classified is None or not classified.retryable:
                        raise
                    logger.warning(
                        "Chat hit retryable Lemonade error (%s) — reloading "
                        "model and retrying once. Original: %s",
                        type(classified).__name__,
                        first_exc,
                    )
                    # Force a reload at the canonical ctx; this is the
                    # same helper the pre-flight uses but called
                    # mid-conversation when the model got evicted.
                    try:
                        _maybe_load_expected_model(
                            _effective_model(agent, model_id), sse_handler
                        )
                    except Exception:  # pylint: disable=broad-except
                        # Reload failure is non-fatal — the retry might
                        # still succeed if Lemonade caught up on its own.
                        pass
                    # Surface a brief status line to the SSE so the user
                    # sees we're recovering, not silently retrying.
                    sse_handler._emit(
                        {
                            "type": "status",
                            "status": "info",
                            "message": "Model reloaded — retrying...",
                        }
                    )
                    # One more attempt. If THIS fails, surface the
                    # friendlier classified message.
                    try:
                        result = agent.process_query(request.message)
                    except Exception as second_exc:  # pylint: disable=broad-except
                        second_classified = _classify_chat_exception(second_exc)
                        msg = (
                            second_classified.user_message
                            if second_classified is not None
                            else str(second_exc)
                        )
                        raise type(second_exc)(msg) from second_exc
                logger.info(
                    "PERF process_query session=%s took=%.3fs",
                    session_id[:8],
                    _time.monotonic() - t_query,
                )
                if isinstance(result, dict):
                    val = result.get("result")
                    result_holder["answer"] = (
                        val if val is not None else result.get("answer", "")
                    )
                else:
                    result_holder["answer"] = str(result) if result else ""
            except Exception as e:
                logger.error("Agent error: %s", e, exc_info=True)
                # Prefer the typed Lemonade error's user_message over the
                # raw exception string. ``_classify_chat_exception`` walks
                # the exception chain so we catch errors raised inside
                # AgentSDK that wrap the original LemonadeError.
                classified = _classify_chat_exception(e)
                if classified is not None:
                    result_holder["error"] = classified.user_message
                else:
                    result_holder["error"] = str(e)
            finally:
                sse_handler.signal_done()

        producer = threading.Thread(target=_run_agent, daemon=True)
        producer.start()

        # Yield SSE events from the handler's queue
        # Also capture agent steps for persistence
        full_response = ""
        captured_steps = []  # Collect agent steps for DB persistence
        persisted_policy_block_msg_id = None
        persisted_policy_block_content = None
        step_id = 0
        idle_cycles = 0
        _stream_start = _time.time()
        _STREAM_TIMEOUT = 600  # 10 minutes — large system prompts need time

        def _blocked_policy_steps():
            return [
                step
                for step in captured_steps
                if step.get("type") == "policy_alert"
                and (step.get("decision") or "BLOCK").upper() == "BLOCK"
            ]

        def _policy_block_response(blocked_steps):
            blocked_tool_names = ", ".join(
                step.get("tool") or "unknown tool" for step in blocked_steps
            )
            return (
                f"Blocked: {blocked_tool_names} "
                f"{'is' if len(blocked_steps) == 1 else 'are'} "
                "restricted by policy."
            )

        def _persist_policy_block_if_needed():
            nonlocal persisted_policy_block_content, persisted_policy_block_msg_id

            blocked_steps = _blocked_policy_steps()
            if not blocked_steps:
                return None

            persisted_policy_block_content = _policy_block_response(blocked_steps)
            persisted_policy_block_msg_id = db.upsert_message(
                request.session_id,
                persisted_policy_block_msg_id,
                "assistant",
                persisted_policy_block_content,
                agent_steps=captured_steps if captured_steps else None,
            )
            return persisted_policy_block_msg_id

        while True:
            # Guard: total timeout for the streaming response
            if _time.time() - _stream_start > _STREAM_TIMEOUT:
                logger.error("Streaming response timed out after %ds", _STREAM_TIMEOUT)
                timeout_event = json.dumps(
                    {
                        "type": "agent_error",
                        "content": f"Response timed out after {_STREAM_TIMEOUT}s. "
                        "Try a simpler query or break it into smaller questions.",
                    }
                )
                yield f"data: {timeout_event}\n\n"
                break
            try:
                event = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: sse_handler.event_queue.get(timeout=0.2)
                )
                idle_cycles = 0
                if event is None:
                    # Sentinel - agent is done
                    break

                event_type = event.get("type", "")

                # Capture answer content for DB storage
                if event_type == "answer":
                    # Always use the answer event to override accumulated chunks.
                    # print_final_answer emits a clean, artifact-free final answer,
                    # while chunks include all intermediate streaming text (planning
                    # sentences, tool call noise, etc.).  Using the answer event
                    # ensures DB storage matches what the MCP client receives.
                    answer_content = event.get("content", "")
                    if answer_content:
                        full_response = answer_content
                elif event_type == "chunk":
                    full_response += event.get("content", "")

                # Capture agent steps for persistence
                if event_type == "thinking":
                    step_id += 1
                    # Deactivate previous steps
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "thinking",
                            "label": "Thinking",
                            "detail": event.get("content"),
                            "active": True,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                elif event_type == "tool_start":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "tool",
                            "label": f"Using {event.get('tool', 'tool')}",
                            "tool": event.get("tool"),
                            "detail": event.get("detail"),
                            "active": True,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                            "mcpServer": event.get("mcp_server"),
                        }
                    )
                elif event_type == "tool_args" and captured_steps:
                    # Update the last TOOL step (not just last step, since thinking
                    # events may have been interleaved during tool execution)
                    tool_step = _find_last_tool_step(captured_steps)
                    if tool_step is not None:
                        tool_step["detail"] = event.get("detail", "")
                elif event_type == "tool_end" and captured_steps:
                    tool_step = _find_last_tool_step(captured_steps)
                    if tool_step is not None:
                        tool_step["active"] = False
                        tool_step["success"] = event.get("success", True)
                elif event_type == "tool_result" and captured_steps:
                    tool_step = _find_last_tool_step(captured_steps)
                    if tool_step is not None:
                        tool_step["active"] = False
                        tool_step["result"] = (
                            event.get("summary") or event.get("title") or "Done"
                        )
                        tool_step["success"] = event.get("success", True)
                        # Persist MCP tool latency
                        if event.get("latency_ms") is not None:
                            tool_step["latencyMs"] = event["latency_ms"]
                        # Persist structured command output for terminal rendering
                        if event.get("command_output"):
                            tool_step["commandOutput"] = event["command_output"]
                        # Persist file list for rich file list rendering
                        result_data = event.get("result_data", {})
                        if result_data.get("type") == "file_list":
                            tool_step["fileList"] = {
                                "files": result_data.get("files", []),
                                "total": result_data.get("total", 0),
                            }
                elif event_type == "plan":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "plan",
                            "label": "Created plan",
                            "planSteps": event.get("steps"),
                            "active": False,
                            "success": True,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                elif event_type == "agent_error":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "error",
                            "label": "Error",
                            "detail": event.get("content"),
                            "active": False,
                            "success": False,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                elif event_type == "policy_alert":
                    step_id += 1
                    for s in captured_steps:
                        s["active"] = False
                    tool_name = event.get("tool") or "unknown tool"
                    reason = (
                        event.get("reason")
                        or event.get("message")
                        or event.get("content")
                        or "Tool execution was blocked by governance policy."
                    )
                    captured_steps.append(
                        {
                            "id": step_id,
                            "type": "policy_alert",
                            "label": f"Policy blocked {tool_name}",
                            "detail": reason,
                            "tool": tool_name,
                            "decision": event.get("decision") or "BLOCK",
                            "reason": reason,
                            "ruleIds": event.get("rule_ids") or [],
                            "policyVersion": event.get("policy_version"),
                            "receiptId": event.get("receipt_id"),
                            "active": False,
                            "success": False,
                            "timestamp": int(asyncio.get_running_loop().time() * 1000),
                        }
                    )
                    if (event.get("decision") or "BLOCK").upper() == "BLOCK":
                        _persist_policy_block_if_needed()

                # Pad each event so Chromium's receive buffer flushes immediately.
                # Events < 512 bytes are held by Chromium until the buffer fills.
                event_data = f"data: {json.dumps(event)}\n\n"
                if len(event_data) < 512:
                    event_data += ": " + "x" * (512 - len(event_data) - 4) + "\n\n"
                yield event_data

            except queue.Empty:
                if not producer.is_alive():
                    break
                idle_cycles += 1
                # Send a padded keepalive every ~5s (25 cycles × 0.2s).
                # The padding flushes Chromium's receive buffer so any events
                # already sent but not yet dispatched arrive immediately.
                if idle_cycles % 25 == 0:
                    yield ": keepalive " + "x" * 490 + "\n\n"
                # Every 15s (75 cycles) emit a visible status so the user knows
                # the model is still processing (prompt prefill is silent).
                # Use status='working' so active=true; consecutive events merge
                # into a single updating step on the frontend.
                if idle_cycles % 75 == 0:
                    elapsed = int(_time.time() - _stream_start)
                    status_evt = json.dumps(
                        {
                            "type": "status",
                            "status": "working",
                            "message": f"Model is processing... ({elapsed}s)",
                        }
                    )
                    status_data = f"data: {status_evt}\n\n"
                    if len(status_data) < 512:
                        status_data += (
                            ": " + "x" * (512 - len(status_data) - 4) + "\n\n"
                        )
                    yield status_data
                continue

        # Signal cancellation (handles client disconnect) then wait for producer.
        _cleanup_stream()

        # Finalize all captured steps (mark as inactive)
        for s in captured_steps:
            s["active"] = False

        # Check for errors from the agent thread
        if result_holder["error"]:
            error_msg = f"Agent error: {result_holder['error']}"
            if not full_response:
                blocked_alert_steps = _blocked_policy_steps()
                if blocked_alert_steps:
                    full_response = (
                        persisted_policy_block_content
                        or _policy_block_response(blocked_alert_steps)
                    )
                    full_response += f"\n\n[Error: {result_holder['error']}]"
                else:
                    full_response = error_msg
            else:
                # Partial response exists -- append error notice so user knows
                # the response may be incomplete
                full_response += f"\n\n[Error: {result_holder['error']}]"
            error_data = json.dumps({"type": "error", "content": error_msg})
            yield f"data: {error_data}\n\n"

        # Use agent result if no streamed answer was captured
        if not full_response and result_holder["answer"]:
            full_response = result_holder["answer"]
            # Send as answer event since it wasn't streamed
            yield f"data: {json.dumps({'type': 'answer', 'content': full_response})}\n\n"

        # Clean LLM output artifacts before DB storage.
        # Apply all canonical patterns so stored content is always clean
        # regardless of which streaming path was taken.
        # Order matters: _clean_answer_json MUST run before _THOUGHT_JSON_SUB_RE.
        # The base agent asks for {"thought":..., "answer":...} JSON; if that JSON
        # leaks into full_response (e.g. streaming buffer released early), the
        # thought-stripper would consume the entire blob including the answer,
        # leaving an empty string.  Extracting the answer first prevents that.
        if full_response:
            full_response = _TOOL_CALL_JSON_SUB_RE.sub("", full_response)
            # Extract answer from {"thought":..., "answer":...} before thought stripping.
            full_response = _clean_answer_json(full_response)
            full_response = _THOUGHT_JSON_SUB_RE.sub("", full_response)
            full_response = _RAG_RESULT_JSON_SUB_RE.sub("", full_response)
            # _ANSWER_JSON_SUB_RE handles mixed content where {"answer": "..."} is
            # embedded after plain text — strips the duplicate JSON wrapper.
            full_response = _ANSWER_JSON_SUB_RE.sub("", full_response)
            full_response = _fix_double_escaped(full_response)
            # Strip trailing JSON artifact sequences (3+ closing braces = nested tool result leak)
            full_response = _re.sub(r"\}{3,}\s*$", "", full_response).strip()
            # Strip trailing code-fence artifacts (e.g. "}\n```" left after JSON extraction)
            full_response = _re.sub(r"[\n\s]*`{3,}\s*$", "", full_response).strip()
            full_response = full_response.strip()

        # Guard: if cleaning reduced the response to JSON/code artifacts only
        # (e.g. "}", "}}", "}\n", "}\n```", backtick-only), fall back to the agent's
        # direct result which is unaffected by streaming fragmentation.
        if full_response and _re.fullmatch(r'[\s{}\[\]",:` ]+', full_response):
            logger.warning(
                "Streaming response reduced to JSON artifacts %r — using agent result",
                full_response[:40],
            )
            full_response = result_holder.get("answer", "") or ""

        blocked_alert_steps = _blocked_policy_steps()
        if not full_response and blocked_alert_steps:
            full_response = persisted_policy_block_content or _policy_block_response(
                blocked_alert_steps
            )

        # Save complete response to DB (including captured agent steps)
        if full_response:
            # Fetch last inference stats from Lemonade (non-blocking).
            # Resolve the Lemonade URL via LemonadeManager so we follow the
            # actually-running instance (matches the resolver used at
            # _chat_helpers.py:331 and :747); raw os.environ lookup misses
            # cases where Lemonade is on a non-default port and the env var
            # isn't set, leaving stats silently empty in eval traces.
            inference_stats = None
            try:
                import httpx

                from gaia.llm.lemonade_client import (
                    lemonade_auth_headers,
                    resolve_lemonade_api_key,
                )
                from gaia.llm.lemonade_manager import LemonadeManager

                base_url = (
                    LemonadeManager.get_base_url() or "http://localhost:13305/api/v1"
                )
                _auth = lemonade_auth_headers(resolve_lemonade_api_key())
                async with httpx.AsyncClient(timeout=3.0) as stats_client:
                    stats_resp = await stats_client.get(
                        f"{base_url}/stats", headers=_auth
                    )
                    if stats_resp.status_code == 200:
                        stats_data = stats_resp.json()
                        inference_stats = {
                            "tokens_per_second": round(
                                stats_data.get("tokens_per_second", 0), 1
                            ),
                            "time_to_first_token": round(
                                stats_data.get("time_to_first_token", 0), 3
                            ),
                            "input_tokens": stats_data.get("input_tokens", 0),
                            "output_tokens": stats_data.get("output_tokens", 0),
                        }
                    else:
                        logger.debug(
                            "Lemonade /stats returned %d at %s — perf telemetry "
                            "missing for this turn",
                            stats_resp.status_code,
                            base_url,
                        )
            except Exception as exc:  # pylint: disable=broad-except
                # Don't fail the user's response if stats fetching breaks; log
                # at debug so eval-time gaps are diagnosable without spamming
                # production logs.
                logger.debug(
                    "Failed to fetch Lemonade /stats: %s — perf telemetry "
                    "missing for this turn",
                    exc,
                )

            if (
                persisted_policy_block_msg_id is not None
                and full_response == persisted_policy_block_content
            ):
                msg_id = persisted_policy_block_msg_id
            else:
                msg_id = db.upsert_message(
                    request.session_id,
                    persisted_policy_block_msg_id,
                    "assistant",
                    full_response,
                    agent_steps=captured_steps if captured_steps else None,
                    inference_stats=inference_stats,
                )
            # Fire-and-forget auto-titling: GAIA renames its own session
            # once the response is complete. Skips Eval: titles, throttled
            # to 30 s/session, runs on the same Lemonade slot the chat
            # just used. Never blocks the user's response.
            #
            # NB: ``agent`` lives inside the producer thread's scope and
            # isn't accessible here. We pass ``session_model`` (set
            # before the producer started) instead — it's the same
            # value ``_effective_model`` would have returned for the
            # default agent factories that honour ``model_id`` kwarg.
            _bg = asyncio.create_task(
                _maybe_update_session_title(
                    db=db,
                    session_id=request.session_id,
                    user_msg=request.message,
                    assistant_msg=full_response,
                    model_id=session_model,
                )
            )
            # Hold a reference so the GC doesn't kill the task before
            # it completes; discard on done.
            _active_sse_handlers.setdefault(f"_titlebg:{request.session_id}", _bg)
            _bg.add_done_callback(
                lambda _t, _sid=request.session_id: _active_sse_handlers.pop(
                    f"_titlebg:{_sid}", None
                )
            )
            done_event: dict = {
                "type": "done",
                "message_id": msg_id,
                "content": full_response,
            }
            if inference_stats:
                done_event["stats"] = inference_stats
            done_data = json.dumps(done_event)
            yield f"data: {done_data}\n\n"
        else:
            # Log details to help diagnose: cold start, empty LLM response, filtered artifacts
            logger.warning(
                "Empty response for session %s — result_holder answer=%r error=%r captured_steps=%d",
                session_id[:8],
                (
                    result_holder.get("answer", "")[:80]
                    if result_holder.get("answer")
                    else None
                ),
                result_holder.get("error"),
                len(captured_steps),
            )
            error_msg = "I wasn't able to generate a response. Please make sure Lemonade Server is running and try again."
            db.add_message(request.session_id, "assistant", error_msg)
            error_data = json.dumps({"type": "error", "content": error_msg})
            yield f"data: {error_data}\n\n"

    except Exception as e:
        logger.error("Chat streaming error: %s", e, exc_info=True)
        _cleanup_stream()
        error_msg = "Sorry, something went wrong on my end. This is usually a temporary issue — try sending your message again."
        try:
            db.add_message(request.session_id, "assistant", error_msg)
        except Exception:
            pass
        error_data = json.dumps({"type": "error", "content": error_msg})
        yield f"data: {error_data}\n\n"
    finally:
        _cleanup_stream()


async def _run_chat_lifecycle(
    run, db: ChatDatabase, session: dict, request: ChatRequest
):
    """Drive a single chat run to completion, independent of any client.

    Runs inside a detached task owned by ``RunManager``. Pumps every SSE
    event from ``_stream_chat_impl`` into the run's replay buffer / live
    subscribers via ``run.emit``. Because this task is not the HTTP
    response, a client disconnect cannot cancel it — the producer finishes
    and persists server-side (#1580 follow-up).
    """
    async for data in _stream_chat_impl(run, db, session, request):
        run.emit(data)
    # Notify the AgentLoop that a user turn completed (best-effort; no-op if
    # the autonomy loop isn't running). Fires on real run completion rather
    # than on subscriber detach.
    try:
        from gaia.ui.agent_loop import agent_loop

        agent_loop.notify_user_message(request.session_id)
    except Exception:  # pylint: disable=broad-except
        pass


async def _stream_chat_response(db: ChatDatabase, session: dict, request: ChatRequest):
    """Stream a chat turn as SSE for the ``/api/chat/send`` client.

    Starts the run's detached lifecycle (the chat router guarantees no run
    is already active for this session — it returns 409 otherwise) and
    subscribes this HTTP connection to it. Yields the replay buffer (empty
    for a fresh run) followed by live events until the run completes or the
    client disconnects. Disconnecting only detaches this subscriber; the
    run keeps going in the background.
    """
    from gaia.ui.run_manager import DONE, run_manager

    session_id = request.session_id
    run = run_manager.get(session_id)
    if run is None:
        run = run_manager.start(
            session_id,
            lambda r: _run_chat_lifecycle(r, db, session, request),
        )
    q = run.subscribe()
    try:
        while True:
            item = await q.get()
            if item is DONE:
                break
            yield item
    finally:
        run.unsubscribe(q)


async def _attach_chat_stream(session_id: str):
    """Re-attach an SSE client to an already-running background run (#1580).

    Used by ``GET /api/chat/attach`` when the user revisits a session whose
    turn is still in flight. Replays everything emitted so far, then streams
    live events to completion. The caller is responsible for returning 404
    when no run is active.
    """
    from gaia.ui.run_manager import DONE, run_manager

    run = run_manager.get(session_id)
    if run is None:
        return
    q = run.subscribe()
    try:
        while True:
            item = await q.get()
            if item is DONE:
                break
            yield item
    finally:
        run.unsubscribe(q)


# ── Document Indexing ────────────────────────────────────────────────────────


async def _index_document(filepath: Path) -> int:
    """Index a document using RAG SDK. Returns chunk count.

    Runs the synchronous RAG indexing in a thread pool executor
    to avoid blocking the async event loop.

    Note: A return value of 0 means RAG reported success but produced
    no chunks. Callers must treat 0 chunks as a failure condition.

    Raises:
        RuntimeError: If indexing fails for any reason.
    """

    def _do_index():
        from gaia.rag.sdk import RAGSDK, RAGConfig

        # Allow access to the file's directory (and user home) since the UI
        # explicitly selected this file via the file browser.
        allowed = [str(filepath.parent), str(Path.home())]
        config = RAGConfig(allowed_paths=allowed)
        rag = RAGSDK(config)
        result = rag.index_document(str(filepath))
        logger.info("RAG index_document result for %s: %s", filepath, result)

        if not isinstance(result, dict):
            raise RuntimeError(
                f"RAG returned unexpected type for {filepath.name}: "
                f"{type(result).__name__}"
            )

        error = result.get("error")
        if error:
            raise RuntimeError(f"RAG indexing failed for {filepath.name}: {error}")

        if not result.get("success"):
            raise RuntimeError(f"RAG indexing unsuccessful for {filepath.name}")

        chunks = result.get("num_chunks", 0) or result.get("chunk_count", 0)
        logger.info("Indexed %s: %d chunks", filepath, chunks)
        return chunks

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _do_index)
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to index {filepath.name}: {e}") from e
