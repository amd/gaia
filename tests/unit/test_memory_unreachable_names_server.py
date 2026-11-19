# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Memory's "unreachable" notice names the server the user set (#5295).

With a base URL the user chose, the notice said "the Lemonade embedding
service" and "Start Lemonade Server", though GAIA can't tell what that URL
serves.
"""

from types import SimpleNamespace

import gaia.agents.base.memory as memory
from gaia.agents.base.memory import MEMORY_UNAVAILABLE_SERVICE_UNREACHABLE, MemoryMixin


def _host():
    return SimpleNamespace(
        _memory_store=None,
        _memory_unavailable_reason=MEMORY_UNAVAILABLE_SERVICE_UNREACHABLE,
        _memory_unavailable_detail="Request failed with status 502",
    )


def test_a_user_set_url_is_named_without_credentials(monkeypatch):
    monkeypatch.setattr(
        memory, "configured_lemonade_url", lambda: "http://u:pw@10.0.0.5:8080/v1"
    )
    message = MemoryMixin.memory_unavailable_message(_host())
    assert "the model server at 10.0.0.5:8080" in message
    assert "pw" not in message and "Start Lemonade Server" not in message
    assert "status 502" in message


def test_gaias_own_lemonade_keeps_the_original_advice(monkeypatch):
    monkeypatch.setattr(memory, "configured_lemonade_url", lambda: None)
    message = MemoryMixin.memory_unavailable_message(_host())
    assert "the Lemonade embedding service was unreachable at startup" in message
    assert "Start Lemonade Server" in message
