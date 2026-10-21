# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""Per-user, per-skill preferences: what a user likes, kept for one skill.

A skill reads and writes its own preferences through
:func:`gaia.skills.context.preferences`; the Agent UI lists, edits and resets
them through ``/api/skills/preferences``. Both go through
:class:`SkillPreferenceStore`.

- **Local only.** One JSON file per skill under
  ``<gaia_home>/skill-preferences/``. Nothing here is uploaded or synced.
- **Schema owned by the skill.** A ``preferences.schema.json`` in the skill
  folder, when present, is the contract every write is checked against. It is
  a small subset of JSON Schema (see :data:`SUPPORTED_KEYWORDS`); a schema using
  any other keyword is refused rather than half-enforced.
- **Private chats and memory-off.** Reads always work. A write from a skill
  tool in a private chat, or with memory off, is skipped and says so — the same
  rule the memory store follows.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillValidationError
from gaia.skills.naming import validated_skill_name

log = get_logger(__name__)

#: The optional schema a skill ships beside its SKILL.md.
SCHEMA_FILENAME = "preferences.schema.json"

#: Folder under GAIA's home holding one ``<skill>.json`` per skill.
PREFERENCES_DIRNAME = "skill-preferences"

#: The JSON Schema keywords this validator enforces. Anything else in a schema
#: is an error: a keyword that is silently ignored is a rule that is not kept.
SUPPORTED_KEYWORDS = frozenset(
    {
        "$schema",
        "title",
        "description",
        "default",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "items",
        "maxItems",
    }
)

_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
    "null": type(None),
}


def preferences_root() -> Path:
    """``<gaia_home>/skill-preferences``, read at call time so tests can move it."""
    from gaia.config import gaia_home

    return gaia_home() / PREFERENCES_DIRNAME


def load_schema(skill_dir: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The skill's preference schema, or ``None`` when it ships none.

    Raises:
        SkillValidationError: the file is not JSON, or uses a keyword this
            validator does not enforce.
    """
    if skill_dir is None:
        return None
    path = Path(skill_dir) / SCHEMA_FILENAME
    if not path.is_file():
        return None
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SkillValidationError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise SkillValidationError(
            f"{path} must describe an object: its top level needs " '"type": "object".'
        )
    _check_keywords(schema, str(path))
    return schema


def _check_keywords(schema: Any, where: str) -> None:
    if not isinstance(schema, dict):
        raise SkillValidationError(f"{where}: a schema must be a JSON object.")
    unknown = sorted(set(schema) - SUPPORTED_KEYWORDS)
    if unknown:
        raise SkillValidationError(
            f"{where} uses {', '.join(unknown)}, which GAIA's preference "
            f"validator does not enforce. Supported: "
            f"{', '.join(sorted(SUPPORTED_KEYWORDS))}."
        )
    for name, sub in (schema.get("properties") or {}).items():
        _check_keywords(sub, f"{where} -> {name}")
    if isinstance(schema.get("items"), dict):
        _check_keywords(schema["items"], f"{where} -> items")
    if isinstance(schema.get("additionalProperties"), dict):
        _check_keywords(schema["additionalProperties"], f"{where} -> *")


def validate(value: Any, schema: Dict[str, Any], where: str = "preferences") -> None:
    """Raise :class:`SkillValidationError` naming the first violation."""
    expected = schema.get("type")
    if expected is not None:
        types = expected if isinstance(expected, list) else [expected]
        if not any(_is_type(value, t) for t in types):
            raise SkillValidationError(
                f"{where} must be {' or '.join(types)}, got {type(value).__name__}."
            )
    if "enum" in schema and value not in schema["enum"]:
        raise SkillValidationError(
            f"{where} must be one of {schema['enum']!r}, got {value!r}."
        )
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise SkillValidationError(f"{where} must be >= {schema['minimum']}.")
        if "maximum" in schema and value > schema["maximum"]:
            raise SkillValidationError(f"{where} must be <= {schema['maximum']}.")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SkillValidationError(
                f"{where} must be at least {schema['minLength']} characters."
            )
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SkillValidationError(
                f"{where} must be at most {schema['maxLength']} characters."
            )
    if isinstance(value, list):
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SkillValidationError(
                f"{where} holds at most {schema['maxItems']} items."
            )
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{where}[{i}]")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for name in schema.get("required") or []:
            if name not in value:
                raise SkillValidationError(f"{where} is missing '{name}'.")
        extra = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                validate(item, properties[name], f"{where}.{name}")
            elif extra is False:
                raise SkillValidationError(
                    f"{where} has no setting '{name}'. Known: "
                    f"{', '.join(sorted(properties)) or '(none)'}."
                )
            elif isinstance(extra, dict):
                validate(item, extra, f"{where}.{name}")


def _is_type(value: Any, name: str) -> bool:
    if name not in _TYPES:
        raise SkillValidationError(f"Unknown schema type {name!r}.")
    if name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[name])


@dataclass
class PreferenceWrite:
    """What a write did. ``saved`` is False when it was skipped, with ``note`` why."""

    saved: bool
    preferences: Dict[str, Any] = field(default_factory=dict)
    note: Optional[str] = None


class SkillPreferenceStore:
    """Read, write and reset the preference file of each skill."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self._root = Path(root) if root is not None else None

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else preferences_root()

    def _path(self, skill: str) -> Path:
        name = validated_skill_name(skill, source="preferences skill name")
        return self.root / f"{name}.json"

    def get(self, skill: str) -> Dict[str, Any]:
        """The saved preferences of *skill*; ``{}`` when it has none yet.

        Raises:
            SkillValidationError: the file exists but is not a JSON object.
        """
        path = self._path(skill)
        if not path.is_file():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SkillValidationError(
                f"Preferences for '{skill}' at {path} are not valid JSON ({e}). "
                "Reset them from Settings or delete the file."
            ) from e
        if not isinstance(data, dict):
            raise SkillValidationError(
                f"Preferences for '{skill}' at {path} must be a JSON object."
            )
        return data

    def update(
        self,
        skill: str,
        changes: Dict[str, Any],
        *,
        schema: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Merge *changes* into the saved preferences and write them.

        A key set to ``None`` is removed. The merged result is validated
        against *schema* before anything is written.

        Returns:
            The preferences as saved.
        """
        if not isinstance(changes, dict):
            raise SkillValidationError(
                f"Preferences for '{skill}' must be updated with an object, got "
                f"{type(changes).__name__}."
            )
        merged = {**self.get(skill), **changes}
        merged = {k: v for k, v in merged.items() if v is not None}
        if schema is not None:
            validate(merged, schema, f"Preferences for '{skill}'")
        self._write(skill, merged)
        return merged

    def reset(self, skill: str) -> bool:
        """Forget every preference of *skill*. True when there was something."""
        path = self._path(skill)
        if not path.is_file():
            return False
        path.unlink()
        log.info("Reset preferences for skill '%s'", skill)
        return True

    def list(self) -> List[Dict[str, Any]]:
        """Every skill with saved preferences, as ``{"skill", "preferences"}``."""
        if not self.root.is_dir():
            return []
        out = []
        for path in sorted(self.root.glob("*.json")):
            out.append({"skill": path.stem, "preferences": self.get(path.stem)})
        return out

    def _write(self, skill: str, data: Dict[str, Any]) -> None:
        path = self._path(skill)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        log.debug("Saved preferences for skill '%s'", skill)
