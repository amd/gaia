# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Registered SD image-editing tools enforce access and save results safely."""

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.sd.mixin import SDToolsMixin
from gaia.security import PathValidator


@pytest.fixture
def registered_tools(tmp_path):
    saved = dict(_TOOL_REGISTRY)
    _TOOL_REGISTRY.clear()
    host = SDToolsMixin()
    host.init_sd(output_dir=str(tmp_path / "outputs"))
    host.sd_client = MagicMock()
    host.sd_client.edit_image.return_value = {
        "data": [{"b64_json": base64.b64encode(b"edited").decode()}]
    }
    host.sd_client.upscale_image.return_value = {
        "data": [{"b64_json": base64.b64encode(b"upscaled").decode()}]
    }
    yield host
    _TOOL_REGISTRY.clear()
    _TOOL_REGISTRY.update(saved)


def _scoped_host(host, allowed):
    validator = PathValidator(allowed_paths=[str(allowed)])
    validator.set_access_prompt(lambda _path: False)
    host.path_validator = validator
    return host


@pytest.mark.parametrize("form", ["direct", "dotdot", "symlink"])
def test_registered_edit_and_upscale_refuse_out_of_scope_without_call(
    tmp_path, registered_tools, form
):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()
    source = outside / "source.png"
    source.write_bytes(b"png")
    if form == "direct":
        requested = source
    elif form == "dotdot":
        requested = allowed / ".." / "outside" / "source.png"
    else:
        try:
            link = allowed / "linked.png"
            link.symlink_to(source)
        except OSError:
            pytest.skip("symlinks unavailable on this platform")
        requested = link
    host = _scoped_host(registered_tools, allowed)

    edit = _TOOL_REGISTRY["edit_image"]["function"]
    upscale = _TOOL_REGISTRY["upscale_image"]["function"]

    assert edit(str(requested), "change it")["status"] == "error"
    assert upscale(str(requested))["status"] == "error"
    host.sd_client.edit_image.assert_not_called()
    host.sd_client.upscale_image.assert_not_called()


def test_registered_tools_allow_in_scope_image_and_save_result(
    tmp_path, registered_tools
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "source.png"
    source.write_bytes(b"png")
    host = _scoped_host(registered_tools, allowed)

    edit = _TOOL_REGISTRY["edit_image"]["function"]
    upscale = _TOOL_REGISTRY["upscale_image"]["function"]

    edit_result = edit(str(source), "change it")
    upscale_result = upscale(str(source))

    assert edit_result["status"] == "success"
    assert upscale_result["status"] == "success"
    assert Path(edit_result["image_path"]).is_relative_to(host.sd_output_dir)
    assert Path(upscale_result["image_path"]).is_relative_to(host.sd_output_dir)
    host.sd_client.edit_image.assert_called_once()
    host.sd_client.upscale_image.assert_called_once()


def test_registered_tools_report_empty_or_invalid_image_response(
    tmp_path, registered_tools
):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = allowed / "source.png"
    source.write_bytes(b"png")
    host = _scoped_host(registered_tools, allowed)
    host.sd_client.edit_image.return_value = {"data": []}
    host.sd_client.upscale_image.return_value = {"data": [{"b64_json": 42}]}

    edit = _TOOL_REGISTRY["edit_image"]["function"]
    upscale = _TOOL_REGISTRY["upscale_image"]["function"]

    assert edit(str(source), "change it")["status"] == "error"
    assert upscale(str(source))["status"] == "error"
