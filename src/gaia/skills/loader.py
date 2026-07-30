# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
Level 2 of progressive disclosure: import a skill's ``tools.py`` and register
its tools into ``_TOOL_REGISTRY`` under the ``<skill-name>/<tool>`` namespace.

Two invariants drive this module:

- **The manifest is the contract.** Every tool declared in
  ``metadata.gaia.tools`` must exist in ``tools.py`` with a matching signature
  (parameter names, requiredness, and inferred types). A mismatch is a defect,
  not a warning.
- **No partial load.** Any failure restores ``_TOOL_REGISTRY`` byte-for-byte to
  its pre-import state, so a rejected skill never leaves half its tools behind.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import threading
from pathlib import Path
from types import ModuleType
from typing import Any, Optional

from gaia.agents.base.tools import _TOOL_REGISTRY
from gaia.logger import get_logger
from gaia.skills.errors import FORMAT_DOCS_URL, SkillValidationError
from gaia.skills.format import SKILL_TOOLS_FILENAME, Skill, SkillTool

log = get_logger(__name__)

# Import + registry mutation must not interleave across threads: the diff-based
# capture below assumes nothing else writes to _TOOL_REGISTRY mid-import.
_IMPORT_LOCK = threading.RLock()

_MODULE_NAME_SAFE = re.compile(r"[^0-9a-zA-Z_]")


def register_skill_tools(skill: Skill) -> dict[str, dict[str, Any]]:
    """Import ``tools.py`` and register the skill's tools, namespaced.

    Args:
        skill: A validated skill with a ``path`` on disk.

    Returns:
        The registry entries that were added, keyed by ``<skill>/<tool>``. Empty
        for an instruction-only skill.

    Raises:
        SkillValidationError: if ``tools.py`` is missing, fails to import, or
            contradicts the declared ``metadata.gaia.tools``. The registry is
            left exactly as it was found.
    """
    if not skill.gaia.tools:
        tools_path = skill.tools_path
        if tools_path is not None and tools_path.is_file():
            log.debug(
                "Skill '%s' ships %s but declares no metadata.gaia.tools; not "
                "importing it — the manifest is the contract.",
                skill.name,
                SKILL_TOOLS_FILENAME,
            )
        return {}

    tools_path = skill.tools_path
    if tools_path is None:
        raise SkillValidationError(
            f"Skill '{skill.name}' declares tools but was parsed from a string, so "
            f"there is no {SKILL_TOOLS_FILENAME} to import. Load the skill from its "
            "directory instead (SkillManager.load / Agent.load_skill)."
        )
    if not tools_path.is_file():
        declared = ", ".join(t.name for t in skill.gaia.tools)
        raise SkillValidationError(
            f"Skill '{skill.name}' declares tools ({declared}) but has no "
            f"{SKILL_TOOLS_FILENAME} at {tools_path}. Add the module with a @tool "
            "function per declared entry, or remove metadata.gaia.tools to make it an "
            f"instruction-only skill. See {FORMAT_DOCS_URL}#tool-registration"
        )

    module_name = f"gaia_skill_{_MODULE_NAME_SAFE.sub('_', skill.name)}_tools"

    with _IMPORT_LOCK:
        before = dict(_TOOL_REGISTRY)
        previous_module = sys.modules.get(module_name)
        skill_dir = str(tools_path.parent)
        added_syspath = skill_dir not in sys.path

        try:
            if added_syspath:
                sys.path.insert(0, skill_dir)
            module = _import_tools_module(module_name, tools_path, skill)
            # A skill tool may share an unqualified name with a registry tool
            # (a skill providing ``search_web`` alongside BrowserToolsMixin is
            # exactly what the ``<skill>/<tool>`` namespace exists for), and the
            # decorator overwrites that key. Compare identity, not presence, so
            # the overwrite is detected and the original is restored below.
            discovered = {
                name: entry
                for name, entry in _TOOL_REGISTRY.items()
                if name not in before or entry is not before[name]
            }
            _validate_declared_tools(skill, discovered, module, tools_path)
            namespaced = _namespace_entries(skill, discovered)
        except BaseException:
            # No partial load: put the registry (and sys.modules) back untouched.
            _TOOL_REGISTRY.clear()
            _TOOL_REGISTRY.update(before)
            _restore_module(module_name, previous_module)
            raise
        finally:
            if added_syspath and skill_dir in sys.path:
                sys.path.remove(skill_dir)

        # Swap the unqualified keys the decorator wrote for namespaced ones.
        _TOOL_REGISTRY.clear()
        _TOOL_REGISTRY.update(before)
        _TOOL_REGISTRY.update(namespaced)

    log.info(
        "Registered %d tool(s) from skill '%s': %s",
        len(namespaced),
        skill.name,
        ", ".join(sorted(namespaced)),
    )
    return namespaced


def unregister_skill_tools(skill_name: str) -> list[str]:
    """Remove every ``<skill_name>/<tool>`` entry from the registry.

    Returns the removed keys. Used by hot-reload and by rollback paths.
    """
    prefix = f"{skill_name}/"
    with _IMPORT_LOCK:
        removed = [key for key in _TOOL_REGISTRY if key.startswith(prefix)]
        for key in removed:
            del _TOOL_REGISTRY[key]
    if removed:
        log.debug("Unregistered %d tool(s) for skill '%s'", len(removed), skill_name)
    return removed


def _import_tools_module(
    module_name: str, tools_path: Path, skill: Skill
) -> ModuleType:
    """Import ``tools.py`` as a standalone module, failing loudly."""
    spec = importlib.util.spec_from_file_location(module_name, tools_path)
    if spec is None or spec.loader is None:
        raise SkillValidationError(
            f"Skill '{skill.name}': Python could not build an import spec for "
            f"{tools_path}. Check that the file has a .py extension and is readable."
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        raise SkillValidationError(
            f"Skill '{skill.name}': importing {tools_path} raised "
            f"{type(exc).__name__}: {exc}. Fix the module (its imports must resolve "
            "in the running environment — declare third-party packages under "
            "metadata.gaia.requirements.dependencies and install them), then reload "
            "the skill."
        ) from exc
    return module


def _restore_module(module_name: str, previous: Optional[ModuleType]) -> None:
    """Undo the ``sys.modules`` entry created for a failed import."""
    if previous is not None:
        sys.modules[module_name] = previous
    else:
        sys.modules.pop(module_name, None)


def _validate_declared_tools(
    skill: Skill,
    discovered: dict[str, dict[str, Any]],
    module: ModuleType,
    tools_path: Path,
) -> None:
    """Cross-check ``metadata.gaia.tools`` against the imported module."""
    declared_names = {t.name for t in skill.gaia.tools}

    missing = sorted(declared_names - set(discovered))
    if missing:
        undecorated = [
            name for name in missing if callable(getattr(module, name, None))
        ]
        hint = (
            f" {', '.join(undecorated)} exist(s) but is not decorated — add "
            "'@tool' from gaia.agents.base.tools."
            if undecorated
            else f" Define one @tool function per declared entry in {tools_path}."
        )
        raise SkillValidationError(
            f"Skill '{skill.name}' declares tool(s) {', '.join(missing)} that "
            f"{SKILL_TOOLS_FILENAME} does not register.{hint} Nothing was loaded. "
            f"See {FORMAT_DOCS_URL}#tool-registration"
        )

    undeclared = sorted(set(discovered) - declared_names)
    if undeclared:
        raise SkillValidationError(
            f"Skill '{skill.name}': {SKILL_TOOLS_FILENAME} registers tool(s) "
            f"{', '.join(undeclared)} that the manifest does not declare. Add them to "
            "metadata.gaia.tools (the manifest is the contract users audit) or remove "
            f"them from the module. Nothing was loaded. "
            f"See {FORMAT_DOCS_URL}#tool-registration"
        )

    for declared in skill.gaia.tools:
        _validate_signature(skill, declared, discovered[declared.name], tools_path)


def _validate_signature(
    skill: Skill,
    declared: SkillTool,
    actual: dict[str, Any],
    tools_path: Path,
) -> None:
    """Compare one declared tool against the signature the decorator inferred."""
    actual_params: dict[str, dict[str, Any]] = actual.get("parameters", {})

    declared_names = set(declared.parameters)
    actual_names = set(actual_params)

    if declared_names != actual_names:
        only_manifest = sorted(declared_names - actual_names)
        only_code = sorted(actual_names - declared_names)
        detail = []
        if only_manifest:
            detail.append(
                f"declared but absent from the function: {', '.join(only_manifest)}"
            )
        if only_code:
            detail.append(f"in the function but undeclared: {', '.join(only_code)}")
        raise SkillValidationError(
            f"Skill '{skill.name}': tool '{declared.name}' parameters do not match "
            f"{tools_path} — {'; '.join(detail)}. Make metadata.gaia.tools mirror the "
            f"function signature exactly. Nothing was loaded. "
            f"See {FORMAT_DOCS_URL}#tool-registration"
        )

    for param_name, declared_spec in declared.parameters.items():
        actual_spec = actual_params[param_name]

        declared_required = bool(declared_spec.get("required", False))
        actual_required = bool(actual_spec.get("required", False))
        if declared_required != actual_required:
            expected = "required" if actual_required else "optional"
            raise SkillValidationError(
                f"Skill '{skill.name}': tool '{declared.name}' parameter "
                f"'{param_name}' is declared "
                f"{'required' if declared_required else 'optional'} but the function "
                f"in {tools_path} makes it {expected} "
                f"({'no default' if actual_required else 'has a default'}). Set "
                f"'required: {str(actual_required).lower()}' or change the signature. "
                f"Nothing was loaded."
            )

        declared_type = declared_spec.get("type")
        actual_type = actual_spec.get("type", "unknown")
        # "unknown" means the decorator could not infer a type from the
        # annotation — there is nothing to contradict, so it is not a mismatch.
        if declared_type and actual_type != "unknown" and declared_type != actual_type:
            raise SkillValidationError(
                f"Skill '{skill.name}': tool '{declared.name}' parameter "
                f"'{param_name}' is declared type '{declared_type}' but the function "
                f"in {tools_path} annotates it as '{actual_type}'. Align the manifest "
                f"with the signature. Nothing was loaded."
            )


def _namespace_entries(
    skill: Skill, discovered: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Re-key the discovered entries under ``<skill-name>/<tool>``."""
    namespaced: dict[str, dict[str, Any]] = {}
    for tool_name, entry in discovered.items():
        key = skill.namespaced_tool_name(tool_name)
        namespaced[key] = {
            **entry,
            "name": key,
            "display_name": f"{tool_name} ({skill.name})",
            "skill": skill.name,
        }
    return namespaced
