# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Secrets GAIA refuses to remember are not kept anywhere else in memory either.

A bank PIN was stored as a memory (the filter knew passwords and API keys, not
PINs), and the conversation log kept the PIN, the password the agent had
refused, a card number and an SSN verbatim, where past-conversation search
reads them back. Runs against a real ``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

import pytest

from gaia.agents.base.memory import MemoryMixin
from gaia.agents.base.memory_store import MemoryStore
from tests.unit.test_memory_forget_sticks import _Host, _tool

SECRETS = [
    "My bank PIN is 4821",
    "pin: 9921",
    "The CVV is 123",
    "my passcode is 778899",
    "My social security number is 123-45-6789",
    "card 4111 1111 1111 1111 exp 12/29",
    "online banking password is Blue-Heron-77",
]

NOT_SECRETS = [
    "Call me at 555-123-4567",
    "Meeting on 2026-10-23 at 3pm",
    "Connect the wire to pin 3",
    "I pinned 12 tabs",
    "Order 1234567890123 shipped",
    # Luhn-valid, but no card network starts with 1 or 9.
    "Tracking 1000000000009 is out for delivery",
    "Logged at 9000000000001 ms",
    "My zip code is 97201",
]


@pytest.fixture
def store(tmp_path):
    db = MemoryStore(db_path=tmp_path / "memory.db")
    yield db
    db.close()


@pytest.mark.parametrize("text", SECRETS)
def test_a_secret_is_recognised(text):
    assert MemoryMixin._looks_like_credential(text)
    assert "[secret removed]" in MemoryMixin._mask_credentials(text)


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_an_ordinary_number_is_left_alone(text):
    assert not MemoryMixin._looks_like_credential(text)
    assert MemoryMixin._mask_credentials(text) == text


def test_remember_refuses_a_pin(store):
    _Host(store, [])
    out = _tool("remember")(fact="My bank PIN is 4821", category="fact")
    assert out["status"] == "skipped"
    assert store._conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0] == 0


def test_the_conversation_log_keeps_no_secret(store):
    host = _Host(store, [])
    host._auto_extract_enabled = False
    host._after_process_query(
        "My bank PIN is 4821 and my card is 4111 1111 1111 1111. Remember them.",
        "I won't keep the PIN 4821 or card 4111 1111 1111 1111; use a password manager.",
    )
    logged = " ".join(
        r[0] for r in store._conn.execute("SELECT content FROM conversations")
    )
    assert "4821" not in logged and "4111" not in logged
    assert "Remember them" in logged
