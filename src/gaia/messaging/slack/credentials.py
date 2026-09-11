# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Where the two Slack tokens live.

The OS keyring, never a file. A bot token is a standing grant to post as GAIA in
the user's workspace and an app-level token opens the socket that carries their
messages; both belong with the credentials the connectors framework already
stores, and neither belongs in a JSON file that a backup or a screen-share would
carry off the machine.

Tokens can also arrive from the environment (``GAIA_SLACK_BOT_TOKEN`` /
``GAIA_SLACK_APP_TOKEN``), which is how a container or a CI job supplies them
without a keyring at all. The environment wins over the keyring, so an operator
overriding a stored token does not have to clear it first.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from gaia.logger import get_logger

log = get_logger(__name__)

#: Shared with the connectors framework so GAIA has one credential service on
#: the user's keyring rather than two.
SERVICE_NAME = "gaia.connections"

BOT_TOKEN_KEY = "slack:bot_token"
APP_TOKEN_KEY = "slack:app_token"

BOT_TOKEN_ENV_VAR = "GAIA_SLACK_BOT_TOKEN"
APP_TOKEN_ENV_VAR = "GAIA_SLACK_APP_TOKEN"


class SlackCredentialsError(RuntimeError):
    """The Slack tokens are missing or could not be reached."""


@dataclass(frozen=True)
class SlackCredentials:
    """The pair of tokens the adapter needs."""

    bot_token: str
    app_token: str


def _keyring():
    """Import keyring through the connectors guard for an actionable error."""
    from gaia.connectors._keyring import keyring, normalize_keyring_backend_env

    normalize_keyring_backend_env()
    return keyring


def save(bot_token: str, app_token: str) -> None:
    """Store both tokens in the OS keyring."""
    keyring = _keyring()
    keyring.set_password(SERVICE_NAME, BOT_TOKEN_KEY, bot_token)
    keyring.set_password(SERVICE_NAME, APP_TOKEN_KEY, app_token)
    log.info("Stored Slack tokens in the OS keyring under %s", SERVICE_NAME)


def clear() -> None:
    """Remove both tokens. Missing entries are not an error."""
    keyring = _keyring()
    for key in (BOT_TOKEN_KEY, APP_TOKEN_KEY):
        try:
            keyring.delete_password(SERVICE_NAME, key)
        except keyring.errors.PasswordDeleteError:
            # Already absent — the desired end state either way.
            log.debug("No stored Slack credential for %s", key)


def load() -> SlackCredentials:
    """Return both tokens, environment first, then the keyring.

    Raises:
        SlackCredentialsError: when either token is missing, naming the command
            that creates them rather than failing later inside a socket
            handshake with an opaque error.
    """
    bot = os.environ.get(BOT_TOKEN_ENV_VAR)
    app = os.environ.get(APP_TOKEN_ENV_VAR)
    if not (bot and app):
        try:
            keyring = _keyring()
            bot = bot or keyring.get_password(SERVICE_NAME, BOT_TOKEN_KEY)
            app = app or keyring.get_password(SERVICE_NAME, APP_TOKEN_KEY)
        except Exception as e:  # noqa: BLE001 - re-raised with the remedy below
            raise SlackCredentialsError(
                f"Could not read the Slack tokens from the OS keyring: {e}. "
                f"Set {BOT_TOKEN_ENV_VAR} and {APP_TOKEN_ENV_VAR} in the "
                f"environment instead, or re-run `gaia slack setup`."
            ) from e

    missing = [
        name
        for name, value in ((BOT_TOKEN_ENV_VAR, bot), (APP_TOKEN_ENV_VAR, app))
        if not value
    ]
    if missing:
        raise SlackCredentialsError(
            f"Slack is not set up on this machine: no "
            f"{' and no '.join(missing)}. Run `gaia slack setup` to create the "
            f"app and store its tokens, or export them directly. "
            f"Docs: https://amd-gaia.ai/docs/guides/slack"
        )
    return SlackCredentials(bot_token=bot, app_token=app)


def load_optional() -> Optional[SlackCredentials]:
    """Return the tokens, or ``None`` when Slack has not been set up.

    For callers that ask "is this configured?" — a status line, the TUI's
    readiness row — where absence is an answer rather than a failure.
    """
    try:
        return load()
    except SlackCredentialsError:
        return None
