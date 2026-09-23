# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Forward UI calls to a running daemon sidecar over HTTP.

Two layers, because two agents now share this transport (#4161):

- :class:`SidecarProxy` — the agent-agnostic part of the frozen wire contract
  every sidecar serves under ``/v1/<agent_id>/``: readiness ``init``, the
  canonical streaming ``query`` loop with its cancel/respond verbs, and
  ``health``/``version``. Nothing here knows what the agent *does*.
- :class:`EmailSidecarProxy` — email's own schema-2.1 surface on top (triage,
  batch triage, search, inbox pre-scan, draft/send + confirm,
  archive/unarchive, quarantine/unquarantine, calendar view/preview/create/
  respond, streamed provisioning). Returns the sidecar's envelopes
  **unchanged** so the existing SSE card pipeline (``pre_scan_inbox`` →
  ``email_pre_scan``) keeps working byte-for-byte.

Keeping the split means the flagship's proxy cannot advertise a mailbox route
it would only 404 on.

A non-2xx is translated loudly into :class:`SidecarHTTPError`, which carries the
sidecar's own actionable ``detail`` (e.g. ``502 local LLM triage failed: …``)
instead of a generic ``HTTPError`` — no fallback, no swallowed error. The
sidecar's connector OAuth *write* routes are deliberately NOT proxied here: all
connector writes stay on the Python backend's single-writer path.
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Callable, Dict, Iterator, Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import SidecarError, SidecarHTTPError

logger = get_logger(__name__)

# POST /init can stay silent for the whole model pull — the read timeout must
# outlast the sidecar's own 30-min pull read timeout (_LEMONADE_PULL_TIMEOUT).
_PROVISION_READ_TIMEOUT = 1830.0

# /query connect timeout: fixed and short — a dead sidecar should fail fast on
# TCP connect, independent of the (much longer) read_timeout that spans the
# whole agent-loop run.
_QUERY_CONNECT_TIMEOUT = 10.0

# Cancel POST timeout: best-effort cleanup must never wait out the general
# sidecar timeout — a hung sidecar would make Cancel as slow as not cancelling.
_CANCEL_TIMEOUT = 10.0


def _extract_detail(resp) -> str:
    """Pull the sidecar's actionable message from a non-2xx response.

    FastAPI puts the actionable text under ``detail``; fall back to the raw body
    so nothing is ever swallowed.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - non-JSON body, use text below
        body = None
    if isinstance(body, dict) and body.get("detail"):
        detail = body["detail"]
        return detail if isinstance(detail, str) else str(detail)
    text = getattr(resp, "text", "") or ""
    return text.strip() or f"(no response body; HTTP {resp.status_code})"


def _timeout_env_var(agent_id: str) -> str:
    """The per-agent request-timeout override, e.g. ``GAIA_EMAIL_SIDECAR_TIMEOUT``.

    Derived rather than listed so a new sidecar gets the same knob for free —
    and matches the token env vars the daemon spec already names this way
    (``GAIA_<AGENT>_SIDECAR_TOKEN``).
    """
    return f"GAIA_{agent_id.upper()}_SIDECAR_TIMEOUT"


class SidecarProxy:
    """HTTP transport onto one running sidecar's ``/v1/<agent_id>/`` contract."""

    def __init__(
        self,
        base_url: str,
        *,
        agent_id: str = "email",
        session=None,
        timeout: float | None = None,
        auth_token: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.prefix = f"/v1/{agent_id}"
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.environ.get(_timeout_env_var(agent_id), "300"))
        )
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        # Per-session caller-auth token (#1706) replayed as a bearer header on
        # every request so the sidecar accepts UI-originated calls; None only in
        # tests / when talking to a sidecar started without auth.
        self._auth_token = auth_token
        if auth_token:
            self._session.headers.update({"Authorization": f"Bearer {auth_token}"})

    def _raise_for_status(self, resp, path: str) -> None:
        # Translate the boundary loudly: keep the sidecar's own actionable detail
        # (e.g. "502 local LLM triage failed: ...") instead of a generic HTTPError.
        if resp.status_code >= 400:
            raise SidecarHTTPError(
                resp.status_code,
                _extract_detail(resp),
                path=path,
                agent_id=self.agent_id,
            )

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._session.post(
            f"{self.base_url}{path}", json=payload, timeout=self.timeout
        )
        self._raise_for_status(resp, path)
        return resp.json()

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> dict:
        resp = self._session.get(
            f"{self.base_url}{path}", params=params, timeout=self.timeout
        )
        self._raise_for_status(resp, path)
        return resp.json()

    # -- Health / version / readiness ----------------------------------------
    def health(self) -> dict:
        return self._get("/health")

    def version(self) -> dict:
        return self._get("/version")

    def init(self) -> tuple:
        """Readiness preflight — returns ``(status_code, body)``, never raises on 503.

        ``GET /v1/<agent>/init`` answers 200 (ready) or 503 (not ready) with the
        same ``InitResponse`` body either way — the 503 is contract, not a
        transport failure, so both must pass through verbatim instead of being
        flattened into a ``SidecarHTTPError`` detail string. Both shipped
        sidecars answer this shape deliberately (see the flagship's ``/init``,
        which cites the email contract it mirrors).
        """
        path = f"{self.prefix}/init"
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        if resp.status_code not in (200, 503):
            self._raise_for_status(resp, path)
        return resp.status_code, resp.json()

    # -- Query (canonical streaming agent-loop, #2109) -----------------------
    def respond_query(self, run_id: str, request_id: str, value: str) -> dict:
        """Deliver an answer to a pending ``needs_input`` question (#2595).

        Loud on failure: a 409 means the question is no longer the one the
        run is waiting on (already answered, timed out, or superseded) and
        must reach the caller as an error, never be swallowed.
        """
        return self._post(
            f"{self.prefix}/query/{run_id}/respond",
            {"request_id": request_id, "value": value},
        )

    def query_stream(
        self,
        body: dict,
        *,
        read_timeout: float = 300.0,
        on_response: Optional[Callable[[Any], None]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Stream ``POST /v1/<agent>/query`` as parsed canonical SSE event dicts.

        One ``data: <json>\\n\\n`` frame per event (no multi-line data), so
        ``resp.iter_lines()`` is safe: blank keep-alive/separator lines are
        skipped, non-``data:`` lines are ignored, and each ``data:`` payload is
        JSON-parsed and yielded. A non-2xx status raises the same loud
        :class:`SidecarHTTPError` boundary as every other proxy method, BEFORE
        any event is yielded — the response is closed either way.

        ``on_response`` — invoked with the live (still-open) response object
        EAGERLY at call time, right after the POST returns and before any
        line is consumed, so a caller holding the returned iterator on a
        worker thread can hand the response to another thread (the cancel
        path), which forces a blocked read to error out by calling
        ``resp.close()`` on it. This method is deliberately NOT a generator
        function: a lazy body would defer both the POST and the registration
        to the first ``next()``, widening the early-cancel race in which a
        cancel finds nothing registered to close.

        A malformed ``data:`` line (the sidecar emitting non-JSON) raises
        loudly — never silently dropped or swallowed into a placeholder event.
        """
        path = f"{self.prefix}/query"
        resp = self._session.post(
            f"{self.base_url}{path}",
            json=body,
            stream=True,
            timeout=(_QUERY_CONNECT_TIMEOUT, read_timeout),
        )
        if resp.status_code >= 400:
            try:
                self._raise_for_status(resp, path)
            finally:
                resp.close()
        if on_response is not None:
            on_response(resp)

        agent_id = self.agent_id

        def _events() -> Iterator[Dict[str, Any]]:
            try:
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8", "replace")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload:
                        continue
                    try:
                        event = _json.loads(payload)
                    except (ValueError, TypeError) as e:
                        raise SidecarError(
                            f"{agent_id} sidecar /query sent a malformed SSE "
                            f"event: {payload!r} ({e})"
                        ) from e
                    yield event
            finally:
                resp.close()

        return _events()

    def cancel_query(self, run_id: str) -> None:
        """Cancel an in-flight ``/query`` run.

        Best-effort by design: the caller's forced response close has already
        unblocked the relay's reader, so this POST uses a short dedicated
        timeout (never the general 300s sidecar timeout) and swallows
        transport failures with a log — a hung or dead sidecar can't honour
        a cancel anyway, and surfacing a transport error AFTER the user
        cancelled would be noise, not signal.

        A 404 means the run already finished by the time the cancel landed —
        benign, log and return rather than raise (mirrors the sidecar's own
        ``cancel_query`` route contract). Any other non-2xx keeps the loud
        :class:`SidecarHTTPError` boundary.
        """
        import requests

        path = f"{self.prefix}/query/{run_id}/cancel"
        try:
            resp = self._session.post(f"{self.base_url}{path}", timeout=_CANCEL_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "%s sidecar: best-effort cancel for run_id=%s failed at "
                "transport level (reader already unblocked by forced close): %s",
                self.agent_id,
                run_id,
                exc,
            )
            return
        if resp.status_code == 404:
            logger.info(
                "%s sidecar: cancel for run_id=%s: no longer in flight (404)",
                self.agent_id,
                run_id,
            )
            return
        self._raise_for_status(resp, path)


class EmailSidecarProxy(SidecarProxy):
    """:class:`SidecarProxy` plus email's own ``/v1/email/*`` schema-2.1 routes."""

    def __init__(self, base_url: str, **kwargs):
        # The mailbox routes below are hardcoded /v1/email, so aiming this
        # class at another agent could only produce 404s. Refuse rather than
        # silently ignore what the caller asked for.
        if kwargs.pop("agent_id", "email") != "email":
            raise TypeError(
                "EmailSidecarProxy is email-only; use "
                "SidecarProxy(base_url, agent_id=...) for another agent."
            )
        super().__init__(base_url, agent_id="email", **kwargs)

    # -- Triage -------------------------------------------------------------
    def triage(self, payload: dict) -> dict:
        return self._post("/v1/email/triage", payload)

    def triage_batch(self, payload: dict) -> dict:
        return self._post("/v1/email/triage/batch", payload)

    # -- Inbox read (search + pre-scan) -------------------------------------
    def search_inbox(self, payload: dict) -> dict:
        return self._post("/v1/email/search", payload)

    def pre_scan_inbox(self, payload: dict) -> dict:
        """Forward an inbox pre-scan to the sidecar's ``/prescan`` route.

        Returns the sidecar's ``EmailPreScanResponse`` envelope unchanged
        (``{"result": {"kind": "email_pre_scan", …}}``). The chat tool reshapes
        ``result`` into the ``email_pre_scan`` card envelope the SSE handler
        injects — the wire shape the renderer depends on is preserved here.
        """
        return self._post("/v1/email/prescan", payload)

    # -- Reply (draft + send, confirmation-gated) ---------------------------
    def draft(self, payload: dict) -> dict:
        return self._post("/v1/email/draft", payload)

    def send(self, payload: dict) -> dict:
        return self._post("/v1/email/send", payload)

    # -- Destructive mailbox actions (confirm-gated) + their undo -----------
    def confirm(self, payload: dict) -> dict:
        """Mint a single-use token for a destructive action (archive/quarantine)."""
        return self._post("/v1/email/confirm", payload)

    def archive(self, payload: dict) -> dict:
        return self._post("/v1/email/archive", payload)

    def unarchive(self, payload: dict) -> dict:
        return self._post("/v1/email/unarchive", payload)

    def quarantine(self, payload: dict) -> dict:
        return self._post("/v1/email/quarantine", payload)

    def unquarantine(self, payload: dict) -> dict:
        return self._post("/v1/email/unquarantine", payload)

    # -- Calendar -----------------------------------------------------------
    def calendar_events(self, params: Optional[Dict[str, Any]] = None) -> dict:
        """View calendar events (read-only). ``params`` → time_min/time_max/provider."""
        return self._get("/v1/email/calendar/events", params=params)

    def calendar_preview(self, payload: dict) -> dict:
        return self._post("/v1/email/calendar/events/preview", payload)

    def calendar_create(self, payload: dict) -> dict:
        return self._post("/v1/email/calendar/events", payload)

    def calendar_respond(self, payload: dict) -> dict:
        return self._post("/v1/email/calendar/events/respond", payload)

    def provision(self) -> tuple:
        """Provisioning verb — ``POST /v1/email/init``, streamed passthrough.

        Returns ``(status_code, media_type, chunk_iterator)``. The sidecar
        streams newline-delimited ``text/plain`` progress: a committed **200**
        whose final ``✓``/``✗`` line is the authoritative outcome, or a **503**
        (Lemonade unreachable) whose actionable lines are equally contract —
        both pass through verbatim. A model pull can take many minutes, so the
        body is never buffered; anything outside 200/503 keeps the loud
        :class:`SidecarHTTPError` boundary, raised before any chunk is yielded.

        Email-only: the flagship's ``/init`` is a read-only GET with no
        provisioning counterpart.
        """
        path = "/v1/email/init"
        resp = self._session.post(
            f"{self.base_url}{path}",
            stream=True,
            timeout=(self.timeout, max(self.timeout, _PROVISION_READ_TIMEOUT)),
        )
        if resp.status_code not in (200, 503):
            try:
                self._raise_for_status(resp, path)
            finally:
                resp.close()
        media_type = resp.headers.get("Content-Type", "text/plain; charset=utf-8")

        def _chunks():
            try:
                yield from resp.iter_content(chunk_size=None)
            finally:
                resp.close()

        return resp.status_code, media_type, _chunks()


__all__ = ["SidecarProxy", "EmailSidecarProxy"]
