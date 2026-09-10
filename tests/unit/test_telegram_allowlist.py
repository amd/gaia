"""Direct coverage of the Telegram allowlist predicate.

`_handle_start` and `_handle_message` gating is covered in
test_telegram_adapter.py; this file pins `_allowed` itself and the refusal to
construct an adapter that would serve everyone.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import TelegramAdapter, TelegramAllowlistError


def test_allowed_user_passes():
    adapter = TelegramAdapter(token="t", allowed_users={1, 2})
    assert adapter._allowed(1) is True
    assert adapter._allowed(2) is True


def test_unknown_user_denied():
    adapter = TelegramAdapter(token="t", allowed_users={1, 2})
    assert adapter._allowed(999) is False


@pytest.mark.parametrize("empty", [set(), None, [], frozenset()])
def test_empty_allowlist_is_refused_at_construction(empty):
    with pytest.raises(TelegramAllowlistError) as excinfo:
        TelegramAdapter(token="t", allowed_users=empty)
    assert "--allowed-users" in str(excinfo.value)


def test_omitted_allowlist_is_refused_at_construction():
    """The default argument is the shape a careless caller actually hits."""
    with pytest.raises(TelegramAllowlistError):
        TelegramAdapter(token="t")


def test_refusal_is_a_valueerror_for_callers_catching_the_base():
    with pytest.raises(ValueError):
        TelegramAdapter(token="t")


def test_string_allowlist_is_rejected_rather_than_split_into_characters():
    """`"12345"` would become {'1','2',...} and deny everyone confusingly."""
    with pytest.raises(TypeError) as excinfo:
        TelegramAdapter(token="t", allowed_users="12345")
    assert "collection of int user IDs" in str(excinfo.value)


def test_allowlist_emptied_after_construction_admits_nobody():
    """Defence in depth: `_allowed` must not fall back to permitting all."""
    adapter = TelegramAdapter(token="t", allowed_users={1})
    adapter.allowed_users = set()
    assert adapter._allowed(1) is False
    assert adapter._allowed(12345) is False


def test_allowlist_is_copied_not_aliased():
    """A caller mutating its own set must not widen a running adapter."""
    caller_set = {1}
    adapter = TelegramAdapter(token="t", allowed_users=caller_set)
    caller_set.add(999)
    assert adapter._allowed(999) is False


def test_refusal_message_names_cause_remedy_and_docs():
    with pytest.raises(TelegramAllowlistError) as excinfo:
        TelegramAdapter(token="t")
    message = str(excinfo.value)
    assert "no allowed-users configured" in message  # what failed
    assert "gaia telegram start --token" in message  # what to do
    assert "amd-gaia.ai/docs/guides/telegram-adapter" in message  # where to look
