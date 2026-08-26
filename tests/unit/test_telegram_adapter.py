import os
import sys


def test_run_telegram_scaffold_returns_adapter(mock_home, monkeypatch):
    # Ensure local "src" directory is on sys.path for imports during tests
    sys.path.insert(0, os.path.abspath("src"))
    from gaia.messaging.telegram import run_telegram

    # GAIA_TEST_MODE stops real polling; mock_home keeps the background pid/log
    # files out of the developer's real ~/.gaia (see test_telegram_background).
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    # Run in background mode so the function does not block; in CI the
    # python-telegram-bot runtime may not be available, so guard accordingly.
    adapter = run_telegram(
        token="fake-token-123", allowed_users={12345}, background=True
    )
    assert adapter.token == "fake-token-123"
    assert 12345 in adapter.allowed_users
    # Application may be None if the telegram dependency is missing.
    assert hasattr(adapter, "application")
