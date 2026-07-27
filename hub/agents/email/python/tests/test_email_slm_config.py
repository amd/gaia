# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Config + fail-safe wiring tests for the SLM layer.

Covers: ``use_slm`` defaults on; ``slm_*`` fields exist; field names never hint
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
    def test_slm_fields_exist_and_default_on(self):
        cfg = EmailAgentConfig()
        assert cfg.use_slm is True
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
