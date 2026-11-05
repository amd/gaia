# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where a user fixes a model-provider failure, worded for the front-end they use.

The agent loop can't tell which front-end it serves (the flagship sidecar
answers the TUI, the Agent UI and headless service callers alike), so LLM
errors carry the TUI's advice and each other front-end swaps those exact
sentences for its own with :func:`surface_wording`. Keep every TUI string here
a whole sentence or a phrase that reads correctly after the swap.
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
SERVICE_MODEL_SWITCH_HINT = (
    "Pick another model with the request's `model` field, or change the GAIA "
    "host's default with `gaia config set default_model <model-id>`."
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
SERVICE_PROVIDER_AUTH_HINT = (
    "Check the provider's key and your account's model access on the model "
    "server this GAIA host uses (LEMONADE_BASE_URL). If that server itself "
    "requires authentication, verify LEMONADE_API_KEY."
)

#: Where the TUI keeps provider connections, as the tail of a sentence.
TUI_PROVIDER_SETTINGS = "in the TUI provider settings"
AGENT_UI_PROVIDER_SETTINGS = "under Settings > Model"
CLI_PROVIDER_SETTINGS = (
    "with /provider in the TUI or under Settings > Model in the Agent UI"
)
SERVICE_PROVIDER_SETTINGS = (
    "on the model server this GAIA host uses (LEMONADE_BASE_URL)"
)

#: How a user repairs GAIA's install (a missing or broken Lemonade Server).
TUI_SETUP_HINT = "Run /setup in the TUI to repair GAIA's setup, then try again."
AGENT_UI_SETUP_HINT = "Open Settings > Setup and press Run setup again, then try again."
CLI_SETUP_HINT = "Run `gaia init` to repair GAIA's setup, then try again."
SERVICE_SETUP_HINT = (
    "Run `gaia init` on the GAIA host to repair its setup, then try again."
)

#: What a user does when the AMD LLM gateway rejects GAIA's key.
TUI_GATEWAY_KEY_HINT = (
    "Run /gateway in the TUI and enter a current AMD LLM gateway key."
)
AGENT_UI_GATEWAY_KEY_HINT = (
    "Enter a current AMD LLM gateway key under Settings > Model."
)
CLI_GATEWAY_KEY_HINT = "Run `gaia gateway auth` with a current AMD LLM gateway key."
SERVICE_GATEWAY_KEY_HINT = (
    "Run `gaia gateway auth` on the GAIA host with a current AMD LLM gateway key."
)

#: When GAIA's background service misbehaves. Both apps read it as written.
APP_RESTART_HINT = "If it keeps happening, close GAIA and open it again."
CLI_RESTART_HINT = (
    "If it keeps happening, run `gaia daemon status` and check the daemon log."
)

#: A model server whose model call keeps timing out.
APP_WEDGED_SERVER_HINT = "If it keeps timing out, close GAIA and open it again."
# `gaia kill`, not `gaia daemon restart`: the daemon reaps only a server it started.
CLI_WEDGED_SERVER_HINT = (
    "If it keeps timing out, clear the wedged server with `gaia kill`; GAIA "
    "restarts it on the next query."
)

_SWAPS = {
    "agent_ui": (
        (TUI_MODEL_SWITCH_HINT, AGENT_UI_MODEL_SWITCH_HINT),
        (TUI_PROVIDER_AUTH_HINT, AGENT_UI_PROVIDER_AUTH_HINT),
        (TUI_PROVIDER_SETTINGS, AGENT_UI_PROVIDER_SETTINGS),
        (TUI_SETUP_HINT, AGENT_UI_SETUP_HINT),
        (TUI_GATEWAY_KEY_HINT, AGENT_UI_GATEWAY_KEY_HINT),
    ),
    "cli": (
        (TUI_MODEL_SWITCH_HINT, CLI_MODEL_SWITCH_HINT),
        (TUI_PROVIDER_AUTH_HINT, CLI_PROVIDER_AUTH_HINT),
        (TUI_PROVIDER_SETTINGS, CLI_PROVIDER_SETTINGS),
        (TUI_SETUP_HINT, CLI_SETUP_HINT),
        (TUI_GATEWAY_KEY_HINT, CLI_GATEWAY_KEY_HINT),
        (APP_RESTART_HINT, CLI_RESTART_HINT),
        (APP_WEDGED_SERVER_HINT, CLI_WEDGED_SERVER_HINT),
    ),
    "service": (
        (TUI_MODEL_SWITCH_HINT, SERVICE_MODEL_SWITCH_HINT),
        (TUI_PROVIDER_AUTH_HINT, SERVICE_PROVIDER_AUTH_HINT),
        (TUI_PROVIDER_SETTINGS, SERVICE_PROVIDER_SETTINGS),
        (TUI_SETUP_HINT, SERVICE_SETUP_HINT),
        (TUI_GATEWAY_KEY_HINT, SERVICE_GATEWAY_KEY_HINT),
        (APP_RESTART_HINT, CLI_RESTART_HINT),
        (APP_WEDGED_SERVER_HINT, CLI_WEDGED_SERVER_HINT),
    ),
    "tui": (),
}

#: Every front-end :func:`surface_wording` knows. ``service`` is a headless
#: caller of the agent's HTTP API, which has no settings screen of its own.
SURFACES = tuple(sorted(_SWAPS))


def surface_wording(text: str, surface: str) -> str:
    """*text* with the TUI's provider advice reworded for *surface*.

    *surface* is one of :data:`SURFACES`. Text without TUI
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
