# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft connector catalog entry (MS Graph, personal accounts).

Registers the Microsoft OAuth PKCE ConnectorSpec into the global REGISTRY and
imports ``oauth_pkce`` so its handler is registered into ``_HANDLER_REGISTRY``.

``offline_access`` is kept in ``default_scopes`` so every connect issues a
refresh token (MS Graph's equivalent of Google's ``access_type=offline``).
The token endpoint returns no refresh token without it, and the shared flow
raises ``ConnectorsError`` in that case.
"""

import gaia.connectors.oauth_pkce  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

MICROSOFT_SPEC = ConnectorSpec(
    id="microsoft",
    display_name="Microsoft",
    icon="https://upload.wikimedia.org/wikipedia/commons/4/44/Microsoft_logo.svg",
    category="productivity",
    tier=1,
    type="oauth_pkce",
    description=(
        "Connect GAIA to your personal Microsoft account for Outlook mail, "
        "calendar, and OneDrive via Microsoft Graph."
    ),
    instructions_md=(
        "Sign in with your personal Microsoft account (Outlook.com, Hotmail, "
        "or Live.com) to allow GAIA to access Outlook mail, your calendar, and "
        "OneDrive. You can revoke access at any time from your "
        "[Microsoft account privacy page](https://account.live.com/consent/Manage)."
    ),
    product_url="https://www.microsoft.com/microsoft-365",
    docs_url="https://amd-gaia.ai/docs/connectors/microsoft",
    default_scopes=(
        "openid",
        "profile",
        "email",
        # offline_access is REQUIRED for a refresh token (MS Graph's
        # equivalent of Google's access_type=offline). Without it the token
        # endpoint returns no refresh_token and the flow fails.
        "offline_access",
        "https://graph.microsoft.com/User.Read",
    ),
    available_scopes=(
        "openid",
        "profile",
        "email",
        "offline_access",
        "https://graph.microsoft.com/User.Read",
        # Scopes named in issue #1105 — listed in available_scopes so the
        # per-agent grant ledger will accept token requests for them.
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.Send",
        "https://graph.microsoft.com/Calendars.ReadWrite",
        "https://graph.microsoft.com/Files.Read",
    ),
    oauth_provider_ref="microsoft",
    # First-time setup form rendered by the AgentUI. Microsoft public
    # (native/desktop) clients use PKCE WITHOUT a client secret, so — unlike
    # Google — only the Application (client) ID is collected. The value is
    # stored in the OS keyring and reused across connect/disconnect cycles.
    # Power users may bypass the form with GAIA_MICROSOFT_CLIENT_ID.
    oauth_setup_fields=(
        ConfigField(
            key="client_id",
            label="Application (client) ID",
            kind="text",
            help_md=(
                "From the Azure Portal → App registrations → your app. "
                "Register the app as a public client (Authentication → Mobile "
                "and desktop applications) with the redirect URI "
                "`http://127.0.0.1/callback` (GAIA's loopback callback; the "
                "port is dynamic and Microsoft ignores it for loopback). No "
                "client secret is required for the PKCE public-client flow. "
                "See docs/connectors/microsoft.mdx for the exact setup."
            ),
        ),
    ),
)

REGISTRY.register(MICROSOFT_SPEC)
