import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging import telegram


def test_background_writes_pid(mock_home, monkeypatch):
    # GAIA_TEST_MODE stops the adapter from starting a real polling loop.
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    # mock_home redirects "~" at a tmp dir, so the adapter's
    # expanduser("~/.gaia") pid file never overwrites a live adapter's real one.
    telegram.run_telegram(token="fake-token-bg", allowed_users=None, background=True)

    # Assert on the sandbox path, not expanduser("~") — if the isolation is
    # removed this fails instead of passing while clobbering the real pid file.
    pid_path = mock_home / ".gaia" / "telegram.pid"
    assert pid_path.exists()
    assert pid_path.read_text(encoding="utf-8").strip().isdigit()


def test_background_test_mode_does_not_start_threads(mock_home, monkeypatch):
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    def fail_if_thread_started(*args, **kwargs):
        raise AssertionError("GAIA_TEST_MODE started a background thread")

    monkeypatch.setattr(telegram.threading, "Thread", fail_if_thread_started)

    telegram.run_telegram(
        token="fake-token-no-threads", allowed_users=None, background=True
    )
