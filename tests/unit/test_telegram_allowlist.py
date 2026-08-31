"""Direct coverage of the Telegram allowlist predicate.

`_handle_start` and `_handle_message` gating are covered in
test_telegram_adapter.py; this file pins `_allowed` itself, including the
deliberate fail-open when the allowlist is empty or None.
"""

import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import TelegramAdapter


def test_allowed_user_passes():
    adapter = TelegramAdapter(token="t", allowed_users={1, 2})
    assert adapter._allowed(1) is True
    assert adapter._allowed(2) is True


def test_unknown_user_denied():
    adapter = TelegramAdapter(token="t", allowed_users={1, 2})
    assert adapter._allowed(999) is False


def test_empty_allowlist_intentionally_allows_all():
    adapter = TelegramAdapter(token="t")
    assert adapter._allowed(12345) is True


def test_none_allowlist_intentionally_allows_all():
    adapter = TelegramAdapter(token="t", allowed_users=None)
    assert adapter._allowed(12345) is True
