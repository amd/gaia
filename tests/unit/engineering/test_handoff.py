# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Native handoff command shape and honest launch status."""

from urllib.parse import parse_qs, urlparse

from gaia.engineering import handoff


def test_connection_explicit_mode_and_no_token_in_argv(tmp_path):
    for backend in ("claude", "codex"):
        config = handoff.connection_config(
            backend, "/test/python", tmp_path, "credential"
        )
        server = config["mcpServers"]["gaia-engineering"]
        assert "--developer-mode" in server["args"]
        assert server["env"]["GAIA_ENGINEERING_TOKEN"] == "credential"
        assert "credential" not in str(server["args"])


def test_claude_prefill_is_not_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        handoff, "detect_apps", lambda: {"claude": {"app": "/Applications/Claude.app"}}
    )
    calls = []
    monkeypatch.setattr(
        handoff.subprocess, "run", lambda argv, **kwargs: calls.append((argv, kwargs))
    )
    result = handoff.open_app("claude", "a" * 32, tmp_path)
    assert result["state"] == "requires_user_action"
    argv, options = calls[0]
    assert argv[:3] == ["open", "-a", "/Applications/Claude.app"]
    query = parse_qs(urlparse(argv[-1]).query)
    assert query["folder"] == [str(tmp_path)]
    assert "a" * 32 in query["q"][0]
    assert options["timeout"] == 15


def test_codex_launch_is_manual(tmp_path, monkeypatch):
    monkeypatch.setattr(handoff.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(
        handoff, "detect_apps", lambda: {"codex": {"app": "/Applications/Codex.app"}}
    )
    calls = []
    monkeypatch.setattr(
        handoff.subprocess, "run", lambda argv, **kwargs: calls.append(argv)
    )
    result = handoff.open_app("codex", "b" * 32, tmp_path)
    assert calls == [["open", "-a", "/Applications/Codex.app"]]
    assert result["state"] == "requires_user_action"
    assert result["backend"] == "codex"
    assert result["task_created"] is False
    assert result["prompt_prefilled"] is False
    assert result["directory_selected"] is False
    assert result["connection_verified"] is False
    assert "Create a new task manually" in result["detail"]
    assert "b" * 32 in result["prompt"]
