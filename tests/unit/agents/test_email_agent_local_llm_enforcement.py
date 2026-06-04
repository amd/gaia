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
from gaia.llm.lemonade_client import MODELS, ModelType


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


class TestGemmaE2BCatalogEntry:
    """The Gemma-4 E2B model MUST be registered in the MODELS catalog so the
    email agent can select it without falling back to the larger E4B model.

    Issue #1282: register E2B as a first-class catalog option.
    """

    E2B_KEY = "gemma-4-e2b"
    # The NPU-native FastFlowLM build (checkpoint gemma4-it:e2b), validated on
    # the Strix Halo box (device=npu, recipe=flm). NOT the llama.cpp GGUF
    # variant — only the FLM build runs on the NPU (issue #1282).
    E2B_MODEL_ID = "gemma4-it-e2b-FLM"

    def test_e2b_key_exists_in_catalog(self):
        """``gemma-4-e2b`` key must be present in MODELS."""
        assert self.E2B_KEY in MODELS, (
            f"'gemma-4-e2b' not found in MODELS — did you add the catalog entry in "
            "src/gaia/llm/lemonade_client.py? (issue #1282)"
        )

    def test_e2b_model_id_is_flm_npu_build(self):
        """model_id must be the NPU-native FLM build (validated on hardware)."""
        req = MODELS[self.E2B_KEY]
        assert req.model_id == self.E2B_MODEL_ID, (
            f"Expected model_id={self.E2B_MODEL_ID!r}, got {req.model_id!r}. "
            "This must be the FLM (NPU) build, not the GGUF (llama.cpp) variant "
            "— only FLM runs on the Strix Halo NPU. Verify against the box's "
            "`/api/v1/models` before changing this."
        )

    def test_e2b_is_llm_type(self):
        """The E2B entry must be an LLM (not VLM/embed) for email triage."""
        req = MODELS[self.E2B_KEY]
        assert (
            req.model_type == ModelType.LLM
        ), f"Expected model_type=ModelType.LLM, got {req.model_type!r}"

    def test_e2b_tool_calling_enabled(self):
        """Email triage tools require tool_calling=True."""
        req = MODELS[self.E2B_KEY]
        assert req.tool_calling is True, (
            "gemma-4-e2b must have tool_calling=True — the email agent relies on "
            "native tool calls for triage, organize, and reply operations."
        )

    def test_e2b_min_ctx_size_matches_npu_window(self):
        """min_ctx_size must match what the E2B/FLM NPU build actually serves.

        Validated on the Strix Halo box: the FLM build serves ctx_size=4096.
        The triage classifier clips email bodies to 4000 chars, so a single
        email + the triage system prompt fit in the 4 K window. Asserting a
        larger minimum than the hardware serves would be a faked requirement
        that the model can't satisfy.
        """
        req = MODELS[self.E2B_KEY]
        assert req.min_ctx_size >= 4096, (
            f"min_ctx_size={req.min_ctx_size} is below the 4 K NPU window the "
            "FLM build serves."
        )

    def test_e2b_display_name_set(self):
        """display_name must be a non-empty string."""
        req = MODELS[self.E2B_KEY]
        assert (
            isinstance(req.display_name, str) and req.display_name.strip()
        ), "gemma-4-e2b.display_name is empty — set a human-readable label."

    def test_email_agent_config_accepts_e2b_model_id(self):
        """``EmailAgentConfig(model_id=<e2b_id>)`` must be constructable and
        pass the base_url allowlist check (no ``base_url`` means local default).
        """
        cfg = EmailAgentConfig(model_id=self.E2B_MODEL_ID)
        cfg.validate()  # MUST NOT raise — default base_url is None (local)
        assert cfg.model_id == self.E2B_MODEL_ID

    def test_email_agent_config_e2b_rejects_cloud_base_url(self):
        """Specifying the E2B model_id must not open a path to a cloud LLM.

        The AC3 allowlist must still block cloud ``base_url`` even when the
        caller explicitly requests the E2B model.
        """
        cfg = EmailAgentConfig(
            model_id=self.E2B_MODEL_ID,
            base_url="https://api.openai.com/v1",
        )
        with pytest.raises(ConfigurationError) as exc:
            cfg.validate()
        assert "AC3" in str(exc.value)


class TestGemmaE2BLazyDownload:
    """Registering the E2B model in the catalog MUST NOT trigger a model
    download at import / config-construction time.  The download must remain
    lazy — deferred to first use via ``_ensure_model_loaded`` /
    ``_preload_on_idle_server``.

    This protects the critical install path: ``gaia init`` and a fresh
    ``import gaia.llm.lemonade_client`` MUST NOT pull multi-GB weights.
    """

    def test_importing_lemonade_client_does_no_network_or_subprocess(self):
        """Importing the module MUST cross no network/subprocess boundary.

        Guards the #1282 AC "no large download in the critical install path":
        merely declaring the E2B entry in ``MODELS`` must not pull weights or
        probe the server at import time. We patch at the *real* chokepoints a
        download/server-spawn must cross — ``requests`` (the HTTP adapter every
        ``requests.get``/``post`` in this module funnels through) and
        ``subprocess`` (``Popen``/``run`` for the server) — BEFORE re-importing,
        so the assertion fails loudly if a future import-time side effect tries
        to reach out. Patching the module's own ``load_model`` here would be
        vacuous: ``importlib`` builds a brand-new class object the patch never
        touches.
        """
        import importlib
        import subprocess
        import sys
        import unittest.mock as mock

        import requests

        mod_name = "gaia.llm.lemonade_client"
        saved = sys.modules.pop(mod_name, None)
        try:
            with (
                mock.patch.object(requests.adapters.HTTPAdapter, "send") as mock_http,
                mock.patch.object(subprocess, "Popen") as mock_popen,
                mock.patch.object(subprocess, "run") as mock_run,
            ):
                importlib.import_module(mod_name)
                mock_http.assert_not_called()
                mock_popen.assert_not_called()
                mock_run.assert_not_called()
        finally:
            # Restore the original module object so later tests (and the rest
            # of the session) keep the same class identity / patched state.
            if saved is not None:
                sys.modules[mod_name] = saved
            else:
                importlib.import_module(mod_name)

    def test_email_agent_config_construction_does_not_call_load_model(
        self, monkeypatch
    ):
        """``EmailAgentConfig(model_id=<e2b>)`` must not trigger a download.

        Construction is purely a dataclass assignment; the download happens
        later when the agent's LLM client first sends a request.
        """
        import unittest.mock as mock

        with mock.patch("gaia.llm.lemonade_client.LemonadeClient.load_model") as m:
            cfg = EmailAgentConfig(model_id="gemma4-it-e2b-FLM")
            cfg.validate()
        m.assert_not_called()
