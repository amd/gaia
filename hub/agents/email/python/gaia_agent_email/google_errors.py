# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Shared Google REST error mapping for the Gmail/Calendar backends.

A fresh BYO Google Cloud project that has never enabled the Gmail (or
Calendar) API fails the first mailbox call with Google's verbatim 403
body ("<API> has not been used in project NNN before or it is
disabled..."). Fail-loud is correct, but dumping Google's raw JSON
through the tool result is the wrong voice — the caller should get
GAIA's three-part actionable error (what failed / what to do / where to
look).

Google already returns the one-time enable URL in the 403 payload as
``error.errors[].extendedHelp`` (the
``console.developers.google.com/apis/api/<api>/overview?project=NNN``
link), so we reuse it rather than reconstructing it from the project id.

Both backends share the same ``_raise_http`` shape; factoring the
mapping here keeps the voice from drifting between them.
"""

from __future__ import annotations

from typing import Optional

import httpx


def access_not_configured_url(response: httpx.Response) -> Optional[str]:
    """Return Google's enable URL if this 403 is ``accessNotConfigured``.

    Returns ``None`` for any other response so the caller falls through to
    its normal error handling. Never raises — a malformed body is treated
    as "not the accessNotConfigured case".
    """
    if response.status_code != 403:
        return None
    try:
        errors = response.json().get("error", {}).get("errors", [])
    except (ValueError, AttributeError):
        return None
    if not isinstance(errors, list):
        return None
    for err in errors:
        if isinstance(err, dict) and err.get("reason") == "accessNotConfigured":
            # ``extendedHelp`` is the enable URL Google already returns.
            return err.get("extendedHelp") or None
    return None


def access_not_configured_message(api_name: str, enable_url: str) -> str:
    """Build the three-part actionable message for a disabled Google API.

    ``api_name`` is the human label, e.g. ``"Gmail API"`` / ``"Calendar API"``.
    """
    return (
        f"{api_name} is not enabled for your Google Cloud project. "
        f"Enable it at {enable_url}, then wait 1-2 minutes for the change "
        "to propagate before retrying. "
        "(Docs: https://amd-gaia.ai/docs/guides/email → Connect Google)"
    )
