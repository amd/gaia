# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Unit tests for schedule output sinks (``gaia.schedule.sinks``).

Sinks decide *where* a scheduled run's output goes. Per the GAIA no-fallback
rule, a sink that cannot deliver raises an actionable error — it never silently
swallows the failure. These tests cover the dispatch routing table and each
sink's success + failure paths.

``requests``, ``platform``, and ``subprocess`` are imported at module scope in
``gaia.schedule.sinks``, so patches target that module (not their origin).
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from gaia.schedule import sinks

# Patch targets — module-scoped imports in gaia.schedule.sinks.
_REQUESTS = "gaia.schedule.sinks.requests"
_PLATFORM_SYSTEM = "gaia.schedule.sinks.platform.system"
_SUBPROCESS_RUN = "gaia.schedule.sinks.subprocess.run"
_SUBPROCESS_POPEN = "gaia.schedule.sinks.subprocess.Popen"


class TestValidate:
    """``validate`` rejects a sink that could never deliver (#4228)."""

    @pytest.mark.parametrize(
        "sink, sink_args",
        [
            ("stdout", {}),
            ("file:/tmp/log.md", {}),
            ("file", {"path": "/tmp/log.md"}),
            ("telegram", {"to": "123"}),
        ],
    )
    def test_deliverable_sinks_pass(self, sink, sink_args):
        sinks.validate(sink, sink_args)

    def test_telegram_token_is_not_required_at_validate_time(self, monkeypatch):
        # The daemon may have GAIA_TELEGRAM_TOKEN when the add shell does not.
        monkeypatch.delenv("GAIA_TELEGRAM_TOKEN", raising=False)
        sinks.validate("telegram", {"to": "123"})

    @pytest.mark.parametrize(
        "sink, sink_args, match",
        [
            ("telegram", {}, "--to"),
            ("carrier-pigeon", {}, "unknown sink"),
            ("stdout ", {}, "unknown sink"),
            ("file", {}, "file sink requires a path"),
            ("file:", {}, "file sink requires a path"),
        ],
    )
    def test_undeliverable_sinks_raise(self, sink, sink_args, match):
        with pytest.raises(ValueError, match=match):
            sinks.validate(sink, sink_args)

    @pytest.mark.parametrize("system", ["Darwin", "Linux"])
    def test_notification_passes_where_supported(self, mocker, system):
        mocker.patch(_PLATFORM_SYSTEM, return_value=system)
        sinks.validate("notification", {})

    def test_notification_is_valid_on_windows(self, mocker):
        mocker.patch(_PLATFORM_SYSTEM, return_value="Windows")
        sinks.validate("notification", {})


# ===========================================================================
# 1. dispatch routing table
# ===========================================================================


class TestDispatchRouting:

    def test_stdout_prints_output(self, capsys):
        sinks.dispatch("stdout", {}, "hello world")
        captured = capsys.readouterr()
        assert captured.out == "hello world\n"

    def test_file_prefix_writes_to_path(self, tmp_path):
        target = tmp_path / "log.md"
        sinks.dispatch(f"file:{target}", {}, "line one")
        assert target.read_text(encoding="utf-8") == "line one\n"

    def test_file_sink_uses_sink_args_path(self, tmp_path):
        target = tmp_path / "viaargs.md"
        sinks.dispatch("file", {"path": str(target)}, "via args")
        assert target.read_text(encoding="utf-8") == "via args\n"

    def test_file_sink_appends(self, tmp_path):
        target = tmp_path / "log.md"
        sinks.dispatch(f"file:{target}", {}, "first")
        sinks.dispatch(f"file:{target}", {}, "second")
        assert target.read_text(encoding="utf-8") == "first\nsecond\n"

    def test_file_sink_missing_path_raises(self):
        with pytest.raises(ValueError, match="file sink requires a path"):
            sinks.dispatch("file", {}, "no path")

    def test_unknown_sink_raises(self):
        with pytest.raises(ValueError, match="unknown sink"):
            sinks.dispatch("carrier-pigeon", {}, "nope")


# ===========================================================================
# 2. file sink — directory creation + write semantics
# ===========================================================================


class TestFileSink:

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "log.md"
        sinks.dispatch(f"file:{target}", {}, "made the dirs")
        assert target.read_text(encoding="utf-8") == "made the dirs\n"

    def test_trailing_newlines_normalized(self, tmp_path):
        target = tmp_path / "log.md"
        sinks.dispatch(f"file:{target}", {}, "trailing\n\n")
        assert target.read_text(encoding="utf-8") == "trailing\n"


# ===========================================================================
# 3. telegram sink
# ===========================================================================


class TestTelegramSink:

    def test_missing_token_raises(self, monkeypatch):
        monkeypatch.delenv("GAIA_TELEGRAM_TOKEN", raising=False)
        with pytest.raises(ValueError, match="needs a bot token"):
            sinks.dispatch("telegram", {"to": "123"}, "msg")

    def test_missing_recipient_raises(self):
        with pytest.raises(ValueError, match="needs a recipient"):
            sinks.dispatch("telegram", {"token": "tok"}, "msg")

    def test_token_from_env_is_accepted(self, mocker, monkeypatch):
        monkeypatch.setenv("GAIA_TELEGRAM_TOKEN", "env-token")
        mock_requests = mocker.patch(_REQUESTS)
        mock_requests.post.return_value.status_code = 200
        # No token in sink_args — must fall back to the env var.
        sinks.dispatch("telegram", {"to": "123"}, "msg")
        mock_requests.post.assert_called_once()

    def test_success_posts_to_send_message(self, mocker):
        mock_requests = mocker.patch(_REQUESTS)
        mock_requests.post.return_value.status_code = 200

        sinks.dispatch("telegram", {"token": "tok", "to": "42"}, "hello")

        mock_requests.post.assert_called_once()
        url = mock_requests.post.call_args.args[0]
        kwargs = mock_requests.post.call_args.kwargs
        assert url == f"{sinks.TELEGRAM_API}/bottok/sendMessage"
        assert kwargs["json"] == {"chat_id": "42", "text": "hello"}

    def test_non_200_raises_runtime_error(self, mocker):
        mock_requests = mocker.patch(_REQUESTS)
        resp = mock_requests.post.return_value
        resp.status_code = 403
        resp.text = "Forbidden: bot was blocked"

        with pytest.raises(RuntimeError, match="HTTP 403"):
            sinks.dispatch("telegram", {"token": "tok", "to": "42"}, "hello")

    def test_request_exception_raises_runtime_error(self, mocker):
        import requests as real_requests

        mock_requests = mocker.patch(_REQUESTS)
        # Preserve the real exception class so the `except requests.RequestException`
        # in production code still matches the raised instance.
        mock_requests.RequestException = real_requests.RequestException
        mock_requests.post.side_effect = real_requests.RequestException("boom")

        with pytest.raises(RuntimeError, match="could not reach"):
            sinks.dispatch("telegram", {"token": "tok", "to": "42"}, "hello")


# ===========================================================================
# 4. notification sink
# ===========================================================================


class TestNotificationSink:

    def test_windows_dispatch_uses_windows_helper(self, mocker):
        mocker.patch(_PLATFORM_SYSTEM, return_value="Windows")
        helper = mocker.patch("gaia.schedule.sinks._to_windows_notification")
        sinks.dispatch("notification", {"title": "T"}, "body")
        helper.assert_called_once_with("T", "body")

    def test_windows_uses_plyer_when_available(self, mocker):
        notification = mocker.Mock()
        mocker.patch.dict(
            sys.modules, {"plyer": SimpleNamespace(notification=notification)}
        )
        sinks._to_windows_notification("T", "body")
        notification.notify.assert_called_once_with(
            title="T", message="body", timeout=5
        )

    def test_windows_falls_back_to_powershell_when_plyer_missing(self, mocker):
        mocker.patch.dict(sys.modules, {"plyer": None})
        process = mocker.Mock()
        process.wait.return_value = 0
        popen = mocker.patch(_SUBPROCESS_POPEN, return_value=process)
        sinks._to_windows_notification("T", "body")
        args, kwargs = popen.call_args
        assert args[0][0] == "powershell"
        assert "body" not in args[0][-1]
        assert kwargs["env"]["GAIA_NOTIFY_TITLE"] == "T"
        assert kwargs["env"]["GAIA_NOTIFY_MESSAGE"] == "body"
        process.wait.assert_called_once_with(timeout=3)

    def test_windows_powershell_failure_is_not_silent(self, mocker):
        mocker.patch.dict(sys.modules, {"plyer": None})
        process = mocker.Mock()
        process.wait.return_value = 7
        process.stderr.read.return_value = b"bad notifier"
        mocker.patch(_SUBPROCESS_POPEN, return_value=process)
        with pytest.raises(RuntimeError, match="PowerShell exited with code 7"):
            sinks._to_windows_notification("T", "body")

    def test_windows_message_box_does_not_block_the_scheduler(self, mocker):
        import subprocess

        mocker.patch.dict(sys.modules, {"plyer": None})
        process = mocker.Mock()
        process.wait.side_effect = subprocess.TimeoutExpired("powershell", 3)
        mocker.patch(_SUBPROCESS_POPEN, return_value=process)
        sinks._to_windows_notification("T", "body")

    def test_windows_missing_powershell_is_actionable(self, mocker):
        mocker.patch.dict(sys.modules, {"plyer": None})
        mocker.patch(_SUBPROCESS_POPEN, side_effect=FileNotFoundError("powershell"))
        with pytest.raises(FileNotFoundError):
            sinks._to_windows_notification("T", "body")

    def test_unsupported_os_raises_not_implemented(self, mocker):
        mocker.patch(_PLATFORM_SYSTEM, return_value="Plan9")
        with pytest.raises(NotImplementedError, match="not implemented for 'Plan9'"):
            sinks.dispatch("notification", {}, "hi")

    def test_darwin_invokes_osascript(self, mocker):
        mocker.patch(_PLATFORM_SYSTEM, return_value="Darwin")
        mock_run = mocker.patch(_SUBPROCESS_RUN)

        sinks.dispatch("notification", {"title": "T"}, "body text")

        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args[0] == "osascript"
        assert args[1] == "-e"
        script = args[2]
        assert "display notification" in script
        assert "body text" in script
        assert "with title" in script
        assert "T" in script
        assert mock_run.call_args.kwargs["check"] is True

    def test_linux_invokes_notify_send(self, mocker):
        mocker.patch(_PLATFORM_SYSTEM, return_value="Linux")
        mock_run = mocker.patch(_SUBPROCESS_RUN)

        sinks.dispatch("notification", {"title": "T"}, "body")

        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        assert args == ["notify-send", "T", "body"]

    def test_called_process_error_raises_runtime_error(self, mocker):
        import subprocess

        mocker.patch(_PLATFORM_SYSTEM, return_value="Darwin")
        mocker.patch(
            _SUBPROCESS_RUN,
            side_effect=subprocess.CalledProcessError(1, "osascript"),
        )
        with pytest.raises(RuntimeError, match="failed to post"):
            sinks.dispatch("notification", {}, "hi")

    def test_hung_notifier_raises_runtime_error(self, mocker):
        import subprocess

        mocker.patch(_PLATFORM_SYSTEM, return_value="Linux")
        mock_run = mocker.patch(
            _SUBPROCESS_RUN,
            side_effect=subprocess.TimeoutExpired("notify-send", 30),
        )
        with pytest.raises(RuntimeError, match="within 30s.*different --sink"):
            sinks.dispatch("notification", {}, "hi")
        assert mock_run.call_args.kwargs["timeout"] == 30

    def test_missing_tool_raises_file_not_found(self, mocker):
        mocker.patch(_PLATFORM_SYSTEM, return_value="Linux")
        mocker.patch(_SUBPROCESS_RUN, side_effect=FileNotFoundError("no notify-send"))
        with pytest.raises(FileNotFoundError, match="notify-send"):
            sinks.dispatch("notification", {}, "hi")


# ===========================================================================
# 5. _osa AppleScript quoting
# ===========================================================================


class TestOsaQuoting:

    def test_wraps_in_double_quotes(self):
        assert sinks._osa("plain") == '"plain"'

    def test_escapes_embedded_double_quotes(self):
        assert sinks._osa('say "hi"') == '"say \\"hi\\""'

    def test_escapes_backslashes_before_quotes(self):
        # Backslash must be escaped first so it does not double-escape the quote.
        assert sinks._osa("a\\b") == '"a\\\\b"'

    def test_backslash_and_quote_together(self):
        assert sinks._osa('a\\"b') == '"a\\\\\\"b"'
