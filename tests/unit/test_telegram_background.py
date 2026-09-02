import builtins
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging import telegram


def test_background_writes_pid(mock_home, monkeypatch):
    # GAIA_TEST_MODE stops the adapter from starting a real polling loop.
    monkeypatch.setenv("GAIA_TEST_MODE", "1")
    monkeypatch.setitem(sys.modules, "telegram", MagicMock())
    monkeypatch.setitem(sys.modules, "telegram.ext", MagicMock())

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

    # python-telegram-bot is optional and is absent from the unit CI job.
    # Stub its builder so the test reaches the GAIA_TEST_MODE guard.
    monkeypatch.setitem(sys.modules, "telegram", MagicMock())
    monkeypatch.setitem(sys.modules, "telegram.ext", MagicMock())

    def fail_if_thread_started(*args, **kwargs):
        raise AssertionError("GAIA_TEST_MODE started a background thread")

    monkeypatch.setattr(
        telegram,
        "threading",
        SimpleNamespace(Thread=fail_if_thread_started, Event=threading.Event),
    )

    adapter = telegram.run_telegram(
        token="fake-token-no-threads", allowed_users=None, background=True
    )

    assert adapter.application is not None


def test_background_missing_dependency_fails_and_removes_pid(mock_home, monkeypatch):
    """A missing optional dependency must not look like a running adapter."""
    real_import = builtins.__import__

    def fail_telegram_ext(name, *args, **kwargs):
        if name == "telegram.ext":
            raise ImportError("No module named telegram")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_telegram_ext)

    with pytest.raises(
        RuntimeError, match="python-telegram-bot is required for Telegram support"
    ):
        telegram.run_telegram(
            token="fake-token-missing-dependency",
            allowed_users=None,
            background=True,
        )

    assert not (mock_home / ".gaia" / "telegram.pid").exists()
