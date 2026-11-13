# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mailbox errors shared by every provider backend.

Provider-neutral on purpose: the mixin catches one pair of types regardless of
which mailbox answered, and ``MailboxAuthError`` is what triggers the one-shot
backend re-resolution when a connection changes mid-session.
"""

from __future__ import annotations

from typing import Iterable, Tuple


class MailboxError(RuntimeError):
    """A mailbox request failed in a way the caller should surface verbatim.

    ``connect`` names the connectors that signing in fixes, and ``reconnect``
    those of them that are connected already. ``setup`` marks a call stopped
    by how accounts are set up right now, before it reached the mailbox.
    """

    def __init__(
        self,
        *args,
        connect: Iterable[str] = (),
        reconnect: Iterable[str] = (),
        setup: bool = False,
    ):
        super().__init__(*args)
        self.connect: Tuple[str, ...] = tuple(connect)
        self.reconnect: Tuple[str, ...] = tuple(reconnect)
        self.setup = setup


class MailboxAuthError(MailboxError):
    """The mailbox rejected our credentials or refused the requested scope."""


class MailboxOutcomeUnknown(MailboxError):
    """A write went out and no clear answer came back, so it may have taken effect."""


__all__ = ["MailboxAuthError", "MailboxError", "MailboxOutcomeUnknown"]
