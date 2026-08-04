// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// SKILL.md parser, writer, and validator — the C++ port of
// src/gaia/skills/format.py. Both runtimes read the same ~/.gaia/skills/
// directory, so the schema, the constants, and the error wording are a
// cross-runtime contract: a skill author must get the same verdict and the
// same message from either one.
//
// The on-disk contract is the Agent Skills (agentskills.io) base — `name`,
// `description`, optional `license` / `metadata` — plus GAIA's two additions:
// a top-level `version` and everything else nested under `metadata.gaia`.
// A bare standard skill (only `name` and `description`) loads as a valid
// instruction-only skill with the most conservative defaults.
//
// Round-trip is identity: parseSkill(toMarkdown(parseSkill(text))) equals
// parseSkill(text). Unknown top-level keys, other `metadata.<vendor>`
// namespaces, and unknown `metadata.gaia` keys are preserved as JSON blobs,
// in the author's key order, so nothing is lost by passing a foreign skill
// through GAIA.
//
// Scalars resolve exactly as PyYAML's SafeLoader resolves them (YAML 1.1), so
// `flag: yes` is a bool and `mode: 0755` is 493 in both runtimes. A value
// PyYAML types as something JSON cannot hold — a timestamp, an infinity, the
// `=` / `<<` control tags — is kept as its literal text and written back
// unquoted, so Python still reads the value it read before.
//
// The standard's `compatibility` / `allowed-tools` / `disallowed-tools` keys
// parse but are **ignored** — they overlap `metadata.gaia` and are never a
// permission mechanism. See docs/plans/skill-format.mdx.

#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "gaia/export.h"

namespace gaia {

/// Frontmatter values GAIA does not model, preserved verbatim. Ordered, so a
/// rewritten skill reproduces the key order its author wrote.
using SkillJson = nlohmann::ordered_json;

// ---------------------------------------------------------------------------
// Constants — ported verbatim from src/gaia/skills/format.py
// ---------------------------------------------------------------------------

/// The one required file in a skill directory.
inline constexpr const char* SKILL_FILENAME = "SKILL.md";

/// Optional module providing the skill's own @tool functions (Python runtime).
inline constexpr const char* SKILL_TOOLS_FILENAME = "tools.py";

/// Install-time trust tiers, most trusted first.
inline constexpr const char* SECURITY_TIERS[] = {"verified", "community",
                                                 "experimental"};

/// The tier a skill takes when it declares none.
inline constexpr const char* DEFAULT_SECURITY_TIER = "experimental";

inline constexpr size_t MAX_NAME_LENGTH = 64;
inline constexpr size_t MAX_DESCRIPTION_LENGTH = 1024;

/// Standard keys GAIA parses but deliberately ignores (see the file header).
inline constexpr const char* IGNORED_STANDARD_KEYS[] = {
    "compatibility", "allowed-tools", "disallowed-tools"};

/// Where every error message points the author next.
inline constexpr const char* FORMAT_DOCS_URL =
    "https://amd-gaia.ai/docs/plans/skill-format";

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// Base class for every skills-runtime failure.
class GAIA_API SkillError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

/// A SKILL.md is malformed, incomplete, or violates the schema.
///
/// Thrown before anything is registered, so a rejected skill never leaves a
/// partial load behind. Every message names what failed, what the author
/// should do, and where to look next.
class GAIA_API SkillValidationError : public SkillError {
public:
    using SkillError::SkillError;
};

// ---------------------------------------------------------------------------
// Frontmatter types
// ---------------------------------------------------------------------------

/// `metadata.gaia.requirements` — all advisory.
struct GAIA_API SkillRequirements {
    std::optional<std::string> model;
    std::optional<std::string> context;
    std::optional<std::string> python;
    std::vector<std::string> dependencies;
    std::vector<std::string> nodeDependencies;
    std::vector<std::string> envVars;
    SkillJson hardware = SkillJson::object();
    /// Requirement keys GAIA does not model, preserved verbatim for round-trip.
    SkillJson extra = SkillJson::object();

    /// True when no constraint is declared (the omitted-default state).
    bool isEmpty() const;

    /// Serialize back to the frontmatter shape, omitting empty fields.
    SkillJson toJson() const;

    /// Build from the frontmatter mapping, failing loudly on a bad shape.
    /// @param data The `requirements` value; JSON null yields the default.
    /// @param skillName Quoted in error messages.
    /// @throws SkillValidationError if any field has the wrong shape.
    static SkillRequirements fromJson(const SkillJson& data,
                                      const std::string& skillName);

    bool operator==(const SkillRequirements& other) const;
    bool operator!=(const SkillRequirements& other) const { return !(*this == other); }
};

/// One entry of `metadata.gaia.tools` — a tool the skill *provides*.
///
/// Distinct from `toolsRequired`, which names registry tools the skill
/// *consumes*. Never conflate the two.
struct GAIA_API SkillTool {
    std::string name;
    std::string description;
    /// `{param_name: {"type": ..., "required": ..., "default": ...}}`
    SkillJson parameters = SkillJson::object();
    std::optional<SkillJson> returns;
    bool atomic = false;

    /// Serialize back to the frontmatter shape.
    SkillJson toJson() const;

    /// Build from a frontmatter entry, failing loudly on a bad shape.
    /// @throws SkillValidationError if the entry is not a mapping with a name.
    static SkillTool fromJson(const SkillJson& data, const std::string& skillName);

    bool operator==(const SkillTool& other) const;
    bool operator!=(const SkillTool& other) const { return !(*this == other); }
};

/// The `metadata.gaia` namespace. Omit it entirely for a bare skill.
struct GAIA_API GaiaMetadata {
    std::string securityTier = DEFAULT_SECURITY_TIER;
    std::vector<std::string> permissions;
    SkillRequirements requirements;
    std::vector<SkillTool> tools;
    std::vector<std::string> toolsRequired;
    /// `metadata.gaia` keys GAIA does not model, preserved for round-trip.
    SkillJson extra = SkillJson::object();

    /// True when every field holds its omitted-default value.
    bool isDefault() const;

    /// Serialize back to the frontmatter shape, omitting defaults.
    SkillJson toJson() const;

    /// Build from the `metadata.gaia` mapping, failing loudly on a bad shape.
    /// @throws SkillValidationError on an unknown tier or a mistyped field.
    static GaiaMetadata fromJson(const SkillJson& data, const std::string& skillName);

    bool operator==(const GaiaMetadata& other) const;
    bool operator!=(const GaiaMetadata& other) const { return !(*this == other); }
};

/// A parsed SKILL.md: frontmatter + Markdown body.
///
/// The location fields (`path`, `root`, `readOnly`) describe *where* the skill
/// came from, not *what* it is, so they are excluded from equality — that is
/// what makes round-trip identity meaningful across directories.
struct GAIA_API Skill {
    std::string name;
    std::string description;
    std::string body;
    std::optional<std::string> license;
    std::optional<std::string> version;
    GaiaMetadata gaia;
    /// Other `metadata.<vendor>` namespaces (hermes, openclaw, …), preserved.
    SkillJson otherMetadata = SkillJson::object();
    /// Top-level keys GAIA does not model — including the deliberately ignored
    /// `compatibility` / `allowed-tools` — preserved for round-trip.
    SkillJson extraFields = SkillJson::object();

    // --- provenance (never part of equality) ---
    /// Path to the SKILL.md, or empty when parsed from a string.
    std::string path;
    /// Label of the discovery root this skill came from.
    std::string root;
    /// True for imported roots (.claude/skills/).
    bool readOnly = false;

    /// The skill's directory, or empty when parsed from a string.
    std::string directory() const;

    /// Install-time trust tier; `experimental` unless declared.
    const std::string& securityTier() const { return gaia.securityTier; }

    /// True when the skill ships instructions and no tools of its own.
    bool isInstructionOnly() const { return gaia.tools.empty(); }

    /// Unqualified names of the tools this skill provides.
    std::vector<std::string> toolNames() const;

    /// Return `<skill-name>/<tool>` — the registry key used on load.
    std::string namespacedToolName(const std::string& toolName) const;

    /// Path to the skill's tools.py, or empty when the skill is not on disk.
    std::string toolsPath() const;

    /// Build the frontmatter mapping in canonical key order.
    SkillJson toFrontmatter() const;

    /// Equality ignores provenance (path / root / readOnly).
    bool operator==(const Skill& other) const;
    bool operator!=(const Skill& other) const { return !(*this == other); }
};

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

/// Parse SKILL.md text into a Skill. Tolerates a UTF-8 BOM and CRLF endings.
///
/// @param text The full file contents (frontmatter + Markdown body).
/// @param source Where the text came from, quoted in error messages.
/// @throws SkillValidationError on missing/malformed frontmatter or a field
///         that violates the schema. Nothing partial is ever returned.
GAIA_API Skill parseSkill(const std::string& text,
                          const std::string& source = "<string>");

/// Parse a SKILL.md from disk.
///
/// @param path Either the SKILL.md file or the directory containing it.
/// @param root Label of the discovery root this skill came from.
/// @param readOnly True for imported roots (.claude/skills/).
/// @param checkDirectoryName Enforce `name` == directory name.
/// @throws SkillValidationError if the file is missing or fails validation.
GAIA_API Skill parseSkillFile(const std::string& path, const std::string& root = "",
                              bool readOnly = false, bool checkDirectoryName = true);

/// Parse only the frontmatter of a SKILL.md, dropping the body.
///
/// Level 1 of progressive disclosure: discovery keeps every skill's metadata
/// resident but never pays for its instructions until the skill triggers.
/// @throws SkillValidationError if the file is missing or fails validation.
GAIA_API Skill parseSkillMetadata(const std::string& path, const std::string& root = "",
                                  bool readOnly = false);

// ---------------------------------------------------------------------------
// Validation and serialization
// ---------------------------------------------------------------------------

/// Validate a parsed skill's fields. Throws on the first violation.
///
/// Checks the schema-level invariants only — the `tools` ↔ tools.py
/// cross-check needs the module and lives in the skill loader.
/// @throws SkillValidationError naming the field and the rule it broke.
GAIA_API void validateSkill(const Skill& skill, const std::string& source = "<skill>");

/// Render a skill back to SKILL.md text.
GAIA_API std::string toMarkdown(const Skill& skill);

}  // namespace gaia
