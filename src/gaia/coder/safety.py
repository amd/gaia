# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT

"""Safety seam for ``gaia-coder`` mutating actions (§4.2 + §7.1 + §15.6).

Every code path that *writes* to disk, runs a CLI tool, opens a PR, or merges
one is supposed to consult the trust contract first:

1. **Capability tier** (§4.2) — a Tier-0 agent may only read; a Tier-1 agent
   may write local files but not open PRs; etc.
2. **Dev-mode** (§7.1) — self-edit of the agent's own source under
   ``src/gaia/coder/`` is forbidden unless dev mode is on.
3. **Repo binding** (§15.6) — ``forbidden_paths`` and ``allowed_branches``
   from ``repo_binding.toml`` are hard rails the agent must never cross.
4. **License gate** (§5.4) — code imported from another repo must be
   permissive (MIT/Apache/BSD/...). Copyleft is rejected.

Before this module landed those four signals were *display only* — nothing in
``self_fix/fixer.py`` or ``self_fix/publisher.py`` actually consulted them
before writing a file or opening a PR. :func:`enforce_action` is the single
seam every mutating tool path must call. The module is deliberately
file-watchable (one module, one entry point) so a future audit can prove the
seam is honoured everywhere it should be.

Fail-loudly per repo ``CLAUDE.md``: every gate failure raises
:class:`ActionDenied` with what failed, what the caller should do, and where
to look next. There is no silent "downgrade to read-only" path.
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Tuple

from gaia.coder import dev_mode as dev_mode_mod
from gaia.coder import repo_binding as repo_binding_mod
from gaia.coder import trust as trust_mod
from gaia.coder.oss_reuse import (
    BLOCKED_LICENSES,
    PERMISSIVE_LICENSES,
)
from gaia.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Action → minimum tier mapping (§4.2)
# ---------------------------------------------------------------------------

#: Explicit per-action tier requirements. Anything not listed falls through to
#: the prefix table below; anything not matched there is **denied** (an unknown
#: action is conservatively treated as too dangerous to allow).
ACTION_MIN_TIER: dict[str, int] = {
    # Tier 0+ (observers can do these).
    "read_file": 0,
    "list_files": 0,
    "search_code": 0,
    "git_status": 0,
    "git_log": 0,
    # Tier 1+ (drafter / local mutation).
    "write_file": 1,
    "edit_file": 1,
    "run_cli": 1,
    # Tier 2+ (branch author / PR-gated).
    "open_pr": 2,
    "open_issue": 2,
    # Tier 3+ (self-maintainer / release).
    "merge_pr": 3,
    "release": 3,
}

#: Prefix fallback for actions not listed explicitly. Order matters: the first
#: matching prefix wins. Mirrors the action verbs called out in the §15.2 tool
#: catalogue plus the gaia-coder REPL's tool names.
_ACTION_PREFIX_TIER: Tuple[Tuple[str, int], ...] = (
    ("read_", 0),
    ("list_", 0),
    ("search_", 0),
    ("write_", 1),
    ("edit_", 1),
    ("git_", 1),
    ("run_", 1),
    ("open_", 2),
    ("comment_", 2),
    ("merge_", 3),
    ("release", 3),
)


def _min_tier_for(action: str) -> Optional[int]:
    """Return the lowest tier that may perform *action*, or ``None`` if unknown.

    ``None`` means "unknown action" — :func:`enforce_action` treats that as a
    denial because the safe default is to refuse rather than guess.
    """
    if action in ACTION_MIN_TIER:
        return ACTION_MIN_TIER[action]
    for prefix, tier in _ACTION_PREFIX_TIER:
        if action.startswith(prefix):
            return tier
    return None


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

#: Repo-relative prefix that identifies a self-edit (i.e. an edit to gaia-coder's
#: own source). Used by the dev-mode gate.
SELF_EDIT_PREFIX: str = "src/gaia/coder/"


@dataclass(frozen=True)
class ActionContext:
    """Everything :func:`enforce_action` needs to make a yes/no decision.

    Attributes:
        action: Verb naming the operation, e.g. ``"write_file"``,
            ``"open_pr"``. Looked up in :data:`ACTION_MIN_TIER` (or prefix
            table) to derive the minimum tier requirement.
        paths: Repo-relative paths the action will touch. May be empty for
            non-file-touching actions (e.g. ``open_pr``).
        branch: For ``open_pr`` / ``merge_pr`` / ``git_push``: the branch the
            action would push to or merge into. Validated against
            ``repo_binding.allowed_branches`` when set.
        license_text: SPDX id (or free-text fallback) for code about to be
            imported via :class:`OSSReuseMixin`. ``None`` skips the license
            check entirely.
        cwd: Working directory the action runs in. Reserved for future
            git-rooted checks; not used today.
    """

    action: str
    paths: Tuple[str, ...] = ()
    branch: Optional[str] = None
    license_text: Optional[str] = None
    cwd: Optional[Path] = None
    # Reserved metadata bag for future signal types (e.g. "tool_name" so
    # audit rows can stay action-agnostic). Frozen dataclass + default_factory
    # keeps construction terse for the common case.
    metadata: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)


class ActionDenied(PermissionError):
    """Raised when :func:`enforce_action` blocks a mutation.

    Subclasses :class:`PermissionError` so callers that already handle
    permission failures (e.g. ``except PermissionError as exc:``) catch this
    naturally. The exception message names *what* was denied and *why*; see
    the docstring on :func:`enforce_action` for the order of checks.
    """


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _config_dir() -> Path:
    """Return the gaia-coder config/state directory.

    Mirrors ``gaia.coder.cli.resolve_config_dir`` (we re-implement to avoid an
    import cycle through ``cli.py``): honour ``GAIA_CODER_HOME`` if set,
    otherwise ``~/.gaia/coder/``. Unlike the CLI helper, we **do not** create
    the directory — the safety check should be read-only.
    """
    override = os.environ.get("GAIA_CODER_HOME")
    if override:
        return Path(override)
    return Path.home() / ".gaia" / "coder"


def _load_em_tier(em_config_path: Optional[Path]) -> int:
    """Return the agent's current tier per ``em.toml``.

    Defaults to **0** when:
      * ``em.toml`` does not exist (unconfigured install);
      * ``em.toml`` exists but the file is unreadable (we surface a log line
        but stay safe — *read* is always allowed at tier 0).

    A genuinely *malformed* ``em.toml`` (TOML parse error) still raises via
    :func:`trust.load_em_config` — that's a configuration bug the EM must fix,
    not something to silently default away. We only catch the "file missing"
    path here.
    """
    cfg_path = em_config_path or (_config_dir() / "em.toml")
    if not cfg_path.exists():
        return 0
    em_cfg = trust_mod.load_em_config(cfg_path)
    return int(em_cfg.current_tier)


def _load_repo_binding(
    repo_binding_path: Optional[Path],
) -> Optional[repo_binding_mod.RepoBinding]:
    """Best-effort load of ``repo_binding.toml``; ``None`` when absent.

    A missing binding is a legitimate state (un-bound install). A *malformed*
    binding raises via :func:`repo_binding.load_repo_binding` — see the
    fail-loudly note on :func:`_load_em_tier`.
    """
    if repo_binding_path is None:
        binding_path = _config_dir() / "repo_binding.toml"
    else:
        binding_path = repo_binding_path
    if not binding_path.exists():
        return None
    return repo_binding_mod.load_repo_binding(binding_path)


def _is_self_edit(path: str) -> bool:
    """Return True iff *path* is inside ``src/gaia/coder/`` (POSIX-normalised).

    Path comparison is purely textual — :func:`enforce_action` runs before any
    write so the file may not exist on disk yet, and we never want to follow
    symlinks for a permission check.
    """
    posix = path.replace("\\", "/")
    return posix.startswith(SELF_EDIT_PREFIX)


def _matches_any(path: str, patterns: list[str]) -> bool:
    """Return True iff *path* matches any of *patterns* (glob, POSIX)."""
    posix = path.replace("\\", "/")
    return any(fnmatch.fnmatchcase(posix, pat) for pat in patterns)


def _branch_allowed(branch: str, allowed: list[str]) -> bool:
    """Return True iff *branch* matches any of *allowed* (glob)."""
    if not allowed:
        # An empty allowlist means "no restriction recorded" — the binding
        # didn't choose to gate branches. We only hard-deny when the EM has
        # explicitly populated ``allowed_branches`` and the request misses.
        return True
    return any(fnmatch.fnmatchcase(branch, pat) for pat in allowed)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def enforce_action(
    ctx: ActionContext,
    *,
    em_config_path: Optional[Path] = None,
    repo_binding_path: Optional[Path] = None,
) -> None:
    """Authorise *ctx* against the trust contract; raise on any denial.

    Order of checks (cheapest first; first failure wins):

    1. **Action → tier**: ``ctx.action`` must map to a minimum tier and the
       agent's current tier (from ``em.toml``) must meet it. Unknown actions
       are denied conservatively. If ``em.toml`` is absent the tier defaults
       to ``0`` — the agent is treated as a fresh install.
    2. **Dev-mode**: any path under :data:`SELF_EDIT_PREFIX` requires
       :func:`gaia.coder.dev_mode.is_enabled` to be ``True``. This is the
       §7.1 invariant — self-edit cannot be enabled by conversation alone on
       a PyPI install.
    3. **Repo-binding ``forbidden_paths``**: any ``ctx.paths`` entry that
       matches a glob in ``repo_binding.forbidden_paths`` is denied.
    4. **Repo-binding ``allowed_branches``**: when ``ctx.branch`` is set and
       the binding has a non-empty ``allowed_branches`` list, the branch must
       glob-match one of the entries.
    5. **License**: when ``ctx.license_text`` is set, it must not be in
       :data:`gaia.coder.oss_reuse.BLOCKED_LICENSES`. Permissive SPDX ids in
       :data:`gaia.coder.oss_reuse.PERMISSIVE_LICENSES` pass; anything else
       (including ``None`` / empty) is denied because §5.4 forbids importing
       code with an unknown license.

    Args:
        ctx: The :class:`ActionContext` describing the operation.
        em_config_path: Override for the path to ``em.toml``. Tests pass a
            ``tmp_path``-rooted file so production state is never read.
        repo_binding_path: Override for the path to ``repo_binding.toml``.
            Same testing rationale.

    Raises:
        ActionDenied: when any gate above fails.
    """
    # 1. Tier check
    min_tier = _min_tier_for(ctx.action)
    if min_tier is None:
        raise ActionDenied(
            f"safety: action {ctx.action!r} is not registered in "
            "ACTION_MIN_TIER (or its prefix table) — refusing to authorise an "
            "unknown action. Add it to gaia.coder.safety.ACTION_MIN_TIER with "
            "the appropriate tier (§4.2) and re-run."
        )
    current_tier = _load_em_tier(em_config_path)
    if current_tier < min_tier:
        raise ActionDenied(
            f"safety: action {ctx.action!r} requires tier ≥ {min_tier} but "
            f"current_tier is {current_tier}. Promote with "
            "`gaia-coder promote --to-tier {min_tier} --em-signature <handle>` "
            "(see §4.2 of docs/plans/coder-agent.mdx)."
        )

    # 2. Dev-mode gate for self-edits
    self_paths = [p for p in ctx.paths if _is_self_edit(p)]
    if self_paths and not dev_mode_mod.is_enabled(em_cfg_path=em_config_path):
        raise ActionDenied(
            f"safety: action {ctx.action!r} touches gaia-coder's own source "
            f"({', '.join(self_paths)}) but dev mode is OFF. Enable it with "
            "`gaia-coder dev-mode enable --reason '<why>'` (§7.1). Note this "
            "requires an editable install; PyPI / Electron installs cannot "
            "self-edit at all."
        )

    # 3 & 4. Repo-binding gates (forbidden paths + allowed branches)
    binding = _load_repo_binding(repo_binding_path)
    if binding is not None:
        if binding.forbidden_paths:
            for p in ctx.paths:
                if _matches_any(p, binding.forbidden_paths):
                    raise ActionDenied(
                        f"safety: path {p!r} matches a forbidden_paths glob in "
                        f"repo_binding.toml ({binding.forbidden_paths!r}). "
                        "These paths are off-limits to gaia-coder by §15.6 "
                        "policy. Edit them by hand or relax the binding."
                    )
        if ctx.branch is not None and not _branch_allowed(
            ctx.branch, binding.allowed_branches
        ):
            raise ActionDenied(
                f"safety: branch {ctx.branch!r} is not in repo_binding "
                f"allowed_branches ({binding.allowed_branches!r}). Either "
                "rename the branch to match one of those globs or relax the "
                "binding (§15.6)."
            )

    # 5. License gate
    if ctx.license_text is not None:
        spdx = ctx.license_text.strip()
        if spdx in BLOCKED_LICENSES:
            raise ActionDenied(
                f"safety: license {spdx!r} is on the blocked list "
                "(GPL/AGPL/LGPL/SSPL/proprietary). §5.4 forbids importing it "
                "into amd/gaia. Choose a permissive source or open an issue "
                "for human review."
            )
        if spdx not in PERMISSIVE_LICENSES:
            raise ActionDenied(
                f"safety: license {spdx!r} is neither permissive nor "
                "explicitly blocked. §5.4 requires an SPDX id from the "
                f"PERMISSIVE_LICENSES allowlist ({sorted(PERMISSIVE_LICENSES)!r}). "
                "If this is a typo (e.g. 'MIT-License' vs 'MIT'), correct it; "
                "if the source genuinely uses an exotic license, escalate to "
                "the EM."
            )

    logger.debug(
        "enforce_action: allowed action=%s tier=%d/%d paths=%d branch=%s",
        ctx.action,
        current_tier,
        min_tier,
        len(ctx.paths),
        ctx.branch,
    )


__all__ = [
    "ACTION_MIN_TIER",
    "ActionContext",
    "ActionDenied",
    "SELF_EDIT_PREFIX",
    "enforce_action",
]
