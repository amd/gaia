import builtins
import os
import subprocess
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
    telegram.run_telegram(token="fake-token-bg", allowed_users={12345}, background=True)

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
        token="fake-token-no-threads", allowed_users={12345}, background=True
    )

    assert adapter.application is not None


def test_background_missing_dependency_fails_and_removes_pid(mock_home, monkeypatch):
    """A missing optional dependency must not look like a running adapter."""
    real_import = builtins.__import__
    pid_path = mock_home / ".gaia" / "telegram.pid"
    removed_paths = []
    real_remove = telegram.os.remove

    def record_remove(path):
        removed_paths.append(path)
        return real_remove(path)

    def fail_telegram_ext(name, *args, **kwargs):
        if name == "telegram.ext":
            raise ImportError("No module named telegram")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_telegram_ext)
    monkeypatch.setattr(telegram.os, "remove", record_remove)

    with pytest.raises(
        RuntimeError,
        match=r"python-telegram-bot is required for Telegram support.*gaia\[telegram\]",
    ):
        telegram.run_telegram(
            token="fake-token-missing-dependency",
            allowed_users={12345},
            background=True,
        )

    assert len(removed_paths) == 1
    assert os.path.normpath(removed_paths[0]) == os.path.normpath(str(pid_path))
    assert not pid_path.exists()


def test_cli_reports_missing_dependency_without_traceback(monkeypatch, capsys):
    """The CLI turns the adapter's actionable error into a clean exit."""
    from gaia import cli

    def fail_start(**_kwargs):
        raise RuntimeError(
            "python-telegram-bot is required for Telegram support. "
            'Install it with: pip install "gaia[telegram]"'
        )

    monkeypatch.setattr(telegram, "run_telegram", fail_start)
    monkeypatch.setattr(
        sys,
        "argv",
        ["gaia", "telegram", "start", "--token", "fake-token", "--background"],
    )

    with pytest.raises(SystemExit) as exc_info:
        cli.main()

    assert exc_info.value.code == 1
    assert "pip install" in capsys.readouterr().err


def test_refused_start_leaves_no_pid_file(mock_home, monkeypatch):
    """The refusal must precede the PID file, or `stop`/`status` see a ghost."""
    monkeypatch.setenv("GAIA_TEST_MODE", "1")

    with pytest.raises(ValueError):
        telegram.run_telegram(
            token="fake-token-open", allowed_users=None, background=True
        )

    assert not (mock_home / ".gaia" / "telegram.pid").exists()


def _run_cli(monkeypatch, *argv):
    from gaia import cli

    monkeypatch.setattr(sys, "argv", ["gaia", "telegram", *argv])
    cli.main()


@pytest.fixture
def unrelated_process():
    """A live process that is not a Telegram adapter, standing in for pid reuse."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield proc
    proc.kill()
    proc.wait(timeout=10)


def test_stop_does_not_signal_a_reused_pid(
    mock_home, monkeypatch, capsys, unrelated_process
):
    pid_path = mock_home / ".gaia" / "telegram.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(str(unrelated_process.pid), encoding="utf-8")

    _run_cli(monkeypatch, "stop")

    assert unrelated_process.poll() is None, "stop signalled an unrelated process"
    assert not pid_path.exists()
    assert "not a Telegram adapter" in capsys.readouterr().out


def test_stop_signals_the_adapter(mock_home, monkeypatch, capsys):
    # The trailing argv makes the cmdline look like `gaia telegram start`.
    adapter = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "telegram", "start"]
    )
    try:
        pid_path = mock_home / ".gaia" / "telegram.pid"
        pid_path.parent.mkdir(parents=True)
        pid_path.write_text(str(adapter.pid), encoding="utf-8")

        _run_cli(monkeypatch, "stop")

        assert adapter.wait(timeout=10) is not None
        assert "Sent SIGTERM" in capsys.readouterr().out
    finally:
        adapter.kill()
        adapter.wait(timeout=10)


def test_status_reports_a_reused_pid_as_not_running(
    mock_home, monkeypatch, capsys, unrelated_process
):
    pid_path = mock_home / ".gaia" / "telegram.pid"
    pid_path.parent.mkdir(parents=True)
    pid_path.write_text(str(unrelated_process.pid), encoding="utf-8")

    # A port nothing listens on, so the health probe fails fast.
    _run_cli(monkeypatch, "status", "--health-port", "1")

    assert "not running" in capsys.readouterr().out


def test_background_polling_exit_removes_pid(mock_home, monkeypatch):
    """Ctrl-C or a clean exit must not leave a pid file for `stop` to trust."""
    monkeypatch.delenv("GAIA_TEST_MODE", raising=False)
    monkeypatch.setitem(sys.modules, "telegram", MagicMock())
    monkeypatch.setitem(sys.modules, "telegram.ext", MagicMock())
    # Keep pytest's own SIGINT/SIGTERM handlers in place.
    monkeypatch.setattr(telegram.signal, "signal", lambda *_a: None)

    adapter = telegram.run_telegram(
        token="fake-token-cleanup",
        allowed_users={12345},
        background=True,
        health_port=0,
    )
    adapter._poll_thread.join(timeout=10)

    assert not adapter._poll_thread.is_alive()
    assert not (mock_home / ".gaia" / "telegram.pid").exists()


@pytest.mark.parametrize(
    "argv_suffix",
    [
        # Telegram Desktop's standard autostart invocation. Both "telegram" and
        # "start" appear in the joined command line, neither as its own argument.
        pytest.param(["telegram-desktop", "-startintray"], id="telegram-desktop"),
        # The two words present but not adjacent.
        pytest.param(["telegram", "--chat", "start"], id="non-adjacent"),
    ],
)
def test_stop_does_not_signal_a_lookalike_cmdline(
    mock_home, monkeypatch, capsys, argv_suffix
):
    """A cmdline that merely contains both words is not the adapter."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", *argv_suffix]
    )
    try:
        pid_path = mock_home / ".gaia" / "telegram.pid"
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(proc.pid), encoding="utf-8")

        _run_cli(monkeypatch, "stop")

        assert proc.poll() is None, "stop signalled a non-adapter process"
        assert "not a Telegram adapter" in capsys.readouterr().out
    finally:
        proc.kill()
        proc.wait(timeout=10)


@pytest.mark.parametrize(
    "contents", [pytest.param("", id="empty"), pytest.param("garbage", id="garbage")]
)
def test_status_reports_an_unreadable_pid_file(
    mock_home, monkeypatch, capsys, contents
):
    """A crash mid-write leaves a file `status` must report, not crash on."""
    pid_path = mock_home / ".gaia" / "telegram.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(contents, encoding="utf-8")

    _run_cli(monkeypatch, "status", "--health-port", "1")

    assert "unreadable PID file" in capsys.readouterr().out


def test_stop_removes_an_unreadable_pid_file(mock_home, monkeypatch, capsys):
    pid_path = mock_home / ".gaia" / "telegram.pid"
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text("", encoding="utf-8")

    _run_cli(monkeypatch, "stop")

    assert not pid_path.exists()
    assert "Unreadable PID file" in capsys.readouterr().out
