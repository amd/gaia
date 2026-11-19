# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Agent UI's connector routes with two Google accounts connected."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from gaia.connectors import accounts
from gaia.connectors.grants import grant_agent, list_agent_grants
from tests.unit.connectors.test_router_connectors import (  # noqa: F401
    UI_HEADER,
    isolated_registry,
)

REVOKE_URL = "https://oauth2.googleapis.com/revoke"


@pytest.fixture
def two_gmails(google_provider):
    for sub, email in (
        ("alice-sub", "alice@example.com"),
        ("bob-sub", "bob@example.com"),
    ):
        accounts.save_signed_in_account(
            "google",
            account_id=sub,
            email=email,
            refresh_token=f"rt-{sub}",
            scopes=["openid"],
            client_id_hash=google_provider.client_id_hash,
        )


@pytest.fixture
def revoked():
    tokens = []

    def _revoke(request: httpx.Request) -> httpx.Response:
        tokens.append(parse_qs(request.content.decode())["token"][0])
        return httpx.Response(200)

    with respx.mock(assert_all_called=False) as router:
        router.post(REVOKE_URL).mock(side_effect=_revoke)
        yield tokens


def _summary(client):
    resp = client.get("/api/connectors/google")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_summary_lists_every_account(ui_api_client, two_gmails):
    body = _summary(ui_api_client)

    assert body["configured"] is True
    # Unchanged for one-account callers: the default account's address.
    assert body["account_id"] == "alice@example.com"
    assert body["accounts"] == [
        {
            "account_id": "alice-sub",
            "email": "alice@example.com",
            "status": "ok",
            "is_default": True,
        },
        {
            "account_id": "bob-sub",
            "email": "bob@example.com",
            "status": "ok",
            "is_default": False,
        },
    ]


def test_a_never_connected_connector_lists_no_accounts(ui_api_client, google_provider):
    body = _summary(ui_api_client)

    assert body["configured"] is False
    assert body["accounts"] == []


def test_choosing_the_default_account(ui_api_client, two_gmails):
    resp = ui_api_client.patch(
        "/api/connectors/google/accounts/bob@example.com",
        json={"is_default": True},
        headers=UI_HEADER,
    )

    assert resp.status_code == 200, resp.text
    assert [a["is_default"] for a in resp.json()["accounts"]] == [False, True]
    assert _summary(ui_api_client)["account_id"] == "bob@example.com"


def test_unsetting_the_default_is_refused(ui_api_client, two_gmails):
    resp = ui_api_client.patch(
        "/api/connectors/google/accounts/bob-sub",
        json={"is_default": False},
        headers=UI_HEADER,
    )
    assert resp.status_code == 400


def test_an_unknown_account_is_a_404_naming_the_choices(ui_api_client, two_gmails):
    resp = ui_api_client.patch(
        "/api/connectors/google/accounts/carol@example.com",
        json={"is_default": True},
        headers=UI_HEADER,
    )

    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["error"] == "unknown_account"
    assert detail["choices"] == ["alice@example.com", "bob@example.com"]


def test_account_routes_need_the_ui_header(ui_api_client, two_gmails):
    assert (
        ui_api_client.delete("/api/connectors/google/accounts/bob-sub").status_code
        == 403
    )
    assert (
        ui_api_client.patch(
            "/api/connectors/google/accounts/bob-sub", json={"is_default": True}
        ).status_code
        == 403
    )


def test_removing_one_account_keeps_the_other_and_the_grants(
    ui_api_client, two_gmails, revoked
):
    grant_agent("google", "agent-A", ["openid"])

    resp = ui_api_client.delete(
        "/api/connectors/google/accounts/alice-sub", headers=UI_HEADER
    )

    assert resp.status_code == 204, resp.text
    assert revoked == ["rt-alice-sub"]
    body = _summary(ui_api_client)
    assert [a["email"] for a in body["accounts"]] == ["bob@example.com"]
    assert body["account_id"] == "bob@example.com"
    assert list_agent_grants("google") == {"agent-A": ["openid"]}

    # The last account going takes the grants with it, as a full disconnect does.
    ui_api_client.delete("/api/connectors/google/accounts/bob-sub", headers=UI_HEADER)
    assert list_agent_grants("google") == {}
    assert _summary(ui_api_client)["configured"] is False


def test_plain_disconnect_asks_which_account_when_there_are_several(
    ui_api_client, two_gmails, revoked
):
    grant_agent("google", "agent-A", ["openid"])

    resp = ui_api_client.delete("/api/connectors/google", headers=UI_HEADER)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "account_choice_required"
    assert detail["connector_id"] == "google"
    assert detail["choices"] == ["alice@example.com", "bob@example.com"]
    assert "--" not in detail["message"]
    assert revoked == []
    assert len(_summary(ui_api_client)["accounts"]) == 2
    assert list_agent_grants("google") == {"agent-A": ["openid"]}


def test_plain_disconnect_of_one_account_needs_no_choice(
    ui_api_client, google_provider, revoked
):
    accounts.save_signed_in_account(
        "google",
        account_id="alice-sub",
        email="alice@example.com",
        refresh_token="rt-alice-sub",
        scopes=["openid"],
        client_id_hash=google_provider.client_id_hash,
    )

    resp = ui_api_client.delete("/api/connectors/google", headers=UI_HEADER)

    assert resp.status_code == 204, resp.text
    assert revoked == ["rt-alice-sub"]


def test_disconnect_all_removes_every_account(ui_api_client, two_gmails, revoked):
    resp = ui_api_client.delete("/api/connectors/google?all=true", headers=UI_HEADER)

    assert resp.status_code == 204, resp.text
    assert sorted(revoked) == ["rt-alice-sub", "rt-bob-sub"]
    assert _summary(ui_api_client)["accounts"] == []


@pytest.mark.parametrize(
    "body, prompt, hint",
    [
        ({"add_account": True}, ["select_account consent"], None),
        ({"account": "bob-sub"}, ["consent"], ["bob@example.com"]),
    ],
)
def test_authorize_carries_the_account_choice(
    ui_api_client, two_gmails, no_browser, body, prompt, hint
):
    resp = ui_api_client.post(
        "/api/connectors/google/authorize",
        json={"scopes": ["openid"], **body},
        headers=UI_HEADER,
    )
    assert resp.status_code == 200, resp.text
    info = resp.json()
    try:
        query = parse_qs(urlparse(info["authorization_url"]).query)
        assert query["prompt"] == prompt
        assert query.get("login_hint") == hint
    finally:
        ui_api_client.delete(
            f"/api/connectors/_flows/{info['flow_id']}", headers=UI_HEADER
        )
