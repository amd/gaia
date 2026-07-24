# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Behavioral tests for the email-agent tool mixins.

Each tool's pure ``*_impl`` function is exercised against a
``FakeGmailBackend`` + an in-memory ``DatabaseMixin``. Tests cover:

- Read tools return the expected envelope shape and wrap body content
  in untrusted-input delimiters (Phase I1).
- Organize tools record actions with the prior-labels payload so the
  undo path can restore them.
- Reply tools set ``In-Reply-To`` / ``References`` correctly across a
  References chain.
- Delete tools enforce the action-store ordering invariant: Gmail call
  first, DB row only on success (Adversarial B2).
- Restore enforces the undo window.
- Calendar tools surface ``missing_organizer`` when an invite has no
  organizer.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Make tests.fixtures importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# EmailTriageAgent ships as the standalone gaia-agent-email wheel (#1102);
# skip when a framework-only env lacks it.

pytest.importorskip("gaia_agent_email")  # noqa: E402
from gaia_agent_email import action_store  # noqa: E402
from gaia_agent_email.tools.calendar_tools import (  # noqa: E402
    list_calendar_events_impl,
)
from gaia_agent_email.tools.delete_tools import (  # noqa: E402
    permanent_delete_impl,
    restore_message_impl,
    trash_message_impl,
)
from gaia_agent_email.tools.organize_tools import (  # noqa: E402
    archive_message_impl,
    label_message_impl,
)
from gaia_agent_email.tools.read_tools import (  # noqa: E402
    UNTRUSTED_BODY_CLOSE,
    UNTRUSTED_BODY_OPEN,
    extract_sender_email,
    list_inbox_impl,
    pre_scan_inbox_impl,
    triage_inbox_impl,
)
from gaia_agent_email.tools.reply_tools import (  # noqa: E402
    draft_reply_impl,
)

from gaia.database.mixin import DatabaseMixin  # noqa: E402
from tests.fixtures.email.fake_gmail import (  # noqa: E402
    FakeCalendarBackend,
    FakeGmailBackend,
)


@pytest.fixture
def db():
    class _DB(DatabaseMixin):
        def __init__(self):
            self.init_db(":memory:")

    db = _DB()
    action_store.init_schema(db)
    yield db
    db.close_db()


@pytest.fixture
def fake_gmail():
    return FakeGmailBackend(
        _REPO_ROOT / "tests" / "fixtures" / "email" / "_stub_inbox.mbox"
    )


@pytest.fixture
def fake_calendar():
    return FakeCalendarBackend()


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


class TestReadTools:
    def test_list_inbox_returns_messages(self, fake_gmail):
        out = list_inbox_impl(fake_gmail, max_results=50)
        assert "messages" in out
        assert len(out["messages"]) > 0
        # The fixture has 9 INBOX messages.
        assert len(out["messages"]) == 9

    def test_body_wrapped_in_untrusted_delimiters(self, fake_gmail):
        """Phase I1 / S2.M3: every body shown to the LLM is delimited."""
        out = list_inbox_impl(fake_gmail, max_results=5)
        for msg in out["messages"]:
            assert UNTRUSTED_BODY_OPEN in msg["body"]
            assert UNTRUSTED_BODY_CLOSE in msg["body"]

    def test_html_only_message_is_stripped_to_plain_text(self, fake_gmail):
        # Stub message 005 is the phishing payload — text/html only.
        out = list_inbox_impl(fake_gmail, max_results=50)
        phish = next(
            m for m in out["messages"] if "Verify your account" in m["subject"]
        )
        # Body should NOT contain the literal HTML tags.
        assert "<a href" not in phish["body"]
        assert "<html>" not in phish["body"]
        # Should still contain the human-readable phrase.
        assert "verify" in phish["body"].lower()

    def test_triage_inbox_emits_per_message_results(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        assert "results" in out
        assert "grouped" in out
        # Every result has the required fields.
        for r in out["results"]:
            assert "id" in r
            assert "category" in r
            assert "is_spam" in r
            assert "is_phishing" in r
            assert "confident" in r

    def test_triage_emits_schema2_taxonomy_only(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        old_taxonomy = {"urgent", "actionable", "informational", "low priority"}
        new_taxonomy = {"URGENT", "NEEDS_RESPONSE", "FYI", "PROMOTIONAL", "PERSONAL"}
        for r in out["results"]:
            assert r["category"] not in old_taxonomy
            assert r["category"] in new_taxonomy

    def test_triage_flags_phishing_payload(self, fake_gmail):
        out = triage_inbox_impl(fake_gmail, max_messages=50)
        phish = [r for r in out["results"] if r["is_phishing"]]
        assert phish, "phishing payload should be flagged in stub fixture"


# ---------------------------------------------------------------------------
# Sender-email helper
# ---------------------------------------------------------------------------


class TestExtractSenderEmail:
    @pytest.mark.parametrize(
        "header,expected",
        [
            ("Alice <alice@example.com>", "alice@example.com"),
            ("alice@example.com", "alice@example.com"),
            ("ALICE@EXAMPLE.COM", "alice@example.com"),
            ('"Alice, Inc." <alice@example.com>', "alice@example.com"),
            ("", ""),
            ("   ", ""),
        ],
    )
    def test_extract(self, header, expected):
        assert extract_sender_email(header) == expected


# ---------------------------------------------------------------------------
# Pre-scan envelope
# ---------------------------------------------------------------------------


class TestPreScanInbox:
    def test_envelope_has_required_keys(self, fake_gmail):
        out = pre_scan_inbox_impl(fake_gmail, max_messages=50)
        assert out["kind"] == "email_pre_scan"
        for key in (
            "urgent",
            "actionable",
            "informational_count",
            "suggested_archives",
            "suggested_drafts",
            "preferences_applied",
            "totals",
        ):
            assert key in out, f"missing pre-scan key: {key}"
        # Drafts placeholder is a stable empty list for forward compat.
        assert out["suggested_drafts"] == []
        # informational_count is an int, never None.
        assert isinstance(out["informational_count"], int)

    def test_section_caps_respected(self, fake_gmail):
        out = pre_scan_inbox_impl(
            fake_gmail,
            max_messages=50,
            urgent_cap=2,
            actionable_cap=2,
            archive_cap=3,
        )
        assert len(out["urgent"]) <= 2
        assert len(out["actionable"]) <= 2
        assert len(out["suggested_archives"]) <= 3

    def test_phishing_lands_in_actionable_not_archives(self, fake_gmail):
        """A phishing-flagged message must be surfaced for human review, not
        silently lifted into ``suggested_archives``. The user has to see it.
        """
        out = pre_scan_inbox_impl(fake_gmail, max_messages=50)
        # The fixture has a phishing message ("Verify your account").
        archive_subjects = [a["subject"] for a in out["suggested_archives"]]
        assert not any(
            "verify your account" in s.lower() for s in archive_subjects
        ), "phishing must not be silently archived"

    def test_phishing_overrides_priority_sender_preference(self, fake_gmail):
        """Safety override: a phishing-flagged message from a priority
        sender must NOT be promoted to ``urgent``. If a user adds a
        sender to the priority list and that sender's mail trips the
        phishing heuristic (e.g. spoofed display name), the phishing
        flag wins. Otherwise the LLM might act on links inside the
        phishing body.
        """
        # Find the phishing fixture's sender.
        phishing_msg = next(
            m
            for m in fake_gmail._messages.values()
            if "verify your account"
            in next(
                (
                    h["value"]
                    for h in m["payload"]["headers"]
                    if h["name"].lower() == "subject"
                ),
                "",
            ).lower()
        )
        phishing_sender = next(
            h["value"]
            for h in phishing_msg["payload"]["headers"]
            if h["name"].lower() == "from"
        )
        addr = extract_sender_email(phishing_sender)
        # Set the phishing sender as a priority sender.
        prefs = {
            "priority_senders": {addr},
            "low_priority_senders": set(),
            "category_defaults": {},
        }
        # Run triage directly so we can inspect the per-message decision.
        triage = triage_inbox_impl(
            fake_gmail, max_messages=50, session_preferences=prefs
        )
        phishing_decision = next(
            r for r in triage["results"] if r["id"] == phishing_msg["id"]
        )
        # Category MUST NOT be "urgent" — phishing wins over the prefs.
        assert phishing_decision["category"] != "urgent"
        assert phishing_decision["is_phishing"] is True
        # The override-skipped marker should be set so logs show why.
        assert phishing_decision.get("preference_applied") == "skipped_phishing_or_spam"

    def test_priority_sender_promotes_to_urgent(self, fake_gmail):
        """A sender flagged via session preference bypasses the heuristic."""
        # Pick a sender from the fixture that the heuristic would NOT
        # classify as urgent — any non-spam non-promo non-phishing one.
        first_msg = fake_gmail.get_message(list(fake_gmail._messages.keys())[0])
        first_sender = next(
            h["value"]
            for h in first_msg["payload"]["headers"]
            if h["name"].lower() == "from"
        )
        addr = extract_sender_email(first_sender)
        prefs = {
            "priority_senders": {addr},
            "low_priority_senders": set(),
            "category_defaults": {},
        }
        out = pre_scan_inbox_impl(
            fake_gmail, max_messages=50, session_preferences=prefs
        )
        urgent_senders = [
            extract_sender_email(item["sender"]) for item in out["urgent"]
        ]
        assert addr in urgent_senders, (
            f"priority sender {addr} should land in urgent; "
            f"saw urgent={urgent_senders}"
        )

    def test_low_priority_sender_lands_in_archives(self, fake_gmail):
        first_msg = fake_gmail.get_message(list(fake_gmail._messages.keys())[0])
        first_sender = next(
            h["value"]
            for h in first_msg["payload"]["headers"]
            if h["name"].lower() == "from"
        )
        addr = extract_sender_email(first_sender)
        prefs = {
            "priority_senders": set(),
            "low_priority_senders": {addr},
            "category_defaults": {},
        }
        out = pre_scan_inbox_impl(
            fake_gmail, max_messages=50, session_preferences=prefs
        )
        archive_senders = [
            extract_sender_email(item["sender"]) for item in out["suggested_archives"]
        ]
        assert addr in archive_senders, (
            f"low-priority sender {addr} should land in archives; "
            f"saw archives={archive_senders}"
        )

    def test_category_default_archive_lifts_informational(self, fake_gmail):
        baseline = pre_scan_inbox_impl(fake_gmail, max_messages=50)
        baseline_archives = len(baseline["suggested_archives"])
        baseline_info = baseline["informational_count"]

        prefs = {
            "priority_senders": set(),
            "low_priority_senders": set(),
            "category_defaults": {"FYI": "archive"},
        }
        out = pre_scan_inbox_impl(
            fake_gmail, max_messages=50, session_preferences=prefs
        )
        # All informational items should now be in suggested_archives;
        # informational_count should drop to 0.
        assert out["informational_count"] == 0
        # archive_cap=10 default may clip; allow >= baseline_archives.
        assert len(out["suggested_archives"]) >= baseline_archives
        # We should have moved at least one item if there was any
        # informational mail to begin with.
        if baseline_info > 0:
            assert len(out["suggested_archives"]) > baseline_archives

    def test_preferences_applied_echo(self, fake_gmail):
        prefs = {
            "priority_senders": {"alice@example.com", "bob@example.com"},
            "low_priority_senders": {"news@example.com"},
            "category_defaults": {"PROMOTIONAL": "archive"},
        }
        out = pre_scan_inbox_impl(
            fake_gmail, max_messages=50, session_preferences=prefs
        )
        assert out["preferences_applied"]["priority_senders"] == sorted(
            prefs["priority_senders"]
        )
        assert out["preferences_applied"]["low_priority_senders"] == sorted(
            prefs["low_priority_senders"]
        )
        assert out["preferences_applied"]["category_defaults"] == {
            "PROMOTIONAL": "archive"
        }


# ---------------------------------------------------------------------------
# Session-preference tools (exercised through the agent's tool registry)
# ---------------------------------------------------------------------------


def _make_email_agent(fake_gmail, fake_calendar, tmp_path):
    """Construct an EmailTriageAgent with backends injected and the
    AgentSDK mocked so we don't need a live LLM. Mirrors the helper
    pattern from ``TestBatchThresholdEnforcement``.
    """
    from unittest.mock import MagicMock, patch

    from gaia_agent_email.agent import EmailTriageAgent
    from gaia_agent_email.config import EmailAgentConfig

    cfg = EmailAgentConfig(
        gmail_backend=fake_gmail,
        calendar_backend=fake_calendar,
        db_path=str(tmp_path / "state.db"),
        silent_mode=True,
    )
    with (
        patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
        patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
    ):
        mock_sdk.return_value = MagicMock()
        agent = EmailTriageAgent(config=cfg)
    return agent


class TestPreferenceTools:
    def _tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]

    def test_set_priority_sender_normalizes_and_persists(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            result = json.loads(self._tool("set_priority_sender")("Alice@Example.COM"))
            assert result["ok"] is True
            assert "alice@example.com" in agent._session_preferences["priority_senders"]
            # Snapshot is sorted + lowercased.
            assert (
                "alice@example.com" in result["data"]["preferences"]["priority_senders"]
            )
        finally:
            agent.close_db()

    def test_set_priority_supersedes_low_priority(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            json.loads(self._tool("set_low_priority_sender")("alice@example.com"))
            json.loads(self._tool("set_priority_sender")("alice@example.com"))
            assert "alice@example.com" in agent._session_preferences["priority_senders"]
            assert (
                "alice@example.com"
                not in agent._session_preferences["low_priority_senders"]
            )
        finally:
            agent.close_db()

    def test_set_priority_sender_rejects_bracketed_header(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            # The tool MUST NOT accept "Alice <alice@example.com>" — the
            # caller should pass the bare address. Bracketed headers
            # could otherwise sneak past via header-injection prompts.
            result = json.loads(
                self._tool("set_priority_sender")("Alice <alice@example.com>")
            )
            # _normalize_email strips brackets, but the trailing '>' will
            # leave an invalid token; either rejected or accepted as the
            # bare address. The contract: the persisted address must be
            # exactly "alice@example.com" if accepted, never the full
            # bracketed form.
            stored = agent._session_preferences["priority_senders"]
            for s in stored:
                assert "<" not in s and ">" not in s
            # And the result must succeed-or-fail cleanly (no half-state)
            assert isinstance(result.get("ok"), bool)
        finally:
            agent.close_db()

    def test_set_priority_sender_rejects_invalid_email(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            result = json.loads(self._tool("set_priority_sender")("not-an-email"))
            assert result["ok"] is False
            assert "email" in result["error"].lower()
            assert not agent._session_preferences["priority_senders"]
        finally:
            agent.close_db()

    def test_set_category_default_round_trip(self, fake_gmail, fake_calendar, tmp_path):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ok = json.loads(self._tool("set_category_default")("FYI", "archive"))
            assert ok["ok"] is True
            assert agent._session_preferences["category_defaults"]["FYI"] == "archive"
            # Setting it back to "keep" clears the override.
            keep = json.loads(self._tool("set_category_default")("FYI", "keep"))
            assert keep["ok"] is True
            assert "FYI" not in agent._session_preferences["category_defaults"]
        finally:
            agent.close_db()

    def test_set_category_default_rejects_unsafe_categories(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            # Defaulting "urgent" to "archive" would silently drop important
            # mail — the tool must refuse.
            result = json.loads(self._tool("set_category_default")("urgent", "archive"))
            assert result["ok"] is False
            assert "category" in result["error"].lower()
            assert not agent._session_preferences["category_defaults"]
        finally:
            agent.close_db()

    def test_set_category_default_rejects_unknown_action(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            result = json.loads(self._tool("set_category_default")("FYI", "delete"))
            assert result["ok"] is False
            assert "action" in result["error"].lower()
            assert not agent._session_preferences["category_defaults"]
        finally:
            agent.close_db()

    def test_clear_session_preferences_wipes_state(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            self._tool("set_priority_sender")("alice@example.com")
            self._tool("set_low_priority_sender")("news@example.com")
            self._tool("set_category_default")("FYI", "archive")
            result = json.loads(self._tool("clear_session_preferences")())
            assert result["ok"] is True
            assert agent._session_preferences["priority_senders"] == set()
            assert agent._session_preferences["low_priority_senders"] == set()
            assert agent._session_preferences["category_defaults"] == {}
        finally:
            agent.close_db()

    def test_pre_scan_inbox_tool_honors_live_session_state(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """End-to-end: setting a priority sender via the tool, then
        invoking pre_scan_inbox via the tool registry, must promote that
        sender to ``urgent`` in the rendered envelope.
        """
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            # Pick a sender from the fixture inbox.
            first_msg = fake_gmail.get_message(list(fake_gmail._messages.keys())[0])
            first_sender = next(
                h["value"]
                for h in first_msg["payload"]["headers"]
                if h["name"].lower() == "from"
            )
            addr = extract_sender_email(first_sender)
            self._tool("set_priority_sender")(addr)

            envelope = json.loads(self._tool("pre_scan_inbox")(50))
            assert envelope["ok"] is True
            data = envelope["data"]
            urgent_addresses = [
                extract_sender_email(item["sender"]) for item in data["urgent"]
            ]
            assert addr in urgent_addresses
        finally:
            agent.close_db()


# ---------------------------------------------------------------------------
# Organize tools
# ---------------------------------------------------------------------------


class TestOrganizeTools:
    def test_archive_records_prior_labels(self, fake_gmail, db):
        # Pick the first inbox message.
        msg_id = list(fake_gmail._messages.keys())[0]
        prior = list(fake_gmail.get_message(msg_id).get("labelIds", []))
        out = archive_message_impl(fake_gmail, db, message_id=msg_id)
        # INBOX removed from the message.
        post = fake_gmail.get_message(msg_id)
        assert "INBOX" not in post["labelIds"]
        # Action row preserves the prior labels for undo.
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        assert row["payload"]["prior_labels"] == prior

    def test_label_message_records_label_id(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        new_label = fake_gmail.create_label(name="follow-up")
        out = label_message_impl(
            fake_gmail, db, message_id=msg_id, label_id=new_label["id"]
        )
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row["payload"]["label_id"] == new_label["id"]


# ---------------------------------------------------------------------------
# Delete tools — ordering invariant + undo window
# ---------------------------------------------------------------------------


class TestOrderingInvariantParameterized:
    """Adversarial B2: every mutate tool must call Gmail BEFORE the DB write.

    A 5xx / KeyError / scope-revoke from Gmail must leave zero rows in
    ``email_actions`` — no phantom undo entries.
    """

    @pytest.mark.parametrize(
        "name,call",
        [
            (
                "archive",
                lambda gmail, db: archive_message_impl(gmail, db, message_id="nope"),
            ),
            (
                # Use a real label so resolution (#2428) passes and the failure
                # still lands at the Gmail mutation for the missing message —
                # the ordering invariant this case exists to prove.
                "label",
                lambda gmail, db: label_message_impl(
                    gmail, db, message_id="nope", label_id="STARRED"
                ),
            ),
            (
                "trash",
                lambda gmail, db: trash_message_impl(gmail, db, message_id="nope"),
            ),
        ],
    )
    def test_mutate_tool_no_phantom_row_on_gmail_failure(
        self, fake_gmail, db, name, call
    ):
        with pytest.raises(KeyError):
            call(fake_gmail, db)
        rows = db.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
        assert rows["n"] == 0, f"{name} wrote a phantom row on Gmail failure"


class TestBatchThresholdEnforcement:
    """Phase I3 / S2.M2: organize closures MUST refuse to fire past the
    batch threshold and surface an error envelope (so the agent's
    planning loop asks the user for confirmation).
    """

    def test_organize_closure_refuses_past_threshold(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        from unittest.mock import MagicMock, patch

        from gaia_agent_email.agent import EmailTriageAgent
        from gaia_agent_email.config import EmailAgentConfig

        cfg = EmailAgentConfig(
            gmail_backend=fake_gmail,
            calendar_backend=fake_calendar,
            db_path=str(tmp_path / "state.db"),
            silent_mode=True,
        )
        with (
            patch("gaia.llm.lemonade_manager.LemonadeManager.ensure_ready"),
            patch("gaia.agents.base.agent.AgentSDK") as mock_sdk,
        ):
            mock_sdk.return_value = MagicMock()
            agent = EmailTriageAgent(config=cfg)

        # Force the threshold trip by hand-bumping the counter past
        # the boundary (>5 ops, >3 senders).
        for sender in ("a", "b", "c", "d"):
            agent._record_organize_op(f"m-{sender}-1", sender)
        agent._record_organize_op("m-x", "a")
        agent._record_organize_op("m-y", "b")
        assert agent._organize_batch_threshold_exceeded() is True

        # archive_message must now refuse and return an error envelope
        # WITHOUT touching the Gmail backend.
        from gaia.agents.base.tools import _TOOL_REGISTRY

        archive_fn = _TOOL_REGISTRY["archive_message"]["function"]
        msg_id = list(fake_gmail._messages.keys())[0]
        # Capture pre-state — message should still be in INBOX after.
        prior = fake_gmail.get_message(msg_id)
        prior_labels = list(prior["labelIds"])
        result_str = archive_fn(msg_id)
        result = json.loads(result_str)
        assert result["ok"] is False
        assert "batch threshold" in result["error"].lower()
        # Message labels unchanged — closure refused before any Gmail call.
        post = fake_gmail.get_message(msg_id)
        assert post["labelIds"] == prior_labels
        agent.close_db()


class TestSendNowAuditTrail:
    """``send_now_impl`` must record an audit row (sent_at populated)
    so a one-shot send is visible alongside drafted-then-sent flows.
    """

    def test_send_now_writes_to_email_drafts(self, fake_gmail, db):
        from gaia_agent_email.tools.reply_tools import send_now_impl

        result = send_now_impl(
            fake_gmail,
            db,
            to="bob@example.com",
            subject="Hi",
            body="One-shot send from a unit test.",
        )
        sent_id = result["sent_id"]
        # The draft row exists AND is marked sent.
        row = action_store.fetch_draft(db, draft_id=sent_id)
        assert row is not None
        assert row["sent_at"] is not None


class TestMoveToLabelBehavior:
    """``move_to_label_impl`` calls ``add_label`` then ``archive_message``
    (two public Protocol calls) so the seam stays provider-agnostic for
    the Outlook backend (#963). Non-atomicity is documented in the impl.
    """

    def test_move_adds_label_and_archives(self, fake_gmail, db):
        from gaia_agent_email.tools.organize_tools import move_to_label_impl

        new_label = fake_gmail.create_label(name="archive-target")
        msg_id = list(fake_gmail._messages.keys())[0]
        move_to_label_impl(fake_gmail, db, message_id=msg_id, label_id=new_label["id"])
        post = fake_gmail.get_message(msg_id)
        # Message should have the new label and NOT be in INBOX.
        assert new_label["id"] in post["labelIds"]
        assert "INBOX" not in post["labelIds"]

    def test_move_records_action_with_prior_labels(self, fake_gmail, db):
        from gaia_agent_email.tools.organize_tools import move_to_label_impl

        new_label = fake_gmail.create_label(name="archive-target-2")
        msg_id = list(fake_gmail._messages.keys())[0]
        prior = fake_gmail.get_message(msg_id)
        prior_labels = list(prior.get("labelIds", []))
        out = move_to_label_impl(
            fake_gmail, db, message_id=msg_id, label_id=new_label["id"]
        )
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        assert row["payload"]["prior_labels"] == prior_labels


class TestDeleteTools:
    def test_trash_records_after_gmail_call(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = trash_message_impl(fake_gmail, db, message_id=msg_id)
        action_id = out["action_id"]
        row = action_store.fetch_undoable(db, action_id=action_id, window_seconds=30)
        assert row is not None
        # Message is now in TRASH, not INBOX.
        post = fake_gmail.get_message(msg_id)
        assert "TRASH" in post["labelIds"]
        assert "INBOX" not in post["labelIds"]

    def test_trash_no_phantom_row_when_gmail_raises(self, fake_gmail, db):
        """Adversarial B2: no DB write if Gmail call fails."""
        # Trigger a failure by trashing a non-existent message.
        with pytest.raises(KeyError):
            trash_message_impl(fake_gmail, db, message_id="nope")
        rows = db.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
        assert rows["n"] == 0

    def test_restore_within_window(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = trash_message_impl(fake_gmail, db, message_id=msg_id)
        action_id = out["action_id"]
        # restore_message_impl now takes a backend resolver (#1603 Phase 2):
        # resolve_backend(action) -> backend. Single-mailbox test → always fake.
        result = restore_message_impl(
            lambda _action: fake_gmail, db, action_id=action_id, window_seconds=30
        )
        assert result["restored"] is True
        post = fake_gmail.get_message(msg_id)
        assert "INBOX" in post["labelIds"]
        assert "TRASH" not in post["labelIds"]

    def test_restore_after_window_raises(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = trash_message_impl(fake_gmail, db, message_id=msg_id)
        action_id = out["action_id"]
        # Force the row's created_at into the past.
        import time

        db.update(
            "email_actions",
            {"created_at": time.time() - 3600},
            "action_id = :id",
            {"id": action_id},
        )
        with pytest.raises(RuntimeError) as exc:
            restore_message_impl(
                lambda _action: fake_gmail,
                db,
                action_id=action_id,
                window_seconds=30,
            )
        assert "undo window" in str(exc.value)

    def test_permanent_delete_removes_from_store(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = permanent_delete_impl(fake_gmail, db, message_id=msg_id)
        assert out["irreversible"] is True
        assert msg_id not in fake_gmail._messages


# ---------------------------------------------------------------------------
# Reply tools — threading headers
# ---------------------------------------------------------------------------


class TestReplyTools:
    def test_draft_reply_sets_in_reply_to_and_references(self, fake_gmail, db):
        # Inject a synthetic message with a deep References chain.
        chain = " ".join(f"<msg-{i}@example.com>" for i in range(12))
        synthetic = {
            "id": "thread-test-1",
            "threadId": "thread-test-1",
            "labelIds": ["INBOX"],
            "snippet": "test",
            "internalDate": "1700000000000",
            "payload": {
                "mimeType": "text/plain",
                "filename": "",
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "Subject", "value": "Test thread"},
                    {"name": "Message-ID", "value": "<latest@example.com>"},
                    {"name": "References", "value": chain},
                ],
                "body": {"size": 0, "data": ""},
            },
            "sizeEstimate": 100,
        }
        fake_gmail.add_message(synthetic)
        out = draft_reply_impl(
            fake_gmail, db, message_id="thread-test-1", body="Got it, thanks."
        )
        # Inspect the recorded draft headers via the fake's transport.
        last_call = [c for c in fake_gmail.transport.calls if c[0] == "create_draft"][
            -1
        ]
        headers = last_call[1].get("headers") or {}
        assert headers["In-Reply-To"] == "<latest@example.com>"
        # Existing references are preserved + the new message-id appended.
        assert "<msg-0@example.com>" in headers["References"]
        assert "<latest@example.com>" in headers["References"]
        assert out["draft_id"]

    def test_draft_reply_persists_draft_metadata(self, fake_gmail, db):
        msg_id = list(fake_gmail._messages.keys())[0]
        out = draft_reply_impl(
            fake_gmail, db, message_id=msg_id, body="Test reply body"
        )
        draft = action_store.fetch_draft(db, draft_id=out["draft_id"])
        assert draft is not None
        # body_preview is truncated.
        assert len(draft["body_preview"]) <= action_store.BODY_PREVIEW_MAX_CHARS


# ---------------------------------------------------------------------------
# Calendar tools
# ---------------------------------------------------------------------------


class TestCalendarTools:
    def test_list_events_flags_missing_organizer(self, fake_calendar):
        # Create an event with no organizer.
        fake_calendar.events["evt-1"] = {
            "id": "evt-1",
            "summary": "lonely event",
            "start": {"dateTime": "2026-05-06T14:00:00Z"},
            "end": {"dateTime": "2026-05-06T15:00:00Z"},
        }
        out = list_calendar_events_impl(fake_calendar, time_min=None, time_max=None)
        events = out["events"]
        assert events[0]["missing_organizer"] is True

    def test_list_events_does_not_flag_when_organizer_present(self, fake_calendar):
        fake_calendar.events["evt-2"] = {
            "id": "evt-2",
            "summary": "with organizer",
            "start": {"dateTime": "2026-05-06T14:00:00Z"},
            "end": {"dateTime": "2026-05-06T15:00:00Z"},
            "organizer": {"email": "alice@example.com"},
        }
        out = list_calendar_events_impl(fake_calendar, time_min=None, time_max=None)
        events = out["events"]
        assert events[0]["missing_organizer"] is False


# ---------------------------------------------------------------------------
# _coerce_ids helper
# ---------------------------------------------------------------------------


class TestCoerceIds:
    """``_coerce_ids`` normalises message_ids input from LLM callers
    which may send comma/semicolon-separated strings, lists, or None.
    """

    def test_coerce_ids(self):
        from gaia_agent_email.tools.organize_tools import _coerce_ids

        # None → empty list.
        assert _coerce_ids(None) == []

        # Comma-separated string → list of stripped tokens.
        assert _coerce_ids("a,b,c") == ["a", "b", "c"]
        assert _coerce_ids("a, b , c") == ["a", "b", "c"]

        # Semicolon-separated string → list.
        assert _coerce_ids("a;b;c") == ["a", "b", "c"]
        assert _coerce_ids("a; b ; c") == ["a", "b", "c"]

        # Mixed separators.
        assert _coerce_ids("a, b; c") == ["a", "b", "c"]

        # List → returned as-is.
        assert _coerce_ids(["a", "b", "c"]) == ["a", "b", "c"]

        # Single string (no separators) → singleton list.
        assert _coerce_ids("single-id") == ["single-id"]

        # Empty string → empty list.
        assert _coerce_ids("") == []

        # String with only whitespace/separators → empty list.
        assert _coerce_ids(" , ; ") == []

        # Unknown type → empty list.
        assert _coerce_ids(42) == []


# ---------------------------------------------------------------------------
# Batch organize tools
# ---------------------------------------------------------------------------


class TestBatchOrganizeTools:
    """Phase I3 / S2.M2: batch variants of organize tools must coerce
    IDs, call Gmail for each message, record per-message actions with a
    shared batch_id, handle partial failures, and enforce the batch
    threshold.
    """

    def _tool(self, name):
        from gaia.agents.base.tools import _TOOL_REGISTRY

        return _TOOL_REGISTRY[name]["function"]

    def test_mark_read_batch_coerces_string_ids(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """Comma-separated string of IDs is coerced to a list."""
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = [
                list(fake_gmail._messages.keys())[0],
                list(fake_gmail._messages.keys())[1],
            ]
            # Pass as a semicolon-separated string (LLM-style).
            result = json.loads(self._tool("mark_read_batch")(f"{ids[0]}; {ids[1]}"))
            assert result["ok"] is True
            assert result["data"]["total"] == 2
            assert len(result["data"]["succeeded"]) == 2
            assert result["data"]["failed"] == []
        finally:
            agent.close_db()

    def test_mark_read_batch_calls_gmail_for_each_message(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """UNREAD label is removed from every message."""
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            result = json.loads(self._tool("mark_read_batch")(ids))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    "UNREAD" not in post["labelIds"]
                ), f"{mid} still has UNREAD after batch mark_read"
        finally:
            agent.close_db()

    def test_mark_read_batch_records_per_message_actions(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """Each message gets its own action row, not a single batch row."""
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            json.loads(self._tool("mark_read_batch")(ids))
            # Query the agent's own database (not the in-memory db fixture).
            rows = agent.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
            assert rows["n"] == 3, "expected 3 action rows, got {}".format(rows["n"])
        finally:
            agent.close_db()

    def test_mark_read_batch_shares_batch_id(self, fake_gmail, fake_calendar, tmp_path):
        """All action rows from a single batch call share a batch_id."""
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            result = json.loads(self._tool("mark_read_batch")(ids))
            assert result["ok"] is True
            batch_id = result["data"]["batch_id"]
            assert batch_id is not None
            # All 3 rows must have the same batch_id.
            all_rows = agent.query("SELECT batch_id FROM email_actions")
            batch_ids = {r["batch_id"] for r in all_rows if r["batch_id"]}
            assert len(batch_ids) == 1, f"expected 1 unique batch_id, got {batch_ids}"
            assert batch_ids.pop() == batch_id
        finally:
            agent.close_db()

    def test_add_star_batch_stars_all_messages(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            result = json.loads(self._tool("add_star_batch")(ids))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    "STARRED" in post["labelIds"]
                ), f"{mid} missing STARRED after batch add_star"
        finally:
            agent.close_db()

    def test_remove_star_batch_unstars_all_messages(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        # First star them, then unstar.
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            json.loads(self._tool("add_star_batch")(ids))
            result = json.loads(self._tool("remove_star_batch")(ids))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    "STARRED" not in post["labelIds"]
                ), f"{mid} still has STARRED after batch remove_star"
        finally:
            agent.close_db()

    def test_mark_unread_batch_marks_all_unread(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        # First mark them read, then unread.
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            json.loads(self._tool("mark_read_batch")(ids))
            result = json.loads(self._tool("mark_unread_batch")(ids))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    "UNREAD" in post["labelIds"]
                ), f"{mid} missing UNREAD after batch mark_unread"
        finally:
            agent.close_db()

    def test_archive_message_batch_archives_all(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:3]
            result = json.loads(self._tool("archive_message_batch")(ids))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    "INBOX" not in post["labelIds"]
                ), f"{mid} still in INBOX after batch archive"
        finally:
            agent.close_db()

    def test_label_message_batch_adds_label_to_all(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            new_label = fake_gmail.create_label(name="batch-label-test")
            ids = list(fake_gmail._messages.keys())[:3]
            result = json.loads(self._tool("label_message_batch")(ids, new_label["id"]))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    new_label["id"] in post["labelIds"]
                ), f"{mid} missing label after batch label"
        finally:
            agent.close_db()

    def test_move_to_label_batch_moves_all(self, fake_gmail, fake_calendar, tmp_path):
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            new_label = fake_gmail.create_label(name="move-batch-test")
            ids = list(fake_gmail._messages.keys())[:3]
            result = json.loads(self._tool("move_to_label_batch")(ids, new_label["id"]))
            assert result["ok"] is True
            for mid in ids:
                post = fake_gmail.get_message(mid)
                assert (
                    new_label["id"] in post["labelIds"]
                ), f"{mid} missing target label after batch move"
                assert (
                    "INBOX" not in post["labelIds"]
                ), f"{mid} still in INBOX after batch move"
        finally:
            agent.close_db()

    def test_batch_empty_input_returns_zero_total(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """Passing an empty list returns a clean zero-result envelope."""
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            result = json.loads(self._tool("mark_read_batch")([]))
            assert result["ok"] is True
            assert result["data"]["total"] == 0
            assert result["data"]["succeeded"] == []
            assert result["data"]["failed"] == []
        finally:
            agent.close_db()

    def test_batch_partial_failure_handled_gracefully(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """Mix of valid and nonexistent message IDs — tool must not
        raise; it returns a partial result with succeeded + failed.
        """
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            good_ids = list(fake_gmail._messages.keys())[:2]
            ids = good_ids + ["nonexistent-id-12345"]
            result = json.loads(self._tool("mark_read_batch")(ids))
            assert result["ok"] is True
            assert result["data"]["total"] == 3
            assert len(result["data"]["succeeded"]) == 2
            assert len(result["data"]["failed"]) == 1
            failed_mid = result["data"]["failed"][0]["message_id"]
            assert failed_mid == "nonexistent-id-12345"
            # Good messages are actually marked read.
            for mid in good_ids:
                post = fake_gmail.get_message(mid)
                assert "UNREAD" not in post["labelIds"]
        finally:
            agent.close_db()

    def test_batch_partial_failure_records_only_succeeded_actions(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """Only succeeded messages get action rows; failed ones do not."""
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            good_ids = list(fake_gmail._messages.keys())[:2]
            ids = good_ids + ["nonexistent-id-xyz"]
            json.loads(self._tool("mark_read_batch")(ids))
            rows = agent.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
            assert (
                rows["n"] == 2
            ), "expected 2 action rows (only succeeded), got {}".format(rows["n"])
        finally:
            agent.close_db()

    def test_batch_threshold_enforced(self, fake_gmail, fake_calendar, tmp_path):
        """Phase I3: batch tool must refuse to fire past the threshold
        and return an error envelope WITHOUT touching Gmail.
        """
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            # Bump the counter past the boundary (>5 ops, >3 senders).
            for sender in ("a", "b", "c", "d"):
                agent._record_organize_op(f"m-{sender}-1", sender)
            agent._record_organize_op("m-x", "a")
            agent._record_organize_op("m-y", "b")
            assert agent._organize_batch_threshold_exceeded() is True

            msg_id = list(fake_gmail._messages.keys())[0]
            prior_labels = list(fake_gmail.get_message(msg_id).get("labelIds", []))
            result = json.loads(self._tool("mark_read_batch")([msg_id]))
            assert result["ok"] is False
            assert "batch threshold" in result["error"].lower()
            # Message labels unchanged — closure refused before any call.
            post = fake_gmail.get_message(msg_id)
            assert post["labelIds"] == prior_labels
        finally:
            agent.close_db()

    def test_batch_records_per_message_not_once(
        self, fake_gmail, fake_calendar, tmp_path
    ):
        """Regression: the Phase I3 fix ensures _record_organize_op
        is called once per message in the batch, not once for the
        entire batch. Verify by checking action row count.
        """
        agent = _make_email_agent(fake_gmail, fake_calendar, tmp_path)
        try:
            ids = list(fake_gmail._messages.keys())[:5]
            json.loads(self._tool("mark_read_batch")(ids))
            rows = agent.query("SELECT COUNT(*) AS n FROM email_actions", one=True)
            # Must be 5 rows — one per message — not 1.
            assert rows["n"] == 5, (
                "expected 5 per-message action rows, got {}; "
                "batch may be recording only once".format(rows["n"])
            )
        finally:
            agent.close_db()
