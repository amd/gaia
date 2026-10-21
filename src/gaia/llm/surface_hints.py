# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a user fixes a model-provider failure, worded for the front-end they use.

The agent loop can't tell which front-end it serves (the flagship sidecar
answers the TUI and the Agent UI alike), so LLM errors carry the TUI's advice
and each other front-end swaps those exact sentences for its own with
:func:`surface_wording`. Keep every TUI string here a whole sentence or a
phrase that reads correctly after the swap.
"""

#: How a TUI user picks another model.
TUI_MODEL_SWITCH_HINT = "Pick another model with /model in the TUI."
AGENT_UI_MODEL_SWITCH_HINT = (
    "Pick another model from the model menu next to the Send button. If you "
    "typed this model ID yourself, clear it under Settings > Advanced > "
    "Custom model ID."
)
CLI_MODEL_SWITCH_HINT = (
    "Pick another model with --model, or change the default with "
    "`gaia config set default_model <model-id>`."
)

#: What a TUI user does when the provider rejects the key (HTTP 401/403). The
#: trailing LEMONADE_API_KEY sentence is what the error classifier anchors on.
TUI_PROVIDER_AUTH_HINT = (
    "Run /provider in the TUI to reconnect the provider, and check your "
    "account's model access. If Lemonade itself requires authentication, "
    "verify LEMONADE_API_KEY."
)
AGENT_UI_PROVIDER_AUTH_HINT = (
    "Open Settings > Model, paste the provider's API key and press Reconnect, "
    "then send your message again. If the key is right, check that your "
    "account has access to this model."
)
CLI_PROVIDER_AUTH_HINT = (
    "Reconnect the provider with /provider in the TUI or under Settings > "
    "Model in the Agent UI, then run the command again, and check your "
    "account's model access. If Lemonade itself requires authentication, "
    "verify LEMONADE_API_KEY."
)

#: Where the TUI keeps provider connections, as the tail of a sentence.
TUI_PROVIDER_SETTINGS = "in the TUI provider settings"
AGENT_UI_PROVIDER_SETTINGS = "under Settings > Model"
CLI_PROVIDER_SETTINGS = (
    "with /provider in the TUI or under Settings > Model in the Agent UI"
)

_SWAPS = {
    "agent_ui": (
        (TUI_MODEL_SWITCH_HINT, AGENT_UI_MODEL_SWITCH_HINT),
        (TUI_PROVIDER_AUTH_HINT, AGENT_UI_PROVIDER_AUTH_HINT),
        (TUI_PROVIDER_SETTINGS, AGENT_UI_PROVIDER_SETTINGS),
    ),
    "cli": (
        (TUI_MODEL_SWITCH_HINT, CLI_MODEL_SWITCH_HINT),
        (TUI_PROVIDER_AUTH_HINT, CLI_PROVIDER_AUTH_HINT),
        (TUI_PROVIDER_SETTINGS, CLI_PROVIDER_SETTINGS),
    ),
    "tui": (),
}


def surface_wording(text: str, surface: str) -> str:
    """*text* with the TUI's provider advice reworded for *surface*.

    *surface* is ``"tui"``, ``"agent_ui"`` or ``"cli"``. Text without TUI
    advice comes back unchanged, so rewording twice is harmless.
    """
    if surface not in _SWAPS:
        raise ValueError(
            f"Unknown front-end {surface!r} for provider advice; expected one "
            f"of {sorted(_SWAPS)}."
        )
    if not text:
        return text
    for tui, own in _SWAPS[surface]:
        text = text.replace(tui, own)
    return text
