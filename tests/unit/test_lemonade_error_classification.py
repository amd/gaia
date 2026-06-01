# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Regression tests for the Lemonade error-classification split done in #1030.

Before #1030, ``_classify_lemonade_response`` lumped two distinct
failure modes into a single ``LemonadeNetworkError``:

* The Lemonade Server itself is unreachable (connection refused / DNS).
* Lemonade *is* reachable, but its libcurl call to its child
  llama-server timed out ("CURL error: Timeout was reached").

The user-facing remediation is very different — the second case means
the model is hung or warming up, not that the server is down — so we
now emit ``LemonadeUpstreamTimeoutError`` for the timeout flavour and
keep ``LemonadeNetworkError`` strictly for connectivity failures.

Both the provider-side classifier (response payload) and the UI-side
classifier (string match on raised exceptions) need to bucket the
timeout case correctly.
"""

from __future__ import annotations

from gaia.llm.providers.lemonade import (
    LemonadeNetworkError,
    LemonadeUpstreamTimeoutError,
    _classify_lemonade_response,
)
from gaia.ui._chat_helpers import _classify_chat_exception

# ── Provider side: response-payload classification ──────────────────────


def test_curl_timeout_payload_routes_to_upstream_timeout() -> None:
    """Lemonade's "Timeout was reached" envelope → upstream-timeout, not network.

    This is the exact payload reported in #1030 when Gemma-4-E4B hung
    on its first PLANNING call against a freshly indexed PDF.
    """
    payload = {
        "error": {
            "type": "network_error",
            "message": "Network error: CURL error: Timeout was reached",
        }
    }
    err, is_err = _classify_lemonade_response(payload)
    assert is_err is True
    assert isinstance(err, LemonadeUpstreamTimeoutError)
    assert err.retryable is False, (
        "upstream timeout is intentionally non-retryable so the chat "
        "layer doesn't blind-retry against a hung backend"
    )


def test_operation_timeout_type_routes_to_upstream_timeout() -> None:
    """``type=operation_timeout`` (whether or not the message says so) is also a timeout."""
    payload = {
        "error": {
            "type": "operation_timeout",
            "message": "request exceeded internal deadline",
        }
    }
    err, _ = _classify_lemonade_response(payload)
    assert isinstance(err, LemonadeUpstreamTimeoutError)


def test_connection_refused_payload_routes_to_network_error() -> None:
    """True connectivity failure stays in the network bucket."""
    payload = {
        "error": {
            "type": "network_error",
            "message": "Network error: CURL error: Connection refused",
        }
    }
    err, _ = _classify_lemonade_response(payload)
    assert isinstance(err, LemonadeNetworkError)
    assert not isinstance(err, LemonadeUpstreamTimeoutError)


def test_could_not_resolve_host_routes_to_network_error() -> None:
    """DNS-style failures stay in the network bucket."""
    payload = {
        "error": {
            "type": "network_error",
            "message": "CURL error: Could not resolve host: localhost",
        }
    }
    err, _ = _classify_lemonade_response(payload)
    assert isinstance(err, LemonadeNetworkError)
    assert not isinstance(err, LemonadeUpstreamTimeoutError)


def test_upstream_timeout_user_message_is_actionable() -> None:
    """The user-facing message must give the user something concrete to do.

    "Couldn't reach the local LLM" was misleading in #1030 — the server
    was reachable; the model call timed out. The new message names
    concrete remediation steps that respect the project's "stick with
    Gemma, no smaller-model fallbacks" rule.
    """
    err = LemonadeUpstreamTimeoutError()
    msg = err.user_message
    assert "didn't respond in time" in msg or "did not respond" in msg.lower()
    # No silent fallback to a smaller model — must not appear in the remediation.
    assert "qwen3-0.6b" not in msg.lower()
    assert "smaller model" not in msg.lower()
    # Concrete next steps the user can actually run.
    assert "gaia kill" in msg or "lemonade-server serve" in msg


# ── UI side: exception-string classification ────────────────────────────


def test_ui_classifier_routes_curl_timeout_string_to_upstream_timeout() -> None:
    """When AgentSDK re-raises with str(original) we classify by substring.

    Same split must hold here: "Timeout was reached" inside the error
    text must surface as the upstream-timeout type.
    """
    exc = RuntimeError(
        "Couldn't reach the local LLM. "
        "Network error: CURL error: Timeout was reached"
    )
    classified = _classify_chat_exception(exc)
    assert isinstance(classified, LemonadeUpstreamTimeoutError)


def test_ui_classifier_routes_connection_refused_to_network_error() -> None:
    exc = RuntimeError("network_error: Connection refused on http://localhost:13305")
    classified = _classify_chat_exception(exc)
    assert isinstance(classified, LemonadeNetworkError)
    assert not isinstance(classified, LemonadeUpstreamTimeoutError)


def test_ui_classifier_returns_none_for_unrelated_error() -> None:
    """Non-Lemonade exceptions stay None (no false positives)."""
    exc = ValueError("some unrelated programming error")
    assert _classify_chat_exception(exc) is None


# ── Agent loop: typed-message surfacing (no generic "try again" wrapper) ─


def test_agent_extract_lemonade_user_message_typed_direct() -> None:
    """The agent loop must surface the typed message when the exception IS typed.

    Pre-#1030, the generic 'Sorry, I ran into an unexpected problem.
    This might be a temporary issue — try again in a moment.' wrapper
    clobbered the actionable remediation for non-retryable errors like
    :class:`LemonadeUpstreamTimeoutError`. Now ``_extract_lemonade_user_message``
    pulls the typed ``user_message`` so the user sees the real guidance.
    """
    from unittest.mock import MagicMock

    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._extract_lemonade_user_message = Agent._extract_lemonade_user_message.__get__(
        agent
    )

    exc = LemonadeUpstreamTimeoutError()
    msg = agent._extract_lemonade_user_message(exc)
    assert msg is not None
    assert "didn't respond in time" in msg
    assert "gaia kill" in msg or "lemonade-server serve" in msg


def test_agent_extract_lemonade_user_message_typed_in_cause_chain() -> None:
    """Walks the cause chain — AgentSDK often re-raises with a wrapper.

    The typed Lemonade error usually sits on ``__cause__`` after AgentSDK
    converts it to a friendlier ``RuntimeError``. The walker must find it.
    """
    from unittest.mock import MagicMock

    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._extract_lemonade_user_message = Agent._extract_lemonade_user_message.__get__(
        agent
    )

    inner = LemonadeUpstreamTimeoutError()
    try:
        raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        msg = agent._extract_lemonade_user_message(outer)
    assert msg is not None
    assert "didn't respond in time" in msg


def test_agent_extract_lemonade_user_message_string_fallback() -> None:
    """When the typed class is lost (just a string), fall back to substring match.

    AgentSDK sometimes re-raises a plain ``Exception`` with the original
    user_message as text. The helper must still recognise the timeout
    pattern via :func:`_classify_chat_exception`.
    """
    from unittest.mock import MagicMock

    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._extract_lemonade_user_message = Agent._extract_lemonade_user_message.__get__(
        agent
    )

    exc = RuntimeError(
        "Network error: CURL error: Timeout was reached on http://localhost:13305"
    )
    msg = agent._extract_lemonade_user_message(exc)
    assert msg is not None
    assert "didn't respond in time" in msg


def test_agent_extract_lemonade_user_message_returns_none_for_unrelated() -> None:
    """No false positives on unrelated exceptions — caller falls through to generic."""
    from unittest.mock import MagicMock

    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._extract_lemonade_user_message = Agent._extract_lemonade_user_message.__get__(
        agent
    )

    exc = ValueError("some unrelated programming error")
    msg = agent._extract_lemonade_user_message(exc)
    assert msg is None


def test_agent_extract_lemonade_user_message_handles_cause_cycle() -> None:
    """A pathological cause-chain cycle must not deadlock the walker.

    The helper builds a ``seen`` set keyed by ``id(exc)`` while walking
    ``__cause__`` / ``__context__``. Without it a cycle like
    ``a.__cause__ = b; b.__cause__ = a`` would loop forever. This test
    pins the protection so we never re-introduce the bug.
    """
    from unittest.mock import MagicMock

    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._extract_lemonade_user_message = Agent._extract_lemonade_user_message.__get__(
        agent
    )

    a = RuntimeError("a")
    b = RuntimeError("b")
    # Build the cycle via __cause__. Both attributes are settable.
    a.__cause__ = b
    b.__cause__ = a

    # Must terminate; neither exception is a LemonadeError so the direct
    # walk returns nothing and the string fallback also won't fire.
    msg = agent._extract_lemonade_user_message(a)
    assert msg is None


def test_agent_extract_lemonade_user_message_typed_in_cycle() -> None:
    """Cycle protection still finds a typed error before terminating.

    Even when a cause-chain cycle exists, if a typed Lemonade error sits
    on the cycle the walker must find and return its message rather than
    looping or returning None.
    """
    from unittest.mock import MagicMock

    from gaia.agents.base.agent import Agent

    agent = MagicMock(spec=Agent)
    agent._extract_lemonade_user_message = Agent._extract_lemonade_user_message.__get__(
        agent
    )

    typed = LemonadeUpstreamTimeoutError()
    wrapper = RuntimeError("wrapper")
    # Cause-chain cycle: wrapper -> typed -> wrapper.
    wrapper.__cause__ = typed
    typed.__cause__ = wrapper

    msg = agent._extract_lemonade_user_message(wrapper)
    assert msg is not None
    assert "didn't respond in time" in msg


# ── #1294: corrupt-download classification must not over-match ───────────
#
# ``LemonadeClient._is_corrupt_download_error`` used to treat the GENERIC
# string ``"llama-server failed to start"`` as proof of a corrupt/incomplete
# download. Lemonade raises that string for many non-corruption failures
# (resource limits, ctx_size issues, GPU/backend startup, port conflicts).
# Misclassifying routed an ordinary load failure into the delete +
# re-download path (the default model is ~25GB) and dead-ended first-boot.
#
# The fix: keep the five SPECIFIC corruption phrases unconditional, but only
# treat ``llama-server failed to start`` as corruption when one of those
# specific phrases is ALSO present (corroboration). A bare load failure must
# classify as NOT corrupt and surface as an actionable error.

from unittest.mock import patch

import pytest

from gaia.llm.lemonade_client import (
    LemonadeClient,
    LemonadeClientError,
    ModelDownloadCancelledError,
)

# The five legitimate corruption signals — each must keep returning True.
_SPECIFIC_CORRUPTION_PHRASES = [
    "download validation failed",
    "files are incomplete",
    "files are missing",
    "incomplete or missing",
    "corrupted download",
]


def _client() -> LemonadeClient:
    """Construct a client without touching the network.

    ``__init__`` only parses host/port and resolves the API key from env;
    it issues no HTTP, so this is safe in a unit test.
    """
    return LemonadeClient(host="localhost", port=13305)


@pytest.mark.parametrize("phrase", _SPECIFIC_CORRUPTION_PHRASES)
def test_specific_corruption_phrase_is_corrupt(phrase: str) -> None:
    """Each of the five specific corruption signals classifies as corrupt (no regression)."""
    client = _client()
    message = f"Failed to load model: {phrase} for Qwen3-Coder-30B-GGUF"
    assert client._is_corrupt_download_error(message) is True


@pytest.mark.parametrize("phrase", _SPECIFIC_CORRUPTION_PHRASES)
def test_specific_corruption_phrase_is_case_insensitive(phrase: str) -> None:
    """Classification is case-insensitive (Lemonade casing is not guaranteed)."""
    client = _client()
    assert client._is_corrupt_download_error(phrase.upper()) is True


def test_bare_llama_server_failed_is_not_corrupt() -> None:
    """A bare ``llama-server failed to start`` is NOT corruption (the #1294 bug)."""
    client = _client()
    message = (
        "Failed to load model Qwen3-Coder-30B-A3B-Instruct-GGUF: "
        "llama-server failed to start"
    )
    assert client._is_corrupt_download_error(message) is False


def test_real_world_model_load_error_payload_is_not_corrupt() -> None:
    """The exact structured payload from the bug report classifies as NOT corrupt.

    ``code``/``type`` is ``model_load_error`` — a generic load failure, not a
    corruption signal — so it must not enter the delete + re-download path.
    """
    client = _client()
    payload = {
        "error": {
            "code": "model_load_error",
            "type": "model_load_error",
            "message": (
                "Failed to load model Qwen3-Coder-30B-A3B-Instruct-GGUF: "
                "llama-server failed to start"
            ),
        }
    }
    assert client._is_corrupt_download_error(payload) is False


@pytest.mark.parametrize("phrase", _SPECIFIC_CORRUPTION_PHRASES)
def test_llama_server_failed_with_corruption_phrase_is_corrupt(phrase: str) -> None:
    """``llama-server failed to start`` PLUS a specific corruption phrase IS corrupt.

    When Lemonade corroborates the startup failure with a real corruption
    signal, the repair path is the correct response.
    """
    client = _client()
    message = f"llama-server failed to start: {phrase}"
    assert client._is_corrupt_download_error(message) is True


def test_unrelated_load_failure_is_not_corrupt() -> None:
    """An unrelated load failure (e.g. ctx/resource) is not corruption."""
    client = _client()
    message = "llama-server failed to start: out of memory allocating KV cache"
    assert client._is_corrupt_download_error(message) is False


class TestLoadModelCorruptRouting:
    """`load_model` must route bare load failures away from the repair path."""

    def test_bare_load_failure_does_not_redownload_and_raises_actionable(self) -> None:
        """Issue #1294 AC#3: a bare ``llama-server failed to start`` load failure
        raises an actionable ``LemonadeClientError`` WITHOUT deleting or
        re-downloading the model.
        """
        client = _client()

        send_error = LemonadeClientError(
            "Failed to load model Qwen3-Coder-30B-A3B-Instruct-GGUF: "
            "llama-server failed to start"
        )

        with (
            patch.object(client, "_send_request", side_effect=send_error),
            patch.object(client, "delete_model") as mock_delete,
            patch.object(client, "pull_model_stream") as mock_pull,
        ):
            with pytest.raises(LemonadeClientError) as exc_info:
                client.load_model(
                    "Qwen3-Coder-30B-A3B-Instruct-GGUF", auto_download=True
                )

        # The destructive repair path must NOT have been touched.
        mock_delete.assert_not_called()
        mock_pull.assert_not_called()

        # Error must name what failed so the user can act on it.
        assert "Qwen3-Coder-30B-A3B-Instruct-GGUF" in str(exc_info.value)
        assert "llama-server failed to start" in str(exc_info.value)

    def test_specific_corruption_enters_repair_path(self) -> None:
        """Issue #1294 AC#4: a SPECIFIC corruption error DOES enter the repair
        flow (resume via ``pull_model_stream``) and reloads on success.
        """
        client = _client()

        corrupt_error = LemonadeClientError(
            "Failed to load model Qwen3-Coder-30B-A3B-Instruct-GGUF: "
            "download validation failed - files are incomplete"
        )

        # First _send_request (initial load) raises corruption; the second
        # (post-resume reload) succeeds.
        send_results = [corrupt_error, {"status": "loaded"}]

        def fake_send(*_args, **_kwargs):
            result = send_results.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        # Resume succeeds in one streamed "complete" event.
        def fake_pull(*_args, **_kwargs):
            yield {"event": "complete"}

        with (
            patch.object(client, "_send_request", side_effect=fake_send),
            patch.object(client, "delete_model") as mock_delete,
            patch.object(
                client, "pull_model_stream", side_effect=fake_pull
            ) as mock_pull,
            patch(
                "gaia.llm.lemonade_client._prompt_user_for_repair", return_value=True
            ),
        ):
            response = client.load_model(
                "Qwen3-Coder-30B-A3B-Instruct-GGUF", auto_download=False
            )

        # Repair path ran: resume (pull) was attempted; reload succeeded.
        mock_pull.assert_called_once()
        assert response == {"status": "loaded"}
        # Resume succeeded, so we never escalated to delete.
        mock_delete.assert_not_called()

    def test_user_declining_repair_raises_cancelled(self) -> None:
        """When corruption is detected but the user declines repair, we raise
        ``ModelDownloadCancelledError`` — we must NOT silently fall through to
        a non-corrupt re-raise or proceed to delete/re-download.
        """
        client = _client()

        corrupt_error = LemonadeClientError(
            "Failed to load model Qwen3-Coder-30B-A3B-Instruct-GGUF: "
            "corrupted download detected"
        )

        with (
            patch.object(client, "_send_request", side_effect=corrupt_error),
            patch.object(client, "delete_model") as mock_delete,
            patch.object(client, "pull_model_stream") as mock_pull,
            patch(
                "gaia.llm.lemonade_client._prompt_user_for_repair", return_value=False
            ),
        ):
            with pytest.raises(ModelDownloadCancelledError):
                client.load_model(
                    "Qwen3-Coder-30B-A3B-Instruct-GGUF", auto_download=False
                )

        mock_delete.assert_not_called()
        mock_pull.assert_not_called()
