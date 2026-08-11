# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft connector catalog entries (#1105, split into two connectors #2628).

Registers TWO ``ConnectorSpec``s into ``REGISTRY``:
  - ``microsoft``      — Microsoft Personal (Outlook.com / Hotmail / Live),
                          the ``consumers`` authority. Keeps its pre-#2628 id
                          so existing grants and keyring entries survive.
  - ``microsoft_work``  — Microsoft Work or School (Microsoft 365 / Entra
                          ID), the ``organizations`` authority. New id.

Both specs set ``oauth_impl="microsoft"`` so ``providers.get()`` dispatches
either one to ``MicrosoftOAuthProvider`` (see plan amendment A1) — but each
keeps its OWN ``oauth_provider_ref`` (equal to its own ``id``), which is the
literal keyring/token-cache storage key. Sharing ``oauth_provider_ref``
between the two would silently clobber one connector's credentials with the
other's (#2628 amendment A1) — do not "simplify" this by pointing
``microsoft_work``'s ref at ``"microsoft"``.

Tenant is now spec data (``oauth_tenant``), not an environment variable —
see ``providers/microsoft.py`` for the resolution chain and
``docs/connectors/microsoft.mdx`` for the user-facing explanation.

Foundation for the Outlook mailbox (#1275) and calendar (#1276) agents. Those
leads add agent tools that request a Bearer token for these Graph scopes via
the generic ``oauth_pkce`` handler — no Microsoft-specific OAuth code in the
agents. #2629 tracks teaching the email agent / Agent UI about
``microsoft_work``; today only ``microsoft`` is in any agent's
``REQUIRED_CONNECTORS``.
"""

import gaia.connectors.oauth_pkce  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

# Shared Graph scope tuples — referenced by BOTH specs so the two connectors'
# scope sets cannot silently drift apart (#2628). openid + offline_access are
# mandatory for the shared OAuth flow (id_token + refresh_token); User.Read
# gives a basic profile for the success page.
_DEFAULT_SCOPES = (
    "openid",
    "offline_access",
    "https://graph.microsoft.com/User.Read",
)
_AVAILABLE_SCOPES = (
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
)

# First-time setup form shared shape: Microsoft public-client PKCE flows take
# ONLY a Client ID — Microsoft forbids a client_secret for public/native
# clients. The optional secret field exists solely for the rare confidential
# web-app registration; it is not required and is stored encrypted in the OS
# keyring.
_CLIENT_ID_FIELD = ConfigField(
    key="client_id",
    label="Application (client) ID",
    kind="text",
    help_md=(
        "From the Azure portal → App registrations → your app → Overview. "
        "Looks like a GUID, e.g. 11112222-bbbb-3333-cccc-4444dddd5555."
    ),
)
_CLIENT_SECRET_FIELD = ConfigField(
    key="client_secret",
    label="Client Secret (confidential apps only)",
    kind="secret",
    required=False,
    help_md=(
        "Leave blank for the standard public-client (desktop) flow — "
        "Microsoft forbids a secret there. Only set this if you registered "
        "a confidential web app. Stored encrypted in your OS keyring."
    ),
)

MICROSOFT_SPEC = ConnectorSpec(
    id="microsoft",
    display_name="Microsoft Personal",
    icon="https://learn.microsoft.com/favicon.ico",
    category="productivity",
    tier=1,
    type="oauth_pkce",
    description=(
        "Connect GAIA to your personal Microsoft account (Outlook.com, "
        "Hotmail, or Live) for Outlook mail, calendar, OneDrive, and more "
        "via Microsoft Graph. For a Microsoft 365 work or school account, "
        "use the Microsoft Work or School connector instead."
    ),
    instructions_md=(
        "Sign in with your personal Microsoft account (Outlook.com, Hotmail, "
        "or Live) to allow GAIA to access your Outlook mail and calendar. "
        "Revoke access any time from the "
        "[Microsoft account privacy page](https://account.live.com/consent/Manage). "
        "Have a work or school account instead? Use the **Microsoft Work or "
        "School** connector."
    ),
    product_url="https://www.microsoft.com/microsoft-365",
    docs_url="https://amd-gaia.ai/docs/connectors/microsoft",
    default_scopes=_DEFAULT_SCOPES,
    available_scopes=_AVAILABLE_SCOPES,
    oauth_provider_ref="microsoft",
    # This connector always authenticates against the "consumers" authority
    # (personal Microsoft accounts only) — see plan amendment D1/#2628.
    oauth_tenant="consumers",
    # Both Microsoft specs share the same provider CLASS; ``oauth_provider_ref``
    # above is what keeps their stored credentials/tokens separate (A1).
    oauth_impl="microsoft",
    # The Microsoft provider implements the RFC 8628 device-code endpoints, so
    # the UI can offer zero-setup "sign in with a code" alongside the browser
    # flow (no Azure app registration / loopback redirect needed).
    supports_device_code=True,
    oauth_setup_fields=(
        ConfigField(
            key="client_id",
            label="Application (client) ID",
            kind="text",
            help_md=(
                "From the Azure portal → App registrations → your app → "
                "Overview. Register the app with the 'Personal Microsoft "
                "accounts only' audience (or the combined 'any organizational "
                "directory and personal Microsoft accounts' audience, which "
                "also works against the consumers authority) and a "
                "http://localhost redirect URI of type 'Mobile and desktop "
                "applications'. Looks like a GUID, e.g. "
                "11112222-bbbb-3333-cccc-4444dddd5555."
            ),
        ),
        _CLIENT_SECRET_FIELD,
    ),
)

MICROSOFT_WORK_SPEC = ConnectorSpec(
    id="microsoft_work",
    display_name="Microsoft Work or School",
    icon="https://learn.microsoft.com/favicon.ico",
    category="productivity",
    tier=1,
    type="oauth_pkce",
    description=(
        "Connect GAIA to your Microsoft 365 work or school account (Entra "
        "ID) for Outlook mail, calendar, OneDrive, and more via Microsoft "
        "Graph. For a personal Outlook.com, Hotmail, or Live account, use "
        "the Microsoft Personal connector instead."
    ),
    instructions_md=(
        "Sign in with your Microsoft 365 work or school account to allow "
        "GAIA to access your Outlook mail and calendar. Revoke access any "
        "time from [My Apps](https://myapps.microsoft.com). Have a personal "
        "Outlook.com, Hotmail, or Live account instead? Use the "
        "**Microsoft Personal** connector."
    ),
    product_url="https://www.microsoft.com/microsoft-365",
    docs_url="https://amd-gaia.ai/docs/connectors/microsoft",
    default_scopes=_DEFAULT_SCOPES,
    available_scopes=_AVAILABLE_SCOPES,
    # Distinct storage key from "microsoft" — required by A1. Sharing this
    # value with MICROSOFT_SPEC would make the two connectors silently
    # overwrite each other's client id / refresh token.
    oauth_provider_ref="microsoft_work",
    # Multi-tenant work/school authority by default; a single-tenant org can
    # narrow this with the optional "Directory (tenant) ID" field below,
    # which overrides this default at connect time (D5/D6, closes #2616).
    oauth_tenant="organizations",
    oauth_impl="microsoft",
    supports_device_code=True,
    oauth_setup_fields=(
        ConfigField(
            key="client_id",
            label="Application (client) ID",
            kind="text",
            help_md=(
                "From the Azure portal → App registrations → your app → "
                "Overview. Register the app with an organizational-directory "
                "audience ('Accounts in this organizational directory only' "
                "for a single tenant, or 'Accounts in any organizational "
                "directory' for multi-tenant) and a http://localhost redirect "
                "URI of type 'Mobile and desktop applications'. Looks like a "
                "GUID, e.g. 11112222-bbbb-3333-cccc-4444dddd5555."
            ),
        ),
        _CLIENT_SECRET_FIELD,
        ConfigField(
            key="tenant_id",
            label="Directory (tenant) ID",
            kind="text",
            required=False,
            help_md=(
                "Optional. Leave blank to use the multi-tenant 'organizations' "
                "authority (any work/school account). Set this to your Azure "
                "AD / Entra ID Directory (tenant) ID GUID if your app "
                "registration is single-tenant — required for the daemon to "
                "refresh correctly with no environment variables set "
                "(#2616). The Overview page shows three GUIDs; copy the one "
                "labeled 'Directory (tenant) ID', not 'Application (client) "
                "ID' or 'Object ID'. Unvalidated here — an incorrect value "
                "fails loudly at Microsoft's sign-in page, not silently."
            ),
        ),
    ),
)

REGISTRY.register(MICROSOFT_SPEC)
REGISTRY.register(MICROSOFT_WORK_SPEC)
