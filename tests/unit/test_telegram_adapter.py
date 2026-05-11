import os
import sys


def test_run_telegram_scaffold_returns_adapter():
    # Ensure local "src" directory is on sys.path for imports during tests
    sys.path.insert(0, os.path.abspath("src"))
    from gaia.messaging.telegram import run_telegram

    # Run in background mode so the function does not block; in CI the
    # python-telegram-bot runtime may not be available, so guard accordingly.
    adapter = run_telegram(
        token="fake-token-123", allowed_users={12345}, background=True
    )
    assert adapter is not None
    assert getattr(adapter, "token", None) == "fake-token-123"
    assert 12345 in getattr(adapter, "allowed_users")
    # Application may be None if dependency missing; check attribute exists
    assert hasattr(adapter, "application")
