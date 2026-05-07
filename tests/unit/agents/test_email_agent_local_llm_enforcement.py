# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
AC3 enforcement tests — the email agent MUST process all email content
locally. There must be no path through the configuration surface to a
cloud LLM, and the runtime must reject ``base_url`` values that point at
known cloud-LLM hosts.

Two layers of defense exercised here:

1. **Architectural**: ``EmailAgentConfig`` has no field whose name even
   suggests a cloud LLM (no ``use_claude``, no ``use_chatgpt``, no
   ``api_key``).
2. **Runtime**: ``EmailAgentConfig.validate()`` parses ``base_url``'s
   host and refuses anything outside the local-LLM allowlist.

The integration test in ``tests/integration/test_email_agent_local_only.py``
provides a third layer (HTTP allow-list) that can only run with a live
LLM, but these unit checks must always pass.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from gaia.agents.email.config import ConfigurationError, EmailAgentConfig


class TestNoCloudLlmFields:
    """The configuration surface MUST NOT name a cloud LLM provider."""

    @pytest.mark.parametrize(
        "forbidden",
        [
            "use_claude",
            "use_chatgpt",
            "use_openai",
            "use_anthropic",
            "claude_api_key",
            "openai_api_key",
            "anthropic_api_key",
        ],
    )
    def test_field_does_not_exist(self, forbidden):
        names = {f.name for f in fields(EmailAgentConfig)}
        assert forbidden not in names, (
            f"EmailAgentConfig.{forbidden} would create a path to cloud LLM "
            "for email body content. AC3 enforcement requires NO such field."
        )

    def test_no_field_name_contains_cloud_token(self):
        for f in fields(EmailAgentConfig):
            lower = f.name.lower()
            for tok in ("claude", "openai", "anthropic", "chatgpt"):
                assert tok not in lower, (
                    f"EmailAgentConfig.{f.name} hints at a cloud LLM "
                    f"(token={tok!r}). AC3 forbids."
                )


class TestBaseUrlAllowlist:
    """``base_url`` must point at a local LLM endpoint, not a cloud host."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:13305/api/v1",
            "http://127.0.0.1:13305/api/v1",
            "http://[::1]:8080/v1",
        ],
    )
    def test_local_hosts_pass_validation(self, url):
        cfg = EmailAgentConfig(base_url=url)
        cfg.validate()  # MUST NOT raise

    @pytest.mark.parametrize(
        "url",
        [
            "https://api.openai.com/v1",
            "https://api.anthropic.com/v1",
            "https://generativelanguage.googleapis.com",
            "https://example.com/llm",
        ],
    )
    def test_remote_hosts_rejected(self, url):
        cfg = EmailAgentConfig(base_url=url)
        with pytest.raises(ConfigurationError) as exc:
            cfg.validate()
        # Error names the violating host AND the allowlist so the user
        # knows what changed.
        from urllib.parse import urlparse

        host = urlparse(url).hostname
        assert host in str(exc.value), str(exc.value)
        assert "AC3" in str(exc.value)

    def test_lemonade_env_var_added_to_allowlist(self, monkeypatch):
        """Setting LEMONADE_BASE_URL allows that host to validate."""
        monkeypatch.setenv("LEMONADE_BASE_URL", "http://gpu-host.local:9001/v1")
        cfg = EmailAgentConfig(base_url="http://gpu-host.local:9001/api/v1")
        cfg.validate()

    def test_none_base_url_passes(self):
        # When base_url is None the agent will fall through to the
        # default (Lemonade); validation has nothing to check.
        EmailAgentConfig(base_url=None).validate()


class TestDbPathDefault:
    def test_default_db_path_under_home(self, tmp_path, monkeypatch):
        monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
        cfg = EmailAgentConfig()
        path = cfg.resolved_db_path()
        assert str(tmp_path) in path
        assert path.endswith("state.db")

    def test_explicit_db_path_overrides(self, tmp_path):
        cfg = EmailAgentConfig(db_path=str(tmp_path / "x.db"))
        assert cfg.resolved_db_path() == str(tmp_path / "x.db")
