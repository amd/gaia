# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Which agent features a model gets.

A small model pays for every tool schema on every step and still fails the
features below, so it is not offered them. The tier is a property of the model
(``ModelRequirement.feature_tier``), never the device: Gemma 4 E4B gets the same
features on the GPU and the NPU. Turn a feature on for "compact" once a compact
model passes its evals.
"""

import os
from dataclasses import dataclass, fields
from typing import Dict, FrozenSet, Optional


@dataclass(frozen=True)
class ModelFeatures:
    """Agent features a model tier offers. Each field names a ``FEATURE_TOOLS`` key."""

    scratchpad: bool
    email: bool
    image_generation: bool
    skill_hub: bool


#: The tools each feature brings, withheld from a model that lacks it.
FEATURE_TOOLS: Dict[str, FrozenSet[str]] = {
    "scratchpad": frozenset(
        {"create_table", "insert_data", "query_data", "list_tables", "drop_table"}
    ),
    "email": frozenset(
        {
            "check_mailbox_access",
            "list_inbox",
            "search_email",
            "read_email",
            "list_mail_folders",
        }
    ),
    "image_generation": frozenset(
        {"generate_image", "list_sd_models", "get_generation_history"}
    ),
    # Installed skills still load; only browsing and changing the library goes.
    "skill_hub": frozenset({"search_skill_hub", "install_skill", "remove_skill"}),
}

FULL = ModelFeatures(scratchpad=True, email=True, image_generation=True, skill_hub=True)
# Measured on Gemma 4 E4B (GPU and NPU): no scratchpad scenario passed, email
# passed 2/12 and followed phishing, image generation only ever fired on an
# injected request, and no skill-hub scenario passed.
COMPACT = ModelFeatures(
    scratchpad=False, email=False, image_generation=False, skill_hub=False
)

TIERS: Dict[str, ModelFeatures] = {"full": FULL, "compact": COMPACT}


#: Forces one tier for every model, e.g. to compare the two on one model.
TIER_ENV = "GAIA_FEATURE_TIER"


def features_for_model(model_id: Optional[str]) -> ModelFeatures:
    """The features *model_id* gets; a model GAIA does not list gets them all."""
    from gaia.llm.lemonade_client import find_model_requirement

    tier = os.environ.get(TIER_ENV, "").strip().lower()
    if tier and tier not in TIERS:
        raise ValueError(
            f"{TIER_ENV}={os.environ[TIER_ENV]!r} is not a feature tier; set it to "
            f"one of {sorted(TIERS)}, or unset it to follow the model."
        )
    if not tier:
        requirement = find_model_requirement(model_id) if model_id else None
        tier = requirement.feature_tier if requirement else "full"
    try:
        return TIERS[tier]
    except KeyError as e:
        raise ValueError(
            f"Model {model_id!r} names feature tier {tier!r}; "
            f"expected one of {sorted(TIERS)} (gaia/agents/base/model_features.py)"
        ) from e


def withheld_tools(features: ModelFeatures) -> FrozenSet[str]:
    """Every tool of every feature *features* turns off."""
    return frozenset().union(
        *(
            FEATURE_TOOLS[f.name]
            for f in fields(features)
            if not getattr(features, f.name)
        )
    )
