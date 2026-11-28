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
     + enabled Claude Code plugins' skills       (named ``<plugin>:<skill>``)
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

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from gaia.config import gaia_home
from gaia.logger import get_logger
from gaia.skills.errors import (
    DOCS_URL,
    SkillError,
    SkillNotFoundError,
    SkillValidationError,
)
from gaia.skills.format import (
    NAME_PATTERN,
    SKILL_FILENAME,
    Skill,
    parse_project_skill_file,
    parse_skill_file,
    parse_skill_metadata,
)

log = get_logger(__name__)

#: Discovery-root labels, highest precedence first.
ROOT_AGENT_BUNDLED = "agent-bundled"
ROOT_USER = "user"
ROOT_CLAUDE_IMPORT = "claude-import"
#: The ``.claude/skills`` of the project the agent was started in. Listed in the
#: skill catalogue, unlike ``claude-import``, and parsed the way Claude Code
#: parses it (:func:`gaia.skills.format.parse_project_skill_file`).
ROOT_CLAUDE_PROJECT = "claude-project"
#: Both ``.claude/skills`` labels: another host's skills, never GAIA-audited.
CLAUDE_ROOTS = frozenset({ROOT_CLAUDE_IMPORT, ROOT_CLAUDE_PROJECT})

# Invalid third-party imports already warned about; discovery reruns every turn.
_WARNED_IMPORT_ERRORS: set[tuple[str, str]] = set()
_WARNED_LOCK = threading.Lock()


def _warn_once(where: str, message: str) -> None:
    key = (where, message)
    with _WARNED_LOCK:
        first = key not in _WARNED_IMPORT_ERRORS
        _WARNED_IMPORT_ERRORS.add(key)
    (log.warning if first else log.debug)("%s: %s", where, message)


def _warn_invalid_import_once(entry: Path, message: str) -> None:
    _warn_once(f"Skipping invalid imported skill at {entry}", message)


@dataclass(frozen=True)
class SkillRoot:
    """One discovery location, with its precedence label and write policy."""

    label: str
    path: Path
    read_only: bool = False
    #: A Claude Code plugin's name: its skills are named ``<plugin>:<skill>``,
    #: and ``path`` may itself be a skill folder.
    plugin: Optional[str] = None

    def __str__(self) -> str:
        return f"{self.label}:{self.path}"


def user_skills_dir() -> Path:
    """Return ``<gaia_home>/skills`` (honoring ``GAIA_HOME`` and ``GAIA_CONFIG_DIR``).

    Read at call time, not import time, so tests can point it at a tmp_path
    without reloading the module.
    """
    return gaia_home() / "skills"


def _parse(entry: Path, root: SkillRoot, *, body: bool = False) -> Skill:
    """Parse *entry*, named as its root names it; *body* also reads instructions."""
    if root.plugin is not None:
        # A one-skill plugin's folder is its version (humanizer/3.1.0), not its name.
        skill = parse_project_skill_file(
            entry, root=root.label, read_only=True, check_directory_name=False
        )
        skill.name = f"{root.plugin}:{skill.name}"
        return skill
    if root.label == ROOT_CLAUDE_PROJECT:
        return parse_project_skill_file(entry, root=root.label, read_only=True)
    parse = parse_skill_file if body else parse_skill_metadata
    return parse(entry, root=root.label, read_only=root.read_only)


def default_project_dir() -> Optional[Path]:
    """The project an agent started here would map, so tools list its skills alike."""
    from gaia.agents.base.project_map import resolve_project_root

    root = resolve_project_root()
    return Path(root) if root else None


def claude_skills_dirs(project_dir: Optional[Path] = None) -> list[Path]:
    """Return the read-only Claude Code import roots (project, then user)."""
    project = Path(project_dir) if project_dir is not None else Path.cwd()
    return [project / ".claude" / "skills", Path.home() / ".claude" / "skills"]


def _json_object(path: Path, key: str) -> dict:
    """``path``'s top-level ``key``, ``{}`` when either is missing.

    Raises:
        OSError, ValueError: the file is unreadable or not that shape.
    """
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(value, (dict, type(None))):
        raise ValueError(f"'{key}' is not a JSON object")
    return value or {}


def _plugin_skill_folders(base: Path, manifest: dict) -> list[Path]:
    """``skills/``, the folders ``manifest["skills"]`` adds, else a root ``SKILL.md``."""
    extra = manifest.get("skills") or []
    extra = [extra] if isinstance(extra, str) else extra
    if not extra and not (base / "skills").is_dir():
        extra = ["."] if (base / SKILL_FILENAME).is_file() else []
    folders: list[Path] = []
    for folder in [base / "skills", *(base / str(p) for p in extra)]:
        resolved = folder.resolve()
        inside = resolved == base.resolve() or base.resolve() in resolved.parents
        if inside and resolved.is_dir() and resolved not in folders:
            folders.append(resolved)
    return folders


def claude_plugin_skill_roots() -> list[SkillRoot]:
    """The skill folders of the Claude Code plugins enabled for this user.

    Read as Claude Code reads them: ``~/.claude/plugins/installed_plugins.json``
    names each user-scope install, ``enabledPlugins`` in
    ``~/.claude/settings.json`` (else the plugin's ``defaultEnabled``) says
    whether it is on, and its skills are ``skills/<name>/SKILL.md``, the
    folders its manifest's ``skills`` adds, or a lone ``SKILL.md`` at its root.
    No registry means no plugins; an unreadable file is warned about, naming it.
    """
    claude = Path.home() / ".claude"
    registry = claude / "plugins" / "installed_plugins.json"
    settings = claude / "settings.json"
    where = registry
    try:
        installs = _json_object(registry, "plugins")
        where = settings
        enabled = _json_object(settings, "enabledPlugins")
    except (OSError, ValueError) as exc:
        _warn_once(f"Not listing Claude Code plugin skills: {where}", str(exc))
        return []

    roots: list[SkillRoot] = []
    for key, records in installs.items():
        install = next(
            (
                r
                for r in (records if isinstance(records, list) else [])
                if isinstance(r, dict)
                and r.get("scope", "user") == "user"
                and isinstance(r.get("installPath"), str)
            ),
            None,
        )
        if install is None:
            continue
        base = Path(install["installPath"])
        manifest_file = base / ".claude-plugin" / "plugin.json"
        try:
            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            manifest = {}
        except (OSError, ValueError) as exc:
            _warn_once(f"Skipping Claude Code plugin {key}", f"{manifest_file}: {exc}")
            continue
        manifest = manifest if isinstance(manifest, dict) else {}
        if not enabled.get(key, manifest.get("defaultEnabled", True)):
            continue
        name = manifest.get("name") or str(key).split("@", 1)[0]
        if not isinstance(name, str) or not NAME_PATTERN.match(name):
            _warn_once(
                f"Skipping Claude Code plugin {key}",
                f"its name {name!r} is not lowercase letters, digits and hyphens",
            )
            continue
        roots.extend(
            SkillRoot(ROOT_CLAUDE_IMPORT, folder, read_only=True, plugin=name)
            for folder in _plugin_skill_folders(base, manifest)
        )
    return roots


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
        excluded_names: Optional[Iterable[str]] = None,
    ) -> None:
        """Build a manager over the v1 discovery roots.

        Args:
            agent_skill_dirs: Agent-bundled ``skills/`` directories (precedence 1).
            user_skills_root: Overrides ``~/.gaia/skills`` (precedence 2).
            claude_skill_dirs: Overrides the ``.claude/skills`` roots (precedence 3),
                Claude Code plugins' skills included.
            project_dir: The project root. Its ``.claude/skills`` is labelled
                ``claude-project`` and listed in the catalogue; when omitted the
                working directory's is used, as an unlisted ``claude-import``.
            include_claude_roots: Set False to skip the read-only import roots.
            excluded_names: Host-disabled skills, omitted from all discovery roots.
        """
        self._excluded_names = frozenset(excluded_names or ())
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
        #: Claude Code plugins' skill folders, read once per discovery.
        self._plugin_roots: Optional[list[SkillRoot]] = None
        self._shadowed: dict[str, list[Skill]] = {}
        #: The root each discovered skill won from, so :meth:`load` names it alike.
        self._winning_roots: dict[str, SkillRoot] = {}
        self._errors: dict[str, str] = {}
        self._import_errors: dict[str, str] = {}
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
            project_skills = (
                claude_skills_dirs(self._project_dir)[0]
                if self._project_dir is not None and self._claude_dirs is None
                else None
            )
            roots.extend(
                SkillRoot(
                    ROOT_CLAUDE_PROJECT if d == project_skills else ROOT_CLAUDE_IMPORT,
                    Path(d),
                    read_only=True,
                )
                for d in claude
            )
            if self._claude_dirs is None:
                if self._plugin_roots is None:
                    self._plugin_roots = claude_plugin_skill_roots()
                roots.extend(self._plugin_roots)
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
            self._plugin_roots = None

            found: dict[str, Skill] = {}
            winning_roots: dict[str, SkillRoot] = {}
            shadowed: dict[str, list[Skill]] = {}
            errors: dict[str, str] = {}
            import_errors: dict[str, str] = {}

            roots = self.roots
            for root in roots:
                if not root.path.is_dir():
                    log.debug("Skill root %s does not exist — skipping", root)
                    continue
                log.debug("Scanning skill root %s", root)
                one_skill = (
                    root.plugin is not None and (root.path / SKILL_FILENAME).is_file()
                )
                for entry in [root.path] if one_skill else sorted(root.path.iterdir()):
                    if not entry.is_dir() or not (entry / SKILL_FILENAME).is_file():
                        continue
                    try:
                        skill = _parse(entry, root)
                        skill.body = ""
                    except SkillError as exc:
                        errors[str(entry)] = str(exc)
                        if root.label in CLAUDE_ROOTS:
                            import_errors[str(entry)] = str(exc)
                            _warn_invalid_import_once(entry, str(exc))
                        else:
                            log.error("Skipping invalid skill at %s: %s", entry, exc)
                        continue

                    if skill.name in self._excluded_names:
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
                    winning_roots[skill.name] = root

            self._metadata = found
            self._winning_roots = winning_roots
            self._shadowed = shadowed
            self._errors = errors
            self._import_errors = import_errors
            log.info(
                "Discovered %d skill(s) across %d root(s); %d shadowed, %d invalid",
                len(found),
                len(roots),
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

        Surfaced by ``gaia skill list`` (and logged — at ERROR, or once at
        WARNING for a Claude import) so a malformed skill is visibly broken
        rather than quietly missing.
        """
        self.discover()
        return dict(self._errors)

    @property
    def import_discovery_errors(self) -> dict[str, str]:
        """The subset of :attr:`discovery_errors` from read-only Claude imports.

        Those folders belong to another tool, so ``gaia skill list`` reports
        them as a warning instead of failing.
        """
        self.discover()
        return dict(self._import_errors)

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

        A skill from the user root is checked against ``skill-lock.json`` here —
        the single chokepoint every consumer (``Agent.load_skill``, ``gaia skill
        info``) passes through — so a signature-backed install whose files
        changed cannot be handed to a model under a tier it no longer earns.

        Raises:
            SkillNotFoundError: no root contains a skill of that name.
            SkillValidationError: the skill exists but fails validation.
            SkillDriftError: the skill was installed at ``community`` /
                ``verified`` and no longer matches what the lock recorded.
        """
        with self._lock:
            metadata = self.get(name)
            root = self._winning_roots[name]
        assert metadata.path is not None  # discovery always sets it
        skill = _parse(metadata.path, root, body=True)
        if metadata.root == ROOT_USER:
            # Deferred: keeps the marketplace modules off the import path of
            # every agent that merely composes a bundled skill.
            from gaia.skills.drift import assert_no_fatal_drift

            assert_no_fatal_drift(self.user_root, skill)
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
                "installed. Install it with: pip install 'watchdog>=2.1.0,<7' (or "
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
