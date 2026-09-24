# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""The Slack app manifest GAIA needs, and the one-click URL that creates it.

Setup cannot be fully automatic and this module is where that limit lives. A
Slack app can only be created by a human at api.slack.com — Slack's own
``apps.manifest.create`` API does not help, because the configuration token it
requires is itself obtained by hand from that same page. What *is* automatable is
everything after the click: :func:`create_app_url` opens the "create an app"
page with a manifest that already has the right scopes, Socket Mode on, and the
DM event subscribed, so the user never edits a settings form.

Socket Mode is not a preference. It makes the connection an OUTBOUND WebSocket,
so a local agent needs no public URL, no inbound port, no tunnel, and no request
signing secret that could leak. A request-URL app would need all four.
"""

from __future__ import annotations

import json
from urllib.parse import urlencode

#: What the user sees the app called in their workspace.
DEFAULT_APP_NAME = "GAIA"

#: Bot scopes, each tied to something the bridge actually does. Anything not
#: needed is absent: the manifest is what the user is asked to approve, and an
#: unexplained scope in it is a reason to refuse.
BOT_SCOPES: tuple[str, ...] = (
    # Post the reply and edit it as tokens stream in.
    "chat:write",
    # Read the DMs addressed to the bot.
    "im:history",
    # Open a DM to reach the user.
    "im:write",
    # Download a document or image the user shares, for RAG/VLM ingestion.
    "files:read",
    # Upload a file the agent produced back to the user.
    "files:write",
    # Resolve a user id to a name, so the allowlist can be shown in words.
    "users:read",
)

#: The only event subscribed. ``message.im`` is DMs to the bot and nothing else:
#: no channel history, no group messages. Widening this is a security decision,
#: not a configuration one — see the adapter's DM-only rule.
BOT_EVENTS: tuple[str, ...] = ("message.im",)

#: Slack's app-creation page. ``new_app=1`` plus a manifest opens the create
#: dialog pre-filled instead of the app list.
CREATE_APP_ENDPOINT = "https://api.slack.com/apps"


def build_manifest(app_name: str = DEFAULT_APP_NAME) -> dict:
    """Return the app manifest describing a Socket Mode GAIA bot.

    ``interactivity`` is enabled because tool approvals are Block Kit buttons;
    without it Slack drops the button click and every gated tool would sit until
    it timed out and denied.
    """
    return {
        "display_information": {
            "name": app_name,
            "description": "Your local GAIA agent, reachable from Slack.",
            "background_color": "#1a1a2e",
        },
        "features": {
            "bot_user": {
                "display_name": app_name,
                # The bridge is started on demand, so claiming always-online
                # would show a green dot while nothing is listening.
                "always_online": False,
            }
        },
        "oauth_config": {"scopes": {"bot": list(BOT_SCOPES)}},
        "settings": {
            "event_subscriptions": {"bot_events": list(BOT_EVENTS)},
            "interactivity": {"is_enabled": True},
            "org_deploy_enabled": False,
            "socket_mode_enabled": True,
            "token_rotation_enabled": False,
        },
    }


def create_app_url(app_name: str = DEFAULT_APP_NAME) -> str:
    """Return the URL that opens Slack's create-app dialog, manifest pre-filled."""
    manifest = json.dumps(build_manifest(app_name), separators=(",", ":"))
    return (
        f"{CREATE_APP_ENDPOINT}?{urlencode({'new_app': 1, 'manifest_json': manifest})}"
    )


#: The two tokens the user pastes back, and how each is recognised. Both are
#: checked before anything is stored: a bot token pasted into the app-token field
#: is the single most common setup mistake, and it fails at connect time with an
#: error that names neither field.
BOT_TOKEN_PREFIX = "xoxb-"
APP_TOKEN_PREFIX = "xapp-"


class TokenFormatError(ValueError):
    """A pasted token is not the kind of token that field needs."""


def validate_tokens(bot_token: str, app_token: str) -> None:
    """Raise :class:`TokenFormatError` unless both tokens look like their kind.

    This is a shape check, not an authenticity check — the caller still proves
    the tokens work by calling ``auth.test``. It exists to catch the swap before
    a network round-trip turns it into a confusing 401.
    """
    bot_token = (bot_token or "").strip()
    app_token = (app_token or "").strip()
    if not bot_token.startswith(BOT_TOKEN_PREFIX):
        hint = (
            " That looks like the app-level token — it goes in the other field."
            if bot_token.startswith(APP_TOKEN_PREFIX)
            else ""
        )
        raise TokenFormatError(
            f"The bot token must start with '{BOT_TOKEN_PREFIX}'.{hint} Find it "
            f"under OAuth & Permissions → Bot User OAuth Token, after clicking "
            f"Install to Workspace."
        )
    if not app_token.startswith(APP_TOKEN_PREFIX):
        hint = (
            " That looks like the bot token — it goes in the other field."
            if app_token.startswith(BOT_TOKEN_PREFIX)
            else ""
        )
        raise TokenFormatError(
            f"The app-level token must start with '{APP_TOKEN_PREFIX}'.{hint} "
            f"Generate it under Basic Information → App-Level Tokens with the "
            f"'connections:write' scope."
        )


#: Printed by `gaia slack setup` and rendered by the TUI. Two manual steps is
#: the floor; stating them plainly beats promising "automatic" and stranding the
#: user on a settings page thirty seconds later.
SETUP_STEPS = (
    "1. A browser opens Slack's create-app page with the manifest filled in. "
    "Pick your workspace and click Create.",
    "2. On Basic Information → App-Level Tokens, click Generate, add the "
    "'connections:write' scope, and copy the 'xapp-' token.",
    "3. On OAuth & Permissions, click Install to Workspace, then copy the "
    "'xoxb-' Bot User OAuth Token.",
    "4. Paste both tokens here. They are stored in your OS keyring, never in a "
    "config file.",
)
