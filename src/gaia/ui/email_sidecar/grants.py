# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep a chat's list of a relayed agent's "always allow" grants true.

An "always" answer to a relayed prompt is held by the sidecar's session, not by
this backend. The chat lists it beside its own grants
(:class:`gaia.ui.permissions.SessionPermissions`) and revokes it through the
sidecar (contract >= 2.19), so what the user sees is what the agent may do:

- after an "always", the list is re-read from the sidecar;
- before each relayed turn, a grant the chat does not list is revoked (one
  made before this backend restarted: consent must not outlive the run it was
  given in) and one the sidecar no longer holds is dropped;
- revoking one asks the sidecar first and drops it here only once it is gone.
"""

from __future__ import annotations

from typing import Any, Optional

from gaia.logger import get_logger
from gaia.ui import permissions as session_permissions
from gaia.ui.email_sidecar.errors import SidecarHTTPError

logger = get_logger(__name__)


def _sidecar_grants(proxy: Any, session_id: str) -> list:
    """The sidecar session's grants; none when it has no such session."""
    try:
        return list(proxy.list_grants(session_id).get("grants") or [])
    except SidecarHTTPError as exc:
        if exc.status_code == 404:
            return []
        raise


def refresh(proxy: Any, session_id: str) -> None:
    """Make the chat's relayed grants exactly what the sidecar holds now."""
    session_permissions.for_session(session_id).set_relayed_grants(
        proxy.agent_id, _sidecar_grants(proxy, session_id)
    )


def reconcile(proxy: Any, session_id: str) -> None:
    """Before a relayed turn: revoke grants the chat doesn't list, drop gone ones.

    Raises ``SidecarError`` when the sidecar can't be read or a revoke fails;
    the turn must not run with a grant the user cannot see.
    """
    perms = session_permissions.for_session(session_id)
    listed = set(perms.relayed_keys())
    held = _sidecar_grants(proxy, session_id)
    for grant in held:
        if grant["key"] not in listed:
            logger.info(
                "Revoking %s grant %r the chat does not list (made before this "
                "backend started)",
                proxy.agent_id,
                grant["label"],
            )
            revoke(proxy, session_id, grant["key"])
    perms.set_relayed_grants(proxy.agent_id, [g for g in held if g["key"] in listed])


def revoke(proxy: Any, session_id: str, key: Optional[str]) -> None:
    """Revoke one relayed grant, or all with *key* None. Gone already is done."""
    try:
        proxy.revoke_grant(session_id, key)
    except SidecarHTTPError as exc:
        if exc.status_code != 404:
            raise
    session_permissions.for_session(session_id).forget_relayed(key)
