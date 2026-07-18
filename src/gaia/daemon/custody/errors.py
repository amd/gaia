# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Loud, actionable, typed errors for the custody API (no silent fallbacks).

Each error carries the HTTP status the route layer maps it to (§0.31): a bad or
missing secret is a 403, a cross-agent access attempt is a 403, an unknown
session is a 404, an audit-chain conflict is a 409, and an unavailable store is a
503. The route layer translates these at the boundary — the store/auth layers
raise them so a wrong caller never silently reads an empty result.
"""

from __future__ import annotations


class CustodyError(Exception):
    """Base for all custody failures. ``http_status`` is the boundary mapping."""

    http_status = 500


class StoreUnavailableError(CustodyError):
    """The custody SQLite store could not be opened or written.

    Fail loud (§ no-silent-fallbacks): custody-backed features do not degrade to
    an empty/None result — the caller gets a 503 naming the store path.
    """

    http_status = 503


class UnknownSecretError(CustodyError):
    """The presented custody secret is not bound to any agent.

    Missing/malformed header and unknown secret both map to 403 here (the
    reverse contract's auth is a bearer secret, not a challenge — there is no
    401 negotiation on this loopback leg per §0.11).
    """

    http_status = 403


class ScopeDeniedError(CustodyError):
    """The caller tried to read/write data owned by another agent (or a scope
    its manifest has not been granted). The core §0.11 boundary — a single
    reader must not become an exfiltration surface."""

    http_status = 403


class SessionNotFoundError(CustodyError):
    """No session with that id exists at all (distinct from ScopeDenied, which
    is a session that exists but belongs to a different agent)."""

    http_status = 404


class AuditConflictError(CustodyError):
    """An audit append conflicted (duplicate ``action_id`` for the agent).

    v1 audit is plain append-only (§0.35.5); the conflict guard is the idempotency
    key, not the deferred hash-chain seal."""

    http_status = 409


class InvalidScopeError(CustodyError):
    """A memory scope literal outside the known set was supplied."""

    http_status = 400
