# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Google connector catalog entry.

Registers the Google OAuth PKCE ConnectorSpec into the global REGISTRY and
imports ``oauth_pkce`` so its handler is registered into ``_HANDLER_REGISTRY``.
"""

import gaia.connectors.oauth_pkce  # noqa: F401  # pylint: disable=unused-import
from gaia.connectors.registry import REGISTRY
from gaia.connectors.spec import ConfigField, ConnectorSpec

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
    docs_url="https://amd-gaia.ai/docs/connectors/google",
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
        # gmail.modify is required by the email triage agent (#962) for label
        # mutations, archive, trash/untrash, and starring. Listed in
        # available_scopes so the per-agent grant ledger will accept the
        # token request — without this entry, every organize/trash tool call
        # on the email agent would raise AuthRequiredError at runtime.
        "https://www.googleapis.com/auth/gmail.modify",
        "https://www.googleapis.com/auth/calendar.readonly",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/drive.file",
    ),
    oauth_provider_ref="google",
    # First-time setup form rendered by the AgentUI when the user has
    # not yet provided OAuth client credentials. Submitted values are
    # stored in the OS keyring (encrypted at rest) and reused across
    # connect/disconnect cycles. Power users may bypass the form by
    # exporting GAIA_GOOGLE_CLIENT_ID / GAIA_GOOGLE_CLIENT_SECRET before
    # launch.
    oauth_setup_fields=(
        ConfigField(
            key="client_id",
            label="OAuth Client ID",
            kind="text",
            help_md=(
                "From Google Cloud Console → APIs & Services → Credentials → "
                "your Desktop-app OAuth 2.0 Client. Looks like "
                "<digits>-<hash>.apps.googleusercontent.com."
            ),
        ),
        ConfigField(
            key="client_secret",
            label="OAuth Client Secret",
            kind="secret",
            help_md=(
                "From the same Desktop-app OAuth client. Required by Google "
                "even for PKCE flows. Stored encrypted in your OS keyring."
            ),
        ),
    ),
)

REGISTRY.register(GOOGLE_SPEC)
