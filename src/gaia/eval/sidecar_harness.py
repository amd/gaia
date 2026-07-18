# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Sidecar eval harness — drive an agent's ``/query`` loop THROUGH the sidecar /
daemon path (not in-process) and assert the canonical SSE event *sequence*
(V2-19, issue #2180).

Why a separate harness
----------------------
``behavior_harness.py`` drives the in-process Agent UI server and asserts tool
side-effects. It cannot see the v2 distributed seam at all: the sidecar's
``POST /v1/<agent>/query`` loop (V2-2 / #2016) and the frozen seven-event wire
contract (§0.2) that every front-door — the daemon relay, ``gaia email``,
``gaia api`` — relays. This harness exercises exactly that surface over REST, so
a drift in the §0.2 vocabulary or the loop→SSE translation is caught before a
third-party agent amplifies it.

What it asserts
---------------
Not a single final string, but the event **sequence** (§0.17):

1. every emitted event is one of the seven canonical §0.2 types;
2. the required ordered milestones appear in order (e.g. a triage run reaches
   ``status`` → ``tool_call`` → ``tool_result`` → ``final``);
3. exactly one terminal event (``final`` / ``error``), and it is last;
4. no forbidden type appears (e.g. a golden run must not end in ``error``).

Assertion 2 uses an ordered-**subsequence** match, not an exact list, because
the count of ``status`` / ``token`` / repeated ``tool_call`` events is
LLM-non-deterministic — pinning the exact list would make the baseline flap on
every model nudge. The *shape* (the §0.2 names and their order) is what the seam
guarantees, and what stays stable.

Serial by construction (CLAUDE.md)
----------------------------------
The eval rule — never two model-loading eval runs at once against the
single-tenant Lemonade slot — is ENFORCED here, not merely inherited:
:class:`SerialEvalLock` is a cross-process file lock the live harness takes for
the duration of a run. A second run blocks up to a timeout, then fails LOUD
naming the holder — it never silently proceeds in parallel (which is exactly the
race-evict CLAUDE.md documents).

No silent fallbacks
-------------------
A missing/unreachable daemon or sidecar raises :class:`SidecarUnavailable` with
an actionable message (what failed, what to do, where to look). The *test*
decides whether that is a skip or a failure — the harness never turns an absent
backend into a green pass.

Pure-vs-live split (mirrors ``behavior_harness``)
-------------------------------------------------
All classification logic (SSE parse, sequence match, baseline load, the lock)
lives in module-level helpers so unit tests cover it WITHOUT a running server or
Lemonade. :class:`SidecarEvalHarness` is the thin live driver.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from gaia.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# The frozen §0.2 canonical event vocabulary (#2015). Kept here as the harness's
# own copy on purpose: the harness must fail if the sidecar drifts from THIS
# set, so it cannot import the set it is checking. The distributed-seams suite
# asserts this copy stays equal to the relay's and the translator's copies.
# ---------------------------------------------------------------------------

CANONICAL_EVENT_TYPES: "frozenset[str]" = frozenset(
    {
        "status",
        "token",
        "tool_call",
        "tool_result",
        "needs_confirmation",
        "final",
        "error",
    }
)

#: The two event types that terminate a ``/query`` stream (exactly one ends it).
TERMINAL_TYPES: "frozenset[str]" = frozenset({"final", "error"})

#: Where the live harness takes its cross-process serial lock. Overridable via
#: ``GAIA_EVAL_LOCK_PATH`` so an isolated test run never contends with a real one.
DEFAULT_LOCK_PATH = Path.home() / ".gaia" / "eval" / ".sidecar-eval.lock"


# ---------------------------------------------------------------------------
# Errors — loud, actionable (CLAUDE.md "No Silent Fallbacks")
# ---------------------------------------------------------------------------


class SidecarUnavailable(RuntimeError):
    """The sidecar/daemon under test could not be reached or driven.

    Names what failed, what to do, and where to look — so a caller (or a test's
    skip reason) is actionable, never a bare "connection refused".
    """


class SerialEvalTimeout(RuntimeError):
    """Another eval run held the serial lock past the timeout.

    Raised instead of silently running in parallel (which race-evicts the shared
    Lemonade model slot — the exact failure CLAUDE.md forbids)."""


# ---------------------------------------------------------------------------
# SSE parsing (pure)
# ---------------------------------------------------------------------------


def parse_sse(text: str) -> List[Dict[str, Any]]:
    """Parse an SSE body into an ordered list of ``data:`` event dicts.

    Tolerant of the CRLF/LF and comment (``:``-prefixed keep-alive) framing a
    real server emits. A ``data:`` line whose payload is not JSON is surfaced as
    a synthetic ``{"type": "__unparseable__", "raw": ...}`` marker rather than
    dropped — a malformed frame is a seam bug the harness must be able to see,
    not hide.
    """
    events: List[Dict[str, Any]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except (ValueError, UnicodeDecodeError):
            events.append({"type": "__unparseable__", "raw": payload})
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            events.append({"type": "__non_object__", "raw": event})
    return events


def event_types(events: Sequence[Dict[str, Any]]) -> List[str]:
    """The ordered list of ``type`` values from parsed events."""
    return [str(e.get("type", "")) for e in events]


def _is_ordered_subsequence(needle: Sequence[str], haystack: Sequence[str]) -> bool:
    """True if every element of *needle* appears in *haystack* in the same order
    (not necessarily contiguous)."""
    it = iter(haystack)
    return all(any(n == h for h in it) for n in needle)


# ---------------------------------------------------------------------------
# Baseline + verdict (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SequenceBaseline:
    """The committed expected shape of one ``/query`` run's event sequence.

    Attributes:
        scenario_id: Stable id — also the baseline file stem.
        required_subsequence: Canonical event types that MUST appear, in this
            order (non-contiguous). The stable §0.2-shape pin.
        terminal: The single terminal type the run must end with (``final`` for
            a golden path, ``error`` for a negative one).
        forbidden: Types that must NOT appear at all (e.g. ``error`` on a golden
            path). ``final``/``error`` are never listed here — ``terminal`` owns
            the terminal check.
    """

    scenario_id: str
    required_subsequence: Tuple[str, ...]
    terminal: str = "final"
    forbidden: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        unknown = set(self.required_subsequence) - CANONICAL_EVENT_TYPES
        if unknown:
            raise ValueError(
                f"baseline {self.scenario_id!r} references non-canonical event "
                f"types {sorted(unknown)}; allowed: {sorted(CANONICAL_EVENT_TYPES)}"
            )
        if self.terminal not in TERMINAL_TYPES:
            raise ValueError(
                f"baseline {self.scenario_id!r} terminal must be one of "
                f"{sorted(TERMINAL_TYPES)}, got {self.terminal!r}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "required_subsequence": list(self.required_subsequence),
            "terminal": self.terminal,
            "forbidden": list(self.forbidden),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SequenceBaseline":
        try:
            return cls(
                scenario_id=data["scenario_id"],
                required_subsequence=tuple(data["required_subsequence"]),
                terminal=data.get("terminal", "final"),
                forbidden=tuple(data.get("forbidden", ())),
            )
        except KeyError as e:
            raise ValueError(
                f"malformed sequence baseline (missing key {e}): {data!r}"
            ) from e


@dataclass
class SequenceVerdict:
    """The result of matching one observed event sequence against a baseline."""

    passed: bool
    scenario_id: str
    observed_types: List[str]
    reasons: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "scenario_id": self.scenario_id,
            "observed_types": self.observed_types,
            "reasons": self.reasons,
        }


def match_sequence(
    events: Sequence[Dict[str, Any]], baseline: SequenceBaseline
) -> SequenceVerdict:
    """Match an observed event sequence against *baseline*, collecting EVERY
    violation (never short-circuit — a full report beats a first-failure)."""
    types = event_types(events)
    reasons: List[str] = []

    # 1. Vocabulary: every event is a canonical §0.2 type.
    non_canonical = [t for t in types if t not in CANONICAL_EVENT_TYPES]
    if non_canonical:
        reasons.append(
            f"non-canonical event type(s) emitted: {sorted(set(non_canonical))}; "
            f"allowed §0.2 set: {sorted(CANONICAL_EVENT_TYPES)}"
        )

    # 2. Required milestones appear in order.
    if not _is_ordered_subsequence(baseline.required_subsequence, types):
        reasons.append(
            f"required ordered milestones {list(baseline.required_subsequence)} "
            f"not found in observed sequence {types}"
        )

    # 3. Exactly one terminal, and it is last.
    terminal_count = sum(1 for t in types if t in TERMINAL_TYPES)
    if terminal_count != 1:
        reasons.append(
            f"expected exactly one terminal event ({sorted(TERMINAL_TYPES)}), "
            f"found {terminal_count} in {types}"
        )
    elif types[-1] not in TERMINAL_TYPES:
        reasons.append(f"terminal event is not last; sequence ends with {types[-1]!r}")
    elif types[-1] != baseline.terminal:
        reasons.append(
            f"terminal event is {types[-1]!r}, baseline expected "
            f"{baseline.terminal!r}"
        )

    # 4. No forbidden type appears.
    hit_forbidden = sorted(set(baseline.forbidden) & set(types))
    if hit_forbidden:
        reasons.append(f"forbidden event type(s) present: {hit_forbidden}")

    return SequenceVerdict(
        passed=not reasons,
        scenario_id=baseline.scenario_id,
        observed_types=types,
        reasons=reasons,
    )


# ---------------------------------------------------------------------------
# Baseline loading (baselines live in the agent package — issue requirement)
# ---------------------------------------------------------------------------


def baselines_dir_for(agent_package_root: Path) -> Path:
    """The committed-baseline directory inside an agent package.

    ``<package_root>/eval_baselines/query_sequences/`` — one JSON file per
    scenario, so a baseline diff shows in the agent package's own review, next
    to the ``/query`` route it pins.
    """
    return Path(agent_package_root) / "eval_baselines" / "query_sequences"


def load_baseline(path: Path) -> SequenceBaseline:
    """Load one committed sequence baseline from *path*."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise SidecarUnavailable(
            f"sequence baseline not found at {path}. Baselines live in the agent "
            "package under eval_baselines/query_sequences/; regenerate with the "
            "harness or restore the committed file."
        ) from e
    return SequenceBaseline.from_dict(data)


def load_baselines(directory: Path) -> Dict[str, SequenceBaseline]:
    """Load every ``*.json`` sequence baseline in *directory*, keyed by id."""
    directory = Path(directory)
    if not directory.is_dir():
        raise SidecarUnavailable(
            f"baseline directory {directory} does not exist. Expected committed "
            "sequence baselines under eval_baselines/query_sequences/ in the "
            "agent package."
        )
    out: Dict[str, SequenceBaseline] = {}
    for f in sorted(directory.glob("*.json")):
        baseline = load_baseline(f)
        out[baseline.scenario_id] = baseline
    if not out:
        raise SidecarUnavailable(f"no *.json sequence baselines found in {directory}.")
    return out


# ---------------------------------------------------------------------------
# Scenario
# ---------------------------------------------------------------------------


@dataclass
class QuerySequenceScenario:
    """One ``/query`` eval scenario driven through the sidecar path.

    Attributes:
        agent_id: The relay/sidecar agent id (e.g. ``"email"``) — selects the
            ``/v1/<agent_id>/query`` route.
        query: The natural-language request driving the agent loop.
        baseline: The committed expected event-sequence shape.
        context: Optional pushed transcript slice (spec §2.4).
        model: Optional model-id override for the run.
        max_steps: Optional agent-loop step ceiling.
    """

    agent_id: str
    query: str
    baseline: SequenceBaseline
    context: List[Dict[str, str]] = field(default_factory=list)
    model: Optional[str] = None
    max_steps: Optional[int] = None


# ---------------------------------------------------------------------------
# Serial lock (cross-process) — enforces the CLAUDE.md serial-eval rule
# ---------------------------------------------------------------------------


class SerialEvalLock:
    """A cross-process advisory lock guaranteeing ONE model-loading eval at a
    time, matching CLAUDE.md's "run evals SERIALLY, never in parallel".

    Implemented as an atomic ``O_CREAT | O_EXCL`` lock file carrying the holder
    pid. A stale lock (holder pid no longer alive) is reclaimed loudly. On
    contention past *timeout* it raises :class:`SerialEvalTimeout` naming the
    holder — it never silently proceeds in parallel.

    Usable as a context manager::

        with SerialEvalLock():
            harness.run_scenario(...)
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        timeout: float = 900.0,
        poll: float = 0.5,
    ) -> None:
        self.path = Path(
            path or os.environ.get("GAIA_EVAL_LOCK_PATH") or DEFAULT_LOCK_PATH
        )
        self.timeout = timeout
        self.poll = poll
        self._acquired = False

    def _holder_pid(self) -> Optional[int]:
        try:
            raw = self.path.read_text(encoding="utf-8").strip()
        except (OSError, ValueError):
            return None
        try:
            return int(raw.split()[0])
        except (ValueError, IndexError):
            return None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            import psutil

            return psutil.pid_exists(pid)
        except Exception:
            # psutil should always be present (a core dep); if it truly is not,
            # treat the holder as alive — refusing to run is the safe, loud
            # choice, never silently stealing the lock.
            return True

    def _try_create(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        try:
            os.write(fd, f"{os.getpid()} {time.time()}\n".encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def acquire(self) -> "SerialEvalLock":
        deadline = time.monotonic() + self.timeout
        while True:
            if self._try_create():
                self._acquired = True
                return self
            holder = self._holder_pid()
            if holder is not None and not self._pid_alive(holder):
                # Stale lock: the previous eval process died without releasing.
                logger.warning(
                    "sidecar eval: reclaiming stale serial lock at %s "
                    "(holder pid %s is gone)",
                    self.path,
                    holder,
                )
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise SerialEvalTimeout(
                    f"another eval run holds the serial lock at {self.path} "
                    f"(holder pid {holder}) and did not release within "
                    f"{self.timeout:.0f}s. Evals MUST run serially against the "
                    "single-tenant Lemonade slot (CLAUDE.md); wait for it to "
                    "finish or, if it is truly dead, remove the lock file."
                )
            time.sleep(self.poll)

    def release(self) -> None:
        if not self._acquired:
            return
        # Only remove a lock this process owns — never steal another holder's.
        if self._holder_pid() == os.getpid():
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
        self._acquired = False

    def __enter__(self) -> "SerialEvalLock":
        return self.acquire()

    def __exit__(self, *exc) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Live harness (requires a running daemon/sidecar + Lemonade)
# ---------------------------------------------------------------------------


class SidecarEvalHarness:
    """Drive ``POST /v1/<agent>/query`` over REST and match the event sequence.

    *base_url* is the front-door the run goes through:

    - the DAEMON relay (``http://127.0.0.1:<daemon-port>``) — the true v2 path
      (client token in ``auth_token``), or
    - a sidecar directly (``http://127.0.0.1:<sidecar-port>``) — the sidecar
      bearer in ``auth_token``.

    Either way the wire is identical (that is the point of the contract), so the
    same harness drives both. Every run takes :class:`SerialEvalLock` unless
    ``serialize=False`` (only for a caller that already holds it).
    """

    def __init__(
        self,
        base_url: str,
        *,
        auth_token: Optional[str] = None,
        timeout: float = 300.0,
        serialize: bool = True,
        lock_path: Optional[Path] = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth_token = auth_token
        self._timeout = timeout
        self._serialize = serialize
        self._lock_path = lock_path

    def _headers(self) -> Dict[str, str]:
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    def _query_url(self, agent_id: str) -> str:
        return f"{self._base_url}/v1/{agent_id}/query"

    def run_scenario(
        self, scenario: QuerySequenceScenario, *, run_id: Optional[str] = None
    ) -> Tuple[SequenceVerdict, List[Dict[str, Any]]]:
        """Run *scenario* once and return ``(verdict, events)``.

        Raises :class:`SidecarUnavailable` (loud, actionable) if the front-door
        cannot be reached or answers non-2xx — never returns a passing verdict
        for an absent backend. Serialized against other eval runs by default.
        """
        if self._serialize:
            with SerialEvalLock(self._lock_path):
                return self._run_locked(scenario, run_id)
        return self._run_locked(scenario, run_id)

    def _run_locked(
        self, scenario: QuerySequenceScenario, run_id: Optional[str]
    ) -> Tuple[SequenceVerdict, List[Dict[str, Any]]]:
        import requests  # local import — only needed when the harness actually runs

        rid = run_id or str(uuid.uuid4())
        body: Dict[str, Any] = {
            "query": scenario.query,
            "run_id": rid,
            "context": scenario.context,
        }
        if scenario.model is not None:
            body["model"] = scenario.model
        if scenario.max_steps is not None:
            body["max_steps"] = scenario.max_steps

        url = self._query_url(scenario.agent_id)
        try:
            resp = requests.post(
                url,
                json=body,
                headers=self._headers(),
                stream=True,
                timeout=(10, self._timeout),
            )
        except requests.RequestException as e:
            raise SidecarUnavailable(
                f"could not reach the /query front-door at {url} "
                f"({e.__class__.__name__}: {e}). Is the daemon running "
                "(`gaia daemon status`) and the sidecar ensured? Check "
                "~/.gaia/host/ and the sidecar logs under ~/.gaia/agents/."
            ) from e

        if resp.status_code != 200:
            detail = self._safe_body(resp)
            resp.close()
            raise SidecarUnavailable(
                f"/query at {url} returned HTTP {resp.status_code}: {detail}. "
                "A 401 means the auth token is wrong; 404/503 means the agent is "
                "not registered/running (ensure it first); 502 means the sidecar "
                "died after registration."
            )

        text_parts: List[str] = []
        try:
            for chunk in resp.iter_content(chunk_size=None):
                if chunk:
                    text_parts.append(
                        chunk.decode("utf-8", "replace")
                        if isinstance(chunk, bytes)
                        else chunk
                    )
        finally:
            resp.close()

        events = parse_sse("".join(text_parts))
        verdict = match_sequence(events, scenario.baseline)
        logger.info(
            "sidecar eval %s: %s (%d events: %s)",
            scenario.baseline.scenario_id,
            "PASS" if verdict.passed else "FAIL",
            len(events),
            verdict.observed_types,
        )
        return verdict, events

    @staticmethod
    def _safe_body(resp) -> str:
        try:
            return resp.text[:500]
        except Exception:  # noqa: BLE001 - diagnostics only, never mask the outer error
            return "<unreadable body>"


__all__ = [
    "CANONICAL_EVENT_TYPES",
    "TERMINAL_TYPES",
    "DEFAULT_LOCK_PATH",
    "SidecarUnavailable",
    "SerialEvalTimeout",
    "parse_sse",
    "event_types",
    "SequenceBaseline",
    "SequenceVerdict",
    "match_sequence",
    "baselines_dir_for",
    "load_baseline",
    "load_baselines",
    "QuerySequenceScenario",
    "SerialEvalLock",
    "SidecarEvalHarness",
]
