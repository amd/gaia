# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""EmailSidecarProxy — forward UI email calls to the running sidecar over HTTP.

Forwards the routes that EXIST today (triage / draft / send / health / version)
and returns the sidecar's envelopes unchanged so the existing SSE card pipeline
keeps working. Routes that do not exist yet (inbox pre-scan, search #1781,
archive/quarantine #1779, calendar #1780) are GATED: they raise loudly with the
tracking issue rather than silently no-op — no fallback, no fake success.
"""

from __future__ import annotations

import os

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import RouteNotAvailableError, SidecarHTTPError

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

    def _get(self, path: str) -> dict:
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        self._raise_for_status(resp, path)
        return resp.json()

    # -- Routes that exist today --------------------------------------------
    def triage(self, payload: dict) -> dict:
        return self._post("/v1/email/triage", payload)

    def draft(self, payload: dict) -> dict:
        return self._post("/v1/email/draft", payload)

    def send(self, payload: dict) -> dict:
        return self._post("/v1/email/send", payload)

    def health(self) -> dict:
        return self._get("/health")

    def version(self) -> dict:
        return self._get("/version")

    # -- Routes not yet built (gated, not silently broken) ------------------
    def _pending(self, capability: str, issue: str):
        raise RouteNotAvailableError(
            f"email {capability} has no REST route on the sidecar yet "
            f"(pending {issue}). The sidecar can only serve inbox features once "
            "that route lands. This is gated deliberately — no fallback."
        )

    def pre_scan_inbox(self, *_args, **_kwargs):
        self._pending("inbox pre-scan", "the inbox pre-scan REST route")

    def search_inbox(self, *_args, **_kwargs):
        self._pending("inbox search", "#1781")

    def archive(self, *_args, **_kwargs):
        self._pending("archive", "#1779")

    def quarantine(self, *_args, **_kwargs):
        self._pending("quarantine", "#1779")

    def calendar(self, *_args, **_kwargs):
        self._pending("calendar", "#1780")
