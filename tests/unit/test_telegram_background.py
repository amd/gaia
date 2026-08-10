import os
import sys

sys.path.insert(0, os.path.abspath("src"))

from gaia.messaging.telegram import run_telegram


def test_background_writes_pid(tmp_path, monkeypatch):
    # Ensure GAIA_TEST_MODE set so we don't actually start polling
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    # Isolate HOME so the test never touches the developer's real
    # ~/.gaia/telegram.pid — deleting that file would break a running
    # `gaia telegram start` adapter. The adapter resolves the pid path via
    # os.path.expanduser("~/.gaia"), so redirect "~" at tmp_path.
    real_expanduser = os.path.expanduser

    def fake_expanduser(path):
        if path.startswith("~"):
            return os.path.join(str(tmp_path), path[1:].lstrip("/\\"))
        return real_expanduser(path)

    monkeypatch.setattr(os.path, "expanduser", fake_expanduser)

    # Use a fake token; background mode should write ~/.gaia/telegram.pid
    adapter = run_telegram(token="fake-token-bg", allowed_users=None, background=True)
    assert adapter is not None
    pid_path = os.path.expanduser("~/.gaia/telegram.pid")
    assert os.path.exists(pid_path)
    with open(pid_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
        assert content.isdigit()
    # No manual cleanup: pid_path lives under the tmp_path fixture, which
    # pytest removes automatically — so a real ~/.gaia/telegram.pid is untouched.
