# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
End-to-end pipeline test for the email agent REST surface (#1229).

Drives the agent's REAL tool implementations against ``FakeGmailBackend``
(the synthetic-corpus seam, never live mail) through the full pipeline:

    pre-scan  ->  categorize  ->  summarize  ->  draft  ->  send-gate

and asserts each stage's contract. The LLM/agent reasoning is NOT invoked —
the deterministic heuristic fast-path and the pure ``*_impl`` tool functions
are exercised directly, so the run is deterministic and needs neither a live
Lemonade server nor an Anthropic key.

The final stage proves the confirmation gate (#1264): ``send_now`` is in
``TOOLS_REQUIRING_CONFIRMATION`` and is refused when the console denies the
confirmation, and allowed only once it is granted. The REST boundary
translation of this gate is covered separately in ``tests/test_api.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make tests.fixtures.email importable: parents[1]=tests, [2]=repo-root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageService lives in gaia_agent_email.api_routes, which imports FastAPI.
# FastAPI ships in the [api]/[ui] extras, NOT [dev] — skip the whole module
# (rather than error at collection) when it is unavailable, matching how the
# rest of the API tests degrade on a [dev]-only runner.
pytest.importorskip("fastapi")

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.
import pytest  # noqa: E402

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email import action_store  # noqa: E402
from gaia_agent_email.agent import EmailTriageAgent  # noqa: E402
from gaia_agent_email.api_routes import EmailTriageService  # noqa: E402
from gaia_agent_email.config import EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    get_message_impl,
    pre_scan_inbox_impl,
    triage_inbox_impl,
)
from gaia_agent_email.tools.reply_tools import (  # noqa: E402
    draft_reply_impl,
    send_now_impl,
)
from gaia_agent_email.tools.triage_heuristics import ALL_CATEGORIES  # noqa: E402

from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)


@pytest.fixture
def stub_inbox_path() -> Path:
    p = _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
    assert p.exists(), p
    return p


@pytest.fixture
def fake_gmail(stub_inbox_path) -> FakeGmailBackend:
    return FakeGmailBackend(stub_inbox_path)


@pytest.fixture
def db():
    """In-memory DatabaseMixin seeded with the email action-store schema —
    the same fixture shape used by the unit-tier reply/send tests."""

    class _DB(DatabaseMixin):
        def __init__(self):
            self.init_db(":memory:")

    d = _DB()
    action_store.init_schema(d)
    yield d
    d.close_db()


@pytest.fixture
def agent(fake_gmail, tmp_path):
    """A real ``EmailTriageAgent`` over the fake backends, with the LLM side
    (``AgentSDK``) patched so no Lemonade connection is made. Used to drive
    the agent's actual ``_execute_tool`` confirmation guard."""
    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        calendar_backend=FakeCalendarBackend(),
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
        debug=False,
    )
    with patch("gaia.agents.base.agent.AgentSDK") as mock_sdk:
        mock_sdk.return_value = MagicMock()
        a = EmailTriageAgent(config=cfg)
    yield a
    a.close_db()


class TestEmailPipelineE2E:
    """The full FakeGmail -> pre-scan -> categorize -> summarize -> draft ->
    send-gate pipeline, deterministic (no LLM)."""

    def test_pre_scan_stage_returns_structured_buckets(self, fake_gmail):
        out = pre_scan_inbox_impl(fake_gmail, max_messages=50)
        # Pre-scan surfaces a structured, bucketed view the chat card renders.
        assert isinstance(out, dict)
        # Backend was actually consulted (not a stub).
        methods = {c[0] for c in fake_gmail.transport.calls}
        assert "list_messages" in methods

    def test_categorize_stage_uses_frozen_taxonomy(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        results = out["results"]
        assert results, "stub inbox should yield messages"
        for r in results:
            assert r["category"] in ALL_CATEGORIES

    def test_summarize_stage_produces_contract_result_per_message(self, fake_gmail):
        """Feed each fetched message through the REST service's summarizer and
        assert it yields a contract-valid EmailTriageResult."""
        from gaia_agent_email.contract import EmailTriageResult

        service = EmailTriageService()
        listing = fake_gmail.list_messages(label_ids=["INBOX"], max_results=5)
        assert listing["messages"]
        for stub in listing["messages"]:
            msg = get_message_impl(fake_gmail, message_id=stub["id"])
            result = service.triage_gmail_message(
                msg, principal_email="user@example.com"
            )
            assert isinstance(result, EmailTriageResult)
            assert result.category.value in ALL_CATEGORIES
            assert result.summary

    def test_draft_stage_creates_proposal_without_sending(self, fake_gmail, db):
        listing = fake_gmail.list_messages(label_ids=["INBOX"], max_results=1)
        msg_id = listing["messages"][0]["id"]
        out = draft_reply_impl(
            fake_gmail, db, message_id=msg_id, body="Thanks, will do."
        )
        assert out["draft_id"]
        # Draft created a draft, but did NOT send anything.
        sent_calls = [c for c in fake_gmail.transport.calls if c[0] == "send_draft"]
        assert sent_calls == []
        assert any(c[0] == "create_draft" for c in fake_gmail.transport.calls)

    def test_send_gate_blocks_without_confirmation_then_allows(self, agent, fake_gmail):
        """Drive the agent's REAL confirmation guard end-to-end.

        ``send_now`` is in the agent's ``confirmation_required_tools()``, so
        ``_execute_tool`` consults ``console.confirm_tool_execution`` before
        running. When the console denies, the tool returns ``status:
        denied`` and NOTHING is sent to Gmail. When it grants, the send
        reaches the backend. This is the agent-side analogue of the REST
        confirmation gate."""
        from gaia_agent_email.agent import EmailTriageAgent

        assert "send_now" in EmailTriageAgent.confirmation_required_tools()

        args = {
            "to": "alice@example.com",
            "subject": "Re: budget",
            "body": "Approved.",
        }

        # DENIED: the console refuses -> the tool body never runs.
        agent.console.confirm_tool_execution = lambda name, a: False
        denied = agent._execute_tool("send_now", dict(args))
        assert denied.get("status") == "denied"
        assert not any(
            c[0] == "send_message" for c in fake_gmail.transport.calls
        ), "send must not reach Gmail while confirmation is denied"

        # GRANTED: the console approves -> the send reaches the backend.
        agent.console.confirm_tool_execution = lambda name, a: True
        granted = agent._execute_tool("send_now", dict(args))
        import json

        # send_now returns the canonical {"ok": true, "data": {...}} envelope.
        envelope = json.loads(granted)
        assert envelope["ok"] is True
        assert envelope["data"]["sent"] is True
        assert any(c[0] == "send_message" for c in fake_gmail.transport.calls)

    def test_full_pipeline_one_run(self, fake_gmail, db):
        """Single run touching every stage in order, asserting each contract."""
        from gaia_agent_email.contract import EmailTriageResult

        service = EmailTriageService()

        # 1. pre-scan
        pre = pre_scan_inbox_impl(fake_gmail, max_messages=50)
        assert isinstance(pre, dict)

        # 2. categorize
        triaged = triage_inbox_impl(fake_gmail, max_messages=50)
        assert triaged["results"]
        first_id = triaged["results"][0]["id"]

        # 3. summarize (contract result for the first message)
        msg = get_message_impl(fake_gmail, message_id=first_id)
        result = service.triage_gmail_message(msg, principal_email="user@example.com")
        assert isinstance(result, EmailTriageResult)

        # 4. draft (proposal only)
        drafted = draft_reply_impl(
            fake_gmail, db, message_id=first_id, body="Acknowledged."
        )
        assert drafted["draft_id"]
        assert not any(
            c[0] in ("send_message", "send_draft") for c in fake_gmail.transport.calls
        )

        # 5. send-gate: allowed once granted (the gate was approved).
        sent = send_now_impl(
            fake_gmail,
            db,
            to=drafted["to"] or "alice@example.com",
            subject=drafted["subject"],
            body="Acknowledged.",
        )
        assert sent["sent"] is True
