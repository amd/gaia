"""Google provider configuration and helpers.

This module centralizes URLs, scopes, and a small helper to load the
configured client_id (from env). The production `client_id` should be
configured via environment variable `GAIA_GOOGLE_CLIENT_ID` or by a secure
config provider (not committed to source control).
"""
import os
import hashlib

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPE_EMAIL = "https://www.googleapis.com/auth/userinfo.email"
SCOPE_GMAIL_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_GMAIL_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"


def get_client_id() -> str:
    cid = os.environ.get("GAIA_GOOGLE_CLIENT_ID")
    if not cid:
        raise RuntimeError("GAIA_GOOGLE_CLIENT_ID not configured in environment")
    return cid


def client_id_hash(client_id: str | None = None) -> str:
    cid = client_id or get_client_id()
    return hashlib.sha256(cid.encode("utf-8")).hexdigest()


__all__ = [
    "AUTH_URL",
    "TOKEN_URL",
    "SCOPE_EMAIL",
    "SCOPE_GMAIL_READONLY",
    "SCOPE_GMAIL_COMPOSE",
    "get_client_id",
    "client_id_hash",
]
