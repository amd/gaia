# Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Keep Claude on the AMD LLM Gateway when this machine is on AMD's network.

On AMD's network no GAIA request may reach Anthropic directly
(``api.anthropic.com``); Claude goes through the gateway's Anthropic route
instead. Off it, nothing changes.

**Detection.** The machine is on AMD's network when either holds:

1. The AMD LLM Gateway is set up here: a non-blank ``LEMONADE_AMD_API_KEY`` or
   ``GAIA_GATEWAY_TOKEN`` in GAIA's environment, a gateway key in the OS
   credential store, or ``~/.gaia/gateway.json`` naming AMD's gateway (or
   unreadable). This is decisive — no probe runs — so a set-up gateway can
   never read as "off the network", however DNS behaves.
2. ``llm-api.amd.com`` accepts a TCP connection on 443 within
   :data:`PROBE_TIMEOUT` seconds. A DNS answer alone doesn't count: public DNS
   resolves the name to an address only reachable from inside AMD.

Anything else reads as off the network. A positive answer is kept for the
process; a negative one is re-checked after :data:`NEGATIVE_TTL` seconds, so a
long-running server notices a VPN that connected after it started.

**The guard.** Every Anthropic SDK client GAIA builds comes from
:func:`anthropic_client`, which refuses one aimed anywhere but the gateway
while on AMD's network. Claude Code CLI launches go through
:func:`assert_claude_cli_allowed`. Neither reroutes: the error names the
gateway model to switch to. The eval judge is the one deliberate exception —
``gaia.eval`` builds its judge on the gateway route itself and says so.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from gaia.logger import get_logger

log = get_logger(__name__)

GATEWAY_HOST = "llm-api.amd.com"
ANTHROPIC_DIRECT_BASE_URL = "https://api.anthropic.com"
PROBE_TIMEOUT = 2.0
NEGATIVE_TTL = 60.0
GATEWAY_DOCS = "https://amd-gaia.ai/docs/guides/llm-gateway"

#: Direct Anthropic id -> the same model on the AMD LLM Gateway. Only models the
#: gateway is known to serve; anything else has no equivalent.
GATEWAY_EQUIVALENTS = {
    "claude-opus-5-5": "amd.Claude-Opus-5-5",
    "claude-sonnet-5-5": "amd.Claude-Sonnet-5-5",
    "claude-opus-5": "amd.Claude-Opus-5",
    "claude-sonnet-5": "amd.Claude-Sonnet-5",
}
#: Suggested when a direct model has no gateway equivalent.
DEFAULT_GATEWAY_CLAUDE = "amd.Claude-Opus-5-5"


class DirectAnthropicBlockedError(RuntimeError):
    """A request would reach Anthropic directly while on AMD's network."""


@dataclass(frozen=True)
class NetworkVerdict:
    """Whether this machine is on AMD's network, and the evidence, in plain words."""

    on_amd_network: bool
    reason: str


_lock = threading.Lock()
_verdict: Optional[NetworkVerdict] = None
_checked_at = 0.0


def _gateway_configured() -> Optional[str]:
    """Where the AMD LLM Gateway is set up on this machine, or None."""
    from gaia.llm import gateway

    for name in (gateway.GATEWAY_ENV_VAR, "GAIA_GATEWAY_TOKEN"):
        if os.environ.get(name, "").strip():
            return f"{name} is set"
    state_file = gateway.GATEWAY_STATE_FILE
    if state_file.exists():
        try:
            if gateway.is_amd_gateway(gateway.GatewayState.load(state_file).base_url):
                return f"{state_file} names the AMD LLM Gateway"
        except gateway.GatewayError as e:
            # Unreadable settings still mean someone set a gateway up here.
            log.warning("Treating this machine as on AMD's network: %s", e)
            return f"{state_file} exists"
    try:
        token = gateway.read_stored_token()
    except gateway.GatewayError as e:
        log.warning("Could not check for a stored gateway key; probing instead: %s", e)
        return None
    if token and token.strip():
        return "a gateway key is stored in the OS credential store"
    return None


def _gateway_answers(timeout: float = PROBE_TIMEOUT) -> bool:
    """True when the gateway host accepts a TCP connection within *timeout*.

    Runs in a thread because name resolution ignores socket timeouts.
    """
    result = {"ok": False}

    def connect() -> None:
        try:
            socket.create_connection((GATEWAY_HOST, 443), timeout=timeout).close()
            result["ok"] = True
        except OSError as e:
            log.debug("%s did not answer: %s", GATEWAY_HOST, e)

    worker = threading.Thread(target=connect, daemon=True, name="amd-network-probe")
    worker.start()
    worker.join(timeout + 0.5)
    return result["ok"]


def _detect() -> NetworkVerdict:
    configured = _gateway_configured()
    if configured:
        return NetworkVerdict(True, f"the AMD LLM Gateway is set up here: {configured}")
    if _gateway_answers():
        return NetworkVerdict(True, f"{GATEWAY_HOST} answered")
    return NetworkVerdict(
        False,
        f"no AMD LLM Gateway is set up and {GATEWAY_HOST} did not answer "
        f"within {PROBE_TIMEOUT:g}s",
    )


def current() -> NetworkVerdict:
    """This machine's verdict; see the module docstring for the rule."""
    global _verdict, _checked_at  # pylint: disable=global-statement
    with _lock:
        fresh = _verdict is not None and (
            _verdict.on_amd_network or time.monotonic() - _checked_at < NEGATIVE_TTL
        )
        if not fresh:
            _verdict = _detect()
            _checked_at = time.monotonic()
            log.debug("AMD network: %s (%s)", _verdict.on_amd_network, _verdict.reason)
        return _verdict


def on_amd_network() -> bool:
    return current().on_amd_network


def forget() -> None:
    """Drop the cached verdict so the next check detects again."""
    global _verdict, _checked_at  # pylint: disable=global-statement
    with _lock:
        _verdict, _checked_at = None, 0.0


def gateway_equivalent(model: Optional[str]) -> Optional[str]:
    """The AMD LLM Gateway id for a direct Claude *model*, or None when it has none."""
    return GATEWAY_EQUIVALENTS.get((model or "").strip().lower())


def _suggestion(model: Optional[str]) -> str:
    return gateway_equivalent(model) or DEFAULT_GATEWAY_CLAUDE


def gateway_only_reason(model: Optional[str]) -> str:
    """Why direct *model* can't be used on AMD's network, for a model list."""
    return (
        f"On the AMD network Claude runs through the AMD LLM Gateway — use "
        f"{_suggestion(model)}"
    )


def blocked_reason(model: Optional[str]) -> Optional[str]:
    """:func:`gateway_only_reason` on AMD's network, else None."""
    return gateway_only_reason(model) if on_amd_network() else None


def _is_gateway_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and (parsed.hostname or "") == GATEWAY_HOST


def _effective_base_url(base_url: Optional[str]) -> str:
    """Where the Anthropic SDK sends requests: its argument, else its env var, else Anthropic."""
    return (
        base_url
        or os.environ.get("ANTHROPIC_BASE_URL", "").strip()
        or ANTHROPIC_DIRECT_BASE_URL
    )


def _blocked(model: Optional[str], destination: str, what: str) -> str:
    verdict = current()
    suggestion = _suggestion(model)
    named = f" {model}" if model else ""
    return (
        f"{what} would send Claude{named} to {destination} instead of the AMD "
        f"LLM Gateway, and GAIA doesn't send Claude traffic to Anthropic "
        f"directly on the AMD network ({verdict.reason}). Use the gateway's "
        f"{suggestion} instead: `gaia gateway use {suggestion}`. See {GATEWAY_DOCS}."
    )


def assert_direct_anthropic_allowed(
    model: Optional[str], base_url: Optional[str] = None, *, what: str
) -> None:
    """Raise when an Anthropic client aimed at *base_url* would leave AMD's network.

    *base_url* None means what the SDK would use: ``ANTHROPIC_BASE_URL``, else
    ``api.anthropic.com``. Only an ``https`` URL on the gateway's host passes on
    AMD's network.

    Raises:
        DirectAnthropicBlockedError: on AMD's network, aimed anywhere else.
    """
    target = _effective_base_url(base_url)
    if _is_gateway_url(target) or not on_amd_network():
        return
    raise DirectAnthropicBlockedError(
        _blocked(model, urlparse(target).hostname or target, what)
    )


def anthropic_client(
    sdk: Any,
    *,
    model: Optional[str],
    what: str,
    asynchronous: bool = False,
    **kwargs: Any,
) -> Any:
    """``sdk.Anthropic(**kwargs)`` (or ``AsyncAnthropic``), refused off the gateway on AMD's network.

    The only place GAIA constructs an Anthropic SDK client.
    """
    from gaia.llm.local_only import assert_provider_permitted

    target = _effective_base_url(kwargs.get("base_url"))
    assert_provider_permitted(
        "the AMD LLM Gateway" if _is_gateway_url(target) else "Anthropic Claude"
    )
    assert_direct_anthropic_allowed(model, kwargs.get("base_url"), what=what)
    cls = sdk.AsyncAnthropic if asynchronous else sdk.Anthropic
    return cls(**kwargs)


def assert_claude_cli_allowed(
    model: Optional[str], env: Optional[Mapping[str, str]] = None, *, what: str
) -> None:
    """Raise when a ``claude`` CLI run with *env* would reach Anthropic on AMD's network.

    The CLI follows ``ANTHROPIC_BASE_URL`` from its environment (*env*, else
    this process's).
    """
    source = os.environ if env is None else env
    target = (source.get("ANTHROPIC_BASE_URL") or "").strip()
    if (target and _is_gateway_url(target)) or not on_amd_network():
        return
    destination = urlparse(target).hostname if target else "api.anthropic.com"
    raise DirectAnthropicBlockedError(_blocked(model, destination or target, what))
