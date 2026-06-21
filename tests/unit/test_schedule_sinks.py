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

import pytest

from gaia.schedule import sinks

# Patch targets — module-scoped imports in gaia.schedule.sinks.
_REQUESTS = "gaia.schedule.sinks.requests"
_PLATFORM_SYSTEM = "gaia.schedule.sinks.platform.system"
_SUBPROCESS_RUN = "gaia.schedule.sinks.subprocess.run"


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
