# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""``configure_console_encoding`` must survive whatever sys.stdout/stderr is.

Callers replace the streams with their own objects (the eval MCP launcher's
stderr tee has only ``write``/``flush``); a stream that can't be reconfigured
keeps its encoding and must never stop GAIA from importing or starting.
"""

import logging
import subprocess
import sys

import pytest

from gaia import logger as gaia_logger


class _WriteFlushOnly:
    """A tee-like stream: no ``closed``, no ``reconfigure``."""

    def __init__(self):
        self.data = []

    def write(self, data):
        self.data.append(data)
        return len(data)

    def flush(self):
        pass


class _Reconfigurable:
    closed = False

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def reconfigure(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error

    def write(self, data):
        return len(data)

    def flush(self):
        pass


@pytest.fixture()
def as_windows(monkeypatch):
    chcp = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: chcp.append(a) or None, raising=True
    )
    return chcp


def test_write_flush_only_stream_is_left_alone(as_windows, monkeypatch):
    tee = _WriteFlushOnly()
    out = _Reconfigurable()
    monkeypatch.setattr(sys, "stderr", tee)
    monkeypatch.setattr(sys, "stdout", out)

    gaia_logger.configure_console_encoding()

    assert out.calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert as_windows, "chcp still runs after skipping the tee"
    tee.write("still works")
    assert tee.data == ["still works"]


@pytest.mark.parametrize(
    "error", [TypeError("bad kwarg"), AttributeError("no buffer"), ValueError("x")]
)
def test_reconfigure_failure_is_logged_not_raised(
    as_windows, monkeypatch, caplog, error
):
    monkeypatch.setattr(sys, "stdout", _Reconfigurable(error))
    monkeypatch.setattr(sys, "stderr", _Reconfigurable())

    with caplog.at_level(logging.DEBUG, logger=gaia_logger.__name__):
        gaia_logger.configure_console_encoding()

    assert any("UTF-8 console setup failed" in r.message for r in caplog.records)


def test_closed_stream_is_not_reconfigured(as_windows, monkeypatch):
    closed = _Reconfigurable()
    closed.closed = True
    monkeypatch.setattr(sys, "stdout", closed)
    monkeypatch.setattr(sys, "stderr", None)

    gaia_logger.configure_console_encoding()

    assert closed.calls == []
