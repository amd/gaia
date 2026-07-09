# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarProxy — forward UI email calls to the running sidecar over HTTP.

Forwards the full schema-2.1 ``/v1/email/*`` contract (triage, batch triage,
search, inbox pre-scan, draft/send + confirm, archive/unarchive,
quarantine/unquarantine, calendar view/preview/create/respond, health, version)
and returns the sidecar's envelopes **unchanged** so the existing SSE card
pipeline (``pre_scan_inbox`` → ``email_pre_scan``) keeps working byte-for-byte.

A non-2xx is translated loudly into :class:`SidecarHTTPError`, which carries the
sidecar's own actionable ``detail`` (e.g. ``502 local LLM triage failed: …``)
instead of a generic ``HTTPError`` — no fallback, no swallowed error. The
sidecar's connector OAuth *write* routes are deliberately NOT proxied here: all
connector writes stay on the Python backend's single-writer path.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import SidecarHTTPError

logger = get_logger(__name__)


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


class EmailSidecarProxy:
    def __init__(self, base_url: str, *, session=None, timeout: float | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.environ.get("GAIA_EMAIL_SIDECAR_TIMEOUT", "300"))
        )
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def _raise_for_status(self, resp, path: str) -> None:
        # Translate the boundary loudly: keep the sidecar's own actionable detail
        # (e.g. "502 local LLM triage failed: ...") instead of a generic HTTPError.
        if resp.status_code >= 400:
            raise SidecarHTTPError(resp.status_code, _extract_detail(resp), path=path)

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

    # -- Health / version ---------------------------------------------------
    def health(self) -> dict:
        return self._get("/health")

    def version(self) -> dict:
        return self._get("/version")
