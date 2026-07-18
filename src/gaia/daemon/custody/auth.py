# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""``CustodyAuth`` — secret→agent-id binding for the custody reverse contract
(design §0.11; acceptance: "secret→agent-id binding happens at mint, not
per-request trust").

The binding is established once, at sidecar **mint** (the registry mints a
secret when it constructs a manager). Every ``/host/v1/*`` request presents that
secret and the daemon *resolves* the agent id from it — the request never
carries a claimed agent id the daemon would have to trust. This is the whole
per-agent scoping guarantee: a sidecar can only be the agent its secret was
bound to.

Secrets live only in memory (re-minted every daemon start, like the client
token — §0.11 note): they are never written to disk, so there is no persisted
credential to leak. Lookups are constant-time to avoid a timing oracle over the
secret set.
"""

from __future__ import annotations

import secrets
import threading

from gaia.daemon.custody.errors import UnknownSecretError

# Per-spawn secret length (URL-safe token, ~256 bits like the client token).
_SECRET_NBYTES = 32


class CustodyAuth:
    """In-memory registry of ``custody secret -> agent_id`` bindings."""

    def __init__(self) -> None:
        # secret -> agent_id, and the reverse for revoke-by-agent. Guarded by a
        # lock so mint/revoke from the spawn path race-cleanly with resolve()
        # from request threads.
        self._by_secret: "dict[str, str]" = {}
        self._by_agent: "dict[str, str]" = {}
        self._lock = threading.Lock()

    def mint(self, agent_id: str) -> str:
        """Bind a fresh secret to *agent_id* and return it (called at mint).

        Re-minting for an agent that already has a binding rotates the secret:
        the old one stops resolving immediately, so a restarted sidecar's new
        secret is the only valid credential.
        """
        secret = secrets.token_urlsafe(_SECRET_NBYTES)
        with self._lock:
            old = self._by_agent.get(agent_id)
            if old is not None:
                self._by_secret.pop(old, None)
            self._by_secret[secret] = agent_id
            self._by_agent[agent_id] = secret
        return secret

    def resolve(self, secret: str) -> str:
        """Return the agent id bound to *secret*, or raise if none is.

        Constant-time over the known secrets: iterate them all with
        ``compare_digest`` so a caller cannot learn a valid secret's prefix from
        response timing.
        """
        if not secret:
            raise UnknownSecretError(
                "Missing custody secret. Send 'Authorization: Bearer <secret>' "
                "using the value the daemon injected at spawn "
                "(GAIA_HOST_CUSTODY_SECRET). The secret rotates on daemon "
                "restart — a re-ensured sidecar receives the current one."
            )
        with self._lock:
            match = None
            for known, agent_id in self._by_secret.items():
                if secrets.compare_digest(known, secret):
                    match = agent_id
            if match is None:
                raise UnknownSecretError(
                    "Custody secret is not bound to any agent. It may have "
                    "rotated (daemon restart) or the sidecar was stopped — "
                    "re-ensure the agent (POST /daemon/v1/agents/<id>/ensure) "
                    "to receive a current secret."
                )
            return match

    def revoke(self, agent_id: str) -> None:
        """Drop *agent_id*'s binding (called on stop/uninstall). Idempotent."""
        with self._lock:
            old = self._by_agent.pop(agent_id, None)
            if old is not None:
                self._by_secret.pop(old, None)

    def secret_for(self, agent_id: str) -> "str | None":
        """The current secret bound to *agent_id*, if any (spawn-env wiring)."""
        with self._lock:
            return self._by_agent.get(agent_id)
