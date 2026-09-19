# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
``skill-lock.json`` — the record of what ``gaia skill install`` actually installed.

Discovery (:class:`~gaia.skills.manager.SkillManager`) answers *what is on disk*.
The lock answers the questions a directory listing cannot: which hub version was
requested, which one resolved, what the artifact hashed to, who signed it, and
which tier it was **installed** at after the ceiling was applied. Without that,
``gaia skill install foo@^1.0`` is unauditable and unrepeatable — the front matter
alone cannot say whether a ``community`` stamp was earned or merely claimed.

The file lives at ``<skills root>/skill-lock.json`` (i.e. ``~/.gaia/skills/``, or
wherever ``GAIA_CONFIG_DIR`` points)::

    {
      "schema_version": 1,
      "generated_at": "2026-07-30T12:00:00+00:00",
      "skills": {
        "web-research": {
          "name": "web-research",
          "version": "1.2.0",
          "requested": "^1.0",
          "source": "hub",
          "hub_url": "https://hub.amd-gaia.ai",
          "artifact_sha256": "…",
          "artifact_filename": "web-research-1.2.0.zip",
          "content_digest": "sha256:…",
          "claimed_tier": "community",
          "installed_tier": "community",
          "attested_tier": "community",
          "signature": {"signed": true, "key_id": "…", "publisher": "acme",
                        "role": "publisher"},
          "permissions": ["network:read:*.brave.com"],
          "installed_at": "2026-07-30T12:00:00+00:00",
          "path": "/Users/me/.gaia/skills/web-research"
        }
      }
    }

``gaia skill install`` writes ``source: "hub"`` entries. A skill created with
``gaia skill create`` or copied in with ``gaia skill import`` has no hub
provenance, so install never invents one; ``gaia skill lock --relock``
(:mod:`gaia.skills.drift`) may record it as ``source: "local"``, which asserts
exactly the origin it has and nothing more. Only a ``hub`` entry's tier is an
enforcement decision; a ``local`` entry's is the author's own claim.

A missing lock file is an **empty** lock, not an error — that is a machine with no
hub-installed skills. A *corrupt* one raises: silently starting over would drop
the provenance of skills that are still installed.

The lock is only useful if something reads it back: :mod:`gaia.skills.drift`
compares it against what is actually on disk, and
:meth:`gaia.skills.manager.SkillManager.load` refuses a signature-backed skill
whose bytes no longer match.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from gaia.logger import get_logger
from gaia.skills.errors import SkillValidationError

log = get_logger(__name__)

#: Lock filename, in the user skills root.
LOCK_FILENAME = "skill-lock.json"

#: Bumped only for a breaking change to the entry shape.
LOCK_SCHEMA_VERSION = 1

#: ``source`` value for a skill pulled from the Agent Hub.
SOURCE_HUB = "hub"

#: ``source`` value for a skill that lives in the user root without hub
#: provenance — authored with ``gaia skill create``, copied in with
#: ``gaia skill import``, or converted by ``gaia skill migrate``. Recorded only
#: by ``gaia skill lock --relock``, so drift detection covers it too.
SOURCE_LOCAL = "local"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class LockEntry:
    """One installed skill's provenance."""

    name: str
    version: str
    #: The range the user or manifest asked for ("^1.0", ">=1.2.0", "*").
    requested: str = "*"
    source: str = SOURCE_HUB
    hub_url: str = ""
    artifact_sha256: str = ""
    artifact_filename: str = ""
    #: ``sha256:<hex>`` over the *installed* directory
    #: (:func:`gaia.skills.audit.findings.content_digest`), recorded after the
    #: tier re-stamp so it describes the bytes that actually landed. This is what
    #: makes post-install tampering detectable — ``artifact_sha256`` only covers
    #: the downloaded archive, which nobody re-reads once it is unpacked.
    content_digest: str = ""
    #: Tier the skill's own front matter claimed.
    claimed_tier: str = ""
    #: Highest tier its signature earned (see :func:`gaia.skills.signing.attested_tier`).
    attested_tier: str = ""
    #: Tier it was actually installed at — ``min(claimed, attested)``, and the
    #: tier stamped into the installed ``SKILL.md``.
    installed_tier: str = ""
    signature: dict[str, Any] = field(default_factory=dict)
    permissions: list[str] = field(default_factory=list)
    installed_at: str = field(default_factory=_now)
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any, *, where: str) -> "LockEntry":
        if not isinstance(data, dict):
            raise SkillValidationError(
                f"{where}: every entry of 'skills' must be a JSON object."
            )
        name = str(data.get("name") or "")
        version = str(data.get("version") or "")
        if not name or not version:
            raise SkillValidationError(
                f"{where}: a lock entry is missing 'name' and/or 'version'. Delete "
                f"{LOCK_FILENAME} and reinstall to rebuild it."
            )
        known = {f for f in cls.__dataclass_fields__}  # pylint: disable=no-member
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["name"] = name
        kwargs["version"] = version
        return cls(**kwargs)


@dataclass
class SkillLock:
    """The parsed ``skill-lock.json``, keyed by skill name."""

    entries: dict[str, LockEntry] = field(default_factory=dict)
    path: Optional[Path] = None

    # -- io ----------------------------------------------------------------

    @classmethod
    def load(cls, skills_root: Path) -> "SkillLock":
        """Read the lock under *skills_root*; an absent file yields an empty lock."""
        path = lock_path(skills_root)
        if not path.is_file():
            return cls(entries={}, path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillValidationError(
                f"Skill lock {path} is not readable JSON: {exc}. Fix the file, or "
                "delete it and re-run 'gaia skill install' for each skill you want "
                "tracked — GAIA will not silently discard the provenance of skills "
                "that are still installed."
            ) from exc
        if not isinstance(data, dict):
            raise SkillValidationError(
                f"Skill lock {path} must contain a JSON object with a 'skills' map."
            )

        version = data.get("schema_version", LOCK_SCHEMA_VERSION)
        if version != LOCK_SCHEMA_VERSION:
            raise SkillValidationError(
                f"Skill lock {path} is schema v{version}; this GAIA build reads "
                f"v{LOCK_SCHEMA_VERSION}. Upgrade GAIA, or delete the lock and "
                "reinstall your skills."
            )

        raw_skills = data.get("skills") or {}
        if not isinstance(raw_skills, dict):
            raise SkillValidationError(
                f"Skill lock {path}: 'skills' must be an object keyed by skill name."
            )
        entries = {
            str(name): LockEntry.from_dict(entry, where=str(path))
            for name, entry in raw_skills.items()
        }
        return cls(entries=entries, path=path)

    def save(self, path: Optional[Path] = None) -> Path:
        """Persist the lock. Returns the path written."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise SkillValidationError(
                "SkillLock.save() needs a path: this lock was built in memory rather "
                "than loaded from a skills root."
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        document = {
            "schema_version": LOCK_SCHEMA_VERSION,
            "generated_at": _now(),
            "skills": {
                name: self.entries[name].to_dict() for name in sorted(self.entries)
            },
        }
        target.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        self.path = target
        log.debug("Wrote skill lock %s (%d entry/entries)", target, len(self.entries))
        return target

    # -- mutation ----------------------------------------------------------

    def record(self, entry: LockEntry) -> None:
        """Add or replace the entry for ``entry.name``."""
        self.entries[entry.name] = entry

    def forget(self, name: str) -> bool:
        """Drop *name*. Returns True if it was tracked."""
        return self.entries.pop(name, None) is not None

    def get(self, name: str) -> Optional[LockEntry]:
        return self.entries.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self.entries


def lock_path(skills_root: Path) -> Path:
    """Path of the lock file for a skills root."""
    return Path(skills_root) / LOCK_FILENAME


def forget_skill(skills_root: Path, name: str) -> bool:
    """Drop *name* from the lock under *skills_root*; True if it was tracked.

    Every path that **replaces** a skill directory without hub provenance —
    ``import``, ``create --force``, ``migrate --force`` — must call this. The
    hub copy those bytes described is gone, so leaving its entry behind would
    have the lock asserting a version, tier, and signature over a different
    skill, which :mod:`gaia.skills.drift` would then (correctly) refuse to load.

    A root with no lock file is a no-op: nothing is created.
    """
    if not lock_path(skills_root).is_file():
        return False
    lock = SkillLock.load(skills_root)
    if not lock.forget(name):
        return False
    lock.save()
    log.info("Dropped lock entry for '%s': it was replaced by a local copy", name)
    return True
