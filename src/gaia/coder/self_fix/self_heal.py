#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Self-detected bug mid-task (§7.5).

The Phase-7 sub-loop covered here runs when the agent encounters a failure
in the middle of a user task and diagnoses it as a bug in its *own* source.
Four public entry points:

* :func:`classify_failure` — LLM-driven triage via
  ``prompts/classify_failure.md`` (§15.8 P8). Conservative: uncertain
  classifications return ``external`` so a wrong diagnosis never burns
  self-edit churn.
* :func:`pause_current_task` — snapshots a task's in-flight state to
  ``~/.gaia/coder/paused-tasks/<task-id>.json`` via the existing
  :mod:`gaia.coder.stores.paused_tasks` helpers.
* :func:`resume_task` — hydrates a snapshot back into a structured
  :class:`ResumeResult` so the caller can rebuild the paused task's context.
* :func:`restart_self` — hot-reload for prompt-only / doc-only changes,
  graceful exit code 42 for code changes. The fork-bomb guard (§7.5 last
  bullet: "no more than 3 restarts in 1 hour") runs on every call.

Only the Python side is exported — wiring into the main ReAct loop and the
CLI is a Phase 11 (production swap) concern, explicitly out of scope for
this module.
"""

from __future__ import annotations

import importlib
import json
import logging
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Protocol

from gaia.coder.stores import audit as audit_store
from gaia.coder.stores import paused_tasks

logger = logging.getLogger(__name__)

#: Canonical kinds returned by the P8 classifier (§7.5).
FAILURE_KINDS: tuple[str, ...] = ("user-task", "self-code", "external")

#: Confidence floor below which a classification is force-rewritten to
#: ``external`` — mirrors §7.2's conservative triage discipline.
CLASSIFY_LOW_CONFIDENCE_FLOOR: int = 50

#: Restart rate-limit window: at most ``_RESTART_MAX_IN_WINDOW`` restarts in
#: ``_RESTART_COUNT_WINDOW`` seconds. §7.5 spells out "no more than 3 in 1
#: hour"; the constants are at module scope so tests and the CLI can reason
#: about the bound.
_RESTART_COUNT_WINDOW: float = 60.0 * 60.0  # one hour, seconds
_RESTART_MAX_IN_WINDOW: int = 3

#: Exit code the supervisor (Autonomy Engine / `gaia-coder daemon`)
#: interprets as "respawn me with the same args" (§7.5 step 7).
RESTART_EXIT_CODE: int = 42


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class SelfHealError(Exception):
    """Self-heal invariant violated (bad snapshot, restart storm, etc.)."""


class RestartStormError(SelfHealError):
    """Raised by :func:`restart_self` when the rate limit would be exceeded.

    §7.5: "No self-restart fork bomb. > 3 restarts / hour halts the
    heartbeat and pages the EM." The caller surfaces the error to the EM
    rather than retrying.
    """


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureClassification:
    """Structured result of :func:`classify_failure`.

    Mirrors the JSON shape the P8 prompt must return, with the addition of
    :attr:`escalated_low_confidence` — set when the classifier's confidence
    fell below :data:`CLASSIFY_LOW_CONFIDENCE_FLOOR` and the kind was
    force-rewritten to ``external``.
    """

    kind: str
    evidence: str
    confidence: int
    suggested_next_action: str
    escalated_low_confidence: bool = False


class ClassifyClient(Protocol):
    """Callable contract for the P8 LLM call.

    Accepts the fully-rendered prompt plus the structured context and
    returns the raw JSON string the model produced. Tests pass a lambda;
    production wires an Anthropic Opus 4.7 client in Phase 11.
    """

    def __call__(
        self,
        *,
        prompt: str,
        error: Mapping[str, Any],
        recent_tool_calls: list[Mapping[str, Any]],
        dev_mode_on: bool,
    ) -> str: ...


ClassifyClientFn = Callable[..., str]


def _default_classify_client(**_kwargs: Any) -> str:  # pragma: no cover
    """Default client refuses to run — forces callers to inject a real one.

    Keeps this module import-clean on systems without the ``anthropic``
    package (e.g. CI runs that only exercise stores / audit). Fail-loudly.
    """
    raise RuntimeError(
        "No ClassifyClient configured. Inject one via classify_failure(client=...) "
        "or wire the default Anthropic client at production swap (Phase 11)."
    )


_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def _render_classify_prompt(
    template: str,
    *,
    error_message: str,
    stack: str,
    tool_name: str,
    tool_args_json: str,
    from_audit_log: str,
    dev_mode_status: str,
) -> str:
    """Substitute the ``{{...}}`` slots used by P8.

    Deterministic string templating — no LLM involvement. Same
    double-brace convention as ``prompts/triage.md`` so the files stay
    human-editable.
    """
    subs: dict[str, str] = {
        "error_message": error_message,
        "stack": stack,
        "tool_name": tool_name,
        "tool_args_json": tool_args_json,
        "from_audit_log": from_audit_log,
        "dev_mode_status": dev_mode_status,
    }
    rendered = template
    for key, value in subs.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    return rendered


def classify_failure(
    error: Mapping[str, Any],
    context_json: Mapping[str, Any],
    *,
    dev_mode_on: bool = False,
    client: Optional[ClassifyClientFn] = None,
) -> FailureClassification:
    """Classify a mid-task failure as user-task / self-code / external.

    Args:
        error: Dict with at least ``message``, ``stack``, ``tool_name``,
            ``tool_args`` keys. ``tool_args`` is serialised to JSON inside
            the prompt.
        context_json: Arbitrary JSON-serialisable context. The
            ``recent_tool_calls`` key (list of audit-log summaries) is
            injected into the prompt at ``{{from_audit_log}}``.
        dev_mode_on: Whether dev mode is currently enabled. Controls the
            ``{{dev_mode_status}}`` slot — the classifier uses this when
            composing ``suggested_next_action``.
        client: Mockable LLM call; see :class:`ClassifyClient`.

    Returns:
        :class:`FailureClassification` — kind is always one of
        :data:`FAILURE_KINDS`; confidence is clamped to [0, 100]; below
        the floor the kind is force-rewritten to ``external`` and
        ``escalated_low_confidence`` is set.

    Raises:
        ValueError: when the model returns invalid JSON, an unknown kind,
            or an out-of-range confidence. Fail-loudly — we refuse to let
            corrupt classifier output drive a self-fix.
    """
    client = client or _default_classify_client
    template = _load_prompt("classify_failure.md")
    tool_args_json = json.dumps(
        error.get("tool_args") or error.get("args") or {}, sort_keys=True
    )
    recent = context_json.get("recent_tool_calls") or []
    recent_rendered = (
        json.dumps(recent, indent=2)
        if isinstance(recent, (list, tuple))
        else str(recent)
    )
    prompt = _render_classify_prompt(
        template,
        error_message=str(error.get("message", "")),
        stack=str(error.get("stack", "")),
        tool_name=str(error.get("tool_name", "")),
        tool_args_json=tool_args_json,
        from_audit_log=recent_rendered,
        dev_mode_status="on" if dev_mode_on else "off",
    )
    raw = client(
        prompt=prompt,
        error=dict(error),
        recent_tool_calls=list(recent) if isinstance(recent, (list, tuple)) else [],
        dev_mode_on=dev_mode_on,
    )
    return _parse_classify_response(raw)


def _parse_classify_response(raw: str) -> FailureClassification:
    """Parse the P8 JSON payload into :class:`FailureClassification`."""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("classify_failure response was empty")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"classify_failure response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("classify_failure response is not a JSON object")

    kind = parsed.get("kind")
    if kind not in FAILURE_KINDS:
        raise ValueError(
            f"classify_failure returned unknown kind {kind!r}; "
            f"expected one of {FAILURE_KINDS}"
        )
    confidence = parsed.get("confidence")
    if not isinstance(confidence, int) or not 0 <= confidence <= 100:
        raise ValueError(
            f"classify_failure returned invalid confidence {confidence!r}; "
            "expected int in [0, 100]"
        )
    evidence = parsed.get("evidence") or ""
    if not isinstance(evidence, str):
        raise ValueError("classify_failure evidence must be a string")
    suggested = parsed.get("suggested_next_action") or ""
    if not isinstance(suggested, str):
        raise ValueError("classify_failure suggested_next_action must be a string")

    escalated = False
    if kind != "external" and confidence < CLASSIFY_LOW_CONFIDENCE_FLOOR:
        logger.info(
            "classify_failure escalated to external (confidence=%d < %d)",
            confidence,
            CLASSIFY_LOW_CONFIDENCE_FLOOR,
        )
        kind = "external"
        escalated = True

    return FailureClassification(
        kind=kind,
        evidence=evidence,
        confidence=confidence,
        suggested_next_action=suggested,
        escalated_low_confidence=escalated,
    )


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PausedTaskPath:
    """Result of :func:`pause_current_task`.

    Fields:
        task_id: The task id that was paused.
        path: Absolute path of the on-disk snapshot.
        reason: Echoed back for caller's logging convenience.
    """

    task_id: str
    path: Path
    reason: str


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def pause_current_task(
    task_id: str,
    reason: str,
    *,
    root: Path,
    cwd: Optional[Path] = None,
    tool_call_history: Optional[list[Mapping[str, Any]]] = None,
    partial_outputs: Optional[Mapping[str, Any]] = None,
    original_prompt: Optional[str] = None,
    extra: Optional[Mapping[str, Any]] = None,
) -> PausedTaskPath:
    """Snapshot the in-flight task state to ``paused-tasks/<task-id>.json``.

    Thin wrapper over :func:`gaia.coder.stores.paused_tasks.write_snapshot`
    that assembles the §7.5 required context (cwd, tool-call history,
    partial outputs, original prompt) plus a ``paused_at`` timestamp and a
    ``reason`` line so the later resume step has everything the task needs.

    Args:
        task_id: Stable task identifier (a UUID in production; any path-safe
            string in tests).
        reason: One-liner explaining why the task is being paused (shown in
            the EM standup and included in the snapshot).
        root: Path to the ``paused-tasks/`` directory. Normally
            ``~/.gaia/coder/paused-tasks`` — tests pass a ``tmp_path``.
        cwd: Working directory at pause time. Defaults to :func:`Path.cwd`
            so the snapshot captures "where was I?" without the caller
            having to pass it.
        tool_call_history: Serialised recent tool calls for the audit
            trail. Empty list when unknown.
        partial_outputs: Arbitrary per-task partial output map. Kept as a
            dict of JSON-friendly values.
        original_prompt: The user's original task prompt — required for
            resume to make sense.
        extra: Escape hatch for task-specific additional fields. Merged
            into the snapshot under ``extra``.

    Returns:
        :class:`PausedTaskPath` with the written file's absolute path.
    """
    snapshot: dict[str, Any] = {
        "task_id": task_id,
        "paused_at": _utc_now_iso(),
        "reason": reason,
        "cwd": str(cwd or Path.cwd()),
        "tool_call_history": list(tool_call_history or []),
        "partial_outputs": dict(partial_outputs or {}),
        "original_prompt": original_prompt or "",
        "extra": dict(extra or {}),
    }
    path = paused_tasks.write_snapshot(Path(root), task_id, snapshot)
    logger.info("pause_current_task: snapshot at %s (reason=%r)", path, reason)
    return PausedTaskPath(task_id=task_id, path=path, reason=reason)


@dataclass(frozen=True)
class ResumeResult:
    """Return shape of :func:`resume_task` — hydrated snapshot for the caller.

    Fields mirror :func:`pause_current_task`. ``snapshot_path`` is preserved
    so callers that want to move or delete the file after successful resume
    can do so without another ``list_snapshots`` lookup.
    """

    task_id: str
    cwd: Path
    tool_call_history: list[Mapping[str, Any]]
    partial_outputs: Mapping[str, Any]
    original_prompt: str
    paused_at: str
    reason: str
    extra: Mapping[str, Any] = field(default_factory=dict)
    snapshot_path: Optional[Path] = None


def resume_task(
    task_id: str,
    *,
    root: Path,
    delete_snapshot: bool = False,
) -> ResumeResult:
    """Hydrate a paused-task snapshot into a structured :class:`ResumeResult`.

    Args:
        task_id: Snapshot id to read.
        root: Paused-tasks directory.
        delete_snapshot: When True, delete the snapshot file after a
            successful read. Defaults to False — the caller decides when
            the task has resumed far enough to discard the evidence.

    Raises:
        FileNotFoundError: when no snapshot exists for ``task_id`` (raised
            directly by :func:`paused_tasks.read_snapshot`). Surfaced, not
            silently treated as "no paused state".
        SelfHealError: when the snapshot file is structurally corrupt
            (missing required fields).
    """
    data = paused_tasks.read_snapshot(Path(root), task_id)

    required = {"task_id", "paused_at", "reason"}
    missing = sorted(required - set(data))
    if missing:
        raise SelfHealError(
            f"paused-task snapshot at {paused_tasks.snapshot_path(root, task_id)} "
            f"is missing required fields: {missing}. Delete the snapshot or "
            "rehydrate it by hand."
        )

    snapshot_path = paused_tasks.snapshot_path(Path(root), task_id)
    result = ResumeResult(
        task_id=str(data["task_id"]),
        cwd=Path(str(data.get("cwd") or Path.cwd())),
        tool_call_history=list(data.get("tool_call_history") or []),
        partial_outputs=dict(data.get("partial_outputs") or {}),
        original_prompt=str(data.get("original_prompt") or ""),
        paused_at=str(data["paused_at"]),
        reason=str(data["reason"]),
        extra=dict(data.get("extra") or {}),
        snapshot_path=snapshot_path,
    )
    if delete_snapshot and snapshot_path.exists():
        snapshot_path.unlink()
        logger.info("resume_task: deleted snapshot %s", snapshot_path)
    return result


# ---------------------------------------------------------------------------
# Restart self — hot reload or exit-42
# ---------------------------------------------------------------------------

#: In-process rolling list of restart UNIX timestamps. Kept as module state
#: because the fork-bomb guard has to survive within one process lifetime;
#: across restarts the supervisor re-instantiates module state from scratch,
#: which is fine — the supervisor is the other half of the bound (it refuses
#: to respawn more than _RESTART_MAX_IN_WINDOW times in _RESTART_COUNT_WINDOW
#: seconds too).
_RESTART_TIMESTAMPS: list[float] = []


def _reset_restart_window() -> None:
    """Clear the in-process restart timestamp list.

    Exported for tests via the ``__all__`` hook below. Production callers
    never touch this.
    """
    _RESTART_TIMESTAMPS.clear()


def _record_restart_attempt(now: float) -> None:
    """Drop stale timestamps, append ``now``; raise if the bound is breached."""
    cutoff = now - _RESTART_COUNT_WINDOW
    # Purge stale entries in-place so memory does not grow unbounded over a
    # long-running daemon.
    _RESTART_TIMESTAMPS[:] = [t for t in _RESTART_TIMESTAMPS if t >= cutoff]
    if len(_RESTART_TIMESTAMPS) >= _RESTART_MAX_IN_WINDOW:
        raise RestartStormError(
            f"Refusing to restart: would exceed {_RESTART_MAX_IN_WINDOW} "
            f"restarts in {_RESTART_COUNT_WINDOW / 60.0:.0f} minutes. "
            "Recent restart times: "
            f"{[datetime.fromtimestamp(t, tz=timezone.utc).isoformat() for t in _RESTART_TIMESTAMPS]}. "
            "Halt the daemon and page the EM per §7.5."
        )
    _RESTART_TIMESTAMPS.append(now)


#: Valid ``kind`` values for :func:`restart_self`.
RESTART_KINDS: tuple[str, ...] = ("prompt-only", "doc-only", "code")

# Modules safe to hot-reload for prompt-only / doc-only restarts. Kept here
# rather than inside the function so tests can monkeypatch the set.
_HOT_RELOAD_MODULES: tuple[str, ...] = (
    "gaia.coder.prompts",
    "gaia.coder.GAIA",
    "gaia.coder.ARCHITECTURE",
)


@dataclass(frozen=True)
class RestartResult:
    """Return shape of :func:`restart_self`.

    For hot-reload restarts, ``exited`` is False and ``reloaded_modules``
    lists the importlib-reloaded names. For cold restarts the function
    calls :func:`sys.exit` before returning — callers on that path see
    :class:`SystemExit`, not a :class:`RestartResult`.
    """

    kind: str
    reason: str
    reloaded_modules: tuple[str, ...]
    exited: bool


def restart_self(
    reason: str,
    kind: str = "code",
    *,
    audit_conn: Optional[sqlite3.Connection] = None,
    em_handle: str = "",
    loop_version: int = 1,
    now: Optional[float] = None,
    exit_fn: Callable[[int], None] = sys.exit,
) -> RestartResult:
    """Restart the agent — hot reload (prompts/docs) or cold exit (code).

    §7.5 step 7: prompt-only / doc-only changes hot-reload via
    :func:`importlib.reload`; code changes return exit code
    :data:`RESTART_EXIT_CODE` (42) so the supervisor respawns the process.

    Args:
        reason: Free text (audit-logged).
        kind: One of :data:`RESTART_KINDS`. Unknown values raise.
        audit_conn: Optional ``audit.log.db`` connection.
        em_handle: EM handle for the audit row.
        loop_version: Loop version for the audit row.
        now: Override clock (tests). Defaults to :func:`time.time`.
        exit_fn: Override exit call (tests). Defaults to :func:`sys.exit`.

    Returns:
        :class:`RestartResult` for hot reloads. Cold restarts never return —
        they call ``exit_fn(RESTART_EXIT_CODE)``.

    Raises:
        RestartStormError: more than :data:`_RESTART_MAX_IN_WINDOW`
            restarts would be recorded inside
            :data:`_RESTART_COUNT_WINDOW` seconds.
        ValueError: unknown ``kind``.
    """
    if kind not in RESTART_KINDS:
        raise ValueError(
            f"restart_self: unknown kind {kind!r}; expected one of {RESTART_KINDS}"
        )

    _record_restart_attempt(now if now is not None else time.time())

    if audit_conn is not None:
        row = audit_store.AuditRow(
            occurred_at=_utc_now_iso(),
            tool_name="self_heal.restart_self",
            args_json=json.dumps(
                {"reason": reason, "kind": kind, "em_handle": em_handle},
                sort_keys=True,
            ),
            loop_version=loop_version,
        )
        audit_store.insert_row(audit_conn, row)

    if kind in ("prompt-only", "doc-only"):
        reloaded = _hot_reload_prompts()
        logger.info(
            "restart_self: hot-reloaded %d modules (%s, reason=%r)",
            len(reloaded),
            kind,
            reason,
        )
        return RestartResult(
            kind=kind,
            reason=reason,
            reloaded_modules=tuple(reloaded),
            exited=False,
        )

    # kind == "code" — cold restart. Log BEFORE calling exit_fn so tests
    # that patch exit_fn into a lambda can still assert the log line.
    logger.warning(
        "restart_self: cold restart (exit code %d, reason=%r)",
        RESTART_EXIT_CODE,
        reason,
    )
    exit_fn(RESTART_EXIT_CODE)
    # Defensive: if an injected exit_fn does not actually exit (tests pass
    # ``calls.append`` which just records the code), return a result so
    # callers can observe the "did-NOT-exit" outcome. The ``pylint: disable``
    # is scoped to the single line — the default ``sys.exit`` path DOES make
    # the return unreachable, but we need it for test injection.
    return RestartResult(  # pylint: disable=unreachable
        kind=kind,
        reason=reason,
        reloaded_modules=(),
        exited=True,
    )


def _hot_reload_prompts() -> list[str]:
    """Call :func:`importlib.reload` on every already-loaded prompt module.

    Skips modules that are not in :data:`sys.modules` — a fresh process that
    has never imported prompts has nothing to reload, which is fine.
    Returns the list of module names that were actually reloaded.
    """
    reloaded: list[str] = []
    for name in _HOT_RELOAD_MODULES:
        mod = sys.modules.get(name)
        if mod is None:
            continue
        importlib.reload(mod)
        reloaded.append(name)
    return reloaded


__all__ = [
    "CLASSIFY_LOW_CONFIDENCE_FLOOR",
    "ClassifyClient",
    "FAILURE_KINDS",
    "FailureClassification",
    "PausedTaskPath",
    "RESTART_EXIT_CODE",
    "RESTART_KINDS",
    "RestartResult",
    "RestartStormError",
    "ResumeResult",
    "SelfHealError",
    "_RESTART_COUNT_WINDOW",
    "_RESTART_MAX_IN_WINDOW",
    "_reset_restart_window",
    "classify_failure",
    "pause_current_task",
    "restart_self",
    "resume_task",
]
