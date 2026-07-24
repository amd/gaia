# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Label-apply must resolve a display name to a label id before the backend call (#2428).

Gmail user labels are addressed by id (``Label_###``), not display name; the
modify API rejects a bare name with ``Invalid label: <name>``. The agent's
``list_labels`` returns display names, the model feeds one back into the apply
call, and the backend rejected it — even for the exact label the agent had just
enumerated as valid.

The fakes below REJECT an unknown label id exactly like the real Gmail modify
API, so a passing test proves the apply path resolved the name to a valid id
(not that a lenient stub accepted anything). ``_FakeOutlook`` models the other
provider (categories: id == name) to guard the mixed-mailbox batch case.
"""

import json
from types import SimpleNamespace

import pytest
from gaia_agent_email import action_store
from gaia_agent_email.tools.organize_tools import (
    OrganizeToolsMixin,
    label_message_impl,
    move_to_label_impl,
)
from gaia_agent_email.tools.read_tools import list_labels_impl

from gaia.agents.base.tools import _TOOL_REGISTRY, get_tool_metadata
from gaia.database.mixin import DatabaseMixin

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeGmail:
    """Gmail-style label-by-id backend that rejects unknown ids like the real
    ``messages.modify`` API (``Invalid label: <x>``)."""

    def __init__(self):
        self.messages = {
            "m1": {"id": "m1", "labelIds": ["INBOX", "UNREAD"], "threadId": "t1"},
            "m2": {"id": "m2", "labelIds": ["INBOX"], "threadId": "t2"},
        }
        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_7", "name": "GAIA-FIXTURE", "type": "user"},
        ]
        self.list_labels_calls = 0

    def list_labels(self):
        self.list_labels_calls += 1
        return [dict(lb) for lb in self.labels]

    def get_message(self, message_id):
        m = self.messages[message_id]
        return {
            "id": message_id,
            "labelIds": list(m["labelIds"]),
            "threadId": m["threadId"],
        }

    def add_label(self, message_id, label_id):
        if label_id not in {lb["id"] for lb in self.labels}:
            raise ValueError(f"Invalid label: {label_id}")  # mirror Gmail modify API
        labels = self.messages[message_id]["labelIds"]
        if label_id not in labels:
            labels.append(label_id)
        return {"id": message_id}

    def archive_message(self, message_id):
        labels = self.messages[message_id]["labelIds"]
        if "INBOX" in labels:
            labels.remove("INBOX")
        return {"id": message_id}


class _FakeOutlook:
    """Outlook-style category backend: ``list_labels`` returns id == name and
    ``add_label`` takes the category string as-is (Outlook auto-creates it)."""

    def __init__(self):
        self.messages = {"m2": {"id": "m2", "labelIds": ["INBOX"], "threadId": "t2"}}
        self.labels = [{"id": "GAIA-FIXTURE", "name": "GAIA-FIXTURE", "type": "user"}]
        self.list_labels_calls = 0

    def list_labels(self):
        self.list_labels_calls += 1
        return [dict(lb) for lb in self.labels]

    def get_message(self, message_id):
        m = self.messages[message_id]
        return {
            "id": message_id,
            "labelIds": list(m["labelIds"]),
            "threadId": m["threadId"],
        }

    def add_label(self, message_id, label_id):
        labels = self.messages[message_id]["labelIds"]
        if label_id not in labels:
            labels.append(label_id)
        return {"id": message_id}

    def archive_message(self, message_id):
        labels = self.messages[message_id]["labelIds"]
        if "INBOX" in labels:
            labels.remove("INBOX")
        return {"id": message_id}


class _DB(DatabaseMixin):
    pass


def _make_db():
    db = _DB()
    db.init_db(":memory:")
    action_store.init_schema(db)
    return db


class _FakeAgent(OrganizeToolsMixin, DatabaseMixin):
    """Minimal host that registers the real organize tool closures so tests can
    drive them at the tool boundary (the surface the agent loop actually calls)."""

    def __init__(self, backends, providers):
        self.config = SimpleNamespace(debug=False, undo_window_seconds=30)
        self._backends = backends
        self._providers = providers
        self.init_db(":memory:")
        action_store.init_schema(self)
        self._register_organize_tools()

    def _organize_batch_threshold_exceeded(self):
        return False

    def _provider_for_message(self, message_id, mailbox=None):
        return self._providers[message_id]

    def _backend_for_message(self, message_id):
        return self._backends[self._providers[message_id]]

    def _record_organize_op(self, message_id, sender):
        pass


@pytest.fixture(autouse=True)
def _preserve_tool_registry():
    """Registering closures mutates the module-global tool registry; restore it
    so this file can't perturb other suites when run together."""
    snapshot = dict(_TOOL_REGISTRY)
    yield
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(snapshot)


# ---------------------------------------------------------------------------
# The fake must reproduce the real failure — otherwise a green test is hollow.
# ---------------------------------------------------------------------------


def test_fake_backend_rejects_display_name_like_real_gmail():
    gmail = _FakeGmail()
    with pytest.raises(ValueError, match="Invalid label"):
        gmail.add_label("m1", "GAIA-FIXTURE")


# ---------------------------------------------------------------------------
# Resolver unit — display name / id / unknown / casing
# (deferred import: the symbol does not exist at RED time)
# ---------------------------------------------------------------------------


def test_resolver_maps_display_name_to_id():
    from gaia_agent_email.tools.organize_tools import _resolve_label_id

    assert _resolve_label_id(_FakeGmail(), "GAIA-FIXTURE") == "Label_7"


def test_resolver_passes_through_valid_ids():
    from gaia_agent_email.tools.organize_tools import _resolve_label_id

    gmail = _FakeGmail()
    assert _resolve_label_id(gmail, "Label_7") == "Label_7"
    assert _resolve_label_id(gmail, "INBOX") == "INBOX"


def test_resolver_unknown_label_fails_loud_and_lists_existing():
    from gaia_agent_email.tools.organize_tools import _resolve_label_id

    with pytest.raises(ValueError) as ei:
        _resolve_label_id(_FakeGmail(), "DoesNotExist")
    msg = str(ei.value)
    assert "Invalid label" in msg
    assert "GAIA-FIXTURE" in msg  # names the labels the caller can actually use


def test_resolver_tolerates_casing_and_whitespace():
    from gaia_agent_email.tools.organize_tools import _resolve_label_id

    gmail = _FakeGmail()
    assert _resolve_label_id(gmail, "gaia-fixture") == "Label_7"
    assert _resolve_label_id(gmail, "  GAIA-FIXTURE  ") == "Label_7"


# ---------------------------------------------------------------------------
# Impl level — label_message / move_to_label resolve the name themselves
# ---------------------------------------------------------------------------


def test_label_message_impl_resolves_display_name():
    gmail = _FakeGmail()
    res = label_message_impl(
        gmail, _make_db(), message_id="m1", label_id="GAIA-FIXTURE"
    )
    assert res["label_id"] == "Label_7"  # resolved id is what gets recorded for undo
    assert "Label_7" in gmail.messages["m1"]["labelIds"]


def test_move_to_label_impl_resolves_display_name():
    gmail = _FakeGmail()
    res = move_to_label_impl(
        gmail, _make_db(), message_id="m1", label_id="GAIA-FIXTURE"
    )
    assert res["label_id"] == "Label_7"
    assert "Label_7" in gmail.messages["m1"]["labelIds"]
    assert "INBOX" not in gmail.messages["m1"]["labelIds"]  # moved out of inbox


# ---------------------------------------------------------------------------
# Tool boundary — AC-1 / AC-2: enumerate, then apply by display name
# ---------------------------------------------------------------------------


def test_label_message_tool_applies_enumerated_label_by_name():
    gmail = _FakeGmail()
    agent = _FakeAgent({"google": gmail}, {"m1": "google"})

    # The agent enumerates labels and sees GAIA-FIXTURE as the custom label...
    enumerated = list_labels_impl(gmail)
    assert any(lb["name"] == "GAIA-FIXTURE" for lb in enumerated)

    # ...then applies it BY THAT NAME. AC-1: succeeds. AC-2: never 'Invalid label'.
    apply = get_tool_metadata("label_message")["function"]
    out = json.loads(apply("m1", "GAIA-FIXTURE"))
    assert out["ok"] is True, out
    assert out["data"]["label_id"] == "Label_7"
    assert "Label_7" in gmail.messages["m1"]["labelIds"]
    assert "Invalid label" not in json.dumps(out)

    del agent


def test_label_message_batch_resolves_per_backend_without_cross_feed():
    gmail = _FakeGmail()
    outlook = _FakeOutlook()
    agent = _FakeAgent(
        {"google": gmail, "microsoft": outlook},
        {"m1": "google", "m2": "microsoft"},
    )

    apply_batch = get_tool_metadata("label_message_batch")["function"]
    out = json.loads(apply_batch(["m1", "m2"], "GAIA-FIXTURE"))

    assert out["ok"] is True, out
    assert len(out["data"]["succeeded"]) == 2
    assert out["data"]["failed"] == []
    # Each message gets ITS OWN provider's id — the name is resolved per backend.
    assert "Label_7" in gmail.messages["m1"]["labelIds"]
    assert "GAIA-FIXTURE" in outlook.messages["m2"]["labelIds"]
    assert "Label_7" not in outlook.messages["m2"]["labelIds"]  # no cross-feed
    # Memoized per backend: exactly one list_labels lookup each.
    assert gmail.list_labels_calls == 1
    assert outlook.list_labels_calls == 1

    del agent
