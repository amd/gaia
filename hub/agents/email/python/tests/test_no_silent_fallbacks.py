# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Fail-loudly guards for degradation paths that used to be silent.

CLAUDE.md's "No Silent Fallbacks" rule exists because fallbacks hide
regressions. These cover the two cases in this package where a failure
produced a plausible-looking result with no trace at all:

* a malformed ``GAIA_EMAIL_TRIAGE_MAX_MESSAGES`` reverted to the 100-message
  default, so a typo in an eval harness silently changed the scan size — and
  therefore the score — with nothing to notice;
* an unreadable sender profile row was reset to an empty record, discarding
  the accumulated reply-latency / interaction history that drives
  priority-sender promotion, without a log line.

The profile reset stays fail-soft (one corrupt row must not kill behavioral
learning outright) — the requirement is that the loss is *observable*, not
that it raises.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.config import (  # noqa: E402
    DEFAULT_INBOX_SCAN_CEILING,
    ConfigurationError,
    EmailAgentConfig,
    default_inbox_scan_ceiling,
)

_ENV = "GAIA_EMAIL_TRIAGE_MAX_MESSAGES"


# ---------------------------------------------------------------------------
# Config override — a bad value is an operator error, not a value to guess past
# ---------------------------------------------------------------------------


def test_unset_override_uses_the_documented_default(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    assert default_inbox_scan_ceiling() == DEFAULT_INBOX_SCAN_CEILING


def test_valid_override_is_honoured(monkeypatch):
    monkeypatch.setenv(_ENV, "250")
    assert default_inbox_scan_ceiling() == 250


def test_bad_override_fails_at_construction_not_per_tool_call(monkeypatch):
    """The raise must reach the operator.

    Both scanning tools wrap their body in ``except Exception -> _envelope_err``,
    so a ceiling resolved at call time would be caught and handed to the LLM as
    a per-call error string — the eval harness would then complete a fully
    failed run and still write a scorecard. Resolving it on EmailAgentConfig
    means a bad value stops startup instead.
    """
    monkeypatch.setenv(_ENV, "not-a-number")
    with pytest.raises(ConfigurationError, match=_ENV):
        EmailAgentConfig()


def test_config_carries_the_resolved_ceiling(monkeypatch):
    monkeypatch.setenv(_ENV, "250")
    assert EmailAgentConfig().inbox_scan_ceiling == 250


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_override_means_unset(monkeypatch, blank):
    """Whitespace is "unset", same as default_undo_window_seconds treats it."""
    monkeypatch.setenv(_ENV, blank)
    assert default_inbox_scan_ceiling() == DEFAULT_INBOX_SCAN_CEILING


@pytest.mark.parametrize("bad", ["abc", "25o", "1e3", "12.5"])
def test_non_integer_override_raises(monkeypatch, bad):
    """A typo is an operator error, not a value to guess past."""
    monkeypatch.setenv(_ENV, bad)
    with pytest.raises(ConfigurationError) as exc:
        default_inbox_scan_ceiling()
    message = str(exc.value)
    assert _ENV in message and repr(bad) in message  # what failed
    assert "positive message count" in message  # what to do
    assert str(DEFAULT_INBOX_SCAN_CEILING) in message  # the way out


@pytest.mark.parametrize("bad", ["0", "-1", "-250"])
def test_non_positive_override_raises(monkeypatch, bad):
    """``max(1, ...)`` used to clamp these into a working-ish value."""
    monkeypatch.setenv(_ENV, bad)
    with pytest.raises(ConfigurationError, match="positive message count"):
        default_inbox_scan_ceiling()


# ---------------------------------------------------------------------------
# Sender-profile reset — fail-soft, but never silent
# ---------------------------------------------------------------------------


class _FakeStore:
    """Memory store whose single row has unparseable ``content``."""

    def __init__(self):
        self.row = {"id": "row-1", "content": "{not json at all"}
        self.writes = []

    def get_by_entity(self, entity):
        return [self.row]

    def add(self, *a, **k):
        self.writes.append(("add", a, k))
        return "row-1"

    def update(self, *a, **k):
        self.writes.append(("update", a, k))
        return True


class _Host:
    """Minimal ProfileToolsMixin host — the mixin reads ``_memory_store``."""

    def __init__(self, store):
        self._memory_store = store
        self._memory_context = "email"
        self._incognito = False


def _drive(caplog, method, *args, **kwargs):
    from gaia_agent_email.tools import profile_tools

    class _Bound(_Host, profile_tools.ProfileToolsMixin):
        pass

    store = _FakeStore()
    host = _Bound(store)
    with caplog.at_level(logging.WARNING):
        getattr(host, method)(*args, **kwargs)
    return [r for r in caplog.records if r.levelno >= logging.WARNING], store


def test_corrupt_reply_record_reset_is_logged(caplog):
    """Restarting the record drops accumulated reply latencies — say so."""
    warnings, store = _drive(
        caplog, "_record_reply_interaction", "alice@example.com", latency_seconds=30.0
    )
    assert warnings, (
        "an unreadable reply record was reset with no log line — the sender's "
        "accumulated reply-latency history (which drives priority-sender "
        "promotion) vanished silently"
    )
    text = " ".join(r.getMessage() for r in warnings)
    # The row id, never the address — this is WARNING-level and gaia
    # diagnostics bundles default-level logs into user-attached bug reports.
    assert "row-1" in text, f"warning does not identify the record: {text}"
    assert "alice@example.com" not in text, f"warning leaks a sender address: {text}"
    assert "lost" in text.lower()
    # Fail-soft is intentional: the write still happens on a fresh record.
    assert store.writes, "the reset should still persist a fresh record"


def test_corrupt_interaction_record_reset_is_logged(caplog):
    warnings, store = _drive(caplog, "_record_interaction", "bob@example.com", "urgent")
    assert warnings, "an unreadable interaction record was reset with no log line"
    text = " ".join(r.getMessage() for r in warnings)
    assert "row-1" in text, f"warning does not identify the record: {text}"
    assert "bob@example.com" not in text, f"warning leaks a sender address: {text}"
    assert "lost" in text.lower()
    assert store.writes, "the reset should still persist a fresh record"


def test_healthy_record_is_not_warned_about(caplog):
    """No warning when the record parses — the guard must not cry wolf."""
    from gaia_agent_email.tools import profile_tools

    class _Bound(_Host, profile_tools.ProfileToolsMixin):
        pass

    store = _FakeStore()
    store.row["content"] = json.dumps(
        {"sender": "carol@example.com", "reply_latencies_seconds": [10.0]}
    )
    host = _Bound(store)
    with caplog.at_level(logging.WARNING):
        host._record_reply_interaction("carol@example.com", latency_seconds=12.0)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
