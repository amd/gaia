# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Google connector catalog entry.

Registers the Google OAuth PKCE ConnectorSpec into the global REGISTRY and
imports ``oauth_pkce`` so its handler is registered into ``_HANDLER_REGISTRY``.
"""

import gaia.connectors.oauth_pkce  # noqa: F401 — triggers register_handler("oauth_pkce", ...)
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConnectorSpec

GOOGLE_SPEC = ConnectorSpec(
    id="google",
    display_name="Google",
    icon="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg",
    category="productivity",
    tier=1,
    type="oauth_pkce",
    description="Connect GAIA to your Google account for Gmail, Calendar, Drive, and more.",
    instructions_md=(
        "Sign in with Google to allow GAIA to access your Gmail, Google Calendar, "
        "and Google Drive. You can revoke access at any time from your "
        "[Google Account security page](https://myaccount.google.com/permissions)."
    ),
    product_url="https://workspace.google.com/",
    default_scopes=(
        "openid",
        "email",
        "profile",
    ),
    available_scopes=(
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
    ),
    oauth_provider_ref="google",
)

REGISTRY.register(GOOGLE_SPEC)
