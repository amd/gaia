# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft identity platform (v2.0) OAuth provider for ``gaia.connectors``.

Foundation for the Outlook mailbox (#1275) and calendar (#1276) agents:
unlocks MS Graph (mail, calendar, OneDrive, Teams, SharePoint) for any agent
through the same generic ``oauth_pkce`` handler that already drives Google.

NO module-level side effects: instantiating the provider reads stored/env
credentials and computes ``client_id_hash``. Importing this module registers
nothing — registration is lazy on first ``get(<connector_id>)`` (see
``providers/__init__.py``), matching ``GoogleOAuthProvider``.

One CLASS, two connectors (#2628): ``microsoft`` (Personal, ``consumers``
authority) and ``microsoft_work`` (Work or School, ``organizations``
authority, optionally narrowed to a single Directory/tenant id) both
instantiate THIS class, dispatched via ``ConnectorSpec.oauth_impl`` in
``providers/__init__.py`` — never via a hard-coded id check here. Each
instance's identity (``provider_id``) drives EVERY per-connector concern:
which keyring slot it reads/writes (``store.peek_provider_credentials``),
which env vars are its fallback (``GAIA_{PROVIDER_ID}_CLIENT_ID`` /
``_CLIENT_SECRET``), which guided walkthrough it renders on a missing
client, and which OAuth authority it authenticates against. Two instances
with different ``provider_id``s never share state — that separation is the
entire point of the split (plan amendment A1: sharing it would silently
clobber one connector's credentials with the other's).

Tenant resolution (D6, plan amendment A16) is a three-tier chain owned
entirely by ``__init__``, exactly mirroring how ``client_id``/``client_secret``
already separate their tiers:
  1. Explicit ``tenant=`` kwarg (tests / library callers).
  2. Stored provider-credentials blob's ``tenant`` key — the optional
     Directory (tenant) ID a user pastes into ``microsoft_work``'s setup
     form (#2616).
  3. ``default_tenant=`` kwarg — the connector spec's own default
     (``ConnectorSpec.oauth_tenant``), passed by ``providers.get()``.

Public-client PKCE: per the Microsoft identity platform docs, public clients
(native/desktop, single-page apps) MUST NOT send a ``client_secret`` when
redeeming an authorization code. This is the key difference from Google, which
*requires* a secret even for installed apps. So the setup form asks only for a
Client ID; ``token_request_body`` / ``refresh_request_body`` omit the secret
unless one is explicitly configured (a confidential web-app edge case).

The shared ``flow.py`` requires a refresh token, which Microsoft returns only
when ``offline_access`` is requested — so that scope is in ``default_scopes``
and a first connect succeeds without any flow.py change. ``openid`` is NOT:
an Entra tenant may reject the whole authorization request over it
(``AADSTS65002``), and everything it carried has a tenant-independent source —
the account email from Graph ``/me`` (``userinfo_url``), the account kind from
this connector's own authority (``classify_account_type``).
"""

from __future__ import annotations

import logging
import os
import zlib
from typing import Iterable, Sequence
from urllib.parse import urlencode

from gaia.connectors.errors import OAuthClientNotConfiguredError
from gaia.connectors.setup_routes import get_route, render_console_steps

logger = logging.getLogger(__name__)

# Safety-net fallback for the rare direct construction that supplies neither
# an explicit tenant, a stored override, NOR a default_tenant (every catalog
# spec sets ``oauth_tenant`` per D2, so the real ``providers.get()`` path
# never hits this). Kept only so an undocumented direct instantiation still
# builds valid URLs instead of a bare "None" segment.
_FALLBACK_TENANT = "common"

# The well-known "consumers" tenant. Every personal Microsoft account (Outlook.com
# / Hotmail / Live) carries this exact GUID in its id_token ``tid`` claim; a
# work/school account carries its organization's Entra tenant id instead. This is
# the canonical, exact discriminator between the two kinds of account — unlike the
# ``mail`` vs ``userPrincipalName`` shape, which only *usually* differs and would
# misclassify silently. It is a property of the signed-in ACCOUNT, independent of
# which tenant the app signed in against.
# https://learn.microsoft.com/en-us/entra/identity-platform/id-token-claims-reference
_MSA_TENANT_ID = "9188040d-6c67-4c5b-b112-36a304b66dad"

# Account kinds derived from ``tid``. Consumed by agents that behave differently
# for a personal vs a work mailbox (e.g. the email agent's skill sets, #2466).
ACCOUNT_TYPE_PERSONAL = "personal"
ACCOUNT_TYPE_WORK = "work"
ACCOUNT_TYPES = (ACCOUNT_TYPE_PERSONAL, ACCOUNT_TYPE_WORK)

# Display labels for known connector ids — used only in the not-configured
# error's copy. Falls back to a title-cased id for anything unlisted (e.g. a
# future fourth Microsoft audience, or a test's throwaway spec) so adding a
# connector never requires an edit here (AC3: no providers/*.py change).
_PROVIDER_LABELS: dict[str, str] = {
    "microsoft": "Microsoft Personal",
    "microsoft_work": "Microsoft Work or School",
}


def account_type_for_tenant(tenant_id: str | None) -> str | None:
    """Classify a Microsoft account from its id_token ``tid`` claim.

    Args:
        tenant_id: The ``tid`` claim value, or ``None`` when the token carried
            none (e.g. a device-code response with no decodable id_token).

    Returns:
        ``"personal"`` for the well-known consumers tenant, ``"work"`` for any
        other tenant GUID, or ``None`` when *tenant_id* is absent/blank. ``None``
        means "unknown" — callers must handle it explicitly rather than assuming
        a kind.
    """
    tid = (tenant_id or "").strip().lower()
    if not tid:
        return None
    return ACCOUNT_TYPE_PERSONAL if tid == _MSA_TENANT_ID else ACCOUNT_TYPE_WORK


def account_type_for_authority(authority: str | None) -> str | None:
    """Classify a Microsoft account from the OAuth authority it signed in against.

    Used when no id_token is available (GAIA does not request ``openid``, #4079).
    Entra enforces the audience per authority: ``consumers`` admits only personal
    Microsoft accounts and ``organizations`` only work/school ones, so the
    connector's own authority is an exact discriminator — not a heuristic.

    Args:
        authority: The tenant segment of the login URL — ``consumers``,
            ``organizations``, ``common``, or a Directory (tenant) GUID.

    Returns:
        ``"personal"`` for ``consumers``, ``"work"`` for ``organizations`` or a
        specific tenant id, ``None`` for ``common`` (which admits both kinds, so
        it discriminates nothing) and for a blank value.
    """
    value = (authority or "").strip().lower()
    if not value or value == "common":
        return None
    if value == "consumers":
        return ACCOUNT_TYPE_PERSONAL
    return ACCOUNT_TYPE_WORK


# Plain-language descriptions for the AgentUI consent dialog, mirroring the
# Google provider's SCOPE_DESCRIPTIONS. The router/CLI render these strings;
# agents declare the Graph scope URLs in REQUIRED_CONNECTORS.
SCOPE_DESCRIPTIONS: dict[str, str] = {
    "https://graph.microsoft.com/Mail.Read": "Read your email",
    "https://graph.microsoft.com/Mail.Send": "Send email on your behalf",
    "https://graph.microsoft.com/Mail.ReadWrite": (
        "Read, organize, and manage your email"
    ),
    "https://graph.microsoft.com/Calendars.Read": "Read your calendar events",
    "https://graph.microsoft.com/Calendars.ReadWrite": "Manage your calendar events",
    "https://graph.microsoft.com/Files.Read": "Read your OneDrive files",
    "https://graph.microsoft.com/User.Read": "See your basic profile",
    "openid": "Verify your identity",
    "profile": "See your basic profile",
    "email": "See your email address",
    "offline_access": "Maintain access to data you've granted it access to",
}


class MicrosoftOAuthProvider:
    """
    Concrete provider for the Microsoft identity platform. One instance per
    connector id (``microsoft`` / ``microsoft_work``) — see the module
    docstring for why ``provider_id`` drives every per-connector concern.
    Implements the ``OAuthProvider`` Protocol structurally — no inheritance,
    matching ``GoogleOAuthProvider``.

    ``client_id_hash`` is a non-cryptographic CRC32 fingerprint used only for
    log correlation / the ``store.load_connection`` tripwire compare.
    """

    # offline_access => refresh token, which the shared flow requires. No
    # `openid`: AMD's Entra tenant rejects the request with AADSTS65002 (#4079).
    default_scopes: Sequence[str] = (
        "offline_access",
        "https://graph.microsoft.com/User.Read",
    )
    # Userinfo fallback (#1275): the flow layer calls this when the token
    # response carries no decodable email in the id_token (common on the
    # device-code path). Provider-agnostic hook — flow.py GETs ``userinfo_url``
    # with the access token and hands the JSON to ``parse_account_email``.
    userinfo_url: str = (
        "https://graph.microsoft.com/v1.0/me?$select=mail,userPrincipalName"
    )

    def classify_account_type(self, claims: dict) -> str | None:
        """Derive ``personal`` / ``work`` at connect time (#2466).

        Duck-typed hook: ``flow.py`` calls this when the provider defines it, so
        the account kind is recorded on the connection and no agent has to
        re-derive it (or make a Graph call) later.

        Prefers the id_token's ``tid`` claim, which describes the account itself.
        GAIA no longer requests ``openid`` (#4079), so in practice there are no
        claims and the connector's own authority decides. Returns ``None`` when
        neither is conclusive — callers must treat that as unknown.
        """
        return account_type_for_tenant(
            (claims or {}).get("tid")
        ) or account_type_for_authority(self.tenant)

    @staticmethod
    def parse_account_email(userinfo: dict) -> str | None:
        """Extract the account email from a Graph ``/me`` response. Personal
        accounts often null ``mail`` and carry the address in
        ``userPrincipalName`` instead."""
        return (userinfo.get("mail") or userinfo.get("userPrincipalName") or "") or None

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        tenant: str | None = None,
        provider_id: str = "microsoft",
        default_tenant: str | None = None,
    ):
        # D4: provider_id is an INSTANCE attribute (was a class constant) so
        # the two connectors' provider objects never share identity. Every
        # credential lookup, env-var name, and error message below is keyed
        # off this, not a literal "microsoft".
        self.provider_id: str = provider_id
        env_prefix = f"GAIA_{self.provider_id.upper()}"

        # One keyring read serves all three credential tiers (client_id,
        # client_secret, tenant) — mirrors the existing client_id/secret
        # resolution order (A16): explicit kwarg > stored > default.
        if client_id is None or client_secret is None or tenant is None:
            # Lazy import to avoid a connectors -> providers -> store cycle at
            # module load time.
            from gaia.connectors.store import peek_provider_credentials

            stored = peek_provider_credentials(self.provider_id) or {}
        else:
            stored = {}

        resolved_id = (
            client_id
            if client_id is not None
            else stored.get("client_id")
            or os.environ.get(f"{env_prefix}_CLIENT_ID", "")
        )
        if not resolved_id:
            route = get_route(self.provider_id)
            if route is not None:
                console_steps = render_console_steps(route)
            else:
                # No authored walkthrough for this connector — generic-but-
                # actionable guidance rather than showing another
                # connector's console steps. Both registered Microsoft
                # connectors have a route today; this covers a future spec
                # that shares oauth_impl="microsoft" without one.
                console_steps = (
                    "  1. Register an app at https://portal.azure.com -> "
                    "Microsoft Entra ID -> App registrations\n"
                    "  2. Set the supported account type / tenant to match "
                    f"this connector's audience ({self.provider_id})\n"
                    "  3. Add a http://localhost redirect URI under "
                    "Authentication -> Mobile & desktop applications\n"
                    "  4. Copy the Application (client) ID"
                )
            raise OAuthClientNotConfiguredError(
                self.provider_id,
                provider_label=_PROVIDER_LABELS.get(
                    self.provider_id, self.provider_id.replace("_", " ").title()
                ),
                console_steps=console_steps,
                example=(
                    "  For the email agent, copy-paste (bash) after creating "
                    "the client above:\n"
                    f"    gaia connectors configure {self.provider_id} "
                    "--client-id <ID>\n"
                    '    SCOPES="https://graph.microsoft.com/Mail.ReadWrite '
                    "https://graph.microsoft.com/Mail.Send "
                    'https://graph.microsoft.com/Calendars.ReadWrite"\n'
                    f"    gaia connectors connect {self.provider_id} "
                    "--scopes $SCOPES --grant-agent installed:email"
                ),
                docs="https://amd-gaia.ai/docs/connectors/microsoft",
            )
        self.client_id: str = resolved_id
        # CRC32 fingerprint for log correlation / tripwire comparison only.
        # Non-cryptographic by design — not used for security.
        self.client_id_hash: str = format(zlib.crc32(resolved_id.encode()), "08x")
        # Public PKCE clients send NO secret. Empty string => omitted from the
        # token/refresh bodies. A non-empty value is the confidential-app
        # opt-in (operator set GAIA_{PREFIX}_CLIENT_SECRET / saved one).
        self.client_secret: str = (
            client_secret
            if client_secret is not None
            else stored.get("client_secret")
            or os.environ.get(f"{env_prefix}_CLIENT_SECRET", "")
        )

        # D6/A16: three-tier tenant chain — explicit kwarg > stored override
        # (microsoft_work's optional "Directory (tenant) ID") > this
        # connector's spec default.
        resolved_tenant = (
            tenant
            if tenant is not None
            else stored.get("tenant") or default_tenant or _FALLBACK_TENANT
        )
        self.tenant: str = resolved_tenant
        self.auth_url: str = (
            f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/authorize"
        )
        self.token_url: str = (
            f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"
        )
        # Device-code endpoint (#1275): enables zero-Azure-app-registration
        # sign-in — the user enters a short code at a Microsoft URL instead of
        # a browser redirect. Presence of this attribute is how the generic
        # device flow detects device-code capable providers (duck-typed, like
        # authorization_params()).
        self.device_code_url: str = (
            f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/devicecode"
        )
        # The Microsoft identity platform has no public endpoint that revokes
        # a single app's refresh/access token (#2591). The closest primitive,
        # ``invalidateAllRefreshTokens``, is account-wide (kills every app's
        # session, not just GAIA's) and requires Graph admin consent GAIA
        # does not request. ``None`` here is what tells
        # ``flow.revoke_provider_token`` to report ``revoke_supported=False``
        # instead of silently claiming a revoke that cannot happen — the
        # user must remove GAIA from https://myaccount.microsoft.com/ (or
        # https://account.live.com/consent/Manage for personal accounts)
        # to fully revoke access.
        self.revoke_url: str | None = None

    def authorization_params(self) -> dict:
        """
        Microsoft-specific extras for the authorization URL.

        ``response_mode=query`` — the loopback ``/callback`` handler reads the
        code from the query string (``?code=...``). Microsoft defaults to
        ``fragment`` in some hybrid-flow cases, and browsers do not forward the
        fragment to the loopback server, so we pin ``query`` explicitly.

        Note: unlike Google, Microsoft does NOT need ``access_type=offline`` /
        ``prompt=consent`` to issue a refresh token — the ``offline_access``
        scope alone does that, and it is in ``default_scopes``.
        """
        return {"response_mode": "query"}

    def authorization_url(
        self,
        redirect_uri: str,
        challenge: str,
        state: str,
        scopes: Iterable[str],
    ) -> str:
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "scope": " ".join(scopes),
        }
        params.update(self.authorization_params())
        return f"{self.auth_url}?{urlencode(params)}"

    def token_request_body(self, code: str, verifier: str, redirect_uri: str) -> dict:
        body: dict = {
            "grant_type": "authorization_code",
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
        }
        # Public client: omit unless a confidential-app secret is configured.
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body

    def refresh_request_body(self, refresh_token: str) -> dict:
        body: dict = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": self.client_id,
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body

    # ---- Device-code flow (#1275) ------------------------------------------
    # RFC 8628 device authorization grant. Used for the zero-setup sign-in that
    # does not need a per-user Azure app registration or a loopback redirect
    # URI — the user just enters a short code at a Microsoft URL. A public
    # client sends NO secret here either (unless a confidential app is
    # configured), matching the auth-code bodies above.

    def device_code_request_body(self, scopes: Iterable[str]) -> dict:
        """POST body for the ``/devicecode`` endpoint (request a user code)."""
        return {"client_id": self.client_id, "scope": " ".join(scopes)}

    def device_token_request_body(self, device_code: str) -> dict:
        """POST body for polling ``/token`` with a pending device code."""
        body: dict = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": self.client_id,
            "device_code": device_code,
        }
        if self.client_secret:
            body["client_secret"] = self.client_secret
        return body
