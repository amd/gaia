# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""One safe way to turn a skill name into a directory under a skills root.

Every skill entry point — install, remove, import, create, migrate — resolves a
directory as ``root / name``, and most of them then ``rmtree`` it. ``pathlib``
collapses ``root / "."`` back to ``root``, leaves ``root / ".."`` at the root's
parent, and lets an absolute name replace the root outright — and ``is_dir()``
is true for all three. So an unvalidated join turned ``gaia skill remove .``
into "delete the skills root, signing keys and trust store included".

:func:`skill_directory` is the only join callers should use. It validates the
name against the canonical :data:`gaia.skills.format.NAME_PATTERN` *and* asserts
the resolved target is a direct child of the resolved root — the second half is
the load-bearing one, because it catches the symlink and ``\\\\?\\`` shapes the
pattern never sees.
"""

from __future__ import annotations

from pathlib import Path

from gaia.skills.errors import FORMAT_DOCS_URL, SkillValidationError
from gaia.skills.format import MAX_NAME_LENGTH, NAME_PATTERN

__all__ = ["validated_skill_name", "skill_directory"]


def validated_skill_name(name: str, *, source: str = "skill name") -> str:
    """Return the stripped name, or raise if it is not a bare skill name.

    Args:
        name: The candidate name — a CLI argument, or the ``name`` a downloaded
            bundle's own ``SKILL.md`` declares.
        source: What produced the name, quoted back in the error.

    Raises:
        SkillValidationError: the name is empty, over-long, or is anything other
            than lowercase letters, digits and single internal hyphens.
    """
    text = (name or "").strip()
    if not text:
        raise SkillValidationError(
            f"{source}: no skill name given. Pass the name exactly as "
            "'gaia skill list' reports it, e.g. 'web-research'."
        )
    if len(text) > MAX_NAME_LENGTH:
        raise SkillValidationError(
            f"{source}: name {text!r} is {len(text)} characters; the limit is "
            f"{MAX_NAME_LENGTH}. See {FORMAT_DOCS_URL}#naming"
        )
    if not NAME_PATTERN.match(text):
        # Same wording as validate_skill's, so a user meets one message whether
        # the name was refused at the join or at manifest validation.
        raise SkillValidationError(
            f"{source}: name {text!r} is not a valid skill name. Use lowercase "
            "letters and digits separated by single hyphens (e.g. 'web-research') "
            "— no slashes, no '.', no '..', no absolute path. Pass the name "
            f"exactly as 'gaia skill list' reports it. See {FORMAT_DOCS_URL}#naming"
        )
    return text


def skill_directory(root: Path | str, name: str, *, source: str = "skill name") -> Path:
    """``root/<name>`` for a validated ``name``, asserted to sit directly in ``root``.

    Call this instead of ``root / name`` anywhere the result may be created,
    overwritten, or deleted.

    Raises:
        SkillValidationError: the name is not a bare skill name, or the join
            resolves outside ``root``.
    """
    validated = validated_skill_name(name, source=source)
    root_path = Path(root)
    target = root_path / validated

    if target.resolve().parent != root_path.resolve():
        raise SkillValidationError(
            f"{source}: {target} does not resolve to a direct child of the skills "
            f"root {root_path} (it resolves to {target.resolve()}). Refusing to "
            "touch it. If the skill directory is a symlink, delete the link "
            "yourself — this command only manages real directories inside the root."
        )
    return target
