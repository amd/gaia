import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import run_telegram


def test_background_writes_pid(tmp_path, monkeypatch):
    # Ensure GAIA_TEST_MODE set so we don't actually start polling
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    # Isolate HOME: the adapter resolves its pid path via expanduser("~/.gaia"),
    # and a real ~/.gaia/telegram.pid belongs to a live `gaia telegram` adapter.
    real_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path == "~" or path.startswith(("~/", "~\\")):
            return os.path.join(str(tmp_path), path[2:])
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)

    # Use a fake token; background mode should write <home>/.gaia/telegram.pid
    adapter = run_telegram(token="fake-token-bg", allowed_users=None, background=True)
    assert adapter is not None

    # Assert on the sandbox path, not expanduser("~") — if the isolation above is
    # removed this fails instead of passing while clobbering the real pid file.
    pid_path = tmp_path / ".gaia" / "telegram.pid"
    assert pid_path.exists()
    assert pid_path.read_text(encoding="utf-8").strip().isdigit()
