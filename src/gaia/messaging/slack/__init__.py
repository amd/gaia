# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Slack Socket Mode bridge to the flagship GAIA agent.

Submodules are imported lazily by their callers: ``adapter`` pulls in
``slack_sdk`` and the RAG ingestion path, which a caller that only wants the
manifest or the onboarding state should not pay for.
"""

from gaia.messaging.slack.manifest import (
    APP_TOKEN_PREFIX,
    BOT_SCOPES,
    BOT_TOKEN_PREFIX,
    SETUP_STEPS,
    TokenFormatError,
    build_manifest,
    create_app_url,
    validate_tokens,
)
from gaia.messaging.slack.onboarding import (
    STATE_CONNECTED,
    STATE_NEVER,
    STATE_SKIPPED,
    STATE_UNSET,
    OnboardingState,
    OnboardingStateError,
    detect_slack,
    load_state,
    record_decision,
    save_state,
    should_offer,
    slack_is_installed,
)

__all__ = [
    "APP_TOKEN_PREFIX",
    "BOT_SCOPES",
    "BOT_TOKEN_PREFIX",
    "SETUP_STEPS",
    "STATE_CONNECTED",
    "STATE_NEVER",
    "STATE_SKIPPED",
    "STATE_UNSET",
    "OnboardingState",
    "OnboardingStateError",
    "TokenFormatError",
    "build_manifest",
    "create_app_url",
    "detect_slack",
    "load_state",
    "record_decision",
    "save_state",
    "should_offer",
    "slack_is_installed",
    "validate_tokens",
]
