# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Mailbox errors shared by every provider backend.

Provider-neutral on purpose: the mixin catches one pair of types regardless of
which mailbox answered, and ``MailboxAuthError`` is what triggers the one-shot
backend re-resolution when a connection changes mid-session.
"""

from __future__ import annotations


class MailboxError(RuntimeError):
    """A mailbox request failed in a way the caller should surface verbatim."""


class MailboxAuthError(MailboxError):
    """The mailbox rejected our credentials or refused the requested scope."""


__all__ = ["MailboxAuthError", "MailboxError"]
