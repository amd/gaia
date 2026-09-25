# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""take_screenshot reports why each capture backend failed."""

import sys
import types

import pytest

from gaia.agents.tools.screenshot_tools import ScreenshotToolsMixin


def _fake_mss(error: Exception) -> types.ModuleType:
    module = types.ModuleType("mss")

    def _refuse():
        raise error

    module.mss = _refuse
    module.tools = types.ModuleType("mss.tools")
    return module


def _fake_image_grab(error: Exception) -> types.ModuleType:
    module = types.ModuleType("PIL.ImageGrab")

    def _refuse(*_args, **_kwargs):
        raise error

    module.grab = _refuse
    return module


@pytest.fixture
def capture(tmp_path):
    return lambda: ScreenshotToolsMixin()._take_screenshot(str(tmp_path / "s.png"))


def _install(monkeypatch, mss_module, grab_module):
    if mss_module is None:
        # A None entry makes ``import mss`` raise ImportError.
        monkeypatch.setitem(sys.modules, "mss", None)
        monkeypatch.setitem(sys.modules, "mss.tools", None)
    else:
        monkeypatch.setitem(sys.modules, "mss", mss_module)
        monkeypatch.setitem(sys.modules, "mss.tools", mss_module.tools)
    pil = pytest.importorskip("PIL")
    if grab_module is None:
        monkeypatch.setitem(sys.modules, "PIL.ImageGrab", None)
        monkeypatch.delattr(pil, "ImageGrab", raising=False)
    else:
        monkeypatch.setitem(sys.modules, "PIL.ImageGrab", grab_module)
        monkeypatch.setattr(pil, "ImageGrab", grab_module, raising=False)


def test_an_installed_mss_that_is_refused_is_not_reported_as_missing(
    monkeypatch, capture
):
    _install(
        monkeypatch,
        _fake_mss(PermissionError("screen recording not permitted")),
        _fake_image_grab(OSError("could not create image")),
    )

    result = capture()

    assert result["status"] == "error"
    assert "PermissionError" in result["error"]
    assert "screen recording not permitted" in result["error"]
    assert "could not create image" in result["error"]
    assert "pip install mss" not in result["error"]


def test_the_install_hint_appears_only_when_no_backend_is_installed(
    monkeypatch, capture
):
    _install(monkeypatch, None, None)

    result = capture()

    assert result["status"] == "error"
    assert "mss is not installed" in result["error"]
    assert "pip install mss" in result["error"]
