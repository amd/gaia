# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
REST contract-surface tests for the Email Triage agent (#1645).

These productionize the cross-implementation contract: the committed
``openapi.email.json`` is what the ``@amd-gaia/agent-email`` npm client and the
future native build conform to, and these tests fail CI if any of the three
sources drift apart:

1. ``version.py`` constants — ``API_VERSION`` must equal the frozen contract's
   ``SCHEMA_VERSION`` (a contract bump is an API bump), and ``AGENT_VERSION``
   must match the installed package metadata.
2. ``api_routes.py`` response models — every documented route's 200 schema must
   reference the contract/local model the handler declares.
3. The exported ``openapi.email.json`` — must be byte-identical to a freshly
   generated spec (otherwise it is stale and must be regenerated).

The runtime ``/health`` and ``/version`` endpoints are exercised through a
FastAPI ``TestClient`` against the same minimal app the exporter builds — no live
mailbox, no LLM.
"""

from __future__ import annotations

import pytest

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it.
pytest.importorskip("gaia_agent_email")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import __version__ as package_version  # noqa: E402
from gaia_agent_email import export_openapi  # noqa: E402
from gaia_agent_email.contract import (  # noqa: E402
    SCHEMA_VERSION,
    EmailTriageRequest,
    EmailTriageResponse,
)
from gaia_agent_email.version import AGENT_VERSION, API_VERSION  # noqa: E402

# Routes whose 200 response model is part of the published contract surface.
# Maps (method, path) -> the component schema name the handler declares.
_EXPECTED_RESPONSE_MODELS = {
    ("post", "/v1/email/triage"): "EmailTriageResponse",
    ("post", "/v1/email/draft"): "EmailDraftResponse",
    ("post", "/v1/email/send"): "EmailSendResponse",
    # Mailbox actions (schema 2.1, #1779).
    ("post", "/v1/email/confirm"): "EmailActionConfirmResponse",
    ("post", "/v1/email/archive"): "EmailArchiveResponse",
    ("post", "/v1/email/unarchive"): "EmailUnarchiveResponse",
    ("post", "/v1/email/quarantine"): "EmailQuarantineResponse",
    ("post", "/v1/email/unquarantine"): "EmailUnquarantineResponse",
    ("get", "/v1/email/health"): "HealthResponse",
    ("get", "/v1/email/version"): "VersionResponse",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    """The freshly built OpenAPI spec (what the committed artifact should be)."""
    return export_openapi.build_spec()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(export_openapi.build_app())


# ---------------------------------------------------------------------------
# 1. Version constants — single source of truth
# ---------------------------------------------------------------------------


def test_api_version_is_the_contract_version():
    # apiVersion MUST be the frozen contract version so bumping the contract
    # bumps the API — they cannot drift. This is the constant the freeze server
    # can import instead of carrying its own copy (#1648).
    assert API_VERSION == SCHEMA_VERSION


def test_agent_version_matches_package_export():
    assert AGENT_VERSION == package_version


def test_agent_version_matches_package_metadata():
    # The pyproject ``version`` and the in-code ``AGENT_VERSION`` must agree, or
    # a published wheel reports a build number its own code denies.
    from importlib.metadata import version as dist_version

    assert dist_version("gaia-agent-email") == AGENT_VERSION


# ---------------------------------------------------------------------------
# 2. Spec ↔ contract.py consistency
# ---------------------------------------------------------------------------


def test_spec_info_version_is_api_version(spec):
    assert spec["info"]["version"] == API_VERSION


def test_contract_models_present_in_spec(spec):
    schemas = spec["components"]["schemas"]
    for name in ("EmailTriageRequest", "EmailTriageResponse", "EmailTriageResult"):
        assert name in schemas, f"{name} missing from exported OpenAPI components"


@pytest.mark.parametrize("model", [EmailTriageRequest, EmailTriageResponse])
def test_spec_schema_matches_contract_model(spec, model):
    """Field names + required set in the exported spec must match the pydantic
    contract model — drift between contract.py and the published spec fails."""
    component = spec["components"]["schemas"][model.__name__]
    pyd = model.model_json_schema()
    assert set(component.get("properties", {})) == set(pyd.get("properties", {}))
    assert set(component.get("required", [])) == set(pyd.get("required", []))


# ---------------------------------------------------------------------------
# 3. Spec ↔ api_routes.py response-model consistency
# ---------------------------------------------------------------------------


def test_documented_routes_match_expected_set(spec):
    documented = {
        (method, path) for path, ops in spec["paths"].items() for method in ops
    }
    assert documented == set(_EXPECTED_RESPONSE_MODELS)


@pytest.mark.parametrize(
    ("method", "path", "model_name"),
    [(m, p, n) for (m, p), n in _EXPECTED_RESPONSE_MODELS.items()],
)
def test_route_response_model_in_spec(spec, method, path, model_name):
    schema = spec["paths"][path][method]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema == {"$ref": f"#/components/schemas/{model_name}"}


# ---------------------------------------------------------------------------
# 4. Committed artifact is not stale
# ---------------------------------------------------------------------------


def test_committed_openapi_artifact_is_up_to_date():
    assert export_openapi.check_artifact(), (
        "openapi.email.json is stale. Regenerate it with:\n"
        "  python -m gaia_agent_email.export_openapi"
    )


# ---------------------------------------------------------------------------
# 5. Runtime /health and /version (dependency-light — no mail, no LLM)
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    resp = client.get("/v1/email/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "service": "gaia-agent-email"}


def test_version_endpoint_reports_constants(client):
    resp = client.get("/v1/email/version")
    assert resp.status_code == 200
    assert resp.json() == {
        "apiVersion": API_VERSION,
        "agentVersion": AGENT_VERSION,
    }


def test_version_endpoint_rejects_unknown_field_loudly(client):
    # _Strict models forbid extras; a GET has no body, but confirm the response
    # shape carries exactly the two documented keys (no silent extras).
    body = client.get("/v1/email/version").json()
    assert set(body) == {"apiVersion", "agentVersion"}


# ---------------------------------------------------------------------------
# 6. Mailbox actions — archive / quarantine (schema 2.1, #1779)
#
# The confirmation-token gate is the safety property under test: a destructive
# action without a valid, action-bound token must be rejected with 403 BEFORE
# any backend call. The happy paths use an in-memory fake Gmail backend +
# real in-memory action log (injected via the module-level resolvers the
# handlers call — the same seam ``send`` uses), so no live mailbox is touched.
# ---------------------------------------------------------------------------


class _FakeMailbox:
    """Minimal in-memory, label-based (Gmail-style) backend for the action impls.

    Tracks a label set per message id; archive removes INBOX (stable id, like
    Gmail), quarantine adds the quarantine label and archives. Enough surface
    for ``archive_message_impl`` / ``undo_archive_batch_impl`` /
    ``quarantine_phishing_impl`` / ``unquarantine_impl`` to round-trip.
    """

    def __init__(self):
        self.messages = {
            "m1": {"labelIds": ["INBOX", "UNREAD"], "threadId": "t1"},
            "m2": {"labelIds": ["INBOX"], "threadId": "t2"},
        }
        self.labels = [{"id": "INBOX", "name": "INBOX"}]
        self._next_label = 1

    def get_message(self, message_id):
        msg = self.messages[message_id]
        return {"labelIds": list(msg["labelIds"]), "threadId": msg["threadId"]}

    def archive_message(self, message_id):
        labels = self.messages[message_id]["labelIds"]
        if "INBOX" in labels:
            labels.remove("INBOX")
        return {"id": message_id}

    def unarchive_message(self, message_id, prior_labels):
        labels = self.messages[message_id]["labelIds"]
        for lab in prior_labels or ["INBOX"]:
            if lab not in labels:
                labels.append(lab)
        return {"id": message_id}

    def list_labels(self):
        return list(self.labels)

    def create_label(self, *, name):
        self._next_label += 1
        entry = {"id": f"Label_{self._next_label}", "name": name}
        self.labels.append(entry)
        return entry

    def add_label(self, message_id, label_id):
        labels = self.messages[message_id]["labelIds"]
        if label_id not in labels:
            labels.append(label_id)
        return {"id": message_id}

    def remove_label(self, message_id, label_id):
        labels = self.messages[message_id]["labelIds"]
        if label_id in labels:
            labels.remove(label_id)
        return {"id": message_id}


@pytest.fixture
def action_env(monkeypatch):
    """Wire the action handlers to a fake backend + in-memory action log.

    Returns ``(client, mailbox)`` — the mailbox is inspectable so a test can
    assert inbox membership after archive/undo.
    """
    from gaia_agent_email import action_store
    from gaia_agent_email import api_routes as email_routes

    from gaia.database.mixin import DatabaseMixin

    class _DB(DatabaseMixin):
        pass

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)

    mailbox = _FakeMailbox()
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: db)
    monkeypatch.setattr(
        email_routes, "_resolve_mutate_backend", lambda provider: (mailbox, "google")
    )
    monkeypatch.setattr(
        email_routes, "_resolve_backend_for_provider", lambda provider: mailbox
    )
    client = TestClient(export_openapi.build_app())
    return client, mailbox


def test_archive_without_token_is_rejected(client):
    # The gate fires before any backend resolution — no fake needed.
    resp = client.post("/v1/email/archive", json={"message_id": "m1"})
    assert resp.status_code == 403
    assert "confirmation token" in resp.json()["detail"].lower()


def test_quarantine_without_token_is_rejected(client):
    resp = client.post(
        "/v1/email/quarantine", json={"message_id": "m1", "is_phishing": True}
    )
    assert resp.status_code == 403
    assert "confirmation token" in resp.json()["detail"].lower()


def test_confirm_then_archive_round_trips(action_env):
    client, mailbox = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m1"}
    ).json()["confirmation_token"]

    resp = client.post(
        "/v1/email/archive",
        json={"message_id": "m1", "confirmation_token": tok},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["archived"] is True
    assert body["schema_version"] == SCHEMA_VERSION
    assert body["post_archive_id"] == "m1"  # label-based backend keeps the id
    assert body["undo_window_seconds"] == 30
    assert "INBOX" not in mailbox.messages["m1"]["labelIds"]

    # The same token cannot be replayed (single-use).
    replay = client.post(
        "/v1/email/archive", json={"message_id": "m1", "confirmation_token": tok}
    )
    assert replay.status_code == 403

    # Undo (ungated) restores it to the inbox via the batch handle.
    undo = client.post("/v1/email/unarchive", json={"batch_id": body["batch_id"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["restored"] == 1
    assert "INBOX" in mailbox.messages["m1"]["labelIds"]


def test_archive_token_cannot_authorize_quarantine(action_env):
    # A token minted for archive must not authorize quarantine of the same id.
    client, _ = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m1"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m1", "is_phishing": True, "confirmation_token": tok},
    )
    assert resp.status_code == 403


def test_confirm_then_quarantine_round_trips(action_env):
    client, mailbox = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "quarantine", "message_id": "m2"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m2", "is_phishing": True, "confirmation_token": tok},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["quarantined"] is True
    qlabel = body["quarantine_label_id"]
    assert qlabel in mailbox.messages["m2"]["labelIds"]
    assert "INBOX" not in mailbox.messages["m2"]["labelIds"]

    undo = client.post("/v1/email/unquarantine", json={"action_id": body["action_id"]})
    assert undo.status_code == 200, undo.text
    assert undo.json()["message_id"] == "m2"
    assert qlabel not in mailbox.messages["m2"]["labelIds"]
    assert "INBOX" in mailbox.messages["m2"]["labelIds"]


def test_quarantine_refuses_non_phishing(action_env):
    client, _ = action_env
    tok = client.post(
        "/v1/email/confirm", json={"action": "quarantine", "message_id": "m2"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m2", "is_phishing": False, "confirmation_token": tok},
    )
    assert resp.status_code == 400
    assert "is_phishing" in resp.json()["detail"].lower()


def test_unquarantine_unknown_action_fails_loudly(action_env):
    # Unknown/expired action_id → 409 short-circuit, even before backend resolution.
    client, _ = action_env
    resp = client.post("/v1/email/unquarantine", json={"action_id": "nope"})
    assert resp.status_code == 409
    assert "undo window" in resp.json()["detail"].lower()


def test_unarchive_expired_window_fails_loudly(action_env):
    client, _ = action_env
    resp = client.post("/v1/email/unarchive", json={"batch_id": "does-not-exist"})
    assert resp.status_code == 409
    assert "undo window" in resp.json()["detail"].lower()


def test_action_request_rejects_unknown_field(client):
    # _Strict contract models forbid extras → 422, never silently dropped.
    resp = client.post(
        "/v1/email/confirm",
        json={"action": "archive", "message_id": "m1", "bogus": 1},
    )
    assert resp.status_code == 422


class _FolderMailbox:
    """Outlook-style fake: archive is a folder MOVE that mints a NEW id (#1738).

    The whole point is to prove archive→unarchive undo uses the post-archive id,
    not the stale request id — undo against the original id would 404 here.
    """

    def __init__(self):
        self.inbox = {"m1"}
        self._archived = {}  # new_id -> original_id
        self._n = 0
        self.unarchive_calls = []

    def get_message(self, message_id):
        return {"labelIds": ["INBOX"], "threadId": "t1"}

    def archive_message(self, message_id):
        self._n += 1
        new_id = f"{message_id}-arch{self._n}"
        self.inbox.discard(message_id)
        self._archived[new_id] = message_id
        return {"id": new_id}

    def unarchive_message(self, message_id, prior_labels):
        self.unarchive_calls.append(message_id)
        if message_id not in self._archived:
            # Mirrors the live #1738 failure: a stale (pre-move) id 404s.
            raise RuntimeError(f"unknown id {message_id!r} (folder move changed it)")
        self.inbox.add(self._archived.pop(message_id))
        return {"id": message_id}


def test_archive_undo_uses_post_archive_id_after_folder_move(monkeypatch):
    # #1738: a folder-based backend mints a new id on archive. Undo MUST restore
    # by that post-archive id (surfaced in the response), not the request id.
    from gaia_agent_email import action_store
    from gaia_agent_email import api_routes as email_routes

    from gaia.database.mixin import DatabaseMixin

    class _DB(DatabaseMixin):
        pass

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)
    mailbox = _FolderMailbox()
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: db)
    monkeypatch.setattr(
        email_routes, "_resolve_mutate_backend", lambda p: (mailbox, "microsoft")
    )
    monkeypatch.setattr(
        email_routes, "_resolve_backend_for_provider", lambda p: mailbox
    )
    client = TestClient(export_openapi.build_app())

    tok = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": "m1"}
    ).json()["confirmation_token"]
    body = client.post(
        "/v1/email/archive", json={"message_id": "m1", "confirmation_token": tok}
    ).json()
    assert body["post_archive_id"] != "m1"  # the move changed the id
    assert "m1" not in mailbox.inbox

    undo = client.post("/v1/email/unarchive", json={"batch_id": body["batch_id"]})
    assert undo.status_code == 200, undo.text
    # Undo restored by the NEW id, not the stale request id — the #1738 fix.
    assert mailbox.unarchive_calls == [body["post_archive_id"]]
    assert "m1" in mailbox.inbox


def test_quarantine_rejected_for_outlook(monkeypatch):
    # Quarantine is Gmail-only: an Outlook mailbox is refused with an actionable
    # 400 rather than performing a folder move its undo cannot reverse.
    from gaia_agent_email import api_routes as email_routes

    monkeypatch.setattr(
        email_routes, "_resolve_mutate_backend", lambda p: (object(), "microsoft")
    )
    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: None)
    client = TestClient(export_openapi.build_app())

    tok = client.post(
        "/v1/email/confirm", json={"action": "quarantine", "message_id": "m1"}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/quarantine",
        json={"message_id": "m1", "is_phishing": True, "confirmation_token": tok},
    )
    assert resp.status_code == 400
    assert "gmail" in resp.json()["detail"].lower()
