#  Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Dev-mode gate for gaia-coder self-edit (§7.1).

Self-edit of the agent's own source is **disabled by default**. Dev mode has
two switches, both of which must be ON for :func:`is_enabled` to return True:

1. **Hard precondition** (auto-detected, non-negotiable — §7.1). The running
   ``gaia.coder`` source resolves into a writable git working tree whose
   ``origin`` remote matches ``repo_binding.toml.repo``. End-user installs
   (PyPI wheel, Electron bundle) cannot satisfy this no matter what.

2. **Soft enablement** — either a session flag in
   ``~/.gaia/coder/session.json`` (written by :func:`enable_session`, cleared
   by :func:`disable_session`, auto-cleared on daemon exit) or
   ``dev_mode_self_edit = true`` in ``em.toml`` (written by
   :func:`enable_permanent`).

Every enablement / disablement appends a row to ``audit.log.db`` when an
audit connection is supplied — the conversational UX is the primary
affordance, but the audit trail is the source of truth for "when did dev
mode flip and why" (§7.1).

Fail-loudly per repo ``CLAUDE.md``: a corrupt session file or a bogus
``em.toml`` raises with the path and next-action. There is no "fall back to
off" silent path — either the check is honoured, or the caller hears about
it.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from gaia.coder import trust as trust_mod
from gaia.coder.stores import audit as audit_store
from gaia.logger import get_logger

logger = get_logger(__name__)

#: Default location of the per-daemon session flag. Kept next to ``em.toml``
#: so the EM (and the agent) can always find it.
DEFAULT_SESSION_PATH: Path = Path.home() / ".gaia" / "coder" / "session.json"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DevModeError(Exception):
    """Dev-mode invariant violated.

    Raised when :func:`enable_session` / :func:`enable_permanent` are asked to
    turn on dev mode without the hard precondition being met, or when the
    session file is structurally invalid. Not raised by :func:`is_enabled`
    — that returns ``False`` instead.
    """


# ---------------------------------------------------------------------------
# Hard-precondition detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DevModeStatus:
    """Result of :func:`detect_dev_mode` — which of the §7.1 checks passed.

    Fields:
        editable_install: ``True`` iff the running source resolves into a git
            working tree whose ``origin`` remote matches
            ``repo_binding.toml.repo``. Concretely the §7.1 "hard precondition"
            — always False on PyPI / Electron installs.
        em_allowlist: ``True`` iff ``em.toml`` sets
            ``dev_mode_self_edit = true``. Independent of the session flag.
        repo_root: The git toplevel that ``editable_install`` was computed
            from, or ``None`` when the precondition failed. Useful in error
            messages and in tests that stage alternate repos.
        origin_url: The ``git remote get-url origin`` output that was
            compared against ``repo_binding.repo``, or ``None`` on failure.
        reason: Short human-readable string explaining why either check is
            off. Empty when both are on.
    """

    editable_install: bool
    em_allowlist: bool
    repo_root: Optional[Path] = None
    origin_url: Optional[str] = None
    reason: str = ""

    @property
    def hard_precondition_met(self) -> bool:
        """Convenience: does the agent *technically* qualify for dev mode?

        The hard precondition is the editable-install check; the EM allowlist
        is the soft gate. ``is_enabled`` composes both (plus the session
        file); this property only tells callers whether they'd be allowed to
        opt in at all.
        """
        return self.editable_install


def _git(*args: str, cwd: Path) -> Optional[str]:
    """Run ``git <args>`` under ``cwd`` and return stdout; ``None`` on failure.

    Used for ``rev-parse --show-toplevel`` and ``remote get-url origin``. We
    swallow :class:`subprocess.CalledProcessError` *only* here — the calling
    function surfaces a structured ``DevModeStatus`` instead of an exception,
    because "no git available" is a legitimate "not editable" signal, not a
    bug to raise on.
    """
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ) as exc:
        logger.debug("git %s under %s failed: %s", args, cwd, exc)
        return None
    return out.stdout.strip() or None


def _source_root() -> Path:
    """Return the parent directory of this module.

    Centralised so tests can monkeypatch a single entry point instead of
    ``Path(__file__).parent`` scattered across call sites.
    """
    return Path(__file__).resolve().parent


def _match_origin(origin_url: str, repo: str) -> bool:
    """Return True if ``origin_url`` references the ``owner/name`` in ``repo``.

    ``git remote get-url`` can return ``https://github.com/owner/name.git``,
    ``git@github.com:owner/name.git``, or a non-GitHub URL. We compare on the
    ``owner/name`` suffix (case-insensitive, ``.git`` stripped) rather than
    the full URL so HTTPS ↔ SSH switches do not break dev mode.
    """
    if not origin_url or not repo:
        return False
    cleaned = origin_url.strip()
    # Trim protocol / host so only "owner/name" remains.
    if cleaned.endswith(".git"):
        cleaned = cleaned[: -len(".git")]
    # Handle SSH form "git@github.com:owner/name"
    if ":" in cleaned and cleaned.startswith("git@"):
        cleaned = cleaned.split(":", 1)[1]
    # Handle HTTPS form by keeping last two path segments.
    parts = [p for p in cleaned.replace("\\", "/").split("/") if p]
    if len(parts) < 2:
        return False
    tail = "/".join(parts[-2:]).lower()
    return tail == repo.strip().lower()


def detect_dev_mode(
    *,
    em_cfg_path: Optional[Path] = None,
    repo_binding_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
) -> DevModeStatus:
    """Perform the two §7.1 checks and return a :class:`DevModeStatus`.

    Args:
        em_cfg_path: Path to ``em.toml``. Defaults to
            ``~/.gaia/coder/em.toml``. When the file is missing the EM
            allowlist is reported as ``False`` (not an error — unconfigured
            installs are a valid "off" state).
        repo_binding_path: Path to ``repo_binding.toml``. Defaults to the
            ``repo_binding.toml`` co-located with this module (the
            production layout). A missing binding is treated as "precondition
            failed" — without a bound repo we cannot verify origin.
        source_root: Override the directory used for the ``git
            rev-parse --show-toplevel`` probe. Tests pass a ``tmp_path`` that
            has been initialised as a git repo.

    Returns:
        :class:`DevModeStatus` with both booleans plus the diagnostic fields.

    The function never raises — it reports. Use :func:`is_enabled` if you
    want a single boolean; call this helper when you need to tell the user
    *why* dev mode is off.
    """
    cfg_path = em_cfg_path or (Path.home() / ".gaia" / "coder" / "em.toml")
    binding_path = repo_binding_path or (_source_root() / "repo_binding.toml")
    probe_root = source_root or _source_root()

    # -- Hard precondition: editable install.
    repo_root: Optional[Path] = None
    origin: Optional[str] = None
    editable = False
    reason = ""
    try:
        binding = trust_mod.load_repo_binding(binding_path)
    except trust_mod.TrustError as exc:
        reason = f"repo_binding.toml not usable: {exc}"
        binding = None

    if binding is not None:
        toplevel = _git("rev-parse", "--show-toplevel", cwd=probe_root)
        if toplevel is None:
            reason = (
                f"git rev-parse --show-toplevel failed under {probe_root}; "
                "source is not inside a writable git working tree (likely a "
                "PyPI / Electron install). Dev mode cannot be enabled."
            )
        else:
            repo_root = Path(toplevel)
            origin = _git("remote", "get-url", "origin", cwd=repo_root)
            if origin is None:
                reason = (
                    f"git remote get-url origin failed under {repo_root}; "
                    "no origin remote bound."
                )
            elif not _match_origin(origin, binding.repo):
                reason = (
                    f"origin remote {origin!r} does not match bound repo "
                    f"{binding.repo!r} from {binding_path}."
                )
            else:
                editable = True

    # -- Soft gate: em.toml allowlist (independent of session flag).
    em_allowlist = False
    if cfg_path.exists():
        try:
            em_cfg = trust_mod.load_em_config(cfg_path)
            em_allowlist = bool(em_cfg.dev_mode_self_edit)
        except trust_mod.TrustError as exc:
            # A broken em.toml is surfacing-worthy but not fatal here — we
            # report it in the reason and continue.
            reason = reason or f"em.toml unreadable: {exc}"

    if editable and em_allowlist and not reason:
        reason = ""

    return DevModeStatus(
        editable_install=editable,
        em_allowlist=em_allowlist,
        repo_root=repo_root,
        origin_url=origin,
        reason=reason,
    )


# ---------------------------------------------------------------------------
# Session flag (file-backed)
# ---------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


def _read_session(session_path: Path) -> dict:
    """Parse ``session.json`` (or return ``{}`` when missing).

    A corrupt JSON file raises :class:`DevModeError` rather than being
    silently ignored — per CLAUDE.md fail-loudly. The caller (CLI or agent)
    can then choose to delete the file explicitly.
    """
    if not session_path.exists():
        return {}
    try:
        return json.loads(session_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DevModeError(
            f"session file at {session_path} is not valid JSON: {exc}. "
            "Delete the file and re-enable dev mode, or inspect by hand."
        ) from exc


def _write_session(session_path: Path, data: dict) -> None:
    """Atomically write ``session.json``.

    Atomic via ``.tmp`` + ``replace`` so a crash mid-write never leaves a
    half-written file (which would raise on next load).
    """
    session_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = session_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(session_path)


def _audit(
    conn: Optional[sqlite3.Connection],
    *,
    tool_name: str,
    em_handle: str,
    reason: str,
    scope: str,
    loop_version: int,
) -> None:
    """Append a dev-mode transition row to ``audit.log.db``.

    Best-effort: when ``conn`` is ``None`` we skip — the CLI always supplies
    one in production, but library callers (tests, ad-hoc scripts) may
    legitimately not have one. No exception-swallowing outside the ``None``
    guard; a real SQLite error bubbles up.
    """
    if conn is None:
        return
    row = audit_store.AuditRow(
        occurred_at=_utc_now_iso(),
        tool_name=tool_name,
        args_json=json.dumps(
            {
                "em_handle": em_handle,
                "reason": reason,
                "scope": scope,
            },
            sort_keys=True,
        ),
        loop_version=loop_version,
    )
    audit_store.insert_row(conn, row)


def enable_session(
    em_cfg: trust_mod.EMConfig,
    reason: str,
    *,
    session_path: Path = DEFAULT_SESSION_PATH,
    audit_conn: Optional[sqlite3.Connection] = None,
    loop_version: int = 1,
    status: Optional[DevModeStatus] = None,
) -> None:
    """Turn dev mode ON for the current session.

    Writes ``{"dev_mode_session": true, ...}`` to ``session_path``. Refuses
    when the hard precondition (editable install + matching origin) is not
    met — §7.1 is explicit that conversation cannot enable dev mode on a
    PyPI install.

    Args:
        em_cfg: Current :class:`EMConfig` — used for the audit row
            (``em_handle``) and no other purpose.
        reason: Free-text conversational rationale. Persisted into
            ``session.json`` *and* into the audit row so ``gaia-coder
            introspect audit`` can reconstruct who enabled and why.
        session_path: Override for the session file. Tests pass a
            ``tmp_path``.
        audit_conn: Open ``audit.log.db`` connection. Optional; the CLI
            always supplies one.
        loop_version: Loop version stamped into the audit row.
        status: Optional pre-computed :class:`DevModeStatus`. Skip the
            ``detect_dev_mode`` round-trip when the caller already has one.

    Raises:
        DevModeError: when the hard precondition is not met. The message
            names the failing check and points at §7.1 — never a silent
            no-op.
    """
    effective = status or detect_dev_mode()
    if not effective.editable_install:
        raise DevModeError(
            "Cannot enable dev mode: hard precondition failed. "
            f"{effective.reason or 'editable install + matching origin required'}. "
            "See §7.1 of docs/plans/coder-agent.mdx."
        )
    if not reason or not reason.strip():
        raise DevModeError(
            "enable_session requires a non-empty reason (audit-trail invariant, §7.1)."
        )

    data = _read_session(session_path)
    data["dev_mode_session"] = True
    data["dev_mode_session_enabled_at"] = _utc_now_iso()
    data["dev_mode_session_reason"] = reason
    data["em_handle"] = em_cfg.em_handle
    _write_session(session_path, data)

    _audit(
        audit_conn,
        tool_name="dev_mode.enable_session",
        em_handle=em_cfg.em_handle,
        reason=reason,
        scope="session",
        loop_version=loop_version,
    )
    logger.info(
        "dev_mode.enable_session: ON for session (em=%s, reason=%r)",
        em_cfg.em_handle,
        reason,
    )


def disable_session(
    *,
    session_path: Path = DEFAULT_SESSION_PATH,
    audit_conn: Optional[sqlite3.Connection] = None,
    em_handle: str = "",
    loop_version: int = 1,
) -> None:
    """Clear the session flag (removes the three ``dev_mode_session_*`` keys).

    Idempotent: a missing or already-cleared session file is a no-op, not
    an error. Does NOT touch ``em.toml`` — use :func:`disable_permanent` for
    that (or equivalently, flip ``dev_mode_self_edit`` via the conversational
    intent handler, §15.4).
    """
    data = _read_session(session_path)
    for key in (
        "dev_mode_session",
        "dev_mode_session_enabled_at",
        "dev_mode_session_reason",
        "em_handle",
    ):
        data.pop(key, None)
    if data:
        _write_session(session_path, data)
    elif session_path.exists():
        session_path.unlink()

    _audit(
        audit_conn,
        tool_name="dev_mode.disable_session",
        em_handle=em_handle,
        reason="",
        scope="session",
        loop_version=loop_version,
    )
    logger.info("dev_mode.disable_session: OFF")


def enable_permanent(
    em_cfg: trust_mod.EMConfig,
    reason: str,
    *,
    em_cfg_path: Optional[Path] = None,
    audit_conn: Optional[sqlite3.Connection] = None,
    loop_version: int = 1,
    status: Optional[DevModeStatus] = None,
) -> trust_mod.EMConfig:
    """Persist ``dev_mode_self_edit = true`` to ``em.toml`` + audit-log.

    Returns the updated :class:`EMConfig`. Callers must keep the returned
    instance — the input is not mutated.

    Raises:
        DevModeError: hard precondition not met or reason empty.
    """
    effective = status or detect_dev_mode(em_cfg_path=em_cfg_path)
    if not effective.editable_install:
        raise DevModeError(
            "Cannot enable dev mode permanently: hard precondition failed. "
            f"{effective.reason or 'editable install + matching origin required'}. "
            "See §7.1 of docs/plans/coder-agent.mdx."
        )
    if not reason or not reason.strip():
        raise DevModeError(
            "enable_permanent requires a non-empty reason (§7.1 audit-trail invariant)."
        )

    now = _utc_now_iso()
    updated = em_cfg.model_copy(
        update={
            "dev_mode_self_edit": True,
            "dev_mode_enabled_at": now,
            "dev_mode_enabled_reason": reason,
        }
    )
    cfg_path = em_cfg_path or (Path.home() / ".gaia" / "coder" / "em.toml")
    trust_mod.save_em_config(cfg_path, updated)

    _audit(
        audit_conn,
        tool_name="dev_mode.enable_permanent",
        em_handle=em_cfg.em_handle,
        reason=reason,
        scope="permanent",
        loop_version=loop_version,
    )
    logger.info(
        "dev_mode.enable_permanent: em.toml updated (em=%s, reason=%r)",
        em_cfg.em_handle,
        reason,
    )
    return updated


def disable_permanent(
    em_cfg: trust_mod.EMConfig,
    *,
    em_cfg_path: Optional[Path] = None,
    audit_conn: Optional[sqlite3.Connection] = None,
    loop_version: int = 1,
) -> trust_mod.EMConfig:
    """Flip ``dev_mode_self_edit = false`` in ``em.toml``.

    Symmetric with :func:`enable_permanent`. Provided here so callers have
    one import for the full dev-mode surface rather than reaching into
    :mod:`gaia.coder.intent` for the inverse.
    """
    updated = em_cfg.model_copy(
        update={
            "dev_mode_self_edit": False,
            "dev_mode_enabled_reason": None,
        }
    )
    cfg_path = em_cfg_path or (Path.home() / ".gaia" / "coder" / "em.toml")
    trust_mod.save_em_config(cfg_path, updated)
    _audit(
        audit_conn,
        tool_name="dev_mode.disable_permanent",
        em_handle=em_cfg.em_handle,
        reason="",
        scope="permanent",
        loop_version=loop_version,
    )
    logger.info("dev_mode.disable_permanent: em.toml updated")
    return updated


def is_enabled(
    *,
    em_cfg_path: Optional[Path] = None,
    session_path: Path = DEFAULT_SESSION_PATH,
    repo_binding_path: Optional[Path] = None,
    source_root: Optional[Path] = None,
) -> bool:
    """Composite check — are we currently allowed to self-edit?

    Formula::

        is_enabled = hard_precondition_met
                     AND (session_flag OR em.toml dev_mode_self_edit)

    A corrupt ``session.json`` raises :class:`DevModeError` (surfaced, not
    silently downgraded) — the caller chooses whether to delete it.
    """
    status = detect_dev_mode(
        em_cfg_path=em_cfg_path,
        repo_binding_path=repo_binding_path,
        source_root=source_root,
    )
    if not status.editable_install:
        return False
    if status.em_allowlist:
        return True
    session = _read_session(session_path)
    return bool(session.get("dev_mode_session"))


def session_state(
    *,
    session_path: Path = DEFAULT_SESSION_PATH,
) -> dict:
    """Return the current session dict (empty if no session file).

    Read-only helper for introspection / CLI ``gaia-coder status`` output.
    """
    return dict(_read_session(session_path))


__all__ = [
    "DEFAULT_SESSION_PATH",
    "DevModeError",
    "DevModeStatus",
    "detect_dev_mode",
    "disable_permanent",
    "disable_session",
    "enable_permanent",
    "enable_session",
    "is_enabled",
    "session_state",
]
