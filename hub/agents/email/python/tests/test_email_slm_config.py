# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Config + fail-safe wiring tests for the SLM layer.

Covers: ``use_slm`` defaults off (experimental) and is overridable via
``GAIA_EMAIL_USE_SLM``; ``slm_*`` fields exist and stay preconfigured so enabling
is a one-flag change; field names never hint
at a cloud LLM (AC3); half-configured tasks fail loudly in ``validate()``;
factories return ``None`` when unconfigured or when classifier construction
fails. Hermetic: no Lemonade, no network, no download.
"""

from __future__ import annotations

import builtins
import sys
from dataclasses import fields
from pathlib import Path

import pytest

# parents[0]=tests/, [1]=email/, [2]=python/, [3]=agents/, [4]=hub/, [5]=repo-root
_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

pytest.importorskip("gaia_agent_email")

from gaia_agent_email.config import ConfigurationError, EmailAgentConfig  # noqa: E402
from gaia_agent_email.tools import slm_common  # noqa: E402
from gaia_agent_email.tools.slm_common import format_slm_input  # noqa: E402
from gaia_agent_email.tools.slm_phishing import (  # noqa: E402
    get_slm_phishing_classifier,
)
from gaia_agent_email.tools.slm_triage import (  # noqa: E402
    get_slm_triage_classifier,
)

@pytest.fixture(autouse=True)
def _reset_slm_cache():
    slm_common._reset_cache_for_tests()
    yield
    slm_common._reset_cache_for_tests()


class TestFormatSlmInput:
    def test_matches_training_layout(self):
        text = format_slm_input(
            subject="Your 40% discount expires in 2 hours — Dev Suite Pro",
            sender="cyrus.chen@toolforge.io",
            body=(
                "Following up on our conversation (the prior message), this "
                "exclusive rate only holds until 4:32 PM today."
            ),
        )
        assert text == (
            "From: cyrus.chen@toolforge.io\n"
            "Subject: Your 40% discount expires in 2 hours — Dev Suite Pro\n"
            "\n"
            "Following up on our conversation (the prior message), this "
            "exclusive rate only holds until 4:32 PM today."
        )


class TestConfigFields:
    def test_slm_defaults_off_but_stays_preconfigured(self, monkeypatch):
        # Experimental, so opt-in — but the model/checkpoint pairs stay filled
        # so enabling it is one flag, not five.
        monkeypatch.delenv("GAIA_EMAIL_USE_SLM", raising=False)
        cfg = EmailAgentConfig()
        assert cfg.use_slm is False
        assert cfg.slm_triage_model
        assert cfg.slm_triage_checkpoint
        assert cfg.slm_phishing_model
        assert cfg.slm_phishing_checkpoint

    def test_no_slm_field_name_hints_at_cloud_llm(self):
        slm_field_names = [
            f.name for f in fields(EmailAgentConfig) if f.name.startswith("slm_")
        ]
        assert slm_field_names
        for name in slm_field_names + ["use_slm"]:
            lower = name.lower()
            for tok in ("claude", "openai", "anthropic", "chatgpt", "api_key"):
                assert tok not in lower, f"{name} hints at a cloud LLM (AC3)."


class TestValidateMisconfig:
    def test_triage_model_without_checkpoint_raises(self):
        cfg = EmailAgentConfig(
            use_slm=True,
            slm_triage_model="user.triage",
            slm_triage_checkpoint=None,
        )
        with pytest.raises(ConfigurationError) as exc:
            cfg.validate()
        assert "triage" in str(exc.value)

    def test_phishing_checkpoint_without_model_raises(self):
        cfg = EmailAgentConfig(
            use_slm=True,
            slm_phishing_model=None,
            slm_phishing_checkpoint="org/phish:m.gguf",
        )
        with pytest.raises(ConfigurationError) as exc:
            cfg.validate()
        assert "phishing" in str(exc.value)

    def test_both_unset_for_a_task_is_valid(self):
        EmailAgentConfig(
            use_slm=True,
            slm_triage_model=None,
            slm_triage_checkpoint=None,
            slm_phishing_model=None,
            slm_phishing_checkpoint=None,
        ).validate()

    def test_half_config_ignored_when_use_slm_off(self):
        EmailAgentConfig(
            use_slm=False,
            slm_triage_model="user.triage",
            slm_triage_checkpoint=None,
        ).validate()

    def test_fully_configured_task_is_valid(self):
        EmailAgentConfig(
            use_slm=True,
            slm_triage_model="user.triage",
            slm_triage_checkpoint="org/triage:m.gguf",
        ).validate()


class TestUseSlmEnvSwitch:
    """``GAIA_EMAIL_USE_SLM`` turns the experimental layer on without a code edit."""

    def test_unset_is_off(self, monkeypatch):
        monkeypatch.delenv("GAIA_EMAIL_USE_SLM", raising=False)
        assert EmailAgentConfig().use_slm is False

    @pytest.mark.parametrize("raw", ["true", "TRUE", " 1 ", "yes"])
    def test_truthy_values_enable(self, monkeypatch, raw):
        monkeypatch.setenv("GAIA_EMAIL_USE_SLM", raw)
        assert EmailAgentConfig().use_slm is True

    @pytest.mark.parametrize("raw", ["false", "0", "no", ""])
    def test_falsy_values_disable(self, monkeypatch, raw):
        monkeypatch.setenv("GAIA_EMAIL_USE_SLM", raw)
        assert EmailAgentConfig().use_slm is False

    def test_unparseable_value_fails_loudly(self, monkeypatch):
        monkeypatch.setenv("GAIA_EMAIL_USE_SLM", "ture")
        with pytest.raises(ConfigurationError) as exc:
            EmailAgentConfig()
        assert "GAIA_EMAIL_USE_SLM" in str(exc.value)
        assert "ture" in str(exc.value)

    def test_explicit_argument_beats_env(self, monkeypatch):
        monkeypatch.setenv("GAIA_EMAIL_USE_SLM", "true")
        assert EmailAgentConfig(use_slm=False).use_slm is False


class TestResolveBaseUrl:
    def test_strips_api_v1_suffix(self):
        cfg = EmailAgentConfig(base_url="http://localhost:13305/api/v1")
        assert slm_common.resolve_base_url(cfg) == "http://localhost:13305"

    def test_strips_v1_suffix(self):
        cfg = EmailAgentConfig(base_url="http://localhost:13305/v1")
        assert slm_common.resolve_base_url(cfg) == "http://localhost:13305"

    def test_root_url_unchanged(self):
        cfg = EmailAgentConfig(base_url="http://localhost:13305")
        assert slm_common.resolve_base_url(cfg) == "http://localhost:13305"


@pytest.mark.real_slm_build
class TestLemonadeCallShape:
    """The base URL we hand the real classifier must be one it accepts.

    A stub returning success would only prove we called the library; these run
    against the installed ``specific_ai_tools`` so a chat-style ``/api/v1``
    base URL can't silently become ``…/api/v1/v1/models`` on the wire.
    """

    def test_resolved_url_survives_library_normalization(self):
        lemonade = pytest.importorskip(
            "specific_ai_tools.embedding_heads.integrations.lemonade"
        )
        for configured in (
            "http://localhost:13305",
            "http://localhost:13305/v1",
            "http://localhost:13305/api/v1",
        ):
            resolved = slm_common.resolve_base_url(
                EmailAgentConfig(base_url=configured)
            )
            assert resolved == "http://localhost:13305"
            assert lemonade.normalize_lemonade_base_url(resolved) == resolved

    def test_model_discovery_hits_server_root(self, monkeypatch):
        pytest.importorskip("specific_ai_tools")
        import requests

        calls: list = []

        class _Response:
            ok = True
            status_code = 200
            text = "{}"

            @staticmethod
            def json():
                # No "data" key -> the library raises before any HF fetch, so
                # the test stays hermetic while still recording the real URL.
                return {}

        def _fake_request(method, url, **kwargs):
            calls.append((method, url))
            return _Response()

        monkeypatch.setattr(requests, "request", _fake_request)

        cfg = EmailAgentConfig(
            base_url="http://localhost:13305/api/v1",
            slm_phishing_model="phish",
            slm_phishing_checkpoint="org/phish:m.gguf",
        )
        # Fail safe: the discovery error is swallowed into a None classifier.
        assert get_slm_phishing_classifier(cfg) is None
        assert calls == [("GET", "http://localhost:13305/v1/models")]


@pytest.mark.real_slm_build
class TestFactoryFailSafe:
    def test_empty_model_returns_none(self):
        cfg = EmailAgentConfig(
            slm_triage_model=None,
            slm_triage_checkpoint=None,
            slm_phishing_model=None,
            slm_phishing_checkpoint=None,
        )
        assert get_slm_triage_classifier(cfg) is None
        assert get_slm_phishing_classifier(cfg) is None

    def test_build_failure_returns_none(self, monkeypatch):
        cfg = EmailAgentConfig(
            use_slm=True,
            slm_triage_model="user.triage",
            slm_triage_checkpoint="org/triage:m.gguf",
        )
        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name.startswith("specific_ai_tools"):
                raise ImportError("simulated import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert get_slm_triage_classifier(cfg) is None
