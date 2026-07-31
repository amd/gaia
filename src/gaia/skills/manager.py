# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Skill discovery, precedence, and progressive disclosure.

**Three roots in v1** (issue #888 trims the spec's five), highest precedence
first:

===  ==========================================  ==========================
 #    Root                                        Notes
===  ==========================================  ==========================
 1    Agent-bundled ``skills/<name>/``            Shipped inside an agent package
 2    ``~/.gaia/skills/<name>/``                  This user, all projects
 3    ``./.claude/skills/`` + ``~/.claude/skills/``  Read-only Claude Code import
===  ==========================================  ==========================

A later root **never** overrides a same-named skill found in an earlier one;
the shadowed copy stays visible via :meth:`SkillManager.shadowed` so precedence
is auditable rather than silent.

Project-local ``./.gaia/skills/`` and the registry-lock root are deferred to a
later phase — do not add them here without updating ``docs/spec/agent-skills``.

Progressive disclosure: :meth:`discover` parses frontmatter only (level 1),
:meth:`load` adds the Markdown body (level 2), and :meth:`resource_path`
resolves bundled files on demand (level 3).
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from gaia.logger import get_logger
from gaia.skills.errors import (
    DOCS_URL,
    SkillError,
    SkillNotFoundError,
    SkillValidationError,
)
from gaia.skills.format import (
    SKILL_FILENAME,
    Skill,
    parse_skill_file,
    parse_skill_metadata,
)

log = get_logger(__name__)

#: Discovery-root labels, highest precedence first.
ROOT_AGENT_BUNDLED = "agent-bundled"
ROOT_USER = "user"
ROOT_CLAUDE_IMPORT = "claude-import"

#: Environment override for the user root's parent (shared with the rest of GAIA).
GAIA_CONFIG_DIR_ENV = "GAIA_CONFIG_DIR"


@dataclass(frozen=True)
class SkillRoot:
    """One discovery location, with its precedence label and write policy."""

    label: str
    path: Path
    read_only: bool = False

    def __str__(self) -> str:
        return f"{self.label}:{self.path}"


def user_skills_dir() -> Path:
    """Return ``~/.gaia/skills`` (honoring ``GAIA_CONFIG_DIR``).

    Read at call time, not import time, so tests can point it at a tmp_path
    without reloading the module.
    """
    base = os.getenv(GAIA_CONFIG_DIR_ENV) or str(Path.home() / ".gaia")
    return Path(base) / "skills"


def claude_skills_dirs(project_dir: Optional[Path] = None) -> list[Path]:
    """Return the read-only Claude Code import roots (project, then user)."""
    project = Path(project_dir) if project_dir is not None else Path.cwd()
    return [project / ".claude" / "skills", Path.home() / ".claude" / "skills"]


class SkillManager:
    """Discovers skills across the v1 roots and loads them by precedence."""

    def __init__(
        self,
        *,
        agent_skill_dirs: Optional[Iterable[Path | str]] = None,
        user_skills_root: Optional[Path | str] = None,
        claude_skill_dirs: Optional[Iterable[Path | str]] = None,
        project_dir: Optional[Path | str] = None,
        include_claude_roots: bool = True,
    ) -> None:
        """Build a manager over the v1 discovery roots.

        Args:
            agent_skill_dirs: Agent-bundled ``skills/`` directories (precedence 1).
            user_skills_root: Overrides ``~/.gaia/skills`` (precedence 2).
            claude_skill_dirs: Overrides the ``.claude/skills`` roots (precedence 3).
            project_dir: Working directory used to locate ``./.claude/skills``.
            include_claude_roots: Set False to skip the read-only import roots.
        """
        self._agent_dirs = [Path(p) for p in (agent_skill_dirs or [])]
        self._user_root = (
            Path(user_skills_root) if user_skills_root is not None else None
        )
        self._project_dir = Path(project_dir) if project_dir is not None else None
        self._include_claude = include_claude_roots
        self._claude_dirs = (
            [Path(p) for p in claude_skill_dirs]
            if claude_skill_dirs is not None
            else None
        )

        self._lock = threading.RLock()
        self._metadata: Optional[dict[str, Skill]] = None
        self._shadowed: dict[str, list[Skill]] = {}
        self._errors: dict[str, str] = {}
        self._watchers: list = []
        self._on_change: Optional[Callable[[], None]] = None

    # ------------------------------------------------------------------
    # Roots
    # ------------------------------------------------------------------

    @property
    def user_root(self) -> Path:
        """The writable user root — where ``gaia skill import`` installs."""
        return self._user_root if self._user_root is not None else user_skills_dir()

    @property
    def roots(self) -> list[SkillRoot]:
        """Every discovery root, highest precedence first."""
        roots = [
            SkillRoot(ROOT_AGENT_BUNDLED, Path(d), read_only=True)
            for d in self._agent_dirs
        ]
        roots.append(SkillRoot(ROOT_USER, self.user_root, read_only=False))
        if self._include_claude:
            claude = (
                self._claude_dirs
                if self._claude_dirs is not None
                else claude_skills_dirs(self._project_dir)
            )
            roots.extend(
                SkillRoot(ROOT_CLAUDE_IMPORT, Path(d), read_only=True) for d in claude
            )
        return roots

    # ------------------------------------------------------------------
    # Discovery (progressive disclosure level 1 — metadata only)
    # ------------------------------------------------------------------

    def discover(self, *, force: bool = False) -> dict[str, Skill]:
        """Scan every root and return ``{name: metadata-only Skill}``.

        Results are cached; pass ``force=True`` (or call :meth:`reload`) to
        rescan. Frontmatter only — bodies are not read here.
        """
        with self._lock:
            if self._metadata is not None and not force:
                return dict(self._metadata)

            found: dict[str, Skill] = {}
            shadowed: dict[str, list[Skill]] = {}
            errors: dict[str, str] = {}

            for root in self.roots:
                if not root.path.is_dir():
                    log.debug("Skill root %s does not exist — skipping", root)
                    continue
                log.debug("Scanning skill root %s", root)
                for entry in sorted(root.path.iterdir()):
                    if not entry.is_dir() or not (entry / SKILL_FILENAME).is_file():
                        continue
                    try:
                        skill = parse_skill_metadata(
                            entry, root=root.label, read_only=root.read_only
                        )
                    except SkillError as exc:
                        errors[str(entry)] = str(exc)
                        log.error("Skipping invalid skill at %s: %s", entry, exc)
                        continue

                    if skill.name in found:
                        shadowed.setdefault(skill.name, []).append(skill)
                        log.debug(
                            "Skill '%s' in %s is shadowed by the copy in root '%s' "
                            "(higher precedence)",
                            skill.name,
                            root,
                            found[skill.name].root,
                        )
                        continue
                    found[skill.name] = skill

            self._metadata = found
            self._shadowed = shadowed
            self._errors = errors
            log.info(
                "Discovered %d skill(s) across %d root(s); %d shadowed, %d invalid",
                len(found),
                len(self.roots),
                sum(len(v) for v in shadowed.values()),
                len(errors),
            )
            return dict(found)

    def reload(self) -> dict[str, Skill]:
        """Drop the discovery cache and rescan every root."""
        return self.discover(force=True)

    def list_skills(self) -> list[Skill]:
        """Every discovered skill's metadata, sorted by name."""
        return sorted(self.discover().values(), key=lambda s: s.name)

    @property
    def discovery_errors(self) -> dict[str, str]:
        """``{skill_dir: message}`` for directories that failed to parse.

        Surfaced by ``gaia skill list`` (and logged at ERROR) so a malformed
        skill is visibly broken rather than quietly missing.
        """
        self.discover()
        return dict(self._errors)

    def shadowed(self, name: Optional[str] = None) -> list[Skill]:
        """Lower-precedence copies that lost to a same-named skill."""
        self.discover()
        if name is not None:
            return list(self._shadowed.get(name, []))
        return [s for copies in self._shadowed.values() for s in copies]

    # ------------------------------------------------------------------
    # Resolution + loading
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill:
        """Return the metadata-only skill for ``name`` (level 1)."""
        skills = self.discover()
        if name not in skills:
            raise SkillNotFoundError(self._not_found_message(name))
        return skills[name]

    def resolve_path(self, name: str) -> Path:
        """Return the winning skill's directory."""
        skill = self.get(name)
        directory = skill.directory
        if directory is None:  # pragma: no cover - discovery always sets a path
            raise SkillNotFoundError(self._not_found_message(name))
        return directory

    def load(self, name: str) -> Skill:
        """Fully parse the winning skill, body included (level 2).

        Raises:
            SkillNotFoundError: no root contains a skill of that name.
            SkillValidationError: the skill exists but fails validation.
        """
        metadata = self.get(name)
        assert metadata.path is not None  # discovery always sets it
        skill = parse_skill_file(
            metadata.path, root=metadata.root, read_only=metadata.read_only
        )
        log.debug(
            "Loaded skill '%s' from root '%s' (%s, tier=%s, %d tool(s))",
            skill.name,
            skill.root,
            skill.path,
            skill.security_tier,
            len(skill.gaia.tools),
        )
        return skill

    def resource_path(self, name: str, relative: str) -> Path:
        """Resolve a bundled resource inside a skill (level 3).

        Raises:
            SkillValidationError: if ``relative`` escapes the skill directory or
                names a file the skill does not ship.
        """
        directory = self.resolve_path(name).resolve()
        candidate = (directory / relative).resolve()
        if candidate != directory and directory not in candidate.parents:
            raise SkillValidationError(
                f"Resource {relative!r} escapes skill '{name}' at {directory}. A skill "
                "may only read files inside its own directory."
            )
        if not candidate.exists():
            raise SkillValidationError(
                f"Skill '{name}' has no resource {relative!r} (looked in {directory}). "
                "Check the path referenced by the skill body."
            )
        return candidate

    # ------------------------------------------------------------------
    # Hot reload
    # ------------------------------------------------------------------

    def start_watching(self, on_change: Optional[Callable[[], None]] = None) -> int:
        """Watch every existing root and drop the cache on any change.

        Args:
            on_change: Optional callback fired after the cache is invalidated.

        Returns:
            The number of roots actually being watched (missing roots are
            skipped — a root that appears later is picked up on the next
            explicit :meth:`reload`).
        """
        from gaia.utils.file_watcher import WATCHDOG_AVAILABLE, FileWatcher

        if not WATCHDOG_AVAILABLE:
            raise SkillError(
                "Skill hot-reload needs the 'watchdog' package, which is not "
                "installed. Install it with: pip install 'watchdog>=2.1.0' (or "
                "uv pip install -e '.[dev]'), or call SkillManager.reload() manually."
            )

        self._on_change = on_change
        self.stop_watching()
        for root in self.roots:
            if not root.path.is_dir():
                continue
            watcher = FileWatcher(
                directory=root.path,
                on_created=self._handle_change,
                on_modified=self._handle_change,
                on_deleted=self._handle_change,
                extensions=[".md", ".py"],
                recursive=True,
            )
            watcher.start()
            self._watchers.append(watcher)
            log.info("Watching skill root %s for changes", root)
        return len(self._watchers)

    def stop_watching(self) -> None:
        """Stop every root watcher. Safe to call when not watching."""
        for watcher in self._watchers:
            watcher.stop()
        self._watchers = []

    @property
    def is_watching(self) -> bool:
        """True while at least one root watcher is running."""
        return any(w.is_running for w in self._watchers)

    def _handle_change(self, path: str) -> None:
        """Invalidate the discovery cache after a filesystem event."""
        log.info("Skill root change detected (%s) — invalidating discovery cache", path)
        with self._lock:
            self._metadata = None
        if self._on_change is not None:
            self._on_change()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _not_found_message(self, name: str) -> str:
        known = sorted(self.discover())
        roots = ", ".join(str(r.path) for r in self.roots)
        known_text = ", ".join(known) if known else "(none discovered)"
        message = (
            f"No skill named {name!r}. Searched: {roots}. Available: {known_text}. "
            f"Create one with 'gaia skill create {name}', import an existing folder "
            f"with 'gaia skill import <path>', or run 'gaia skill list'. See {DOCS_URL}"
        )
        if self._errors:
            message += (
                f" Note: {len(self._errors)} skill folder(s) failed to parse — run "
                "'gaia skill list' to see them."
            )
        return message


_DEFAULT_MANAGER: Optional[SkillManager] = None
_DEFAULT_LOCK = threading.Lock()


def get_default_manager() -> SkillManager:
    """Return the process-wide manager over the default roots."""
    global _DEFAULT_MANAGER  # pylint: disable=global-statement
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = SkillManager()
        return _DEFAULT_MANAGER


def reset_default_manager() -> None:
    """Drop the process-wide manager (used by tests and by root changes)."""
    global _DEFAULT_MANAGER  # pylint: disable=global-statement
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is not None:
            _DEFAULT_MANAGER.stop_watching()
        _DEFAULT_MANAGER = None
