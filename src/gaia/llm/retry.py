# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Retry transient failures of a model endpoint, and only those.

Hosted OpenAI-compatible endpoints, the gateways and proxies in front of them,
and a local server under load all drop the occasional request: a reset
connection, a 502/503/504 from a proxy, a 429, a 500 that is really a failed
upstream hop. Those recover on the next attempt a few seconds later. Other
failures never recover and must not be repeated: a 400 (bad request, content
filter), 401/403, 404, 413, 422, a context-window overflow, or a 500 that is the
server's verdict on the shape of the request.

This module holds the three pieces every chat-completion path shares:

* :func:`classify_http_failure` / :func:`classify_exception` — is this failure
  worth another attempt? Returns a short, body-free reason or ``None``.
* :func:`backoff_delay` — exponential backoff with full jitter, honouring
  ``Retry-After``.
* :class:`ModelCallRetrier` — runs a call (or a stream, before its first
  token) under a :class:`RetryPolicy`, with injectable sleep/clock/random so
  tests never sleep.

When retries run out, the original exception is re-raised unchanged with a
:class:`RetryState` attached (see :func:`retry_state_of`), so a caller further
up — the agent loop — can tell "the endpoint is flaky and we already tried"
from every other failure without re-parsing error text.
"""

from __future__ import annotations

import email.utils
import json
import logging
import os
import random
import ssl
import time
from dataclasses import dataclass
from typing import (
    Any,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    TypeVar,
)

import requests

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ── Configuration ────────────────────────────────────────────────────────

#: Attempts per model request, the first one included. ``1`` disables retry.
RETRY_ATTEMPTS_ENV = "GAIA_LLM_RETRY_ATTEMPTS"
#: Backoff base in seconds: retry *n* (0-based) waits up to ``base * 2**n``.
RETRY_BASE_DELAY_ENV = "GAIA_LLM_RETRY_BASE_DELAY"
#: Ceiling on any single wait, ``Retry-After`` included.
RETRY_MAX_DELAY_ENV = "GAIA_LLM_RETRY_MAX_DELAY"
#: Wall-clock budget for one request across all its attempts and waits.
RETRY_DEADLINE_ENV = "GAIA_LLM_RETRY_DEADLINE"

DEFAULT_RETRY_ATTEMPTS = 5
DEFAULT_RETRY_BASE_DELAY = 2.0
DEFAULT_RETRY_MAX_DELAY = 30.0
DEFAULT_RETRY_DEADLINE = 120.0


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try one model request before giving up.

    The defaults allow four retries whose jittered waits average about 15 s in
    total and never exceed 30 s each: enough to ride out a dropped connection
    or a proxy restart, short enough that a real outage surfaces in about two
    minutes instead of hanging a turn. A wait is never started if it would end
    past the deadline, and a new attempt is never started after it.
    """

    max_attempts: int = DEFAULT_RETRY_ATTEMPTS
    base_delay: float = DEFAULT_RETRY_BASE_DELAY
    max_delay: float = DEFAULT_RETRY_MAX_DELAY
    deadline: float = DEFAULT_RETRY_DEADLINE

    @classmethod
    def from_env(cls) -> "RetryPolicy":
        """The defaults, overridden by any ``GAIA_LLM_RETRY_*`` variable.

        Read at call time so a variable set after import still applies. A
        present-but-invalid value raises rather than silently falling back —
        a retry budget other than the one you set is worse than being told.
        """
        return cls(
            max_attempts=int(
                _env_number(RETRY_ATTEMPTS_ENV, DEFAULT_RETRY_ATTEMPTS, int, minimum=1)
            ),
            base_delay=_env_number(
                RETRY_BASE_DELAY_ENV, DEFAULT_RETRY_BASE_DELAY, float, minimum=0
            ),
            max_delay=_env_number(
                RETRY_MAX_DELAY_ENV, DEFAULT_RETRY_MAX_DELAY, float, minimum=0
            ),
            deadline=_env_number(
                RETRY_DEADLINE_ENV, DEFAULT_RETRY_DEADLINE, float, minimum=0
            ),
        )


def _env_number(name: str, default: float, kind: type, *, minimum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = kind(raw)
    except ValueError as e:
        raise ValueError(
            f"{name} must be a {'whole ' if kind is int else ''}number, got "
            f"{raw!r}. Unset it to use the default ({default})."
        ) from e
    if value < minimum:
        raise ValueError(
            f"{name} must be at least {minimum}, got {value}. Unset it to use "
            f"the default ({default})."
        )
    return float(value)


# ── Classification ───────────────────────────────────────────────────────

#: Statuses that mean "not now" rather than "not this request".
RETRYABLE_STATUSES = frozenset({408, 429, 502, 503, 504})

#: Body wording of a failed upstream hop: nothing was computed, so the request
#: is safe to send again. Matched case-insensitively. These win over
#: :data:`_NOT_RETRYABLE_PHRASES` because a proxy that lost its upstream often
#: wraps the upstream's own words in the same body.
_TRANSIENT_PHRASES = (
    # .NET HttpClient wording, which many hosted gateways and proxies relay.
    "an error occurred while sending the request",
    "error while copying content to a stream",
    # Envoy / nginx / generic reverse-proxy wording.
    "upstream connect error",
    "upstream request timeout",
    "connection reset by peer",
    "connection termination",
    "server disconnected",
    "temporarily unavailable",
    "currently overloaded",
    "server is overloaded",
)

#: Body wording that makes a failure permanent whatever its status. A 500 is
#: retried by default, so these are what keep a deterministic rejection from
#: burning the whole retry budget.
_NOT_RETRYABLE_PHRASES = (
    # The request's shape or route is wrong for this model/deployment.
    "invalid_request_error",
    "invalid request",
    "is not supported",
    "not supported for",
    "unsupported",
    "model_not_found",
    "model not found",
    "does not exist",
    # Content filters answer with a verdict on the prompt, not a fault.
    "content_filter",
    "content management policy",
    "content_policy",
    "content_safety",
    "responsibleaipolicyviolation",
    # A local server whose model call hung past its own long timeout. Sending
    # the same request again hangs the same way; that failure gets its own
    # remediation message instead (LemonadeUpstreamTimeoutError).
    "timeout was reached",
)


def classify_http_failure(status: Optional[int], body: str = "") -> Optional[str]:
    """Is a failed HTTP response worth another attempt?

    Returns a short reason (``"HTTP 503"``) when it is, ``None`` when it is
    not. The reason is built only from the status, never from *body*, so it is
    safe to log.

    * A context-window overflow or content-filter rejection is never retried,
      whatever status it wears.
    * 408, 429, 502, 503 and 504 are retried.
    * 500 is retried unless its body says the request itself is at fault —
      some servers answer a request they will never accept with a 500.
    * Every other status — 400, 401, 403, 404, 413, 422, 501, … — is not.
    * With no status at all (an error event inside a stream), only a body
      that names a failed upstream hop is retried.
    """
    from gaia.llm.lemonade_client import is_context_overflow_error

    text = (body or "").lower()
    if text and is_context_overflow_error(body):
        return None
    transient = any(phrase in text for phrase in _TRANSIENT_PHRASES)
    permanent = any(phrase in text for phrase in _NOT_RETRYABLE_PHRASES)
    if status is None:
        return "upstream failure" if transient and not permanent else None
    if status in RETRYABLE_STATUSES:
        # A content filter or a request-shape verdict behind a proxy status is
        # still a verdict.
        return None if permanent and not transient else f"HTTP {status}"
    if status == 500:
        if transient or not permanent:
            return "HTTP 500"
        return None
    return None


def _httpx_transport_errors() -> tuple:
    """httpx transport exceptions, for SDKs that let them escape mid-stream."""
    found = []
    for name in ("httpx", "httpx2"):
        try:
            module = __import__(name)
        except ImportError:
            continue
        error = getattr(module, "TransportError", None)
        if isinstance(error, type):
            found.append(error)
    return tuple(found)


_HTTPX_TRANSPORT_ERRORS = _httpx_transport_errors()

#: Attribute a raise site sets to pre-classify an exception whose response body
#: it is about to discard (see :func:`tag_http_failure`).
_REASON_ATTR = "retry_reason"
_RETRY_AFTER_ATTR = "retry_after"
#: Attribute carrying the :class:`RetryState` of a request that gave up.
_STATE_ATTR = "model_retry_state"


def tag_http_failure(
    error: BaseException,
    status: Optional[int],
    body: str = "",
    headers: Optional[Mapping[str, str]] = None,
) -> BaseException:
    """Record *error*'s retry verdict while its response is still at hand.

    Some raise sites deliberately drop the response body from the exception
    they raise (a cloud provider's body is never reflected to the user). The
    verdict is computed from it first and stored on the exception, so
    :func:`classify_exception` can still tell a permanent 500 from a transient
    one. Returns *error* for ``raise tag_http_failure(...)``.
    """
    if not isinstance(status, int) or isinstance(status, bool):
        status = None
    if not isinstance(body, str):
        body = ""
    setattr(error, _REASON_ATTR, classify_http_failure(status, body))
    setattr(error, _RETRY_AFTER_ATTR, parse_retry_after(headers))
    return error


def _error_body_text(error: BaseException) -> str:
    body = getattr(error, "body", None)
    parts = [str(getattr(error, "message", "") or "")]
    if isinstance(body, (dict, list)):
        try:
            parts.append(json.dumps(body))
        except (TypeError, ValueError):
            parts.append(str(body))
    elif body is not None:
        parts.append(str(body))
    return " ".join(p for p in parts if p)


def _caused_by_tls(error: BaseException) -> bool:
    cur: Optional[BaseException] = error
    seen: set = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, (ssl.SSLError, requests.exceptions.SSLError)):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def classify_exception(error: BaseException) -> Optional[str]:
    """Is the exception a model call raised worth another attempt?

    Returns a short, log-safe reason, or ``None`` when retrying cannot help.
    Covers the shapes the chat paths actually raise: ``requests`` errors from
    the plain HTTP path, OpenAI SDK errors from the streaming path, httpx
    transport errors that escape a stream mid-read, and exceptions a raise site
    pre-classified with :func:`tag_http_failure`.
    """
    if hasattr(error, _REASON_ATTR):
        tagged = getattr(error, _REASON_ATTR)
        return tagged if isinstance(tagged, str) else None

    import openai

    if _caused_by_tls(error):
        # Certificate or protocol mismatch: the same handshake fails again.
        return None
    if isinstance(error, requests.exceptions.ConnectTimeout):
        return "connect timeout"
    if isinstance(error, requests.exceptions.ReadTimeout):
        return "read timeout"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "connection error"
    if isinstance(error, requests.exceptions.ChunkedEncodingError):
        return "connection dropped"
    if isinstance(error, openai.AuthenticationError):
        return None
    if isinstance(error, openai.APITimeoutError):
        return "read timeout"
    if isinstance(error, openai.APIConnectionError):
        return "connection error"
    if isinstance(error, openai.APIStatusError):
        return classify_http_failure(error.status_code, _error_body_text(error))
    if isinstance(error, openai.APIError):
        # An error event inside an SSE stream: no status, only a body.
        return classify_http_failure(None, _error_body_text(error))
    if _HTTPX_TRANSPORT_ERRORS and isinstance(error, _HTTPX_TRANSPORT_ERRORS):
        name = type(error).__name__
        if name == "UnsupportedProtocol":
            return None
        return "read timeout" if "Timeout" in name else "connection error"
    if isinstance(error, TimeoutError):
        return "read timeout"
    if isinstance(error, ConnectionError):
        return "connection error"
    return None


def parse_retry_after(headers: Optional[Mapping[str, str]]) -> Optional[float]:
    """Seconds a server asked us to wait, from ``Retry-After`` (seconds or an
    HTTP date) or ``retry-after-ms``. ``None`` when absent or unreadable."""
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None

    def header(name: str) -> Optional[str]:
        # Case-insensitive mappings (requests, httpx) answer either spelling.
        for key in (name, name.title()):
            value = getter(key)
            if isinstance(value, (str, bytes)) and value:
                return value.decode() if isinstance(value, bytes) else value
        return None

    raw_ms = header("retry-after-ms")
    if raw_ms:
        try:
            return max(0.0, float(raw_ms) / 1000.0)
        except ValueError:
            pass
    raw = header("retry-after")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    return max(0.0, when.timestamp() - time.time())


def _retry_after_of(error: BaseException) -> Optional[float]:
    if hasattr(error, _RETRY_AFTER_ATTR):
        tagged = getattr(error, _RETRY_AFTER_ATTR)
        return float(tagged) if isinstance(tagged, (int, float)) else None
    response = getattr(error, "response", None)
    return parse_retry_after(getattr(response, "headers", None))


def backoff_delay(
    retry_index: int,
    policy: RetryPolicy,
    rng: random.Random,
    retry_after: Optional[float] = None,
) -> float:
    """Seconds to wait before retry *retry_index* (0 for the first retry).

    Full jitter — ``uniform(0, min(max_delay, base * 2**n))`` — so clients that
    failed together do not retry together. A server's ``Retry-After`` replaces
    the jittered value, still capped at ``max_delay``.
    """
    if retry_after is not None:
        return min(max(0.0, retry_after), policy.max_delay)
    ceiling = min(policy.max_delay, policy.base_delay * (2**retry_index))
    return rng.uniform(0.0, ceiling)


# ── Running a call under a policy ────────────────────────────────────────


@dataclass
class RetryState:
    """What happened to one model request across its attempts."""

    attempts: int = 0
    #: Reason for the most recent retryable failure (log-safe, body-free).
    last_reason: Optional[str] = None
    #: Seconds spent waiting between attempts.
    waited: float = 0.0
    #: A stream had already handed output to its consumer when it failed, so
    #: it could not be retried without duplicating that output.
    output_delivered: bool = False
    #: The request failed transiently and this layer stopped trying.
    exhausted: bool = False

    @property
    def retries(self) -> int:
        return max(0, self.attempts - 1)


def retry_state_of(error: BaseException) -> Optional[RetryState]:
    """The :class:`RetryState` of a request that gave up on a transient
    failure, found anywhere in *error*'s cause chain; ``None`` otherwise.

    ``None`` means retrying is not known to help: the failure was permanent,
    or it did not come from a call made through :class:`ModelCallRetrier`.
    """
    cur: Optional[BaseException] = error
    seen: set = set()
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        state = getattr(cur, _STATE_ATTR, None)
        if isinstance(state, RetryState) and state.exhausted:
            return state
        cur = cur.__cause__ or cur.__context__
    return None


def attach_retry_state(error: BaseException, state: RetryState) -> BaseException:
    """Carry *state* on *error* when it records a transient give-up.

    For raise sites that translate the original exception into another one
    (often ``from None``, which cuts the cause chain). Returns *error*.
    """
    if state.exhausted:
        setattr(error, _STATE_ATTR, state)
    return error


class ModelCallRetrier:
    """Run a model call under a :class:`RetryPolicy`.

    ``sleep``, ``clock`` and ``rng`` are injectable so the timing can be tested
    without sleeping. Every retry is logged at WARNING with the attempt number,
    the wait, and a body-free reason — never a request body or header.
    """

    def __init__(
        self,
        policy: Optional[RetryPolicy] = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        rng: Optional[random.Random] = None,
        log: Optional[logging.Logger] = None,
        classify: Callable[[BaseException], Optional[str]] = classify_exception,
    ):
        self.policy = policy or RetryPolicy.from_env()
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        self._log = log or logger
        self._classify = classify

    def _wait_or_give_up(
        self,
        error: BaseException,
        state: RetryState,
        started: float,
        what: str,
    ) -> bool:
        """After a failed attempt: sleep and return True to try again, or
        return False to give up. Marks *state* exhausted when the failure was
        transient but the budget is spent."""
        reason = self._classify(error)
        if reason is None:
            return False
        state.last_reason = reason
        if state.attempts >= self.policy.max_attempts:
            state.exhausted = True
            self._log.warning(
                "%s failed (%s); giving up after %d attempts",
                what,
                reason,
                state.attempts,
            )
            return False
        delay = backoff_delay(
            state.attempts - 1, self.policy, self._rng, _retry_after_of(error)
        )
        if self._clock() - started + delay > self.policy.deadline:
            state.exhausted = True
            self._log.warning(
                "%s failed (%s); giving up after %d attempts, retry budget of "
                "%.0fs spent",
                what,
                reason,
                state.attempts,
                self.policy.deadline,
            )
            return False
        self._log.warning(
            "%s failed (%s); retrying in %.1fs (attempt %d of %d)",
            what,
            reason,
            delay,
            state.attempts + 1,
            self.policy.max_attempts,
        )
        if delay > 0:
            self._sleep(delay)
        state.waited += delay
        return True

    def call(
        self,
        fn: Callable[[], T],
        *,
        what: str = "Model request",
        state: Optional[RetryState] = None,
    ) -> T:
        """Return ``fn()``, retrying transient failures.

        On giving up, the last exception is re-raised unchanged; if it was
        transient, it carries the :class:`RetryState` (:func:`retry_state_of`).
        """
        state = state if state is not None else RetryState()
        started = self._clock()
        while True:
            state.attempts += 1
            try:
                return fn()
            except Exception as error:
                if not self._wait_or_give_up(error, state, started, what):
                    attach_retry_state(error, state)
                    raise

    def stream(
        self,
        open_stream: Callable[[], Iterable[T]],
        *,
        is_output: Callable[[T], bool],
        what: str = "Model stream",
        state: Optional[RetryState] = None,
    ) -> Iterator[T]:
        """Yield from ``open_stream()``, retrying transient failures that
        happen before any output reached the consumer.

        Items for which *is_output* is false (a role-only first frame, an empty
        keep-alive) are held back until the first real one, so a stream that
        dies right after its headers is still retried cleanly. Once output has
        been yielded a failure is never retried — that would duplicate it — and
        is re-raised with ``output_delivered`` set on its state.
        """
        state = state if state is not None else RetryState()
        started = self._clock()
        while True:
            state.attempts += 1
            held: list = []
            delivered = False
            iterator = None
            try:
                iterator = iter(open_stream())
                for item in iterator:
                    if delivered:
                        yield item
                    elif is_output(item):
                        delivered = True
                        state.output_delivered = True
                        yield from held
                        held.clear()
                        yield item
                    else:
                        held.append(item)
                # Nothing but non-output frames: hand them over as they are.
                yield from held
                return
            except Exception as error:
                if delivered:
                    reason = self._classify(error)
                    if reason is not None:
                        state.exhausted = True
                        state.last_reason = reason
                        attach_retry_state(error, state)
                    raise
                if not self._wait_or_give_up(error, state, started, what):
                    attach_retry_state(error, state)
                    raise
            finally:
                # Release the failed (or abandoned) attempt's connection now,
                # not whenever the garbage collector gets to it.
                close = getattr(iterator, "close", None)
                if close is not None:
                    close()


def describe_retry_state(state: Any) -> str:
    """One plain sentence about a give-up, for logs and user-facing text."""
    if not isinstance(state, RetryState):
        return ""
    reason = state.last_reason or "unknown error"
    if state.output_delivered:
        return f"the response was cut off part-way: {reason}"
    if state.attempts == 1:
        return f"the request failed: {reason}"
    return f"{state.attempts} attempts failed, the last with {reason}"
