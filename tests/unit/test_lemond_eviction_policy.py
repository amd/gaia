# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""GAIA states its eviction policy instead of inheriting whatever the default is.

Lemonade v2026.39.1 added VRAM auto-eviction (``auto_evict``,
``auto_evict_threshold_pct``). GAIA's residency model rests on which way that
points, so the value is written into the config GAIA controls rather than left to
a default that upstream is free to change in a release GAIA has not read yet.

The policy is OFF, and that is a decision rather than caution. GAIA assumes a chat
model and an embedder stay co-resident — ``RAGSDK`` scopes its embedder unload so a
global ``/unload`` cannot take the chat model with it (#1544), the non-streaming
path regained a reload check after an embedder warm-up evicted the model (#1030),
and ``ModelSlotBroker`` serialises sidecar loads so they do not race the slot.
Eviction under VRAM pressure is that same failure arriving from underneath, and
between a loud load failure and a silent eviction that resurfaces as a cold reload
or a truncated answer, fail-loudly picks the loud one.

Turning it on is a real option for VRAM-constrained machines — see #4170 — but it
needs measurement on AMD GPU hardware first.
"""

from gaia.llm.lemonade_client import GPU_CTX_SIZE
from gaia.llm.lemonade_embedded import _LEMOND_CONFIG, _lemond_config


def test_the_eviction_policy_is_stated_not_inherited():
    """An upstream default flip must not silently change GAIA's residency model."""
    assert "auto_evict" in _LEMOND_CONFIG, (
        "auto_evict is no longer pinned — GAIA would inherit whatever Lemonade "
        "defaults to, and its co-residency assumptions depend on it being off."
    )


def test_automatic_eviction_is_off():
    assert _LEMOND_CONFIG["auto_evict"] is False


def test_the_written_config_carries_the_policy():
    """The value has to survive into the file lemond actually reads."""
    assert _lemond_config(GPU_CTX_SIZE)["auto_evict"] is False


def test_the_threshold_is_not_stated_while_eviction_is_off():
    """auto_evict_threshold_pct is inert here; writing it would only mislead."""
    config = _lemond_config(GPU_CTX_SIZE)
    if config["auto_evict"] is False:
        assert "auto_evict_threshold_pct" not in config, (
            "a threshold written next to auto_evict=false reads as if it applies; "
            "state it only when eviction is turned on."
        )
