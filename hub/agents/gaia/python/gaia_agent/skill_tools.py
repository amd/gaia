# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
# pylint: disable=protected-access

"""Runtime access to the skill library — discover, install, load, unload.

Makes the skill library reachable *from the conversation*: the model can see
what is installed, search the hub, install into ``~/.gaia/skills``, and activate
or deactivate a skill mid-session with the system prompt rebuilt underneath it —
no manifest edit, no restart.

Everything here is a thin, model-facing surface over machinery that already
exists — :class:`~gaia.skills.manager.SkillManager` for discovery,
:func:`gaia.skills.hub.search_skills` for the hub lane,
:func:`gaia.skills.install.install_skill` for the trust path, and the base
``Agent.load_skill`` / ``unload_skill`` for activation. Nothing is
reimplemented, and in particular **no check is re-derived**: an install from
here runs the same signature → tier → permission gauntlet as
``gaia skill install``.

Four deliberate refusals
------------------------
Trust decisions stay with the human, so these fail closed rather than prompting
a model that would happily approve itself:

* ``install_skill`` refuses an **experimental** (unsigned, unaudited) skill. Its
  ``tools.py`` would be imported into this process with this agent's access, so
  the opt-in belongs at a terminal (``gaia skill install <name>
  --allow-experimental``).
* ``install_skill`` refuses a **dangerous permission** grant — it passes a
  confirmer that always says no, so the interactive ``input()`` prompt in
  :mod:`gaia.skills.install` can never be answered by the chat stream.
* ``load_skill`` refuses a **code-bearing skill from ``.claude/skills``**. That
  root is a read-only import of another marketplace that GAIA's install path
  never signature-, tier-, or audit-checked, and the shared loader has no tier
  gate of its own (verified, not assumed) — so it would import that Python on a
  model-issued call. Instruction-only imports still load.
* Every disk-touching tool refuses a **name that is not a bare skill name**.
  ``gaia.skills.install.remove_skill`` joins the caller's string onto the skills
  root and ``rmtree``\\ s it unvalidated, so ``../notes`` escapes the root. That
  is a substrate flaw reported upstream; until it is fixed there, these tools
  will not hand it a path.

Each surfaces as an error naming what would proceed instead.

Loading stays opt-in
--------------------
Registering these tools does not load anything. Skill bodies cost prompt tokens
(#2848), so the agent still starts with no skill set active; the model — or the
user through it — has to ask.
"""

from __future__ import annotations

from typing import Any, Dict, List

from gaia.logger import get_logger

logger = get_logger(__name__)

#: Registry names of every tool this mixin registers, in registration order.
#: ``gaia-agent.yaml``'s ``tools_count`` includes these, so this tuple is the one
#: place that has to change when the surface grows.
SKILL_LIBRARY_TOOL_NAMES = (
    "list_skills",
    "search_skill_hub",
    "install_skill",
    "remove_skill",
    "load_skill",
    "unload_skill",
    "skill_status",
)

#: Same 4-chars-per-token estimate the rest of GAIA uses (``ApiAgent.estimate_tokens``).
_CHARS_PER_TOKEN = 4

_DOCS_URL = "https://amd-gaia.ai/docs/spec/agent-skills"


def estimate_prompt_tokens(text: str) -> int:
    """Approximate prompt tokens for *text* (4 chars ≈ 1 token)."""
    return len(text) // _CHARS_PER_TOKEN


def _failure(action: str, exc: Exception) -> Dict[str, Any]:
    """Render a skills-runtime error for the model, message intact.

    ``gaia.skills.errors`` guarantees every message names what failed, what to
    do, and where to look, so it is surfaced verbatim rather than summarized.
    """
    logger.warning("%s failed: %s: %s", action, type(exc).__name__, exc)
    return {
        "status": "error",
        "action": action,
        "error_type": type(exc).__name__,
        "error": str(exc),
    }


def _reject_bad_name(action: str, name: str) -> Dict[str, Any] | None:
    """Refuse anything that is not a bare skill name; ``None`` when it is fine.

    ``remove_skill`` in the substrate does ``rmtree(root / name)`` with no
    validation, so a model-supplied ``../documents`` deletes outside the skills
    root. Validated against the canonical ``NAME_PATTERN`` rather than a
    hand-rolled check, so the two can't drift.
    """
    from gaia.skills.format import NAME_PATTERN

    text = (name or "").strip()
    if text and NAME_PATTERN.match(text):
        return None
    logger.warning("%s: refusing skill name %r", action, name)
    return {
        "status": "error",
        "action": action,
        "error": (
            f"{name!r} is not a skill name. A skill name is lowercase letters, "
            "digits, and internal hyphens — no slashes, no '..', no path. Pass "
            "the name exactly as list_skills or search_skill_hub reported it, "
            f"e.g. 'web-research'. See {_DOCS_URL}"
        ),
    }


def _refuse_ungated_code(skill: Any) -> Dict[str, Any]:
    """Refuse to import code from a root GAIA's install path never checked.

    ``.claude/skills`` is a read-only import of somebody else's marketplace: no
    signature, no tier, no audit ran on it, yet ``register_skill_tools`` will
    import its ``tools.py`` into this process (verified — the base loader has no
    tier gate). Declaring the skill in ``gaia-agent.yaml``, or importing it into
    ``~/.gaia/skills``, keeps that decision with the human where it belongs.
    """
    logger.warning(
        "Refusing model-issued load of '%s': it ships tool(s) %s from the "
        "un-gated '%s' root",
        skill.name,
        ", ".join(skill.tool_names),
        skill.root,
    )
    return {
        "status": "error",
        "action": "load_skill",
        "error_type": "SkillPermissionError",
        "error": (
            f"Refusing to load '{skill.name}': it provides tool(s) "
            f"{', '.join(skill.tool_names)}, which means importing its Python "
            f"into this agent's own process, and it comes from "
            f"{skill.directory} — a read-only .claude/skills import that GAIA's "
            "install path never signature-, tier-, or audit-checked. Ask the "
            f"user to run 'gaia skill import {skill.directory}' to bring it "
            "under ~/.gaia/skills deliberately, or to add it to the agent's "
            f"gaia-agent.yaml. Instruction-only imported skills still load "
            f"normally. See {_DOCS_URL}"
        ),
    }


def _describe(skill: Any, *, loaded: bool) -> Dict[str, Any]:
    """One skill as the model sees it: what it is, where from, how trusted."""
    return {
        "name": skill.name,
        "description": skill.description,
        "version": skill.version or "",
        "root": skill.root or "",
        "security_tier": skill.security_tier,
        "provides_tools": list(skill.tool_names),
        "permissions": list(skill.gaia.permissions),
        "loaded": loaded,
    }


class SkillLibraryToolsMixin:
    """Agent-callable discovery, install, and activation for SKILL.md skills.

    Compose it onto an agent and call :meth:`register_skill_library_tools` from
    ``_register_tools``. The mixin holds no state of its own — the loaded set
    lives on the agent (``Agent.loaded_skills``) and the installed set lives on
    disk, so the tools stay a view over the real thing.
    """

    def register_skill_library_tools(self) -> None:
        """Register the seven skill-library tools onto this agent.

        Call this **before** ``super()._register_tools()``: ChatAgent's
        registration ends by snapshotting the global registry, and a tool added
        after that snapshot never reaches the composed prompt.
        """
        from gaia.agents.base.tools import tool

        agent = self

        # ------------------------------------------------------------------
        # Discovery
        # ------------------------------------------------------------------

        @tool(atomic=True)
        def list_skills() -> dict:
            """List the skills available on this machine, loaded or not.

            Call this before load_skill to see what you can activate. Covers
            every discovery root: skills bundled with this agent, skills
            installed into ~/.gaia/skills, and skills imported from
            .claude/skills. It does not touch the network — use
            search_skill_hub to find skills that are not installed yet.

            Presenting the result: give the user a markdown list, one skill per
            line, loaded ones first — never a comma-separated run. Thirty names
            in a paragraph wrap mid-word (``testing-`` / ``the-`` / ``gaia-agent``
            on three lines) and cannot be scanned. Wrap each name in backticks so
            it is never broken at its hyphens.

            Returns:
                Dictionary with the skill list (name, description, version,
                origin root, security tier, tools it provides, and whether it
                is currently loaded), the roots searched, and any skill folder
                that failed to parse.
            """
            from gaia.skills.errors import SkillError

            manager = agent.skill_manager
            try:
                # Rescan rather than serve the cache: a skill the user dropped
                # in by hand between turns must show up.
                discovered = manager.reload()
            except SkillError as exc:
                return _failure("list_skills", exc)

            loaded = set(agent.loaded_skills)
            skills = [
                _describe(skill, loaded=skill.name in loaded)
                for skill in sorted(discovered.values(), key=lambda s: s.name)
            ]
            payload: Dict[str, Any] = {
                "status": "success",
                "count": len(skills),
                "skills": skills,
                "loaded": sorted(loaded),
                "roots_searched": [str(root.path) for root in manager.roots],
            }
            invalid = manager.discovery_errors
            if invalid:
                payload["invalid"] = invalid
                payload["warning"] = (
                    f"{len(invalid)} skill folder(s) failed to parse and were "
                    "skipped — they are listed under 'invalid'. Tell the user; "
                    "a broken skill is missing, not absent."
                )
            if not skills:
                payload["hint"] = (
                    "No skills are installed. Use search_skill_hub to see what "
                    f"is published, then install_skill to add one. See {_DOCS_URL}"
                )
            return payload

        @tool(atomic=True)
        def search_skill_hub(query: str = "") -> dict:
            """Search the GAIA Agent Hub for skills you could install.

            Needs network access. This only reads the catalog — nothing is
            downloaded, installed, or activated. Use install_skill to add one
            of the results.

            Args:
                query: Words to match against skill name, description, and the
                    tool names a skill provides. Leave empty to list every
                    published skill.

            Returns:
                Dictionary with the matching hub entries (id, name,
                description, version, security tier, and whether that skill is
                already installed here) plus a warning when the catalog came
                from the offline cache and may be stale.
            """
            from gaia.skills.errors import SkillError
            from gaia.skills.hub import search_skills

            try:
                found = search_skills(query)
            except SkillError as exc:
                return _failure("search_skill_hub", exc)

            installed = set(agent.skill_manager.discover())
            entries: List[Dict[str, Any]] = []
            for entry in found.entries:
                metadata = entry.get("skill_metadata") or {}
                name = str(entry.get("id") or entry.get("name") or "")
                entries.append(
                    {
                        "name": name,
                        "title": entry.get("name") or name,
                        "description": entry.get("description") or "",
                        # Catalog entries carry latest_version; ``version`` is the
                        # per-agent-package spelling.
                        "version": entry.get("latest_version")
                        or entry.get("version")
                        or "",
                        "security_tier": entry.get("security_tier")
                        or metadata.get("security_tier")
                        or "",
                        "installed": name in installed,
                    }
                )

            payload: Dict[str, Any] = {
                "status": "success",
                "query": query,
                "count": len(entries),
                "results": entries,
            }
            if found.offline:
                payload["warning"] = (
                    "The hub was unreachable, so these results come from the "
                    f"offline catalog cache generated {found.generated_at or 'at an unknown time'}. "
                    "A skill published since then is missing, and one listed "
                    "here may have been unpublished. Say so before acting on it."
                )
            return payload

        # ------------------------------------------------------------------
        # Install / remove
        # ------------------------------------------------------------------

        @tool
        def install_skill(name: str, version: str = "*") -> dict:
            """Download a skill from the Agent Hub into ~/.gaia/skills.

            The user is asked to approve this before it runs. It then goes
            through the hub's full trust path — checksum, signature, trust
            tier, and permission ceiling. Any check that fails refuses the
            install and leaves nothing behind. Installing does not activate the
            skill: call load_skill afterwards.

            Two refusals you cannot override from here, by design. Report them
            to the user and stop; do not look for a way around them.
              - An unsigned ("experimental") skill needs the user to run
                `gaia skill install <name> --allow-experimental` in a terminal,
                because its code would run inside this agent's own process.
              - A skill requesting a dangerous permission needs the user to
                grant it with `gaia skill install <name> --yes`.

            Args:
                name: Skill name exactly as search_skill_hub reported it.
                version: SemVer version or range to pin, e.g. "1.2.0" or
                    "^1.0". The default "*" installs the newest published
                    version.

            Returns:
                Dictionary with the installed version, path, the trust tier it
                actually landed on (which may be lower than the tier it
                claimed), and the permissions it declares.
            """
            from gaia.skills.errors import SkillError
            from gaia.skills.install import install_skill as _hub_install

            rejection = _reject_bad_name("install_skill", name)
            if rejection is not None:
                return rejection
            skill_name = name.strip()
            pin = (version or "*").strip() or "*"
            reference = skill_name if pin == "*" else f"{skill_name}@{pin}"

            def _refuse_dangerous_grant(prompt: str) -> bool:
                """Never grant a dangerous permission on the model's say-so."""
                logger.warning(
                    "Refusing dangerous-permission grant requested during an "
                    "agent-driven install: %s",
                    prompt,
                )
                return False

            try:
                result = _hub_install(
                    reference,
                    manager=agent.skill_manager,
                    confirm=_refuse_dangerous_grant,
                )
            except SkillError as exc:
                return _failure("install_skill", exc)

            payload: Dict[str, Any] = {
                "status": "success",
                "name": result.name,
                "version": result.version,
                "requested": result.requested,
                "path": str(result.path),
                "security_tier": result.installed_tier,
                "permissions": list(result.permissions),
                "next_step": (
                    f"Installed but not active. Call load_skill('{result.name}') "
                    "to use it in this session."
                ),
            }
            if result.downgraded:
                payload["warning"] = (
                    f"'{result.name}' claimed tier '{result.claimed_tier}' but its "
                    f"signature only attests to '{result.installed_tier}', so it "
                    "was installed at the lower tier. Mention this to the user."
                )
            return payload

        @tool
        def remove_skill(name: str) -> dict:
            """Delete an installed skill from ~/.gaia/skills.

            The user is asked to approve this before it runs. Only removes
            skills installed from the hub. A skill bundled with
            this agent, or imported from .claude/skills, is refused with the
            reason — those are removed by uninstalling the agent or deleting
            the folder. If the skill is loaded right now, it is unloaded too.

            Args:
                name: Skill name, as shown by list_skills.

            Returns:
                Dictionary with the deleted path and version.
            """
            from gaia.skills.errors import SkillError
            from gaia.skills.install import remove_skill as _hub_remove

            rejection = _reject_bad_name("remove_skill", name)
            if rejection is not None:
                return rejection
            skill_name = name.strip()
            try:
                # Delete first, unload second: a refused delete (read-only root)
                # must not leave the session stripped of a skill that is still
                # installed.
                result = _hub_remove(skill_name, manager=agent.skill_manager)
            except SkillError as exc:
                return _failure("remove_skill", exc)

            unloaded = agent.unload_skill(skill_name)
            return {
                "status": "success",
                "name": result.name,
                "version": result.version,
                "path": str(result.path),
                "unloaded_from_session": unloaded,
            }

        # ------------------------------------------------------------------
        # Activation
        # ------------------------------------------------------------------

        @tool
        def load_skill(name: str) -> dict:
            """Activate an installed skill for the rest of this session.

            Adds the skill's instructions to your own instructions and
            registers any tools it provides, so you can follow its recipe from
            your next step onward. Loading costs prompt tokens — load what the
            task needs, and call unload_skill when you are done with it.

            Args:
                name: Skill name, as shown by list_skills. Install it first
                    with install_skill if list_skills does not show it.

            Returns:
                Dictionary with the loaded skill's tier, the tools it
                registered, and how many prompt tokens the loaded set now
                costs. A "warning" key appears when the skill's instructions
                depend on tools this agent does not have.
            """
            from gaia.skills.errors import SkillError
            from gaia.skills.manager import ROOT_CLAUDE_IMPORT

            rejection = _reject_bad_name("load_skill", name)
            if rejection is not None:
                return rejection
            skill_name = name.strip()
            already_loaded = skill_name in agent.loaded_skills
            try:
                # Rescan and re-parse rather than trusting cached frontmatter:
                # the gate below has to see the SKILL.md that is on disk now, not
                # the one discovery read before the last list_skills.
                agent.skill_manager.reload()
                candidate = agent.skill_manager.load(skill_name)
            except SkillError as exc:
                return _failure("load_skill", exc)

            if candidate.gaia.tools and candidate.root == ROOT_CLAUDE_IMPORT:
                return _refuse_ungated_code(candidate)

            try:
                skill = agent.load_skill(skill_name)
            except SkillError as exc:
                return _failure("load_skill", exc)

            payload: Dict[str, Any] = {
                "status": "success",
                "name": skill.name,
                "already_loaded": already_loaded,
                "security_tier": skill.security_tier,
                "registered_tools": [
                    skill.namespaced_tool_name(t) for t in skill.tool_names
                ],
                "loaded_skills": sorted(agent.loaded_skills),
                "prompt_tokens_estimate": estimate_prompt_tokens(
                    agent.get_skills_system_prompt()
                ),
            }

            # tools_required is advisory at load time — the base loader logs the
            # gap and loads anyway, so surface it here or the model discovers it
            # mid-recipe as a missing tool.
            unmet = [
                required
                for required in skill.gaia.tools_required
                if required not in agent._tools_registry
            ]
            if unmet:
                payload["unmet_tools_required"] = unmet
                payload["warning"] = (
                    f"Skill '{skill.name}' expects tool(s) {', '.join(unmet)}, which "
                    "this agent does not have registered. The parts of its "
                    "instructions that use them cannot run — say so instead of "
                    "improvising a substitute."
                )
            return payload

        @tool
        def unload_skill(name: str) -> dict:
            """Deactivate a loaded skill, removing its instructions and tools.

            Frees the prompt tokens the skill's instructions were using. The
            skill stays installed — load_skill brings it back.

            Args:
                name: Skill name, as shown by skill_status.

            Returns:
                Dictionary with what is still loaded and the prompt-token cost
                after unloading.
            """
            skill_name = (name or "").strip()
            if not agent.unload_skill(skill_name):
                loaded = sorted(agent.loaded_skills)
                return {
                    "status": "error",
                    "action": "unload_skill",
                    "error": (
                        f"Skill '{skill_name}' is not loaded, so there is nothing "
                        f"to unload. Currently loaded: "
                        f"{', '.join(loaded) if loaded else '(none)'}. Call "
                        "skill_status to see the active set, or list_skills to "
                        "see what is installed."
                    ),
                }
            return {
                "status": "success",
                "name": skill_name,
                "loaded_skills": sorted(agent.loaded_skills),
                "prompt_tokens_estimate": estimate_prompt_tokens(
                    agent.get_skills_system_prompt()
                ),
            }

        @tool(atomic=True)
        def skill_status() -> dict:
            """Report which skills are active right now and what they cost.

            Use this to decide whether to unload something before loading
            more, or to answer "what skills do you have loaded?".

            Returns:
                Dictionary with each loaded skill's prompt-token estimate, the
                total for the loaded set, the active skill set (if the agent
                launched with one), and how many skills are installed but not
                loaded.
            """
            loaded = agent.loaded_skills
            active = [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "security_tier": skill.security_tier,
                    "prompt_tokens_estimate": estimate_prompt_tokens(skill.body),
                }
                for skill in sorted(loaded.values(), key=lambda s: s.name)
            ]
            installed = agent.skill_manager.discover()
            return {
                "status": "success",
                "loaded_count": len(active),
                "loaded": active,
                "total_prompt_tokens_estimate": estimate_prompt_tokens(
                    agent.get_skills_system_prompt()
                ),
                "active_skill_set": agent.active_skill_set or "",
                "installed_not_loaded": sorted(set(installed) - set(loaded)),
            }


__all__ = [
    "SkillLibraryToolsMixin",
    "SKILL_LIBRARY_TOOL_NAMES",
    "estimate_prompt_tokens",
]
