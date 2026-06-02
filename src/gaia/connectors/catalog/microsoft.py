# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft connector catalog entry (#1105).

Registers the Microsoft OAuth-PKCE ConnectorSpec into the global REGISTRY and
imports ``oauth_pkce`` so its handler is registered into ``_HANDLER_REGISTRY``.
The AgentUI tile grid is driven by ``REGISTRY.all()``, so registering this spec
is all that is needed for a Microsoft tile to appear in Settings → Connectors —
no frontend change required (the tile renders from the catalog, exactly like
Google).

Foundation for the Outlook mailbox (#1275) and calendar (#1276) agents. Those
leads add agent tools that request a Bearer token for these Graph scopes via
the generic ``oauth_pkce`` handler — no Microsoft-specific OAuth code in the
agents.

Tenant is ``consumers`` (personal Outlook.com / Hotmail / Live). ``openid`` and
``offline_access`` are in ``default_scopes`` because the shared OAuth flow
requires an id_token (account email) and a refresh token respectively.
"""

import gaia.connectors.oauth_pkce  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

MICROSOFT_SPEC = ConnectorSpec(
    id="microsoft",
    display_name="Microsoft",
    icon="https://learn.microsoft.com/favicon.ico",
    category="productivity",
    tier=1,
    type="oauth_pkce",
    description=(
        "Connect GAIA to your personal Microsoft account for Outlook mail, "
        "calendar, OneDrive, and more via Microsoft Graph."
    ),
    instructions_md=(
        "Sign in with your personal Microsoft account (Outlook.com, Hotmail, "
        "or Live) to allow GAIA to access your Outlook mail and calendar. You "
        "can revoke access at any time from your "
        "[Microsoft account privacy page](https://account.live.com/consent/Manage)."
    ),
    product_url="https://www.microsoft.com/microsoft-365",
    docs_url="https://amd-gaia.ai/docs/connectors/microsoft",
    # openid + offline_access are mandatory for the shared flow (id_token +
    # refresh_token). User.Read gives a basic profile for the success page.
    default_scopes=(
        "openid",
        "offline_access",
        "https://graph.microsoft.com/User.Read",
    ),
    available_scopes=(
        "openid",
        "profile",
        "email",
        "offline_access",
        "https://graph.microsoft.com/User.Read",
        # Outlook mailbox (#1275).
        "https://graph.microsoft.com/Mail.Read",
        "https://graph.microsoft.com/Mail.Send",
        # Mail.ReadWrite anticipates the triage agent's organize/flag/move
        # tools (mirrors why google.py lists gmail.modify) so the per-agent
        # grant ledger will accept the token request when #1275 lands.
        "https://graph.microsoft.com/Mail.ReadWrite",
        # Outlook calendar (#1276).
        "https://graph.microsoft.com/Calendars.Read",
        "https://graph.microsoft.com/Calendars.ReadWrite",
    ),
    oauth_provider_ref="microsoft",
    # First-time setup form. Microsoft public-client PKCE flows take ONLY a
    # Client ID — Microsoft forbids a client_secret for public/native clients.
    # The optional secret field exists solely for the rare confidential
    # web-app registration; it is not required and is stored encrypted in the
    # OS keyring. Power users may instead export GAIA_MICROSOFT_CLIENT_ID
    # (and, only for a confidential app, GAIA_MICROSOFT_CLIENT_SECRET).
    oauth_setup_fields=(
        ConfigField(
            key="client_id",
            label="Application (client) ID",
            kind="text",
            help_md=(
                "From the Azure portal → App registrations → your app → "
                "Overview. Register the app with the "
                "'Personal Microsoft accounts only' audience and a "
                "http://localhost redirect URI of type 'Mobile and desktop "
                "applications'. Looks like a GUID, e.g. "
                "11112222-bbbb-3333-cccc-4444dddd5555."
            ),
        ),
        ConfigField(
            key="client_secret",
            label="Client Secret (confidential apps only)",
            kind="secret",
            required=False,
            help_md=(
                "Leave blank for the standard public-client (desktop) flow — "
                "Microsoft forbids a secret there. Only set this if you "
                "registered a confidential web app. Stored encrypted in your "
                "OS keyring."
            ),
        ),
    ),
)

REGISTRY.register(MICROSOFT_SPEC)
