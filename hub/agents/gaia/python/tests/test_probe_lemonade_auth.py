# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The sidecar's readiness probe reaches GAIA's own Lemonade with its key.

GAIA's embedded Lemonade binds a port chosen at start and rejects keyless
requests, so a probe that guessed the port or skipped the key reported a
healthy server as "not reachable".
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("gaia_agent")

from gaia_agent import server as server_mod  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.ok = True

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.fixture
def own_server(monkeypatch, tmp_path):
    (tmp_path / "lemonade").mkdir()
    (tmp_path / "lemonade" / "state.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 51234, "api_key": "own-key"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GAIA_HOME", str(tmp_path))
    for name in ("LEMONADE_BASE_URL", "LEMONADE_API_KEY", "GAIA_LEMONADE_EMBEDDED"):
        monkeypatch.delenv(name, raising=False)
    return "http://localhost:51234/api/v1"


def test_probe_sends_every_request_to_gaias_server_with_its_key(own_server, mocker):
    calls = []

    def fake_get(url, timeout=None, headers=None):
        calls.append((url, headers))
        if url.endswith("/models"):
            return _Resp({"data": [{"id": "Gemma-4-E4B-it-GGUF", "ctx_size": 65536}]})
        return _Resp({"version": "10.3.0"})

    mocker.patch("requests.get", side_effect=fake_get)

    probe = server_mod._probe_lemonade()

    assert calls == [
        (f"{own_server}/models", {"Authorization": "Bearer own-key"}),
        (f"{own_server}/health", {"Authorization": "Bearer own-key"}),
    ]
    assert probe["reachable"] is True
    assert probe["present"] is True
    assert probe["version"] == "10.3.0"
