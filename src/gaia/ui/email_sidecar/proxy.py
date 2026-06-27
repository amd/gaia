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

from gaia.logger import get_logger
from gaia.ui.email_sidecar.errors import RouteNotAvailableError

logger = get_logger(__name__)


class EmailSidecarProxy:
    def __init__(self, base_url: str, *, session=None, timeout: float = 900.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        if session is None:
            import requests

            session = requests.Session()
        self._session = session

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._session.post(
            f"{self.base_url}{path}", json=payload, timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def _get(self, path: str) -> dict:
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        resp.raise_for_status()
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
