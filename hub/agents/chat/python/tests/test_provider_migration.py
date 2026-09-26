# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The published chat CLI rejects removed providers before startup."""

import sys

import pytest


def test_published_chat_cli_rejects_flag_before_startup(monkeypatch, capsys):
    from gaia_agent_chat.app import parse_args

    monkeypatch.setattr(sys, "argv", ["gaia-chat", "--use-chatgpt"])
    with pytest.raises(SystemExit) as error:
        parse_args()
    assert error.value.code == 2
    assert "discarded tool calls" in capsys.readouterr().err
