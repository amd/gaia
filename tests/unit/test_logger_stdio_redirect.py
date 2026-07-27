# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Tests for routing console logging to stderr in stdio transports.

`gaia mcp serve --stdio` speaks JSON-RPC over stdout. GAIA's console log
handler writes to stdout, so any log line emitted during a request corrupts
the protocol frame and the session goes quiet after the first reply (#2472).
``route_console_logging_to_stderr`` moves stdout log handlers to stderr so the
protocol stream stays clean.
"""

import io
import logging
import sys

import pytest

from gaia.logger import GaiaLogger


@pytest.fixture()
def clean_root():
    """Snapshot/restore root logger handlers so tests don't leak handlers."""
    root = logging.getLogger()
    before = root.handlers[:]
    yield root
    root.handlers[:] = before


def test_redirect_switches_stdout_handler_to_stderr(clean_root, monkeypatch, tmp_path):
    fake_stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    gl = GaiaLogger(log_file=tmp_path / "t.log")

    console = [
        h
        for h in clean_root.handlers
        if isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.FileHandler)
        and h.stream is fake_stdout
    ]
    assert console, "expected a stdout console handler after GaiaLogger init"

    gl.route_console_logging_to_stderr()

    assert all(h.stream is sys.stderr for h in console)
    assert all(h.stream is not fake_stdout for h in console)


def test_file_handler_is_not_redirected(clean_root, monkeypatch, tmp_path):
    """The log FileHandler is a StreamHandler subclass but must stay on file."""
    fake_stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    gl = GaiaLogger(log_file=tmp_path / "t.log")
    file_handlers = [
        h for h in clean_root.handlers if isinstance(h, logging.FileHandler)
    ]
    streams_before = [h.stream for h in file_handlers]

    gl.route_console_logging_to_stderr()

    assert [h.stream for h in file_handlers] == streams_before
    assert all(h.stream is not sys.stderr for h in file_handlers)


def test_no_log_output_reaches_stdout_after_redirect(clean_root, monkeypatch, tmp_path):
    """A request-time INFO log must not write to stdout once redirected."""
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdout", fake_stdout)

    gl = GaiaLogger(log_file=tmp_path / "t.log")
    gl.route_console_logging_to_stderr()

    # Point the redirected handler(s) at a capturable stderr buffer.
    for h in clean_root.handlers:
        if (
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
            and h.stream is sys.stderr
        ):
            h.setStream(fake_stderr)

    logging.getLogger("gaia.test.stdio").info("json-rpc-must-stay-clean")

    assert fake_stdout.getvalue() == ""
    assert "json-rpc-must-stay-clean" in fake_stderr.getvalue()
