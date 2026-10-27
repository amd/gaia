# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Secrets GAIA refuses to remember are not kept anywhere else in memory either.

A bank PIN was stored as a memory (the filter knew passwords and API keys, not
PINs), and the conversation log kept the PIN, the password the agent had
refused, a card number and an SSN verbatim, where past-conversation search
reads them back. Runs against a real ``MemoryStore``; only the model is stubbed.
"""

from __future__ import annotations

import time

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
    # Worded the way the memory model writes a fact down.
    "The user's PIN for Chase is 4821",
    "User's PIN number for the ATM is 4821",
    "my pin was 4821",
    "Bank PIN — 4821",
    "User's SSN is 123 45 6789",
    "User's social security number is 123456789",
    # A card number grouped or written some other way is the same number.
    "4111\u00a01111\u00a01111\u00a01111",  # non-breaking spaces
    "4111–1111–1111–1111",
    "4111.1111.1111.1111",
    "4111  1111  1111  1111",
    "４" + "１" * 15,  # full-width digits
    "card4111111111111111",
    "Amex 3782 822463 10005",
    # Other digits right next to the card number.
    "Card: 4111 1111 1111 1111 12/29 123",
    "4111111111111111 12/29",
    "call 5 4111 1111 1111 1111",
    "Amex 3782 822463 10005 0425",
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
    # IMEIs pass the Luhn check by design; 15 digits is no card but Amex.
    "My phone's IMEI is 356938035643809",
    "IMEI 490154203237518",
    "Call 1-800-555-0199 or +1 (408) 555 0123",
    "See pages 4111-4122",
    "the security code review is 2026 Q4",
    "pin the tab to slot 2",
    # Numbers side by side that a card could hide in, but not grouped like one.
    "408-555-0123 408-555-0199",
    "IMEI 356938035643809 12",
    "IMEI 356938035643809 12/29",
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


def test_every_card_in_a_long_list_is_masked():
    text = "4111 1111 1111 1111 " * 500

    assert MemoryMixin._mask_credentials(text).count("[secret removed]") == 500


LONG_INPUTS = {
    "keyword then spaces": "pin" + " " * 100_000,
    "keyword words and spaces": "pin  number  for   the  is   " * 3_500,
    "ssn then spaces": "social security number" + " " * 100_000,
    "password then spaces": "password" + " " * 100_000,
    "spaced digits": "4 " * 50_000,
    "dotted digits": "1." * 50_000,
    "card-grouped digits": "4111 " * 20_000,
    "unbroken digits": "4" * 100_000,
}


@pytest.mark.parametrize("name", LONG_INPUTS)
def test_masking_stays_fast_on_long_input(name):
    text = LONG_INPUTS[name]
    started = time.perf_counter()

    MemoryMixin._mask_credentials(text)
    MemoryMixin._looks_like_credential(text)

    assert time.perf_counter() - started < 1.0


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


@pytest.mark.parametrize(
    "text",
    (
        "User's PIN for their Chase debit card is 4821",
        "I changed my PIN to 4821",
        "PIN 4821",
        "My Chase card PIN is 4821",
        "Chase bank PIN: 4821",
        "My PIN is 4821",
        "PIN number 4821",
        "PIN code: 4821",
        "PIN code 4821",
        "PIN of my card is 4821",
        "PIN for my card 4821",
        "PIN for Chase 4821",
        "PIN is 4821",
        "4821 is my PIN",
        "pin#4821",
        "PIN (4821)",
    ),
)
def test_pin_variants_are_masked(text):
    assert MemoryMixin._looks_like_credential(text)
    assert "[secret removed]" in MemoryMixin._mask_credentials(text)


@pytest.mark.parametrize(
    "text",
    (
        "GPIO pin 1023",
        "Pin on page 123",
        "My PIN code is 560001",
        "PIN code 560001",
    ),
)
def test_ordinary_pin_phrases_are_not_masked(text):
    assert not MemoryMixin._looks_like_credential(text)
    assert MemoryMixin._mask_credentials(text) == text


@pytest.mark.parametrize(
    "text",
    (
        "4111 - 1111 - 1111 - 1111",
        "4111   1111   1111   1111",
        "4111\t1111\t1111\t1111",
    ),
)
def test_card_grouped_with_spaced_separators_is_masked(text):
    assert MemoryMixin._looks_like_credential(text)
    assert "[secret removed]" in MemoryMixin._mask_credentials(text)


def test_amex_4_4_4_3_is_masked():
    text = "3782 8224 6310 005"
    assert MemoryMixin._looks_like_credential(text)
    assert "[secret removed]" in MemoryMixin._mask_credentials(text)


def test_card_groups_do_not_cross_a_newline():
    text = "4111\n1111\n1111\n1111"
    assert not MemoryMixin._looks_like_credential(text)
    assert MemoryMixin._mask_credentials(text) == text


def test_phone_lists_and_tracking_numbers_are_not_masked():
    texts = (
        "+49 30 12345678 or +49 89 98765432",
        "9400 1000 0000 0000 0000 00",
    )
    for text in texts:
        assert not MemoryMixin._looks_like_credential(text)
        assert MemoryMixin._mask_credentials(text) == text
