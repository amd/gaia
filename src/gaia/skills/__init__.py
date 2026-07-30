# Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
# SPDX-License-Identifier: MIT
"""
GAIA skills runtime — parse, discover, and load ``SKILL.md`` capabilities.

The format is the `Agent Skills <https://agentskills.io>`_ open standard, so a
plain Claude Code skill loads unchanged; GAIA layers typed tools, a permission
grammar, and security tiers under ``metadata.gaia``.

Quick start::

    from gaia.skills import SkillManager

    manager = SkillManager()
    for skill in manager.list_skills():
        print(skill.name, "-", skill.description)

    skill = manager.load("web-research")   # body + validation

From an agent::

    class WebAgent(Agent):
        def _register_tools(self):
            self.load_skill("web-research")   # tools land under 'web-research/'

Phase 1 (issue #888) bridges connector-backed permissions only. A skill
declaring a local-capability permission (``filesystem``, ``shell``,
``database``, ``desktop``, ``env``) is refused — see
:mod:`gaia.skills.permissions`.
"""

from gaia.skills.errors import (
    SkillError,
    SkillNotFoundError,
    SkillPermissionError,
    SkillValidationError,
)
from gaia.skills.format import (
    DEFAULT_SECURITY_TIER,
    MAX_DESCRIPTION_LENGTH,
    MAX_NAME_LENGTH,
    SECURITY_TIERS,
    SKILL_FILENAME,
    SKILL_TOOLS_FILENAME,
    GaiaMetadata,
    Skill,
    SkillRequirements,
    SkillTool,
    parse_skill,
    parse_skill_file,
    parse_skill_metadata,
    reset_security_tier,
    split_frontmatter,
    validate_skill,
)
from gaia.skills.loader import register_skill_tools, unregister_skill_tools
from gaia.skills.manager import (
    ROOT_AGENT_BUNDLED,
    ROOT_CLAUDE_IMPORT,
    ROOT_USER,
    SkillManager,
    SkillRoot,
    get_default_manager,
    reset_default_manager,
    user_skills_dir,
)
from gaia.skills.migrate import (
    HERMES_NAMESPACES,
    OPENCLAW_NAMESPACES,
    VENDOR_HERMES,
    VENDOR_OPENCLAW,
    VENDORS,
    MigrationOutcome,
    detect_vendor,
    find_source_skills,
    format_report,
    install_migrated,
    migrate_skill_dir,
    migrate_text,
)
from gaia.skills.permissions import (
    CONNECTOR_BRIDGED_DOMAINS,
    LOCAL_CAPABILITY_DOMAINS,
    Permission,
    connector_requirements,
    parse_permissions,
    refuse_unbridged_permissions,
)

__all__ = [
    # Format
    "Skill",
    "SkillTool",
    "SkillRequirements",
    "GaiaMetadata",
    "parse_skill",
    "parse_skill_file",
    "parse_skill_metadata",
    "split_frontmatter",
    "reset_security_tier",
    "validate_skill",
    "SKILL_FILENAME",
    "SKILL_TOOLS_FILENAME",
    "SECURITY_TIERS",
    "DEFAULT_SECURITY_TIER",
    "MAX_NAME_LENGTH",
    "MAX_DESCRIPTION_LENGTH",
    # Discovery
    "SkillManager",
    "SkillRoot",
    "ROOT_AGENT_BUNDLED",
    "ROOT_USER",
    "ROOT_CLAUDE_IMPORT",
    "get_default_manager",
    "reset_default_manager",
    "user_skills_dir",
    # Migration (OpenClaw / Hermes → GAIA)
    "MigrationOutcome",
    "detect_vendor",
    "migrate_text",
    "migrate_skill_dir",
    "find_source_skills",
    "install_migrated",
    "format_report",
    "VENDORS",
    "VENDOR_OPENCLAW",
    "VENDOR_HERMES",
    "OPENCLAW_NAMESPACES",
    "HERMES_NAMESPACES",
    # Tools
    "register_skill_tools",
    "unregister_skill_tools",
    # Permissions
    "Permission",
    "parse_permissions",
    "connector_requirements",
    "refuse_unbridged_permissions",
    "CONNECTOR_BRIDGED_DOMAINS",
    "LOCAL_CAPABILITY_DOMAINS",
    # Errors
    "SkillError",
    "SkillValidationError",
    "SkillNotFoundError",
    "SkillPermissionError",
]
