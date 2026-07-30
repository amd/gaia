# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Microsoft account-type derivation from the id_token ``tid`` claim (#2466).

Personal Microsoft accounts always carry the well-known consumers tenant;
work/school accounts carry their organization's Entra tenant id. That makes the
classification exact rather than heuristic, and — because it comes from a token
claim — testable end to end with synthetic id_tokens and no live account.
"""

from __future__ import annotations

import base64
import json

import pytest

from gaia.connectors.flow import (
    _decode_email_from_id_token,
    _decode_id_token_claims,
    _resolve_account_type,
)
from gaia.connectors.providers.microsoft import (
    _MSA_TENANT_ID,
    ACCOUNT_TYPE_PERSONAL,
    ACCOUNT_TYPE_WORK,
    MicrosoftOAuthProvider,
    account_type_for_tenant,
)
from gaia.connectors.store import peek_connection, save_connection

_WORK_TENANT = "72f988bf-86f1-41af-91ab-2d7cd011db47"


def _id_token(**claims) -> str:
    """A synthetic (unsigned) id_token carrying the given claims."""

    def seg(payload: dict) -> str:
        raw = json.dumps(payload).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{seg({'alg': 'none'})}.{seg(claims)}.signature"


# ----------------------------------------------------------------------
# tid → account type
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "tenant, expected",
    [
        (_MSA_TENANT_ID, ACCOUNT_TYPE_PERSONAL),
        (_MSA_TENANT_ID.upper(), ACCOUNT_TYPE_PERSONAL),
        (f"  {_MSA_TENANT_ID}  ", ACCOUNT_TYPE_PERSONAL),
        (_WORK_TENANT, ACCOUNT_TYPE_WORK),
        (None, None),
        ("", None),
        ("   ", None),
    ],
)
def test_account_type_for_tenant(tenant, expected):
    assert account_type_for_tenant(tenant) == expected


def test_msa_tenant_is_the_documented_consumers_guid():
    """Pin the constant — a typo here misclassifies every personal account."""
    assert _MSA_TENANT_ID == "9188040d-6c67-4c5b-b112-36a304b66dad"


@pytest.mark.parametrize(
    "claims, expected",
    [
        ({"tid": _MSA_TENANT_ID}, ACCOUNT_TYPE_PERSONAL),
        ({"tid": _WORK_TENANT}, ACCOUNT_TYPE_WORK),
        ({"oid": "abc"}, None),
        ({}, None),
    ],
)
def test_provider_classify_account_type(claims, expected):
    assert MicrosoftOAuthProvider.classify_account_type(claims) == expected


# ----------------------------------------------------------------------
# id_token decoding
# ----------------------------------------------------------------------


def test_decode_id_token_claims_round_trip():
    claims = _decode_id_token_claims(
        _id_token(tid=_WORK_TENANT, preferred_username="user@example.com")
    )
    assert claims["tid"] == _WORK_TENANT
    assert claims["preferred_username"] == "user@example.com"


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b", "a.!!!not-base64!!!.c"])
def test_decode_id_token_claims_tolerates_garbage(token):
    assert _decode_id_token_claims(token) == {}


def test_email_extraction_still_works_after_the_refactor():
    """The claim-priority order (#1275) must be unchanged."""
    assert (
        _decode_email_from_id_token(_id_token(email="a@example.com")) == "a@example.com"
    )
    assert (
        _decode_email_from_id_token(_id_token(preferred_username="b@example.com"))
        == "b@example.com"
    )
    assert (
        _decode_email_from_id_token(_id_token(upn="c@example.com")) == "c@example.com"
    )
    assert _decode_email_from_id_token(_id_token(tid=_WORK_TENANT)) is None
    assert _decode_email_from_id_token("garbage") is None


# ----------------------------------------------------------------------
# The flow-layer hook
# ----------------------------------------------------------------------


class _NoClassifyProvider:
    provider_id = "google"


class _ExplodingProvider:
    provider_id = "microsoft"

    @staticmethod
    def classify_account_type(claims):
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    "tid, expected",
    [(_MSA_TENANT_ID, ACCOUNT_TYPE_PERSONAL), (_WORK_TENANT, ACCOUNT_TYPE_WORK)],
)
def test_resolve_account_type_from_a_microsoft_id_token(tid, expected):
    provider = MicrosoftOAuthProvider(client_id="test-client")
    assert _resolve_account_type(provider, _id_token(tid=tid)) == expected


def test_resolve_account_type_is_none_for_a_provider_without_the_hook():
    """Google has no tenant to inspect — unknown, not guessed."""
    assert (
        _resolve_account_type(_NoClassifyProvider(), _id_token(email="a@b.c")) is None
    )


def test_resolve_account_type_is_none_without_a_decodable_token():
    provider = MicrosoftOAuthProvider(client_id="test-client")
    assert _resolve_account_type(provider, "") is None
    assert _resolve_account_type(provider, "garbage") is None
    assert _resolve_account_type(provider, _id_token(email="a@b.c")) is None


def test_a_broken_classifier_never_fails_the_connect():
    assert (
        _resolve_account_type(_ExplodingProvider(), _id_token(tid=_WORK_TENANT)) is None
    )


# ----------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------


def test_account_type_persists_on_the_connection_blob():
    save_connection(
        provider="microsoft",
        account_email="user@contoso.com",
        refresh_token="rt",
        scopes=["openid"],
        client_id_hash="hash",
        account_type=ACCOUNT_TYPE_WORK,
    )
    assert peek_connection("microsoft")["account_type"] == ACCOUNT_TYPE_WORK


def test_an_unknown_account_type_leaves_the_key_absent():
    """A Google connection records no kind at all — readers see 'unknown'."""
    save_connection(
        provider="google",
        account_email="user@gmail.com",
        refresh_token="rt",
        scopes=["openid"],
        client_id_hash="hash",
    )
    blob = peek_connection("google")
    assert "account_type" not in blob
    assert blob.get("account_type") is None


def test_refresh_token_rotation_preserves_the_account_type():
    """A rotation re-saves the blob; losing the kind would silently reclassify."""
    save_connection(
        provider="microsoft",
        account_email="user@contoso.com",
        refresh_token="rt-1",
        scopes=["openid"],
        client_id_hash="hash",
        account_type=ACCOUNT_TYPE_WORK,
    )
    stored = peek_connection("microsoft")

    save_connection(
        provider="microsoft",
        account_email=stored["account_email"],
        refresh_token="rt-2",
        scopes=stored["scopes"],
        client_id_hash="hash",
        connected_at=stored["connected_at"],
        account_type=stored.get("account_type"),
    )

    rotated = peek_connection("microsoft")
    assert rotated["refresh_token"] == "rt-2"
    assert rotated["account_type"] == ACCOUNT_TYPE_WORK
