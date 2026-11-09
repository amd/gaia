# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Forward UI calls to a running daemon sidecar over HTTP.

:class:`SidecarProxy` is the agent-agnostic part of the frozen wire contract
every sidecar serves under ``/v1/<agent_id>/``: readiness ``init``, the
canonical streaming ``query`` loop with its cancel/respond verbs, and
``health``/``version``. Nothing here knows what the agent *does*.

A non-2xx is translated loudly into :class:`SidecarHTTPError`, which carries the
sidecar's own actionable ``detail`` (e.g. ``502 local LLM triage failed: …``)
instead of a generic ``HTTPError`` — no fallback, no swallowed error. The
sidecar's connector OAuth *write* routes are deliberately NOT proxied here: all
connector writes stay on the Python backend's single-writer path.
"""

from __future__ import annotations

import json as _json
import os
from typing import Any, Callable, Dict, Iterator, Optional

from gaia.logger import get_logger
from gaia.ui.sidecar.errors import SidecarError, SidecarHTTPError

logger = get_logger(__name__)

# /query connect timeout: fixed and short — a dead sidecar should fail fast on
# TCP connect, independent of the (much longer) read_timeout that spans the
# whole agent-loop run.
_QUERY_CONNECT_TIMEOUT = 10.0

# Cancel POST timeout: best-effort cleanup must never wait out the general
# sidecar timeout — a hung sidecar would make Cancel as slow as not cancelling.
_CANCEL_TIMEOUT = 10.0

# Internet switch POST timeout: it runs under the chat's switch lock, so a hung
# sidecar must not stall the toggle (and every later flip) for the general timeout.
_SWITCH_TIMEOUT = 10.0


def _extract_detail(resp) -> str:
    """Pull the sidecar's actionable message from a non-2xx response.

    FastAPI puts the actionable text under ``detail``; fall back to the raw body
    so nothing is ever swallowed.
    """
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001 - non-JSON body, use text below
        body = None
    if isinstance(body, dict) and body.get("detail"):
        detail = body["detail"]
        return detail if isinstance(detail, str) else str(detail)
    text = getattr(resp, "text", "") or ""
    return text.strip() or f"(no response body; HTTP {resp.status_code})"


def _timeout_env_var(agent_id: str) -> str:
    """The per-agent request-timeout override, e.g. ``GAIA_GAIA_SIDECAR_TIMEOUT``.

    Derived rather than listed so a new sidecar gets the same knob for free —
    and matches the token env vars the daemon spec already names this way
    (``GAIA_<AGENT>_SIDECAR_TOKEN``).
    """
    return f"GAIA_{agent_id.upper()}_SIDECAR_TIMEOUT"


class SidecarProxy:
    """HTTP transport onto one running sidecar's ``/v1/<agent_id>/`` contract.

    ``agent_id`` is required, not defaulted, so a transport never guesses
    which agent it is talking to.
    """

    def __init__(
        self,
        base_url: str,
        *,
        agent_id: str,
        session=None,
        timeout: float | None = None,
        auth_token: str | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.agent_id = agent_id
        self.prefix = f"/v1/{agent_id}"
        self.timeout = (
            timeout
            if timeout is not None
            else float(os.environ.get(_timeout_env_var(agent_id), "300"))
        )
        if session is None:
            import requests

            session = requests.Session()
        self._session = session
        # Per-session caller-auth token (#1706) replayed as a bearer header on
        # every request so the sidecar accepts UI-originated calls; None only in
        # tests / when talking to a sidecar started without auth.
        self._auth_token = auth_token
        if auth_token:
            self._session.headers.update({"Authorization": f"Bearer {auth_token}"})

    def _raise_for_status(self, resp, path: str) -> None:
        # Translate the boundary loudly: keep the sidecar's own actionable detail
        # (e.g. "502 local LLM triage failed: ...") instead of a generic HTTPError.
        if resp.status_code >= 400:
            raise SidecarHTTPError(
                resp.status_code,
                _extract_detail(resp),
                path=path,
                agent_id=self.agent_id,
            )

    def _post(self, path: str, payload: dict) -> dict:
        resp = self._session.post(
            f"{self.base_url}{path}", json=payload, timeout=self.timeout
        )
        self._raise_for_status(resp, path)
        return resp.json()

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> dict:
        resp = self._session.get(
            f"{self.base_url}{path}", params=params, timeout=self.timeout
        )
        self._raise_for_status(resp, path)
        return resp.json()

    # -- Health / version / readiness ----------------------------------------
    def health(self) -> dict:
        return self._get("/health")

    def version(self) -> dict:
        return self._get("/version")

    def init(self) -> tuple:
        """Readiness preflight — returns ``(status_code, body)``, never raises on 503.

        ``GET /v1/<agent>/init`` answers 200 (ready) or 503 (not ready) with the
        same ``InitResponse`` body either way — the 503 is contract, not a
        transport failure, so both must pass through verbatim instead of being
        flattened into a ``SidecarHTTPError`` detail string. Both shipped
        sidecars answer this shape deliberately.
        """
        path = f"{self.prefix}/init"
        resp = self._session.get(f"{self.base_url}{path}", timeout=self.timeout)
        if resp.status_code not in (200, 503):
            self._raise_for_status(resp, path)
        return resp.status_code, resp.json()

    # -- Query (canonical streaming agent-loop, #2109) -----------------------
    def respond_query(self, run_id: str, request_id: str, value: str) -> dict:
        """Deliver an answer to a pending ``needs_input`` question (#2595).

        Loud on failure: a 409 means the question is no longer the one the
        run is waiting on (already answered, timed out, or superseded) and
        must reach the caller as an error, never be swallowed.
        """
        return self._post(
            f"{self.prefix}/query/{run_id}/respond",
            {"request_id": request_id, "value": value},
        )

    def tool_decision(self, run_id: str, decision: str, confirm_id: str) -> dict:
        """Answer the ``needs_confirmation`` run *run_id* is parked on.

        *confirm_id* is required: without it the sidecar resolves whichever
        prompt is live, so a late click could approve one the user never saw.
        A 404/409 (run over, prompt expired or replaced) raises, never passes.
        """
        import requests

        if not confirm_id:
            raise SidecarError(
                f"{self.agent_id} sidecar: a tool decision needs the confirm_id "
                "of the prompt it answers; none was given."
            )
        path = f"{self.prefix}/query/{run_id}/tool_decision"
        try:
            return self._post(path, {"decision": decision, "confirm_id": confirm_id})
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to deliver the "
                f"decision: {exc}"
            ) from exc

    def followup(self, run_id: str, text: str) -> dict:
        """Hand run *run_id* a message the user sent mid-turn (contract >= 2.15).

        A 404/409 (run over, agent not taking input) raises: the caller must
        keep the message rather than show it as delivered.
        """
        import requests

        path = f"{self.prefix}/query/{run_id}/followup"
        try:
            return self._post(path, {"text": text})
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to add the "
                f"message to its reply: {exc}"
            ) from exc

    def list_grants(self, session_id: str) -> dict:
        """The sidecar session's "always allow" grants (contract >= 2.26).

        A 404 (no such session) raises; the caller decides what it means.
        """
        import requests

        try:
            return self._get(f"{self.prefix}/sessions/{session_id}/grants")
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to list its "
                f"permissions: {exc}"
            ) from exc

    def revoke_grant(self, session_id: str, key: Optional[str] = None) -> dict:
        """Revoke one "always allow" grant, or all with *key* None (>= 2.26)."""
        import requests

        path = f"{self.prefix}/sessions/{session_id}/grants"
        try:
            resp = self._session.delete(
                f"{self.base_url}{path}",
                params={"key": key} if key is not None else None,
                timeout=self.timeout,
            )
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to revoke a "
                f"permission: {exc}"
            ) from exc
        self._raise_for_status(resp, path)
        return resp.json()

    def skills(self) -> dict:
        """The skills the agent can load (flagship, contract >= 2.17)."""
        import requests

        try:
            return self._get(f"{self.prefix}/skills")
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to list its "
                f"skills: {exc}"
            ) from exc

    def session_tools(self, session_id: str) -> dict:
        """The tools the sidecar session's agent has (flagship, contract >= 2.27).

        A 404 (no such session) raises; the caller decides what it means.
        """
        import requests

        try:
            return self._get(f"{self.prefix}/sessions/{session_id}/tools")
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to list its "
                f"tools: {exc}"
            ) from exc

    def set_bypass(self, session_id: str, enabled: bool) -> dict:
        """Turn the sidecar session's unattended tool approval on or off.

        Raises on any failure, including the 404 for a session the sidecar has
        not created yet — the caller decides what that means.
        """
        import requests

        path = f"{self.prefix}/sessions/{session_id}/bypass"
        try:
            return self._post(path, {"enabled": bool(enabled)})
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to set full "
                f"access: {exc}"
            ) from exc

    def set_internet(self, session_id: str, internet_off: bool) -> dict:
        """Turn the sidecar session's internet on or off (contract >= 2.23).

        Raises on any failure, including the 404 for a session the sidecar has
        not created yet.
        """
        import requests

        path = f"{self.prefix}/sessions/{session_id}/internet"
        try:
            resp = self._session.post(
                f"{self.base_url}{path}",
                json={"internet_off": bool(internet_off)},
                timeout=_SWITCH_TIMEOUT,
            )
            self._raise_for_status(resp, path)
            return resp.json()
        except requests.exceptions.RequestException as exc:
            raise SidecarError(
                f"could not reach the {self.agent_id} sidecar to switch the "
                f"internet: {exc}"
            ) from exc

    def query_stream(
        self,
        body: dict,
        *,
        read_timeout: float = 300.0,
        on_response: Optional[Callable[[Any], None]] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Stream ``POST /v1/<agent>/query`` as parsed canonical SSE event dicts.

        One ``data: <json>\\n\\n`` frame per event (no multi-line data), so
        ``resp.iter_lines()`` is safe: blank keep-alive/separator lines are
        skipped, non-``data:`` lines are ignored, and each ``data:`` payload is
        JSON-parsed and yielded. A non-2xx status raises the same loud
        :class:`SidecarHTTPError` boundary as every other proxy method, BEFORE
        any event is yielded — the response is closed either way.

        ``on_response`` — invoked with the live (still-open) response object
        EAGERLY at call time, right after the POST returns and before any
        line is consumed, so a caller holding the returned iterator on a
        worker thread can hand the response to another thread (the cancel
        path), which forces a blocked read to error out by calling
        ``resp.close()`` on it. This method is deliberately NOT a generator
        function: a lazy body would defer both the POST and the registration
        to the first ``next()``, widening the early-cancel race in which a
        cancel finds nothing registered to close.

        A malformed ``data:`` line (the sidecar emitting non-JSON) raises
        loudly — never silently dropped or swallowed into a placeholder event.
        """
        path = f"{self.prefix}/query"
        resp = self._session.post(
            f"{self.base_url}{path}",
            json=body,
            stream=True,
            timeout=(_QUERY_CONNECT_TIMEOUT, read_timeout),
        )
        if resp.status_code >= 400:
            try:
                self._raise_for_status(resp, path)
            finally:
                resp.close()
        if on_response is not None:
            on_response(resp)

        agent_id = self.agent_id

        def _events() -> Iterator[Dict[str, Any]]:
            try:
                for raw_line in resp.iter_lines():
                    if not raw_line:
                        continue
                    line = (
                        raw_line.decode("utf-8", "replace")
                        if isinstance(raw_line, bytes)
                        else raw_line
                    )
                    if not line.startswith("data:"):
                        continue
                    payload = line[len("data:") :].strip()
                    if not payload:
                        continue
                    try:
                        event = _json.loads(payload)
                    except (ValueError, TypeError) as e:
                        raise SidecarError(
                            f"{agent_id} sidecar /query sent a malformed SSE "
                            f"event: {payload!r} ({e})"
                        ) from e
                    yield event
            finally:
                resp.close()

        return _events()

    def cancel_query(self, run_id: str) -> None:
        """Cancel an in-flight ``/query`` run.

        Best-effort by design: the caller's forced response close has already
        unblocked the relay's reader, so this POST uses a short dedicated
        timeout (never the general 300s sidecar timeout) and swallows
        transport failures with a log — a hung or dead sidecar can't honour
        a cancel anyway, and surfacing a transport error AFTER the user
        cancelled would be noise, not signal.

        A 404 means the run already finished by the time the cancel landed —
        benign, log and return rather than raise (mirrors the sidecar's own
        ``cancel_query`` route contract). Any other non-2xx keeps the loud
        :class:`SidecarHTTPError` boundary.
        """
        import requests

        path = f"{self.prefix}/query/{run_id}/cancel"
        try:
            resp = self._session.post(f"{self.base_url}{path}", timeout=_CANCEL_TIMEOUT)
        except requests.exceptions.RequestException as exc:
            logger.warning(
                "%s sidecar: best-effort cancel for run_id=%s failed at "
                "transport level (reader already unblocked by forced close): %s",
                self.agent_id,
                run_id,
                exc,
            )
            return
        if resp.status_code == 404:
            logger.info(
                "%s sidecar: cancel for run_id=%s: no longer in flight (404)",
                self.agent_id,
                run_id,
            )
            return
        self._raise_for_status(resp, path)


__all__ = ["SidecarProxy"]
