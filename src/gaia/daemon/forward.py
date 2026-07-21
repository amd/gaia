# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""OAuth forward-OUT — the daemon (custody home) forwards short-lived connector
access tokens to a sidecar (issue #2154 / V2-14).

Role inversion (design §0.6): the daemon owns the long-lived OAuth refresh token
and the per-agent grant ledger; N sidecars each holding a refresh token would
rotate each other out, so the daemon stays the single writer and forwards only
SHORT-LIVED access tokens OUT to the sidecar's ``/v1/connections/{provider}``
intake. The sidecar never receives the refresh token or the OAuth client secret.

What this module does NOT do (kept in the layers that already own them, no
duplication):

- **Refresh** — reuses the client-neutral engine via
  ``gaia.connectors.api.get_access_token_with_expiry_sync`` (grant + scope gated,
  then ``tokens.get_token_with_expiry``). There is no second refresh path here.
- **Grant policy** — the connectors grant ledger is the source of truth. A
  provider not granted to the agent's ``grant_agent_id`` is never forwarded.
- **Custody stores** — this is credential forwarding only; memory/RAG/sessions
  custody is #2153's ``/host/v1``.

Fail loudly, no silent fallbacks (CLAUDE.md):

- provider not granted to the agent → :class:`NotGrantedError` (the daemon route
  maps it to 403; the sidecar never gets a token it wasn't authorized for);
- mint fails because the connection was revoked / not connected → the underlying
  ``AuthRequiredError`` is re-raised AND any stale forward is withdrawn from the
  sidecar, so the sidecar loses access loudly rather than running on a stale
  token;
- the sidecar intake POST/DELETE fails → :class:`ForwardDeliveryError` naming
  the sidecar URL and the transport error.

``forward_all`` (the on-spawn push) is best-effort *per provider* — one
provider's failure is logged with context and does not abort forwarding the
others — but nothing is swallowed: every failure is surfaced in the returned
summary and in the log, and the sidecar's own resolver raises loudly at
mailbox-use time if a token never arrived.
"""

from __future__ import annotations

from typing import Callable, Dict, List, Optional, Tuple

from gaia.daemon.sidecars.errors import UnknownAgentError
from gaia.daemon.sidecars.spec import AgentSidecarSpec
from gaia.logger import get_logger

logger = get_logger(__name__)

# How long the daemon waits on the sidecar intake POST/DELETE. The sidecar route
# is a cheap in-memory write; a slow answer means it is wedged, so fail fast.
_INTAKE_TIMEOUT = 10.0


class ForwardError(Exception):
    """Base for OAuth forward-out failures. Loud + actionable."""


class NotGrantedError(ForwardError):
    """The provider is not granted to the agent, so nothing may be forwarded."""


class ForwardDeliveryError(ForwardError):
    """The sidecar's connections intake refused or dropped the forward."""


class ConnectionForwarder:
    """Forwards granted connector access tokens OUT to a sidecar's intake.

    Constructed once per daemon with the registered specs. The network/keyring
    seams are injectable so unit tests exercise the grant/scope/expiry logic and
    the HTTP boundary without a live keyring or a running sidecar.
    """

    def __init__(
        self,
        specs: "Dict[str, AgentSidecarSpec]",
        *,
        mint: Optional[Callable[..., Tuple[str, float]]] = None,
        list_grants: Optional[Callable[[str], Dict[str, List[str]]]] = None,
        connected_providers: Optional[Callable[[], List[str]]] = None,
        http_post: Optional[Callable[..., object]] = None,
        http_delete: Optional[Callable[..., object]] = None,
    ):
        self._specs = dict(specs)
        self._mint = mint or self._default_mint
        self._list_grants = list_grants or self._default_list_grants
        self._connected_providers = (
            connected_providers or self._default_connected_providers
        )
        self._http_post = http_post or self._default_http_post
        self._http_delete = http_delete or self._default_http_delete

    # -- default seams (production wiring) ---------------------------------

    @staticmethod
    def _default_mint(
        *, provider: str, scopes: List[str], agent_id: str
    ) -> Tuple[str, float]:
        from gaia.connectors.api import get_access_token_with_expiry_sync

        return get_access_token_with_expiry_sync(
            provider=provider, scopes=scopes, agent_id=agent_id
        )

    @staticmethod
    def _default_list_grants(provider: str) -> Dict[str, List[str]]:
        from gaia.connectors.api import list_agent_grants

        return list_agent_grants(provider)

    @staticmethod
    def _default_connected_providers() -> List[str]:
        from gaia.connectors.api import connected_mailbox_providers

        return connected_mailbox_providers()

    @staticmethod
    def _default_http_post(url: str, *, json: dict, headers: dict, timeout: float):
        import requests

        return requests.post(url, json=json, headers=headers, timeout=timeout)

    @staticmethod
    def _default_http_delete(url: str, *, headers: dict, timeout: float):
        import requests

        return requests.delete(url, headers=headers, timeout=timeout)

    # -- spec resolution ---------------------------------------------------

    def _spec(self, agent_id: str) -> AgentSidecarSpec:
        spec = self._specs.get(agent_id)
        if spec is None:
            raise UnknownAgentError(
                f"unknown agent '{agent_id}'; registered agents: "
                + ", ".join(sorted(self._specs))
            )
        return spec

    def _grant_agent_id(self, spec: AgentSidecarSpec) -> str:
        if not spec.grant_agent_id:
            raise NotGrantedError(
                f"agent '{spec.agent_id}' has no grant_agent_id configured, so no "
                "connector credential can be forwarded to it. Set "
                "grant_agent_id/forward_providers on its AgentSidecarSpec if it "
                "consumes forwarded connectors."
            )
        return spec.grant_agent_id

    def _granted_scopes(
        self, provider: str, grant_agent_id: str
    ) -> Optional[List[str]]:
        """Scopes granted to ``grant_agent_id`` for ``provider`` (the grant
        ledger is the source of truth), or ``None`` when the pair has no grant."""
        scopes = self._list_grants(provider).get(grant_agent_id)
        return list(scopes) if scopes else None

    # -- forwarding --------------------------------------------------------

    def forward_provider(
        self, agent_id: str, provider: str, *, base_url: str, bearer: str
    ) -> dict:
        """Mint and forward one provider's access token to the sidecar.

        Raises :class:`NotGrantedError` when the provider is not granted to the
        agent (the daemon never forwards a credential the user did not grant). A
        mint failure from a revoked/absent connection re-raises the connectors
        error AND withdraws any stale forward from the sidecar.
        """
        spec = self._spec(agent_id)
        grant_agent_id = self._grant_agent_id(spec)
        if provider not in spec.forward_providers:
            raise NotGrantedError(
                f"provider '{provider}' is not a forwardable connector for agent "
                f"'{agent_id}'. Forwardable: "
                f"{', '.join(spec.forward_providers) or 'none'}."
            )
        granted = self._granted_scopes(provider, grant_agent_id)
        if granted is None:
            raise NotGrantedError(
                f"agent '{agent_id}' ({grant_agent_id}) has no grant for "
                f"'{provider}'. Connect the account and grant the agent in one "
                f"command — no Agent UI required:\n"
                f"  gaia connectors connect {provider} --scopes <scopes> "
                f"--grant-agent {grant_agent_id}\n"
                f"(`gaia connectors connect {provider}` prints the full OAuth-client "
                f"setup if the connector isn't configured yet.) In the Agent UI you "
                f"can instead use Settings -> Connections."
            )

        try:
            token, expires_at = self._mint(
                provider=provider, scopes=granted, agent_id=grant_agent_id
            )
        except Exception as e:
            # A revoked / not-connected mint is loud here; also withdraw any
            # stale token already on the sidecar so it cannot keep running on it.
            logger.warning(
                "forward-out: minting '%s' for agent '%s' failed (%s); "
                "withdrawing any stale forward from the sidecar",
                provider,
                agent_id,
                e,
            )
            try:
                self._delete_forward(base_url, bearer, provider)
            except ForwardDeliveryError as withdraw_err:
                logger.warning(
                    "forward-out: best-effort withdraw of '%s' after mint "
                    "failure also failed: %s",
                    provider,
                    withdraw_err,
                )
            raise

        self._post_forward(base_url, bearer, provider, token, granted, expires_at)
        logger.info(
            "forward-out: forwarded '%s' access token to agent '%s' (%d scopes, "
            "expires_at=%.0f)",
            provider,
            agent_id,
            len(granted),
            expires_at,
        )
        return {
            "provider": provider,
            "scopes": granted,
            "expires_at": expires_at,
            "forwarded": True,
        }

    def forward_all(self, agent_id: str, *, base_url: str, bearer: str) -> dict:
        """Forward every granted+connected provider to the sidecar (on-spawn push).

        Best-effort per provider: a provider not granted or not connected is
        skipped (not an error — not every agent is granted every connector); a
        mint/delivery failure for a granted+connected provider is recorded and
        logged but does not abort the others. The sidecar's resolver still
        raises loudly at use time if a needed token never arrived.
        """
        spec = self._spec(agent_id)
        grant_agent_id = self._grant_agent_id(spec)
        connected = set(self._connected_providers())
        forwarded: List[dict] = []
        skipped: List[dict] = []
        errors: List[dict] = []
        for provider in spec.forward_providers:
            granted = self._granted_scopes(provider, grant_agent_id)
            if granted is None:
                skipped.append({"provider": provider, "reason": "not_granted"})
                continue
            if provider not in connected:
                skipped.append({"provider": provider, "reason": "not_connected"})
                continue
            try:
                forwarded.append(
                    self.forward_provider(
                        agent_id, provider, base_url=base_url, bearer=bearer
                    )
                )
            except Exception as e:  # noqa: BLE001 - collected + logged, not hidden
                logger.warning(
                    "forward-out: could not forward '%s' to agent '%s': %s",
                    provider,
                    agent_id,
                    e,
                )
                errors.append({"provider": provider, "error": str(e)})
        return {
            "agent_id": agent_id,
            "forwarded": forwarded,
            "skipped": skipped,
            "errors": errors,
        }

    def withdraw(
        self, agent_id: str, provider: str, *, base_url: str, bearer: str
    ) -> dict:
        """Withdraw a forwarded credential from the sidecar (revoke/uninstall,
        §0.20). Idempotent from the sidecar's side; loud on a delivery failure."""
        self._spec(agent_id)
        self._delete_forward(base_url, bearer, provider)
        logger.info(
            "forward-out: withdrew '%s' forward from agent '%s'", provider, agent_id
        )
        return {"provider": provider, "withdrawn": True}

    # -- HTTP boundary to the sidecar intake -------------------------------

    def _post_forward(
        self,
        base_url: str,
        bearer: str,
        provider: str,
        access_token: str,
        scopes: List[str],
        expires_at: float,
    ) -> None:
        url = f"{base_url}/v1/connections/{provider}"
        try:
            resp = self._http_post(
                url,
                json={
                    "access_token": access_token,
                    "scopes": list(scopes),
                    "expires_at": expires_at,
                },
                headers={"Authorization": f"Bearer {bearer}"},
                timeout=_INTAKE_TIMEOUT,
            )
        except Exception as e:  # transport-level failure to the sidecar
            raise ForwardDeliveryError(
                f"could not deliver the '{provider}' forward to the sidecar at "
                f"{url} ({e.__class__.__name__}: {e}). The sidecar may have died "
                "after registration — re-ensure it and retry."
            ) from e
        if resp.status_code >= 400:
            raise ForwardDeliveryError(
                f"the sidecar rejected the '{provider}' forward at {url}: HTTP "
                f"{resp.status_code} {self._resp_text(resp)}"
            )

    def _delete_forward(self, base_url: str, bearer: str, provider: str) -> None:
        url = f"{base_url}/v1/connections/{provider}"
        try:
            resp = self._http_delete(
                url,
                headers={"Authorization": f"Bearer {bearer}"},
                timeout=_INTAKE_TIMEOUT,
            )
        except Exception as e:
            raise ForwardDeliveryError(
                f"could not withdraw the '{provider}' forward from the sidecar at "
                f"{url} ({e.__class__.__name__}: {e})."
            ) from e
        # 404 is fine — nothing to withdraw is the desired end-state (idempotent).
        if resp.status_code >= 400 and resp.status_code != 404:
            raise ForwardDeliveryError(
                f"the sidecar rejected withdrawing '{provider}' at {url}: HTTP "
                f"{resp.status_code} {self._resp_text(resp)}"
            )

    @staticmethod
    def _resp_text(resp) -> str:
        try:
            return resp.text[:200]
        except Exception:  # noqa: BLE001 - diagnostics only
            return "<no body>"


__all__ = [
    "ConnectionForwarder",
    "ForwardError",
    "NotGrantedError",
    "ForwardDeliveryError",
]
