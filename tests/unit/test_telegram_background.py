import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import run_telegram


def test_background_writes_pid(tmp_path, monkeypatch):
    # Ensure GAIA_TEST_MODE set so we don't actually start polling
    monkeypatch.setenv("GAIA_TEST_MODE", "1")
    # Use a fake token; background mode should write ~/.gaia/telegram.pid
    adapter = run_telegram(token="fake-token-bg", allowed_users=None, background=True)
    pid_path = os.path.expanduser("~/.gaia/telegram.pid")
    try:
        assert os.path.exists(pid_path)
        with open(pid_path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            assert content.isdigit()
    finally:
        try:
            os.remove(pid_path)
        except OSError:
            pass
