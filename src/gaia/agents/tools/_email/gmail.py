# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Read-only Gmail mailbox client for the flagship agent's email tools.

Same five reads as the Graph backend next door, emitting the same
provider-neutral summary shape, so neither the skill nor the model learns
which mailbox is connected.

This is a reimplementation of the read half of
``gaia_agent_email.gmail_backend``, not a relocation of it: the flagship must
not depend on the standalone email package it is meant to replace, and that
backend returns raw Gmail resources — its flattening lives in the sidecar's
tool layer, which does not come along. The wire contract (Gmail API v1) is the
shared truth, not the Python.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

import httpx

from gaia.agents.tools._email.errors import MailboxAuthError, MailboxError

logger = logging.getLogger(__name__)

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Gmail's list returns id stubs only, so every message costs a `messages.get`.
# 100 matches the tool layer's own ceiling; Graph's 999 would be 999 fetches.
_GMAIL_MAX_LIMIT = 100

# Fanned out over a bounded pool: 25 sequential round-trips would spend most of
# a tool's timeout waiting. Small enough to stay under Gmail's per-user
# concurrency limit.
_FETCH_CONCURRENCY = 5

# `metadataHeaders` is only honoured alongside `format=metadata`; sending
# either alone silently returns the wrong thing.
_METADATA_HEADERS = ("Subject", "From", "To", "Cc", "Date")

# Gmail label IDs that are mailbox state, not user tags. `categories` must
# carry only what a user would recognise as a label they created.
_RESERVED_LABEL_IDS = frozenset(
    {
        "INBOX",
        "SENT",
        "DRAFT",
        "SPAM",
        "TRASH",
        "UNREAD",
        "STARRED",
        "IMPORTANT",
        "CHAT",
    }
)
_RESERVED_LABEL_PREFIX = "CATEGORY_"

_GMAIL_ENABLE_URL = (
    "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
)


def _error_detail(response: httpx.Response) -> tuple:
    """Gmail's structured error fields — a reason token and its remedy link.

    Deliberately never the raw response body: that is upstream-controlled text
    and it ends up in front of a user.
    """
    try:
        err = (response.json() or {}).get("error") or {}
    except ValueError:
        return "", ""
    errors = err.get("errors") or [{}]
    first = errors[0] if isinstance(errors[0], dict) else {}
    reason = str(first.get("reason") or err.get("status") or "")
    help_url = str(first.get("extendedHelp") or "")
    if not help_url.startswith("https://"):
        help_url = ""
    return reason, help_url


class GmailReadBackend:
    """Read-only Gmail API v1 client for one connected Google mailbox."""

    def __init__(
        self,
        access_token_fn: Callable[[], str],
        *,
        http_client: Optional[httpx.Client] = None,
        timeout_seconds: float = 15.0,
    ) -> None:
        self._access_token_fn = access_token_fn
        # Tests inject an httpx.MockTransport-backed client so no test ever
        # needs the network or a real token.
        self._client = http_client or httpx.Client(timeout=timeout_seconds)
        self._label_names: Optional[Dict[str, str]] = None

    # -- HTTP ---------------------------------------------------------------

    def _raise(self, response: httpx.Response, where: str) -> None:
        reason, help_url = _error_detail(response)
        code = response.status_code
        if code == 401:
            raise MailboxAuthError(
                "Gmail rejected the access token (401). The Google connection "
                "has expired or been revoked. Reconnect it with `gaia "
                "connectors connect google` (or Settings -> Connectors in the "
                f"Agent UI), then retry. (request: {where})"
            )
        if code == 403 and reason == "accessNotConfigured":
            raise MailboxAuthError(
                "The Gmail API is not enabled for the Google Cloud project "
                "behind this connection (403 accessNotConfigured), so no "
                "mailbox can be read. Enable it, wait a minute, then retry: "
                f"{help_url or _GMAIL_ENABLE_URL} (request: {where})"
            )
        if code == 403:
            raise MailboxAuthError(
                "Gmail refused the request (403 — insufficient permissions). "
                "The connected Google account has not granted this agent a "
                "gmail read scope. Re-grant it with `gaia connectors grants "
                "grant google installed:gaia --scopes <scope>`, or reconnect "
                f"Google, then retry. (request: {where}; reason: "
                f"{reason or 'forbidden'})"
            )
        if code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise MailboxError(
                "Gmail is rate-limiting this mailbox (429). Retry after "
                f"{retry_after}s. Reduce `limit` if this repeats. "
                f"(request: {where})"
            )
        raise MailboxError(
            f"Gmail request failed: {where} returned {code}"
            + (f" ({reason})" if reason else "")
            + ". Retry, and check https://www.google.com/appsstatus if it "
            "persists."
        )

    def _get(
        self, path: str, *, params: Optional[dict] = None, token: Optional[str] = None
    ) -> Any:
        # Re-minted per request unless a caller already minted one for a fan-out
        # it owns: a cached token would let a mid-scan revoke look like success.
        bearer = token or self._access_token_fn()
        try:
            resp = self._client.get(
                f"{GMAIL_API_BASE}{path}",
                headers={"Authorization": f"Bearer {bearer}"},
                params=params,
            )
        except httpx.HTTPError as exc:
            raise MailboxError(
                f"Could not reach Gmail for {path}: {type(exc).__name__}. Check "
                "network connectivity, then retry."
            ) from exc
        if resp.status_code != 200:
            self._raise(resp, f"GET {path}")
        return resp.json()

    # -- Reads --------------------------------------------------------------

    def get_user_email(self) -> str:
        """The connected mailbox's address."""
        data = self._get("/users/me/profile")
        address = (data.get("emailAddress") or "").strip()
        if not address:
            raise MailboxError(
                "Gmail returned no address for the connected account "
                "(`emailAddress` was empty). The connection is in an unusable "
                "state — reconnect Google with `gaia connectors connect google`."
            )
        return address


__all__ = [
    "GMAIL_API_BASE",
    "GmailReadBackend",
]
