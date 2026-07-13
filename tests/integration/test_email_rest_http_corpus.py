# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""True-HTTP integration test: REST endpoints driven with a FastAPI
``TestClient`` against the full vendor-derived corpus (#1897).

Every REST-contract test in
``hub/agents/python/email/tests/test_rest_contract.py`` proves the *shape* of
the wire contract against small, hand-built fixtures. None of them drive the
endpoints against the full corpus, so a bug that only manifests at corpus
scale (label filtering across hundreds of messages, an archive that silently
fails to leave INBOX, ...) has no HTTP-level test surface. This file closes
that gap: three tests exercise ``/search``, ``/prescan`` and
``/confirm``+``/archive`` over real HTTP against the full corpus, using the
same per-endpoint injection seams as ``test_rest_contract.py``.

LLM-free throughout (heuristic-only triage path, same as the sibling
``prescan_client`` fixture in ``test_rest_contract.py``).

Counts are derived from the corpus at test time, never hardcoded: the corpus
is vendor-derived and regenerates from a committed seed (#1911 grew it
249->299), so a hardcoded expected count would silently rot the moment the
seed changes.
"""

from __future__ import annotations

import sys
from collections import Counter
from email.utils import parseaddr
from pathlib import Path
from typing import Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytestmark = pytest.mark.integration

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip cleanly when a framework-only env lacks it. fastapi comes from the
# [api] extra, so a [dev]-only env must skip too — not error at collection.
pytest.importorskip("gaia_agent_email")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from gaia_agent_email import api_routes as email_routes  # noqa: E402
from gaia_agent_email import export_openapi  # noqa: E402

from tests.fixtures.email.fake_gmail import FakeGmailBackend  # noqa: E402
from tests.fixtures.email.generate_mbox import (  # noqa: E402
    TOTAL_MESSAGES as _EXPECTED_TOTAL,
)

# ``tests/integration/`` does NOT inherit ``tests/fixtures/email/conftest.py``
# fixtures (those are scoped to that subtree) -- import the path constant
# directly, exactly as the sibling test_email_corpus_alignment.py does.
FIXTURES_DIR = _REPO_ROOT / "tests" / "fixtures" / "email"
CORPUS_INBOX_MBOX = FIXTURES_DIR / "synthetic_inbox.mbox"


def _from_header(msg: dict) -> str:
    headers = {
        (h.get("name") or "").lower(): h.get("value", "")
        for h in (msg.get("payload") or {}).get("headers", [])
    }
    return headers.get("from", "")


def _from_address(msg: dict) -> str:
    """The bare email address from the ``From`` header (no display name).

    The fake's ``from:`` query matching splits the query string on
    whitespace (a tiny Gmail-DSL subset, ``fake_gmail._query_matches``), so a
    query built from the full "Display Name <addr>" header breaks into
    multiple mismatched tokens. Use the bare address only.
    """
    return parseaddr(_from_header(msg))[1].lower()


def _pick_common_sender(backend: FakeGmailBackend) -> Tuple[str, int]:
    """Pick a real corpus sender with enough (but not too many) messages to
    exercise a non-trivial /search without tripping the route's
    ``max_results<=100`` cap.

    Derived from the corpus at test time -- never a hardcoded address or
    count -- so this survives a corpus regeneration (#1911 already grew the
    corpus once).
    """
    counts = Counter(
        _from_address(m) for m in backend._messages.values()  # noqa: SLF001
    )
    for addr, n in counts.most_common():
        if 5 <= n <= 90:
            return addr, n
    raise AssertionError(
        "no corpus sender has between 5 and 90 messages -- corpus shape "
        "changed; the derivation in this test needs revisiting"
    )


@pytest.fixture
def corpus_backend() -> FakeGmailBackend:
    assert CORPUS_INBOX_MBOX.exists(), CORPUS_INBOX_MBOX
    backend = FakeGmailBackend(CORPUS_INBOX_MBOX)
    assert len(backend._messages) == _EXPECTED_TOTAL  # noqa: SLF001 -- sanity
    return backend


def test_search_over_http_returns_corpus_derived_count(corpus_backend):
    """POST /v1/email/search over the wire, against the full corpus, returns
    exactly the number of matches the corpus actually contains for a real
    sender query -- not a hand-picked number, and not merely `count > 0`.
    """
    addr, expected_n = _pick_common_sender(corpus_backend)
    assert expected_n >= 5  # floor: an empty/broken corpus can't pass on 0==0

    app = export_openapi.build_app()
    app.dependency_overrides[email_routes.get_search_backend] = lambda: corpus_backend
    try:
        client = TestClient(app)
        resp = client.post(
            "/v1/email/search",
            json={"query": f"from:{addr}", "max_results": 100},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == expected_n
    assert len(body["messages"]) == expected_n
    assert all(addr in m["from"].lower() for m in body["messages"])


def test_prescan_over_http_sees_the_full_corpus(corpus_backend):
    """POST /v1/email/prescan against the corpus-backed fake actually scans
    corpus messages (the populated-inbox branch, not the 0-connected 503).
    """
    app = export_openapi.build_app()
    app.dependency_overrides[email_routes.get_prescan_backend] = lambda: corpus_backend
    try:
        client = TestClient(app)
        resp = client.post("/v1/email/prescan", json={"max_messages": 100})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    totals = result["totals"]
    scanned = (
        totals["urgent"]
        + totals["actionable"]
        + totals["informational"]
        + totals["suggested_archives"]
    )
    # 100 corpus messages went through the heuristic path -- not an empty scan.
    assert scanned > 0


@pytest.fixture
def mutate_client(monkeypatch, corpus_backend):
    """Wire archive/confirm to the corpus-backed fake + an in-memory action
    log -- mirrors ``action_env`` in test_rest_contract.py, but over the real
    corpus instead of a hand-built ``_FakeMailbox``.
    """
    from gaia_agent_email import action_store

    from gaia.database.mixin import DatabaseMixin

    class _DB(DatabaseMixin):
        pass

    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)

    monkeypatch.setattr(email_routes, "resolve_action_db", lambda: db)
    monkeypatch.setattr(
        email_routes,
        "_resolve_mutate_backend",
        lambda provider: (corpus_backend, "google"),
    )
    monkeypatch.setattr(
        email_routes, "_resolve_backend_for_provider", lambda provider: corpus_backend
    )
    app = export_openapi.build_app()
    app.dependency_overrides[email_routes.get_search_backend] = lambda: corpus_backend
    return TestClient(app)


def test_archive_over_http_removes_message_from_inbox_search(
    mutate_client, corpus_backend
):
    """Headline HTTP x corpus test: an archive over the wire actually mutates
    the mailbox -- the archived message drops out of an INBOX-filtered
    /search that previously found it, at corpus scale.

    ``test_confirm_then_archive_round_trips`` (test_rest_contract.py) already
    proves the archive-over-HTTP round trip against a small ``_FakeMailbox``;
    the new value here is the same proof against the 299-message corpus, via
    a corpus-scale /search re-query instead of inspecting the fake directly.
    """
    client = mutate_client
    mid, target_msg = next(iter(corpus_backend._messages.items()))  # noqa: SLF001
    sender = _from_address(target_msg)
    assert sender  # sanity: the picked message actually has a From header

    def _search_sender() -> Tuple[int, set]:
        resp = client.post(
            "/v1/email/search",
            json={
                "labels": ["INBOX"],
                "query": f"from:{sender}",
                "max_results": 100,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        return body["count"], {m["id"] for m in body["messages"]}

    before_count, before_ids = _search_sender()
    assert mid in before_ids

    token = client.post(
        "/v1/email/confirm", json={"action": "archive", "message_id": mid}
    ).json()["confirmation_token"]
    resp = client.post(
        "/v1/email/archive", json={"message_id": mid, "confirmation_token": token}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["archived"] is True

    after_count, after_ids = _search_sender()
    assert mid not in after_ids
    assert after_count == before_count - 1
