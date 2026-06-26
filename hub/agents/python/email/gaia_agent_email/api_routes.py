# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
REST surface for the Email Triage Agent (#1229).

A consuming application invokes the email agent over HTTP through these
endpoints, mounted on the existing OpenAI-compatible FastAPI app
(``gaia.api.openai_server``):

    POST /v1/email/triage  — single email or full thread in (the FROZEN
                             #1262 contract), structured triage result out
                             (category, summary, action items, optional
                             draft proposal).
    POST /v1/email/draft   — propose a reply and obtain a single-use
                             confirmation token bound to the exact
                             ``(to, subject, body)`` payload.
    POST /v1/email/send    — send a reply. REJECTED with a 4xx unless a
                             valid confirmation token for *this* payload is
                             supplied. This is the send-confirmation gate
                             (#1264) translated to the API boundary — the
                             same rule the agent enforces via
                             ``TOOLS_REQUIRING_CONFIRMATION`` /
                             ``console.confirm_tool_execution``.

Design commitments
------------------
- **Reuse the frozen contract.** ``/v1/email/triage`` takes and returns the
  exact pydantic models from ``gaia_agent_email.contract`` — no parallel
  schema. A consuming app that drifts from the contract gets a 422.
- **Reuse the agent's real categorizer.** Category / spam / phishing come
  from ``triage_heuristics.classify_category_heuristic`` — the same
  deterministic fast-path the agent runs pre-LLM — not a reimplementation.
- **Fail loudly, never auto-confirm.** A send without a valid, payload-bound
  confirmation token raises a 403 with an actionable message. The token is
  single-use and bound to the payload, so a stale token cannot authorize a
  different message (no bait-and-switch).
- **No live mail in tests.** The send backend is a FastAPI dependency
  (:func:`get_send_backend`) so tests inject ``FakeGmailBackend`` via
  ``app.dependency_overrides``; production resolves the live Gmail backend
  and fails loudly if it is unavailable.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import threading
from typing import Any, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from gaia_agent_email.contract import (
    ActionItem,
    DraftReply,
    EmailAddress,
    EmailCategory,
    EmailMessage,
    EmailSearchRequest,
    EmailSearchResponse,
    EmailSearchResultItem,
    EmailTriageRequest,
    EmailTriageResponse,
    EmailTriageResult,
    SingleEmailInput,
    ThreadInput,
    TriageUsage,
)
from gaia_agent_email.tools.llm_triage import LLMTriageError
from gaia_agent_email.tools.summarize_tools import EmailSummarizeError
from gaia_agent_email.tools.triage_heuristics import (
    classify_category_heuristic,
    default_action_for,
)
from gaia_agent_email.version import AGENT_VERSION, API_VERSION
from pydantic import BaseModel, ConfigDict, Field

from gaia.connectors.api import connected_mailbox_providers
from gaia.connectors.errors import (
    AuthRequiredError,
    ConfigurationError,
    ConnectionRevokedError,
    ConnectorsError,
    ScopeMismatchError,
)
from gaia.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/v1/email", tags=["email"])


# ---------------------------------------------------------------------------
# Deterministic triage service
# ---------------------------------------------------------------------------

# Phrases that mark a line as an action the principal must take. Deliberately
# small and explicit — this is a deterministic extractor, not an LLM. The
# agent's LLM path produces richer action items; this fast-path covers the
# obvious imperative cues so the REST surface returns useful structure
# without a model round-trip.
_ACTION_CUES = (
    "please ",
    "can you ",
    "could you ",
    "kindly ",
    "let me know",
    "reply by",
    "respond by",
    "rsvp",
    "action required",
    "follow up",
    "follow-up",
    "review the",
    "send me",
    "send the",
    "confirm ",
)

# Crude due-date hints surfaced verbatim (never parsed — the contract's
# ``due_hint`` is explicitly free-text).
_DUE_HINT_RE = re.compile(
    r"\b(by\s+(?:eod|cob|tomorrow|today|monday|tuesday|wednesday|thursday|"
    r"friday|saturday|sunday|next\s+\w+|\d{1,2}(?::\d{2})?\s*(?:am|pm)?))",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_URL_RE = re.compile(r"https?://[^\s>\"']+", re.IGNORECASE)
_MAX_SUMMARY_CHARS = 300

# Fast pre-flight timeouts for the "is Lemonade even up?" probe (#1677). The
# real chat path uses a 900s scalar timeout — correct for long generation, but
# it also governs the TCP connect, so an unreachable server blocks on the OS
# SYN timeout (~30s) before erroring. A short connect timeout turns "server
# down" into a prompt 502 instead of a 30s hang.
_LEMONADE_PROBE_CONNECT_TIMEOUT = 2.0
_LEMONADE_PROBE_READ_TIMEOUT = 3.0


def _split_sentences(text: str) -> List[str]:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if not text:
        return []
    return [s.strip() for s in _SENTENCE_SPLIT_RE.split(text) if s.strip()]


def _aggregate_usage(call_stats: List[dict]) -> Optional[TriageUsage]:
    """Sum the per-call ``AgentResponse.stats`` (#1277/#1278) across the
    classify + summarize LLM calls into a single :class:`TriageUsage`.

    prompt_tokens = Σ input_tokens; total_tokens = Σ(input + output);
    tokens_per_second = Σ output / Σ decode_time, where each call's decode time
    is output_tokens / tokens_per_second (so the aggregate is total output
    tokens over total decode time, not a naive TPS average). Returns ``None``
    when no LLM call produced stats (the heuristic-only path).
    """
    if not call_stats:
        return None
    total_input = 0
    total_output = 0
    decode_output = 0  # output only from calls with a usable TPS (>0)
    total_decode_time = 0.0
    for s in call_stats:
        inp = int(s.get("input_tokens") or 0)
        out = int(s.get("output_tokens") or 0)
        tps = float(s.get("tokens_per_second") or 0.0)
        total_input += inp
        total_output += out
        if out and tps > 0:
            decode_output += out
            total_decode_time += out / tps
    # Numerator excludes output from tps==0 calls so they can't inflate the
    # aggregate (they add nothing to the decode-time denominator).
    agg_tps = decode_output / total_decode_time if total_decode_time > 0 else 0.0
    return TriageUsage(
        prompt_tokens=total_input,
        completion_tokens=total_output,
        total_tokens=total_input + total_output,
        tokens_per_second=agg_tps,
    )


class EmailTriageService:
    """Convert contract inputs (or raw Gmail-API messages) into a contract
    :class:`EmailTriageResult`.

    Triage always uses the local Lemonade LLM. High-confidence heuristic
    signals (spam, promotions) skip the LLM classify call as an internal
    optimisation, but the LLM is always used for summaries and for any
    message the heuristic cannot confidently classify.
    """

    # -- Public: contract path ---------------------------------------------

    def triage_request(
        self,
        request: EmailTriageRequest,
        chat: Optional[Any] = None,
    ) -> EmailTriageResponse:
        """Triage a contract request envelope into a contract response.

        Args:
            request: The frozen #1262 contract request.
            chat:    Pre-built chat client. When None a local Lemonade client
                     is constructed via :meth:`_build_llm_chat`.
        """
        payload = request.payload
        resolved_chat = chat or self._build_llm_chat()
        context = request.context
        if isinstance(payload, SingleEmailInput):
            kind = "single"
            result = self._triage_single_llm(payload, resolved_chat, context=context)
        elif isinstance(payload, ThreadInput):
            kind = "thread"
            result = self._triage_thread_llm(payload, resolved_chat, context=context)
        else:  # pragma: no cover - discriminated union guarantees one of the two
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported payload kind: {getattr(payload, 'kind', '?')!r}",
            )
        return EmailTriageResponse(request_kind=kind, result=result)

    def _build_llm_chat(self, base_url: Optional[str] = None) -> Any:
        """Build a local Lemonade chat client for LLM triage/summarise.

        Validates the AC3 local-only contract before constructing the client.
        Raises ``ConfigurationError`` loudly when ``base_url`` points at a
        cloud LLM — no silent fallback to heuristic.
        """
        from gaia_agent_email.config import EmailAgentConfig

        from gaia.chat.sdk import AgentConfig, AgentSDK

        cfg = EmailAgentConfig(base_url=base_url)
        cfg.validate()

        # Fail fast + loud if Lemonade isn't reachable, before the chat path's
        # long-timeout connect can stall ~30s (#1677).
        self._assert_lemonade_reachable(base_url)

        sdk_cfg = AgentConfig(
            base_url=base_url,
            use_local_llm=True,
            use_claude=False,
            use_chatgpt=False,
            # Output cap (not input); context window governs what fits.
            # 4096 gives Gemma-4-E4B room for its reasoning chain + JSON.
            max_tokens=4096,
            # Surface per-call token/TPS stats on AgentResponse.stats so the
            # triage result can report usage metrics (#1540) — reuses the
            # existing measurement; no new path.
            show_stats=True,
        )
        return AgentSDK(sdk_cfg)

    def _assert_lemonade_reachable(self, base_url: Optional[str]) -> None:
        """Probe Lemonade's /health with a short connect timeout (#1677).

        Raises ``LLMTriageError`` (→ HTTP 502 at the route) when the local
        server can't be reached, so "Lemonade is down" surfaces as a prompt,
        actionable failure instead of a ~30s hang. Any HTTP response — even
        an error status — means the server is up; only a connection/timeout
        failure counts as unreachable (auth/model errors surface later on the
        real chat call, where their messages are specific).
        """
        import requests

        from gaia.llm.lemonade_client import _get_lemonade_config

        if base_url:
            probe_base = base_url.rstrip("/")
            if not probe_base.endswith("/api/v1"):
                probe_base = f"{probe_base}/api/v1"
        else:
            _, _, probe_base = _get_lemonade_config()
        health_url = f"{probe_base}/health"

        try:
            requests.get(
                health_url,
                timeout=(
                    _LEMONADE_PROBE_CONNECT_TIMEOUT,
                    _LEMONADE_PROBE_READ_TIMEOUT,
                ),
            )
        except requests.exceptions.RequestException as exc:
            raise LLMTriageError(
                f"Local Lemonade Server is not reachable at {probe_base} "
                f"({type(exc).__name__}: {exc}). Start it with "
                "`lemonade-server serve` (or run `gaia init`), then retry."
            ) from exc

    def triage_gmail_message(
        self, msg: dict, *, principal_email: str
    ) -> EmailTriageResult:
        """Triage a Gmail-API-shaped message (e.g. from ``get_message_impl``).

        Used by the agent-pipeline e2e to assert the REST surface's
        summarizer agrees with what the agent's read tools fetch.
        """
        from gaia_agent_email.gmail_backend import decode_message_body

        payload = msg.get("payload") or {}
        headers = {
            (h.get("name") or "").lower(): h.get("value", "")
            for h in payload.get("headers", [])
        }
        body, _attachments = decode_message_body(payload)
        subject = headers.get("subject", "")
        sender = headers.get("from", "")
        label_ids = list(msg.get("labelIds", []))
        principal = EmailAddress(email=principal_email)
        return self._build_result(
            subject=subject,
            sender_raw=sender,
            body=body,
            label_ids=label_ids,
            principal=principal,
            reply_to=_parse_address(sender),
        )

    def _triage_single_llm(
        self, payload: SingleEmailInput, chat: Any, context: Any = None
    ) -> EmailTriageResult:
        msg = payload.message
        return self._build_result_llm(
            subject=msg.subject,
            sender_raw=_format_address(msg.from_),
            body=msg.body,
            label_ids=[],
            principal=payload.principal,
            reply_to=msg.from_,
            chat=chat,
            message_id=msg.message_id,
            context=context,
        )

    def _triage_thread_llm(
        self, payload: ThreadInput, chat: Any, context: Any = None
    ) -> EmailTriageResult:
        messages: List[EmailMessage] = payload.messages
        last = messages[-1]
        # Join newest-first so the model sees the most recent context first.
        combined_body = "\n\n".join(
            f"{_format_address(m.from_)}: {m.body}" for m in reversed(messages)
        )
        return self._build_result_llm(
            subject=last.subject,
            sender_raw=_format_address(last.from_),
            body=combined_body,
            label_ids=[],
            principal=payload.principal,
            reply_to=last.from_,
            summary_prefix=f"Thread of {len(messages)} messages. ",
            chat=chat,
            message_id=payload.thread_id,
            context=context,
        )

    def _build_result_llm(
        self,
        *,
        subject: str,
        sender_raw: str,
        body: str,
        label_ids: List[str],
        principal: EmailAddress,
        reply_to: Optional[EmailAddress],
        summary_prefix: str = "",
        chat: Any,
        message_id: Optional[str] = None,
        context: Any = None,
    ) -> EmailTriageResult:
        """Build a result using LLM escalation when heuristic confidence is low."""
        from gaia_agent_email.tools.llm_triage import classify_email_llm
        from gaia_agent_email.tools.summarize_tools import summarize_email_llm

        heuristic = classify_category_heuristic(
            subject=subject, sender=sender_raw, label_ids=label_ids
        )

        # Per-call LLM stats accumulate here so the result can report aggregate
        # usage (#1540). Reuses AgentResponse.stats — no new measurement.
        call_stats: List[dict] = []

        if heuristic.confident:
            category = EmailCategory(heuristic.category)
        else:
            llm_result = classify_email_llm(
                chat,
                subject=subject,
                sender=sender_raw,
                body=body,
                collect_stats=call_stats,
                context=context,
            )
            category = EmailCategory(llm_result["category"])

        llm_summary = summarize_email_llm(
            chat,
            subject=subject,
            sender=sender_raw,
            body=body,
            collect_stats=call_stats,
            context=context,
        )
        summary = summary_prefix + llm_summary

        action_items = self._extract_action_items(body)
        draft = self._build_draft(
            subject=subject,
            reply_to=reply_to,
            principal=principal,
            is_spam=heuristic.is_spam,
            is_phishing=heuristic.is_phishing,
        )
        suggested_action = (
            llm_result.get("suggested_action")
            if not heuristic.confident
            else default_action_for(category.value)
        ) or default_action_for(category.value)
        return EmailTriageResult(
            category=category,
            is_spam=heuristic.is_spam,
            is_phishing=heuristic.is_phishing,
            summary=summary,
            action_items=action_items,
            draft=draft,
            message_id=message_id,
            suggested_action=suggested_action,
            usage=_aggregate_usage(call_stats),
        )

    def _build_result(
        self,
        *,
        subject: str,
        sender_raw: str,
        body: str,
        label_ids: List[str],
        principal: EmailAddress,
        reply_to: Optional[EmailAddress],
        summary_prefix: str = "",
        message_id: Optional[str] = None,
    ) -> EmailTriageResult:
        heuristic = classify_category_heuristic(
            subject=subject, sender=sender_raw, label_ids=label_ids
        )
        category = EmailCategory(heuristic.category)
        summary = summary_prefix + self._summarize(subject, body)
        action_items = self._extract_action_items(body)
        draft = self._build_draft(
            subject=subject,
            reply_to=reply_to,
            principal=principal,
            is_spam=heuristic.is_spam,
            is_phishing=heuristic.is_phishing,
        )
        suggested_action = default_action_for(category.value)
        return EmailTriageResult(
            category=category,
            is_spam=heuristic.is_spam,
            is_phishing=heuristic.is_phishing,
            summary=summary,
            action_items=action_items,
            draft=draft,
            message_id=message_id,
            suggested_action=suggested_action,
        )

    def _summarize(self, subject: str, body: str) -> str:
        sentences = _split_sentences(body)
        lead = " ".join(sentences[:2]) if sentences else ""
        if subject and lead:
            summary = f"{subject.strip()} — {lead}"
        elif subject:
            summary = subject.strip()
        elif lead:
            summary = lead
        else:
            summary = "(no content)"
        if len(summary) > _MAX_SUMMARY_CHARS:
            summary = summary[: _MAX_SUMMARY_CHARS - 1].rstrip() + "…"
        return summary

    def _extract_action_items(self, body: str) -> List[ActionItem]:
        items: List[ActionItem] = []
        seen: set[str] = set()
        for sentence in _split_sentences(body):
            low = sentence.lower()
            if not any(cue in low for cue in _ACTION_CUES):
                continue
            normalized = sentence.strip()
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            due_match = _DUE_HINT_RE.search(sentence)
            due_hint = due_match.group(1) if due_match else None
            url_match = _URL_RE.search(sentence)
            if url_match:
                # Trim trailing sentence punctuation the greedy match grabs
                # ("...report." → "...report") so the link is well-formed.
                # Strip char-by-char, but keep a ")" that closes a "(" inside
                # the URL itself (e.g. .../Python_(programming_language)) so we
                # don't silently truncate Wikipedia/Confluence-style links.
                url = url_match.group(0)
                _trailing = ".,;:!?)]}\"'"
                while url and url[-1] in _trailing:
                    if url[-1] == ")" and "(" in url:
                        break
                    url = url[:-1]
                items.append(
                    ActionItem(
                        description=normalized,
                        due_hint=due_hint,
                        type="link",
                        url=url,
                    )
                )
            else:
                items.append(ActionItem(description=normalized, due_hint=due_hint))
        return items

    def _build_draft(
        self,
        *,
        subject: str,
        reply_to: Optional[EmailAddress],
        principal: EmailAddress,
        is_spam: bool,
        is_phishing: bool,
    ) -> Optional[DraftReply]:
        # Never propose a reply to spam/phishing — surfacing a draft would
        # nudge the user toward engaging with a hostile sender.
        if is_spam or is_phishing or reply_to is None:
            return None
        # Don't propose replying to yourself (e.g. a thread whose last message
        # the principal sent) — there is no one to reply to.
        if reply_to.email.lower() == principal.email.lower():
            return None
        reply_subject = subject.strip()
        if reply_subject and not reply_subject.lower().startswith("re:"):
            reply_subject = f"Re: {reply_subject}"
        elif not reply_subject:
            reply_subject = "Re:"
        return DraftReply(
            to=[reply_to],
            subject=reply_subject,
            body="",  # proposal scaffold; the user/agent fills the body in
        )


def _format_address(addr: EmailAddress) -> str:
    if addr.name:
        return f"{addr.name} <{addr.email}>"
    return addr.email


def _parse_address(raw: str) -> Optional[EmailAddress]:
    """Parse a raw ``From`` header (``"Alice <a@b.com>"``) into an
    :class:`EmailAddress`. Returns None when no plausible address is found.
    """
    raw = (raw or "").strip()
    if not raw:
        return None
    m = re.search(r"<([^>]+)>", raw)
    if m:
        email = m.group(1).strip()
        name = raw[: m.start()].strip().strip('"') or None
    else:
        email = raw
        name = None
    try:
        return EmailAddress(name=name, email=email)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Confirmation-token store (the send-confirmation gate)
# ---------------------------------------------------------------------------


def _payload_fingerprint(to: List[EmailAddress], subject: str, body: str) -> str:
    """A stable fingerprint of the exact message a token authorizes.

    Binding the token to the payload means a token issued for one message
    cannot be replayed to send different content.
    """
    recipients = ",".join(sorted(a.email.lower() for a in to))
    material = "\x1f".join([recipients, subject, body])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class ConfirmationStore:
    """Single-use confirmation tokens bound to a message fingerprint.

    A token is minted by the draft endpoint and consumed by the send
    endpoint. Consuming a token removes it (single-use). The server-side
    secret makes tokens unforgeable; the fingerprint makes them payload-
    specific.

    Tokens may optionally carry a provider binding (D5): when the draft
    specified ``provider="microsoft"``, the token stores that binding so the
    send handler routes to the correct backend even when multiple mailboxes
    are connected.
    """

    def __init__(self, secret: Optional[bytes] = None):
        self._secret = secret or secrets.token_bytes(32)
        self._lock = threading.Lock()
        # token -> (fingerprint, provider_or_None)
        self._tokens: dict[str, tuple[str, Optional[str]]] = {}

    def issue(self, fingerprint: str, *, provider: Optional[str] = None) -> str:
        token = hmac.new(
            self._secret, (fingerprint + secrets.token_hex(8)).encode("utf-8"), "sha256"
        ).hexdigest()
        with self._lock:
            self._tokens[token] = (fingerprint, provider)
        return token

    def consume(self, token: str, fingerprint: str) -> bool:
        """Validate and consume a token for ``fingerprint``.

        Returns True only when the token was issued for exactly this
        payload. The token is removed on a successful match (single-use).
        A blank/unknown token, or a token issued for a different payload,
        returns False and is NOT consumed.
        """
        ok, _ = self.consume_with_provider(token, fingerprint)
        return ok

    def consume_with_provider(
        self, token: str, fingerprint: str
    ) -> tuple[bool, Optional[str]]:
        """Like ``consume`` but also returns the bound provider (or None).

        Returns ``(True, provider_or_None)`` on success; ``(False, None)`` on
        rejection. The provider is the value passed to ``issue(provider=...)``.
        """
        if not token:
            return False, None
        with self._lock:
            entry = self._tokens.get(token)
            if entry is None:
                return False, None
            expected_fp, bound_provider = entry
            if not hmac.compare_digest(expected_fp, fingerprint):
                # Right token, wrong payload — do not consume; reject.
                return False, None
            del self._tokens[token]
            return True, bound_provider


# Process-wide store. Tokens live only for the life of the server process —
# acceptable for a confirmation handshake (draft then send within a session).
confirmation_store = ConfirmationStore()


# ---------------------------------------------------------------------------
# Send-backend dependency (injectable for tests; fail-loud in production)
# ---------------------------------------------------------------------------


def get_send_backend():
    """Resolve the send backend from the connected OAuth mailbox.

    Production derives the backend from whichever mailbox the user connected
    via Settings → Connectors. Fails loudly when the count is ambiguous —
    never silently chooses or falls back:

      - 0 connected → HTTP 503 (actionable: go connect a mailbox)
      - 2+ connected → HTTP 400 (actionable: use the draft-token provider
        binding to specify which mailbox to send from)
      - exactly 1 → build the matching live backend

    IMPORTANT: invoked AFTER the confirmation gate, not as a FastAPI
    ``Depends``, so a gate rejection (403) always preempts a backend-health
    error (503/400).
    """
    providers = connected_mailbox_providers()
    if not providers:
        raise HTTPException(
            status_code=503,
            detail=(
                "No mailbox connected — connect Google or Microsoft in "
                "Settings → Connectors before sending."
            ),
        )
    if len(providers) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Multiple mailboxes connected ({', '.join(providers)}); "
                "the send API can't choose. Send from the agent/UI (sends "
                "from the message's mailbox), or include a draft confirmation "
                "token that binds the provider."
            ),
        )
    provider = providers[0]
    if provider == "google":
        from gaia_agent_email.gmail_backend import LiveGmailBackend, _get_gmail_token

        return LiveGmailBackend(_get_gmail_token)
    if provider == "microsoft":
        from gaia_agent_email.outlook_backend import (
            LiveOutlookBackend,
            _get_outlook_token,
        )

        return LiveOutlookBackend(_get_outlook_token)
    raise HTTPException(
        status_code=503,
        detail=(
            f"Connected mailbox provider '{provider}' has no send backend. "
            "Expected 'google' or 'microsoft'."
        ),
    )


# Module-level indirection the send handler calls after the gate. Tests swap
# this (e.g. ``monkeypatch.setattr(email_routes, "resolve_send_backend",
# lambda: FakeGmailBackend())``) to inject a fake without touching live mail.
# Default is the fail-loud live resolver above.
resolve_send_backend = get_send_backend


def _resolve_backend_for_provider(provider: Optional[str]):
    """Resolve a send backend for a specific provider.

    When a draft token carries a provider binding, send uses this helper
    instead of the count-based ``get_send_backend()``. Validates the provider
    is in the connected set before building the backend — fail loud if not.
    ``provider=None`` falls through to the count-based resolver.
    """
    if provider is None:
        return resolve_send_backend()
    connected = connected_mailbox_providers()
    if provider not in connected:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Mailbox '{provider}' is not connected. Connect it via "
                "Settings → Connectors, or omit the provider to use the "
                f"single connected mailbox. Connected: {connected or '(none)'}."
            ),
        )
    if provider == "google":
        from gaia_agent_email.gmail_backend import LiveGmailBackend, _get_gmail_token

        return LiveGmailBackend(_get_gmail_token)
    if provider == "microsoft":
        from gaia_agent_email.outlook_backend import (
            LiveOutlookBackend,
            _get_outlook_token,
        )

        return LiveOutlookBackend(_get_outlook_token)
    raise HTTPException(
        status_code=503,
        detail=(
            f"Provider '{provider}' has no send backend. "
            "Expected 'google' or 'microsoft'."
        ),
    )


# ---------------------------------------------------------------------------
# Search-backend dependency (injectable for tests; fail-loud in production)
# ---------------------------------------------------------------------------


def get_search_backend():
    """Resolve the read/search backend from the connected OAuth mailbox.

    Read-only mirror of :func:`get_send_backend`: inbox search lists messages
    from whichever mailbox the user connected via Settings → Connectors. Wired
    as a FastAPI ``Depends`` so the contract test injects a fake via
    ``app.dependency_overrides``; production fails loudly when the mailbox count
    is ambiguous — it never silently picks one:

      - 0 connected → HTTP 503 (actionable: go connect a mailbox)
      - 2+ connected → HTTP 400 (actionable: search can't choose which inbox)
      - exactly 1 → build the matching live backend
    """
    providers = connected_mailbox_providers()
    if not providers:
        raise HTTPException(
            status_code=503,
            detail=(
                "No mailbox connected — connect Google or Microsoft in "
                "Settings → Connectors before searching the inbox."
            ),
        )
    if len(providers) > 1:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Multiple mailboxes connected ({', '.join(providers)}); the "
                "search API can't choose which inbox to search. Search from the "
                "agent/UI, or disconnect all but one mailbox."
            ),
        )
    provider = providers[0]
    if provider == "google":
        from gaia_agent_email.gmail_backend import LiveGmailBackend, _get_gmail_token

        return LiveGmailBackend(_get_gmail_token)
    if provider == "microsoft":
        from gaia_agent_email.outlook_backend import (
            LiveOutlookBackend,
            _get_outlook_token,
        )

        return LiveOutlookBackend(_get_outlook_token)
    raise HTTPException(
        status_code=503,
        detail=(
            f"Connected mailbox provider '{provider}' has no search backend. "
            "Expected 'google' or 'microsoft'."
        ),
    )


def _search_inbox(
    backend: Any,
    *,
    query: Optional[str],
    labels: Optional[List[str]],
    max_results: int,
    page_token: Optional[str] = None,
) -> EmailSearchResponse:
    """List/search mailbox messages and hydrate each into inbox-list metadata.

    Reuses the agent's ``_format_message_for_llm`` so the headers the REST
    surface returns match exactly what the in-loop search tool surfaces — no
    parallel header-parsing to drift. The body is intentionally dropped: this is
    a list view, not a read. Imported lazily so the OpenAPI export stays
    dependency-light (it never pulls the live-mail machinery).
    """
    from gaia_agent_email.tools.read_tools import _format_message_for_llm

    listing = backend.list_messages(
        query=query,
        label_ids=labels,
        max_results=max_results,
        page_token=page_token,
    )
    items: List[EmailSearchResultItem] = []
    for stub in listing.get("messages", []):
        msg = backend.get_message(stub["id"])
        formatted = _format_message_for_llm(msg)
        items.append(
            EmailSearchResultItem(
                id=formatted["id"],
                thread_id=formatted["thread_id"],
                subject=formatted["subject"],
                from_=formatted["from"],
                to=formatted["to"],
                date=formatted["date"],
                snippet=formatted["snippet"],
                label_ids=formatted["label_ids"],
            )
        )
    return EmailSearchResponse(
        query=query,
        count=len(items),
        messages=items,
        next_page_token=listing.get("nextPageToken"),
    )


# ---------------------------------------------------------------------------
# Send / draft request & response models (LOCAL — contract.py is frozen and
# triage-only; the send handshake is not part of the #1262 contract).
# ---------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(_Strict):
    """Liveness/readiness probe for the email surface.

    Dependency-light by design: it never touches a live mailbox or the LLM, so a
    host can confirm the router is mounted and serving before any connector or
    model is configured.
    """

    status: Literal["ok"] = Field(
        default="ok", description="Always 'ok' when the surface is serving."
    )
    service: Literal["gaia-agent-email"] = Field(
        default="gaia-agent-email", description="Stable service identifier."
    )


class VersionResponse(_Strict):
    """The two version numbers a host negotiates against.

    ``apiVersion`` is the frozen REST/contract version (a contract bump bumps it);
    ``agentVersion`` is the package build. Both come from
    ``gaia_agent_email.version`` so this endpoint and the freeze server's
    ``/version`` report identical values.
    """

    apiVersion: str = Field(
        ..., description="REST/contract version (contract.SCHEMA_VERSION)."
    )
    agentVersion: str = Field(..., description="Package build version.")


class EmailDraftRequest(_Strict):
    """Propose a reply and obtain a confirmation token for it."""

    to: List[EmailAddress] = Field(
        ..., min_length=1, description="Proposed recipients (non-empty)."
    )
    subject: str = Field(..., description="Proposed subject line.")
    body: str = Field(..., description="Proposed reply body.")
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Optional provider binding ('google' or 'microsoft'). When set, "
            "the confirmation token is bound to this provider so the send "
            "routes to the correct mailbox even when multiple are connected."
        ),
    )


class EmailDraftResponse(_Strict):
    """The proposed reply plus a single-use confirmation token bound to it."""

    draft: DraftReply = Field(
        ..., description="The proposed reply (to / subject / body)."
    )
    confirmation_token: str = Field(
        ...,
        description=(
            "Echo this back to POST /v1/email/send to authorize sending "
            "exactly this payload. Single-use; bound to (to, subject, body)."
        ),
    )


class EmailSendRequest(_Strict):
    """Send a reply. Requires a valid confirmation token for this payload."""

    to: List[EmailAddress] = Field(
        ..., min_length=1, description="Recipients (non-empty)."
    )
    subject: str = Field(..., description="Subject line.")
    body: str = Field(..., description="Reply body.")
    confirmation_token: Optional[str] = Field(
        default=None,
        description=(
            "Confirmation token from POST /v1/email/draft. A send without a "
            "valid token for this exact payload is rejected (403)."
        ),
    )
    provider: Optional[str] = Field(
        default=None,
        description=(
            "Optional provider ('google' or 'microsoft'), used ONLY as the "
            "fallback when the confirmation token carries no provider binding. "
            "A token's bound provider always wins; with two mailboxes connected "
            "and neither a binding nor this field set, the send is rejected as "
            "ambiguous (400)."
        ),
    )


class EmailSendResponse(_Strict):
    sent_id: str = Field(..., description="Provider message id of the sent email.")
    to: List[EmailAddress] = Field(
        ..., description="Recipients the message was sent to."
    )
    subject: str = Field(..., description="Subject of the sent message.")
    sent: bool = Field(default=True, description="Always true on success.")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_service = EmailTriageService()


@router.post("/triage", response_model=EmailTriageResponse)
async def triage_email(request: EmailTriageRequest) -> EmailTriageResponse:
    """Triage a single email or a full thread.

    Accepts the FROZEN #1262 ``EmailTriageRequest`` (a single-email or
    thread payload, discriminated on ``kind``) and returns the structured
    ``EmailTriageResponse`` — category, spam/phishing signals, a plain-text
    summary, extracted action items, and an optional draft-reply proposal.
    No mail is read or sent; this analyzes only the payload in the request.
    """
    try:
        return await asyncio.to_thread(_service.triage_request, request)
    except (LLMTriageError, EmailSummarizeError) as e:
        raise HTTPException(
            status_code=502, detail=f"local LLM triage failed: {e}"
        ) from e


@router.post("/search", response_model=EmailSearchResponse)
async def search_inbox(
    request: EmailSearchRequest,
    backend: Any = Depends(get_search_backend),
) -> EmailSearchResponse:
    """Search the connected mailbox (read-only) — #1781.

    Lists messages matching ``query``/``labels`` from the connected Gmail or
    Outlook mailbox and returns inbox-list metadata (id, thread, subject, from,
    to, date, snippet, labels) — not the message body. No mail is sent or
    modified. This restores the agent's in-loop inbox-search capability on the
    REST contract so the Agent UI can drive it through the package.

    The mailbox is resolved by :func:`get_search_backend` (fail-loud on 0 or
    ambiguous mailbox counts). Backend auth / config / transport errors surface
    as actionable 4xx/5xx, never a silent empty result.
    """
    try:
        return await asyncio.to_thread(
            _search_inbox,
            backend,
            query=request.query,
            labels=request.labels,
            max_results=request.max_results,
            page_token=request.page_token,
        )
    except (AuthRequiredError, ScopeMismatchError, ConnectionRevokedError) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConfigurationError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ConnectorsError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@router.post("/draft", response_model=EmailDraftResponse)
async def draft_reply(request: EmailDraftRequest) -> EmailDraftResponse:
    """Propose a reply and mint a confirmation token bound to its payload.

    The returned token must be echoed to :func:`send_email` to authorize
    sending exactly this ``(to, subject, body)``. This is the explicit
    user-confirmation step — the consuming app surfaces the draft to the
    user, and only a user-approved send echoes the token back.
    """
    fingerprint = _payload_fingerprint(request.to, request.subject, request.body)
    token = confirmation_store.issue(fingerprint, provider=request.provider)
    draft = DraftReply(to=request.to, subject=request.subject, body=request.body)
    return EmailDraftResponse(draft=draft, confirmation_token=token)


@router.post("/send", response_model=EmailSendResponse)
async def send_email(request: EmailSendRequest) -> EmailSendResponse:
    """Send a reply — gated on explicit confirmation (#1264).

    The confirmation gate is enforced FIRST: a request without a valid,
    payload-bound confirmation token is rejected with HTTP 403 before any
    backend call (or even backend resolution). This mirrors the agent's
    ``TOOLS_REQUIRING_CONFIRMATION`` guard, translated to the API boundary,
    and guarantees the gate fires regardless of backend health. Never
    auto-confirms.
    """
    fingerprint = _payload_fingerprint(request.to, request.subject, request.body)
    gate_ok, bound_provider = confirmation_store.consume_with_provider(
        request.confirmation_token or "", fingerprint
    )
    if not gate_ok:
        raise HTTPException(
            status_code=403,
            detail=(
                "Send rejected: missing or invalid confirmation token for this "
                "message. Call POST /v1/email/draft to obtain a confirmation "
                "token bound to this exact (to, subject, body), then echo it in "
                "'confirmation_token'. Emails are never sent without explicit "
                "confirmation."
            ),
        )

    # Gate passed — resolve the backend now (AFTER the gate) and send. Gmail's
    # send takes a single 'to' header string. Run off the event loop so
    # get_access_token_sync (used inside the token resolvers) does not hit
    # the "called from a thread with a running event loop" guard (#1594).
    # Provider precedence: the token's bound provider (D5) always wins; only an
    # unbound token falls back to request.provider; with neither, the
    # count-based resolver decides (and 400s when 2+ are connected).
    try:
        backend = _resolve_backend_for_provider(bound_provider or request.provider)
        to_header = ", ".join(_format_address(a) for a in request.to)
        result = await asyncio.to_thread(
            backend.send_message,
            to=to_header,
            subject=request.subject,
            body=request.body,
        )
    except (AuthRequiredError, ScopeMismatchError, ConnectionRevokedError) as e:
        raise HTTPException(status_code=403, detail=str(e)) from e
    except ConfigurationError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    except ConnectorsError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    sent_id = result.get("id") or ""
    # Graph sendMail returns 202 with no body → no id, but result["sent"]=True
    # signals a successful send. Gmail raises on failure, so no-id + no-sent
    # is an unknown failure state that we still reject loudly.
    if not sent_id and not result.get("sent"):
        raise HTTPException(
            status_code=502,
            detail="Email backend did not return a message id for the send.",
        )
    logger.info("email send: id=%s to=%s", sent_id, to_header)
    return EmailSendResponse(sent_id=sent_id, to=request.to, subject=request.subject)


@router.get("/health", response_model=HealthResponse)
async def email_health() -> HealthResponse:
    """Report that the email REST surface is mounted and serving.

    Dependency-light: no live mailbox, no LLM. A host uses this for the sidecar
    readiness handshake and for liveness checks once mounted on the product app.
    """
    return HealthResponse()


@router.get("/version", response_model=VersionResponse)
async def email_version() -> VersionResponse:
    """Report the REST/contract version and the package build version.

    Both values come from ``gaia_agent_email.version`` — the same constants the
    freeze server's root ``/version`` reads — so the product surface and the
    frozen binary can never disagree on what contract they speak.
    """
    return VersionResponse(apiVersion=API_VERSION, agentVersion=AGENT_VERSION)


@router.get("/spec", response_class=HTMLResponse, include_in_schema=False)
async def email_spec() -> HTMLResponse:
    """Serve the self-contained HTML endpoint spec page.

    Not included in the OpenAPI schema — this is a human-readable convenience
    page, not a machine-readable contract endpoint.
    """
    from gaia_agent_email.spec_html import render_endpoint_spec_html

    return HTMLResponse(content=render_endpoint_spec_html())


# The local-only guarantee (#1796) is enforced HERE, not promised: connect-src
# 'self' makes the browser refuse any non-local fetch, so the page can only ever
# reach this sidecar. Served same-origin → no CORS, no remote-controlled code.
_PLAYGROUND_CSP = (
    "default-src 'none'; "
    "connect-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'none'"
)


@router.get("/playground", response_class=HTMLResponse, include_in_schema=False)
async def email_playground() -> HTMLResponse:
    """Serve the self-contained, localhost-only playground page (#1796).

    A GAIA-styled health-check + endpoint playground that runs entirely against
    this local sidecar. Excluded from the OpenAPI schema — it's a human page, not
    a contract endpoint. The CSP header makes "data never leaves the box" a
    structural guarantee rather than a promise.
    """
    from gaia_agent_email.playground_html import render_playground_html

    return HTMLResponse(
        content=render_playground_html(),
        headers={"Content-Security-Policy": _PLAYGROUND_CSP},
    )


__all__ = [
    "router",
    "EmailTriageService",
    "ConfirmationStore",
    "confirmation_store",
    "get_send_backend",
    "get_search_backend",
    "EmailDraftRequest",
    "EmailDraftResponse",
    "EmailSendRequest",
    "EmailSendResponse",
    "HealthResponse",
    "VersionResponse",
    # Shared formatting helpers reused by the MCP surface.
    "_format_address",
    "_payload_fingerprint",
]
