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
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timezone
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
DEFAULT_STALL_ATTEMPTS = 2
#: Longest one request waits for a model load the server reports in progress
#: (another app's, on a shared server), apart from the retry deadline.
LOAD_WAIT_ENV = "GAIA_LLM_LOAD_WAIT"
DEFAULT_LOAD_WAIT = 600.0


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
    #: Attempts that may end in a stalled stream. Each one costs a whole idle
    #: limit, so this, not ``deadline``, is what bounds a stalling endpoint.
    max_stall_attempts: int = DEFAULT_STALL_ATTEMPTS
    #: Seconds a request may spend behind a model load the server reports.
    #: Kept out of ``deadline``: a 30B cold load outlasts it.
    load_wait: float = DEFAULT_LOAD_WAIT

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
            load_wait=_env_number(LOAD_WAIT_ENV, DEFAULT_LOAD_WAIT, float, minimum=0),
        )


#: Seconds a stream may send nothing before it counts as stalled: the whole
#: time for a hosted model, and the time after the first token for any model.
STREAM_IDLE_TIMEOUT_ENV = "GAIA_LLM_STREAM_IDLE_TIMEOUT"
DEFAULT_STREAM_IDLE_TIMEOUT = 120.0


def stream_idle_timeout() -> float:
    """The stream idle limit, from ``GAIA_LLM_STREAM_IDLE_TIMEOUT`` or the
    default. Raises on a present-but-invalid value, like the retry settings."""
    value = _env_number(
        STREAM_IDLE_TIMEOUT_ENV, DEFAULT_STREAM_IDLE_TIMEOUT, float, minimum=0
    )
    if value <= 0:
        raise ValueError(
            f"{STREAM_IDLE_TIMEOUT_ENV} must be greater than 0, got {value}. "
            f"Unset it to use the default ({DEFAULT_STREAM_IDLE_TIMEOUT})."
        )
    return value


#: Seconds a hosted model may take to send a whole non-streaming reply, which
#: arrives all at once. Overrides the size computed from the reply's tokens.
HOSTED_REPLY_TIMEOUT_ENV = "GAIA_LLM_HOSTED_REPLY_TIMEOUT"
#: Floor for that limit, and the pessimistic decode rate it scales by: a hosted
#: model prefills in seconds, so the reply's length is what takes time.
HOSTED_REPLY_MIN_SECONDS = 300.0
HOSTED_REPLY_MIN_TOKENS_PER_SECOND = 40.0


def hosted_reply_timeout(max_tokens: Optional[int]) -> float:
    """Read timeout for one non-streaming reply from a hosted model.

    ``GAIA_LLM_HOSTED_REPLY_TIMEOUT`` when set (an invalid value raises);
    otherwise 300 s, or longer when *max_tokens* at 40 tok/s needs it.
    """
    if os.environ.get(HOSTED_REPLY_TIMEOUT_ENV, "").strip():
        value = _env_number(
            HOSTED_REPLY_TIMEOUT_ENV, HOSTED_REPLY_MIN_SECONDS, float, minimum=0
        )
        if value <= 0:
            raise ValueError(
                f"{HOSTED_REPLY_TIMEOUT_ENV} must be greater than 0, got {value}. "
                "Unset it to size the limit from the reply length."
            )
        return value
    tokens = max(int(max_tokens or 0), 0)
    return max(HOSTED_REPLY_MIN_SECONDS, tokens / HOSTED_REPLY_MIN_TOKENS_PER_SECOND)


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

#: libcurl's wording for a transfer that broke at the connection level, as
#: Lemonade relays it in a status-less in-stream error frame when its hop to a
#: cloud provider fails, e.g. "backend connection failed during SSE stream
#: before DONE: CURL error: Failure when receiving data from the peer".
_CONNECTION_DROP_PHRASES = (
    "backend connection failed during sse stream",
    "failure when receiving data from the peer",
    "failed sending data to the peer",
    "transferred a partial file",
    "server returned nothing",
    "couldn't connect to server",
    "couldn't resolve host name",
    "stream error in the http/2 framing layer",
)

#: Lemonade's frame when its request to a cloud provider hit libcurl's time
#: limit. Only the cloud proxy sends this to the client; a local llama-server
#: timeout arrives in other words and stays unretried (see below).
_CLOUD_TIMEOUT_PHRASE = "sse stream failed: curl error: timeout was reached"

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
      that names a failed upstream hop — a dropped connection, a cloud time
      limit, a proxy's upstream error — is retried.
    """
    from gaia.llm.lemonade_client import is_context_overflow_error

    text = (body or "").lower()
    if text and is_context_overflow_error(body):
        return None
    dropped = any(phrase in text for phrase in _CONNECTION_DROP_PHRASES)
    transient = dropped or any(phrase in text for phrase in _TRANSIENT_PHRASES)
    permanent = any(phrase in text for phrase in _NOT_RETRYABLE_PHRASES)
    if status is None:
        if _CLOUD_TIMEOUT_PHRASE in text:
            return "upstream timeout"
        if permanent:
            return None
        if dropped:
            return "connection dropped"
        return "upstream failure" if transient else None
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


class StreamStalledError(Exception):
    """A model request sent nothing for longer than its idle limit: a stream
    gone quiet, or a non-streaming reply that never came."""

    def __init__(
        self,
        *,
        limit: float,
        remote: bool,
        before_output: bool,
        setting: str = STREAM_IDLE_TIMEOUT_ENV,
    ):
        self.limit = limit
        self.remote = remote
        self.before_output = before_output
        #: The variable that raises *limit*, named in the message.
        self.setting = setting
        super().__init__(self.describe(1))

    @property
    def retryable(self) -> bool:
        # A local model silent for its whole prefill budget would hang the
        # same way again; anything else is a stall worth one more try.
        return self.remote or not self.before_output

    def describe(self, attempts: int) -> str:
        """The give-up message: what stalled, how long, and what to do."""
        seconds = f"{self.limit:.0f}s"
        tries = f" (tried {attempts} times)" if attempts > 1 else ""
        if self.before_output and self.remote:
            what = f"The hosted model accepted the request but sent nothing for {seconds}{tries}."
        elif self.before_output:
            what = (
                f"The local model produced no output within {seconds}{tries}, the "
                "whole prefill budget for this context window."
            )
        else:
            what = (
                "The model stopped sending partway through its answer and sent "
                f"nothing for {seconds}{tries}."
            )
        if self.before_output and not self.remote:
            from gaia.llm.lemonade_client import REQUEST_BUDGET_ENV

            return (
                f"{what} Shorten the prompt, or on a machine slower than GAIA "
                f"assumes, raise {REQUEST_BUDGET_ENV} (seconds)."
            )
        if self.remote:
            advice = (
                "Send your message again; if it keeps happening the provider is "
                "overloaded, so wait a few minutes or pick another model."
            )
        else:
            advice = (
                "Send your message again; if it repeats, check the Lemonade "
                "server log."
            )
        return (
            f"{what} {advice} To wait longer before giving up, set "
            f"{self.setting} (seconds)."
        )


class StreamCancelledError(Exception):
    """Stop was pressed while a model stream was waiting for data."""


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
    if isinstance(error, StreamStalledError):
        return "stream stalled" if error.retryable else None
    if isinstance(error, StreamCancelledError):
        return None

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
        # An error event inside an SSE stream. Lemonade's carries the upstream
        # status (``{"status_code": 429, ...}``): a cloud gateway's rate limit
        # or 502 arrives this way once the stream has opened.
        from gaia.llm.lemonade_client import _cloud_error_status

        return classify_http_failure(
            _cloud_error_status(error), _error_body_text(error)
        )
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
    if when.tzinfo is None:
        # "-0000" parses naive; an HTTP-date is always UTC, never local time.
        when = when.replace(tzinfo=timezone.utc)
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


@dataclass(frozen=True)
class RetryNotice:
    """One client-level retry about to happen, for a front-end status line."""

    #: Log-safe, body-free reason (``"HTTP 429"``, ``"connection error"``).
    reason: str
    #: Seconds until the next attempt.
    delay: float
    #: This retry's number, from 1, out of the retries the policy allows.
    retry: int
    max_retries: int


_retry_listener: ContextVar[Optional[Callable[[RetryNotice], None]]] = ContextVar(
    "gaia_model_retry_listener", default=None
)
_retry_cancelled: ContextVar[Optional[Callable[[], bool]]] = ContextVar(
    "gaia_model_retry_cancelled", default=None
)

#: Longest stretch of a backoff wait between two checks of the cancel hook.
_CANCEL_CHECK_INTERVAL = 0.5


@contextmanager
def model_retry_notices(
    listener: Callable[[RetryNotice], None],
    is_cancelled: Optional[Callable[[], bool]] = None,
) -> Iterator[None]:
    """Call *listener* before every client-level retry made in this context,
    so whoever drives the turn can tell the user why it went quiet.

    When *is_cancelled* returns True during a backoff wait, the wait ends and
    the failure is raised without another attempt, so Stop is never stuck
    behind a ``Retry-After``.
    """
    token = _retry_listener.set(listener)
    cancel_token = _retry_cancelled.set(is_cancelled)
    try:
        yield
    finally:
        _retry_cancelled.reset(cancel_token)
        _retry_listener.reset(token)


def current_cancel_check() -> Optional[Callable[[], bool]]:
    """The Stop hook installed by :func:`model_retry_notices`, if any."""
    return _retry_cancelled.get()


class _StallBudget:
    """Stalled attempts of one request. Each already spent a whole idle limit,
    so its time is kept out of the retry deadline (or one stall would rule out
    every retry) and ``max_stall_attempts`` bounds them instead."""

    def __init__(self) -> None:
        self.count = 0
        self.seconds = 0.0

    def spent(
        self,
        retrier: "ModelCallRetrier",
        error: BaseException,
        state: RetryState,
        attempt_started: float,
        what: str,
    ) -> bool:
        """Record *error* if it is a retryable stall; True when that was the
        last stall the policy allows (and *state* is marked exhausted)."""
        if not (isinstance(error, StreamStalledError) and error.retryable):
            return False
        self.count += 1
        self.seconds += retrier._clock() - attempt_started
        if self.count < retrier.policy.max_stall_attempts:
            return False
        state.exhausted = True
        state.last_reason = "stream stalled"
        retrier._log.warning(
            "%s stalled (no data for %.0fs); giving up after %d stalled attempts",
            what,
            error.limit,
            self.count,
        )
        return True


#: Seconds between checks of a model load in progress, and between notices.
_LOAD_POLL_INTERVAL = 5.0
_LOAD_NOTICE_INTERVAL = 30.0


class _LoadWait:
    """Time one request spent behind a model load the server reported.

    A transient failure while the server says it is loading a model is the
    load's doing, not the endpoint's: the failed attempt and the wait for the
    load to end are kept out of the retry deadline and bounded by
    ``policy.load_wait`` instead. Measured by the clock, probes included.
    """

    def __init__(self) -> None:
        self.seconds = 0.0

    def after_failure(
        self,
        retrier: "ModelCallRetrier",
        error: BaseException,
        state: RetryState,
        attempt_started: float,
        what: str,
    ) -> Optional[bool]:
        """``None`` when no load is reported (the normal policy applies); True
        to try again now that it ended; False to give up (*state* says why)."""
        probe = retrier._server_loading
        if (
            probe is None
            or state.attempts >= retrier.policy.max_attempts
            or retrier._classify(error) is None
        ):
            return None
        loading = probe()
        if loading is None:
            return None
        state.last_reason = loading
        clock = retrier._clock
        is_cancelled = _retry_cancelled.get()
        listener = _retry_listener.get()
        max_retries = retrier.policy.max_attempts - 1
        self.seconds += clock() - attempt_started
        resumed = clock()
        last_notice: Optional[float] = None
        try:
            while loading is not None:
                if is_cancelled is not None and is_cancelled():
                    retrier._log.warning(
                        "%s failed while %s; cancelled during the wait",
                        what,
                        loading,
                    )
                    return False
                remaining = retrier.policy.load_wait - (
                    self.seconds + clock() - resumed
                )
                if remaining <= 0:
                    state.exhausted = True
                    state.last_reason = (
                        f"{loading} for over {retrier.policy.load_wait:.0f}s"
                    )
                    retrier._log.warning(
                        "%s failed while %s; gave up after waiting %.0fs for "
                        "it. Set %s (seconds) to wait longer.",
                        what,
                        loading,
                        retrier.policy.load_wait - remaining,
                        LOAD_WAIT_ENV,
                    )
                    return False
                pause = min(_LOAD_POLL_INTERVAL, remaining)
                if (
                    last_notice is None
                    or clock() - last_notice >= _LOAD_NOTICE_INTERVAL
                ):
                    retrier._log.warning(
                        "%s failed while %s; waiting for it (up to %.0fs more)",
                        what,
                        loading,
                        remaining,
                    )
                    if listener is not None:
                        listener(
                            RetryNotice(
                                reason=loading,
                                delay=pause,
                                retry=min(state.attempts, max_retries),
                                max_retries=max_retries,
                            )
                        )
                    last_notice = clock()
                retrier._sleep(pause)
                state.waited += pause
                loading = probe()
            return True
        finally:
            self.seconds += clock() - resumed


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
        server_loading: Optional[Callable[[], Optional[str]]] = None,
        log: Optional[logging.Logger] = None,
        classify: Callable[[BaseException], Optional[str]] = classify_exception,
    ):
        self.policy = policy or RetryPolicy.from_env()
        self._sleep = sleep
        self._clock = clock
        self._rng = rng or random.Random()
        #: What the server reports it is loading ("Lemonade is loading X"),
        #: or None. Without it, no wait is excused from the deadline.
        self._server_loading = server_loading
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
        listener = _retry_listener.get()
        if listener is not None:
            listener(
                RetryNotice(
                    reason=reason,
                    delay=delay,
                    retry=state.attempts,
                    max_retries=self.policy.max_attempts - 1,
                )
            )
        is_cancelled = _retry_cancelled.get()
        if is_cancelled is None:
            if delay > 0:
                self._sleep(delay)
            state.waited += delay
            return True
        remaining = delay
        while not is_cancelled():
            if remaining <= 0:
                return True
            pause = min(_CANCEL_CHECK_INTERVAL, remaining)
            self._sleep(pause)
            state.waited += pause
            remaining -= pause
        self._log.warning(
            "%s failed (%s); cancelled during the retry wait, not retrying",
            what,
            reason,
        )
        return False

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
        stalls = _StallBudget()
        loads = _LoadWait()
        while True:
            state.attempts += 1
            attempt_started = self._clock()
            try:
                return fn()
            except Exception as error:
                if stalls.spent(self, error, state, attempt_started, what):
                    attach_retry_state(error, state)
                    raise
                load_done = loads.after_failure(
                    self, error, state, attempt_started, what
                )
                if load_done:
                    continue
                if load_done is False or not self._wait_or_give_up(
                    error, state, started + stalls.seconds + loads.seconds, what
                ):
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

        A stalled attempt is bounded by ``max_stall_attempts``, not
        ``deadline`` (see :class:`_StallBudget`).
        """
        state = state if state is not None else RetryState()
        started = self._clock()
        stalls = _StallBudget()
        loads = _LoadWait()
        while True:
            state.attempts += 1
            attempt_started = self._clock()
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
                if stalls.spent(self, error, state, attempt_started, what):
                    attach_retry_state(error, state)
                    raise
                load_done = loads.after_failure(
                    self, error, state, attempt_started, what
                )
                if load_done:
                    continue
                if load_done is False or not self._wait_or_give_up(
                    error, state, started + stalls.seconds + loads.seconds, what
                ):
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
