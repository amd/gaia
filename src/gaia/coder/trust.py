# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Trust contract data layer for ``gaia-coder`` (§4, §7.1, §15.6).

This module is the durable-state half of the trust contract. It owns:

* :class:`EMConfig` — Pydantic mirror of ``~/.gaia/coder/em.toml`` per §7.1.
* :class:`CapabilityTier` — the 0-5 tier ladder from §4.2.
* :class:`RepoBinding` — Pydantic mirror of ``repo_binding.toml`` per §15.6.
* :func:`load_em_config` / :func:`save_em_config` — TOML round-trip helpers.
* :func:`load_repo_binding` — TOML reader for the repo-binding manifest.
* :func:`promote` / :func:`demote` — tier changes with audit-log writes.

Every tier change writes an append-only row into ``audit.log.db`` so
``gaia-coder trust --history`` can reconstruct the timeline.

Fail-loudly policy (per repo ``CLAUDE.md``): a bad signature, a missing
required field, or an out-of-range tier raises :class:`TrustError` with an
actionable message. No silent fallbacks, no defaults-to-something-workable.
"""

from __future__ import annotations

import json
import sqlite3
import tomllib
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from gaia.coder.stores import audit


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with trailing ``Z`` — the audit-log convention."""
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class TrustError(Exception):
    """Trust contract invariant violated.

    Raised when a tier change is requested without a valid signature, when an
    ``em.toml`` file is missing required fields, or when a promotion targets
    an out-of-range tier. Messages name *what failed*, *what the caller should
    do*, and *where to look next* per the repo-level fail-loudly convention.
    """


# ---------------------------------------------------------------------------
# Capability ladder (§4.2)
# ---------------------------------------------------------------------------


class CapabilityTier(IntEnum):
    """Capability tiers defined in §4.2 of ``docs/plans/coder-agent.mdx``.

    Tiers are ordinal — each tier grants everything below it plus the
    incremental authority listed in the table. ``TIER_0`` ships by default on
    new installs (§4.2 "New instances ship at Tier 0").
    """

    TIER_0 = 0  # Read-only observer
    TIER_1 = 1  # Drafter
    TIER_2 = 2  # Branch author (PR-gated)
    TIER_3 = 3  # Self-maintainer
    TIER_4 = 4  # Self-coder (dev mode)
    TIER_5 = 5  # Trusted integrator

    @property
    def label(self) -> str:
        """Human-readable tier name per §4.2's table."""
        return _TIER_LABELS[int(self)]


_TIER_LABELS: dict[int, str] = {
    0: "Read-only observer",
    1: "Drafter",
    2: "Branch author (PR-gated)",
    3: "Self-maintainer",
    4: "Self-coder",
    5: "Trusted integrator",
}

#: Incremental capability bullets shown by ``gaia-coder trust``. Keyed by the
#: tier that *first* grants each capability so the CLI can render the "you
#: may:" block as the cumulative set for the current tier.
TIER_CAPABILITIES: dict[int, str] = {
    0: "Read files, run tests, query GitHub, browse the web",
    1: "Draft proposals and patches (scratch/, *.patch)",
    2: "Create branches, open draft PRs against `coder`",
    3: (
        "Autonomously fix CI failures on `coder`, react to events, own the "
        "task queue"
    ),
    4: "Edit your own source (dev-mode opt-in required — §7.1)",
    5: "Self-merge PRs into `coder` and open `coder` → `main` PRs",
}


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class EMConfig(BaseModel):
    """Persisted trust-contract state — mirrors ``em.toml`` from §7.1.

    Fields:
      em_handle: GitHub handle of the bound Engineering Manager. Required.
      em_channel: Preferred contact channel (e.g. ``github-issue-comment``).
        Required.
      persona_name: Optional name the agent uses when signing standups /
        comments. Defaults to ``None`` meaning "sign as gaia-coder".
      dev_mode_self_edit: Whether the EM has granted persistent self-edit
        permission. Defaults to ``False``; flipped by ``enable self-edit
        permanently`` (§7.1). Session-scoped enablement does *not* touch this
        field — it lives in process memory only.
      dev_mode_enabled_at: ISO-8601 UTC timestamp recording when persistent
        self-edit was granted. ``None`` if self-edit has never been enabled
        persistently.
      dev_mode_enabled_reason: Free-text line captured from the conversation
        that granted persistent self-edit.
      allow_state_machine_edit: Separate opt-in for §7.8 loop edits. Defaults
        to ``False``; enabling dev mode does NOT enable loop edits (§7.1).
      auto_merge_classes: Fix-classes the EM has graduated to auto-merge per
        §7.6. Defaults to empty list — no auto-merge.
      current_tier: Current capability tier (0-5). Defaults to Tier 0 per
        §4.2 "New instances ship at Tier 0."
    """

    em_handle: str = Field(description="GitHub handle of the bound EM")
    em_channel: str = Field(description="Preferred EM contact channel")
    persona_name: Optional[str] = None
    dev_mode_self_edit: bool = False
    dev_mode_enabled_at: Optional[str] = None
    dev_mode_enabled_reason: Optional[str] = None
    allow_state_machine_edit: bool = False
    auto_merge_classes: list[str] = Field(default_factory=list)
    current_tier: int = Field(default=int(CapabilityTier.TIER_0), ge=0, le=5)

    @field_validator("em_handle")
    @classmethod
    def _handle_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "em_handle must be a non-empty GitHub handle; "
                "run the §4.1 bootstrap prompt to record one"
            )
        return v

    @field_validator("em_channel")
    @classmethod
    def _channel_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError(
                "em_channel must be non-empty (e.g. 'github-issue-comment' or 'email')"
            )
        return v


class RepoBinding(BaseModel):
    """Persisted GitHub-App identity — mirrors ``repo_binding.toml`` from §15.6.

    Only the fields the agent reads today are typed. Additional keys are
    ignored (forward-compat for future GitHub App fields) rather than
    silently dropped; ``model_config = {"extra": "ignore"}`` preserves this.
    """

    repo: str = Field(description="owner/name on GitHub (e.g. 'amd/gaia')")
    github_app_id: int = Field(ge=1)
    github_installation_id: int = Field(ge=1)
    webhook_secret_keyring_slot: str
    private_key_keyring_slot: str
    allowed_branches: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)

    model_config = {"extra": "ignore"}

    @field_validator("repo")
    @classmethod
    def _repo_shape(cls, v: str) -> str:
        if "/" not in v or v.startswith("/") or v.endswith("/"):
            raise ValueError(
                f"repo must be 'owner/name' (got {v!r}); see §15.6 for the schema"
            )
        return v


# ---------------------------------------------------------------------------
# TOML I/O
# ---------------------------------------------------------------------------


def load_em_config(path: str | Path) -> EMConfig:
    """Read and validate ``em.toml`` from *path*.

    Raises:
        TrustError: if the file is missing or cannot be parsed. The error
            message names the path and points at the §4.1 bootstrap flow so
            the CLI can surface it verbatim.
        pydantic.ValidationError: if a field is malformed. The CLI lets this
            bubble so the user sees the exact field path.
    """
    p = Path(path)
    if not p.exists():
        raise TrustError(
            f"EM config not found at {p}. "
            "Run `gaia-coder trust` to start the §4.1 bootstrap "
            "('Who is my engineering manager?') or create the file by hand."
        )
    try:
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise TrustError(
            f"EM config at {p} is not valid TOML: {e}. "
            "Fix by hand or delete the file and re-run `gaia-coder trust`."
        ) from e
    return EMConfig(**raw)


def save_em_config(path: str | Path, cfg: EMConfig) -> None:
    """Serialise *cfg* to TOML at *path*.

    We hand-roll a tiny TOML writer because ``tomli_w`` is not in the project's
    dependency set and ``em.toml``'s schema is flat and well-known. Strings are
    quoted, booleans are lower-case, ``None`` fields are omitted (rather than
    emitting ``= ""``, which round-trips as an empty string rather than
    "absent").
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    lines.append(f'em_handle = "{_toml_escape(cfg.em_handle)}"')
    lines.append(f'em_channel = "{_toml_escape(cfg.em_channel)}"')
    if cfg.persona_name is not None:
        lines.append(f'persona_name = "{_toml_escape(cfg.persona_name)}"')
    lines.append(
        f"dev_mode_self_edit = {'true' if cfg.dev_mode_self_edit else 'false'}"
    )
    if cfg.dev_mode_enabled_at is not None:
        lines.append(f'dev_mode_enabled_at = "{_toml_escape(cfg.dev_mode_enabled_at)}"')
    if cfg.dev_mode_enabled_reason is not None:
        lines.append(
            f'dev_mode_enabled_reason = "{_toml_escape(cfg.dev_mode_enabled_reason)}"'
        )
    lines.append(
        "allow_state_machine_edit = "
        f"{'true' if cfg.allow_state_machine_edit else 'false'}"
    )
    classes = ", ".join(f'"{_toml_escape(c)}"' for c in cfg.auto_merge_classes)
    lines.append(f"auto_merge_classes = [{classes}]")
    lines.append(f"current_tier = {int(cfg.current_tier)}")

    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _toml_escape(s: str) -> str:
    """Escape a string for embedding inside double-quoted TOML.

    TOML basic-string escaping: backslash, double-quote, control chars. The
    fields we write are short handles, channel names, and ISO timestamps —
    in practice none of them contain embedded quotes, but we escape
    defensively so a surprising persona_name or reason text doesn't corrupt
    the file.
    """
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


def load_repo_binding(path: str | Path) -> RepoBinding:
    """Read and validate ``repo_binding.toml`` from *path*.

    Raises:
        TrustError: if the file is missing or unparseable. §15.6 lists the
            bot-provisioning checklist the EM must run before this file
            exists, so the error points there.
    """
    p = Path(path)
    if not p.exists():
        raise TrustError(
            f"Repo binding not found at {p}. "
            "Run the §15.6 bot-provisioning checklist "
            "(`gaia-coder doctor` will guide you)."
        )
    try:
        with p.open("rb") as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise TrustError(
            f"Repo binding at {p} is not valid TOML: {e}. See §15.6 for the schema."
        ) from e
    return RepoBinding(**raw)


# ---------------------------------------------------------------------------
# Tier changes
# ---------------------------------------------------------------------------


def _write_tier_audit(
    *,
    conn: sqlite3.Connection,
    tool_name: str,
    from_tier: int,
    to_tier: int,
    reason: str,
    em_handle: str,
    signature: Optional[str],
    loop_version: int,
) -> int:
    """Append a tier-change audit row. Returns the new audit id.

    Kept private because the audit shape (tool_name + args_json) is an
    implementation detail of this module; callers use :func:`promote` /
    :func:`demote`.
    """
    args_json = json.dumps(
        {
            "from_tier": from_tier,
            "to_tier": to_tier,
            "reason": reason,
            "em_handle": em_handle,
            "em_signature": signature,
        },
        sort_keys=True,
    )
    row = audit.AuditRow(
        occurred_at=_utc_now_iso(),
        tool_name=tool_name,
        args_json=args_json,
        loop_version=loop_version,
    )
    return audit.insert_row(conn, row)


def promote(
    em_cfg: EMConfig,
    to_tier: int | CapabilityTier,
    reason: str,
    em_signature: str,
    *,
    audit_conn: Optional[sqlite3.Connection] = None,
    loop_version: int = 1,
) -> EMConfig:
    """Promote to *to_tier* after validating the EM signature.

    §4.2 makes promotion explicit: the EM signs a promotion with their handle;
    the agent refuses to promote without that signature. ``em_signature`` here
    is the string the EM provided (CLI ``--em-signature <handle>``); it must
    equal ``em_cfg.em_handle``. A GitHub-App-verified signature is deferred to
    §15.6's bot provisioning.

    Args:
        em_cfg: Current EM config (read).
        to_tier: Target tier. Must be ``CapabilityTier`` member or 0-5 int.
        reason: Free text for the audit row (§4.4 reporting cadence requires
            promotions be justified).
        em_signature: String the EM typed to sign the promotion. Must match
            ``em_cfg.em_handle``.
        audit_conn: Optional open ``audit.log.db`` connection. When provided,
            a row is appended; when ``None``, no audit side effect. Callers
            at the CLI layer always provide it; unit tests can omit.
        loop_version: Loop version stamped into the audit row. Defaults to 1.

    Returns:
        A new :class:`EMConfig` with ``current_tier`` updated. The caller is
        responsible for persisting via :func:`save_em_config`.

    Raises:
        TrustError: on bad signature or out-of-range tier.
    """
    if not em_signature or em_signature.strip() != em_cfg.em_handle:
        raise TrustError(
            "Promotion rejected: signature does not match bound EM. "
            f"Expected {em_cfg.em_handle!r}, got {em_signature!r}. "
            "Re-run with `--em-signature <bound-em-handle>` or run "
            "`gaia-coder em-handoff` if the EM has changed."
        )
    try:
        target = CapabilityTier(int(to_tier))
    except ValueError as e:
        raise TrustError(f"Tier {to_tier} out of range (valid: 0-5; see §4.2).") from e
    if not reason or not reason.strip():
        raise TrustError(
            "Promotion rejected: reason is required (§4.4 standup cadence "
            "depends on a non-empty rationale)."
        )

    from_tier = em_cfg.current_tier
    updated = em_cfg.model_copy(update={"current_tier": int(target)})

    if audit_conn is not None:
        _write_tier_audit(
            conn=audit_conn,
            tool_name="trust.promote",
            from_tier=from_tier,
            to_tier=int(target),
            reason=reason,
            em_handle=em_cfg.em_handle,
            signature=em_signature,
            loop_version=loop_version,
        )

    return updated


def demote(
    em_cfg: EMConfig,
    reason: str,
    *,
    audit_conn: Optional[sqlite3.Connection] = None,
    to_tier: int | CapabilityTier | None = None,
    loop_version: int = 1,
) -> EMConfig:
    """Demote the agent immediately; no signature required per §4.2.

    §4.2: "Demotions are immediate and require no justification." We *do* ask
    for a reason so the audit row has context (silent demotions are
    concealment-adjacent — see §4.4), but we do not gate the call on it.

    Args:
        em_cfg: Current EM config.
        reason: Free text; may be empty (use ``"no reason given"`` to make
            that explicit in the audit).
        audit_conn: Optional open audit connection.
        to_tier: Explicit target tier. Defaults to "drop one tier" (e.g.
            Tier 3 → Tier 2). Cannot go below Tier 0.
        loop_version: Loop version for the audit row.

    Returns:
        Updated :class:`EMConfig`.

    Raises:
        TrustError: if ``to_tier`` is specified and not strictly below the
            current tier (demotion-as-promotion is a bug, not a feature).
    """
    current = em_cfg.current_tier
    if to_tier is None:
        # Drop one tier, floored at 0.
        target_int = max(0, current - 1)
    else:
        try:
            target_int = int(CapabilityTier(int(to_tier)))
        except ValueError as e:
            raise TrustError(
                f"Tier {to_tier} out of range (valid: 0-5; see §4.2)."
            ) from e
        if target_int >= current:
            raise TrustError(
                f"Demotion target {target_int} must be strictly below "
                f"current tier {current}; use `gaia-coder promote` instead."
            )

    updated = em_cfg.model_copy(update={"current_tier": target_int})

    if audit_conn is not None:
        _write_tier_audit(
            conn=audit_conn,
            tool_name="trust.demote",
            from_tier=current,
            to_tier=target_int,
            reason=reason or "no reason given",
            em_handle=em_cfg.em_handle,
            signature=None,
            loop_version=loop_version,
        )

    return updated


# ---------------------------------------------------------------------------
# Convenience: read tier-change history from audit
# ---------------------------------------------------------------------------


def tier_history(
    audit_conn: sqlite3.Connection,
    limit: Optional[int] = None,
) -> list[dict]:
    """Return tier-change audit rows oldest-first, deserialised for display.

    Each entry is a plain dict with ``occurred_at``, ``event`` (``promote`` or
    ``demote``), ``from_tier``, ``to_tier``, ``reason``, and ``em_handle``.
    Used by ``gaia-coder trust --history``.
    """
    rows = audit.list_rows(audit_conn, filter=None)
    out: list[dict] = []
    for r in rows:
        if r.tool_name not in ("trust.promote", "trust.demote"):
            continue
        try:
            args = json.loads(r.args_json)
        except json.JSONDecodeError:
            # A corrupt args_json is a surfacing-worthy anomaly but not
            # worth crashing history-display over; surface the fact loudly.
            args = {"_parse_error": True, "raw": r.args_json}
        out.append(
            {
                "occurred_at": r.occurred_at,
                "event": r.tool_name.removeprefix("trust."),
                "from_tier": args.get("from_tier"),
                "to_tier": args.get("to_tier"),
                "reason": args.get("reason"),
                "em_handle": args.get("em_handle"),
            }
        )
    if limit is not None:
        out = out[-limit:]
    return out
