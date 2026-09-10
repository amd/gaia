// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Declarative skill sets — the `skills:` / `skill_sets:` manifest grammar.
// C++ port of src/gaia/skills/sets.py.
//
// An agent declares two things in its gaia-agent.yaml:
//
//   * `skills:`     — always-on skills, loaded on every launch.
//   * `skill_sets:` — named, mutually-exclusive bundles. Exactly **one** is
//                     active per launch, chosen by SkillSets::resolve().
//
//     skills:
//       - mailbox-hygiene            # always on
//
//     skill_sets:
//       personal: [inbox-triage, newsletter-digest]
//       work: [inbox-triage, meeting-scheduling]
//
//     default_skill_set: personal
//
// Selection order is explicit request -> agent-supplied selector ->
// `default_skill_set`. An unknown name never falls back to the default: it
// throws SkillSetError naming the valid sets (GAIA's no-silent-fallbacks rule,
// CLAUDE.md). Selecting the wrong capability bundle silently is worse than not
// launching.
//
// This header is pure data + validation — it neither reads a skill off disk nor
// loads one, so the manifest validator and the base Agent can share it.
//
// The grammar, the validation verdicts, and the error wording are a
// cross-runtime contract with sets.py: a manifest that parses in one runtime
// must parse in the other and be refused by both for the same reason (#2805).

#pragma once

#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "gaia/export.h"
#include "gaia/skill.h"

namespace gaia {

// ---------------------------------------------------------------------------
// Constants — ported verbatim from src/gaia/skills/sets.py
// ---------------------------------------------------------------------------

/// Manifest keys this module owns.
inline constexpr const char* SKILLS_KEY = "skills";
inline constexpr const char* SKILL_SETS_KEY = "skill_sets";
inline constexpr const char* DEFAULT_SET_KEY = "default_skill_set";

/// The filename searched for beside an agent's binary/module.
inline constexpr const char* AGENT_MANIFEST_FILENAME = "gaia-agent.yaml";

/// Where every skill-set error message points the author next.
inline constexpr const char* SETS_DOCS_URL =
    "https://amd-gaia.ai/docs/spec/agent-skills#skill-sets";

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/// A skill set was requested that the agent does not declare.
///
/// Never downgraded to the default set — see the header note.
class GAIA_API SkillSetError : public SkillError {
public:
    using SkillError::SkillError;
};

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Where a resolved set name came from. Surfaced in logs and asserted by tests
/// so "which rule picked this set" is never guesswork.
enum class SkillSetSource {
    Explicit,  ///< An explicit request (--skill-set / AgentConfig::skillSet).
    Selector,  ///< The agent's Agent::selectSkillSet() hook.
    Default,   ///< The manifest's `default_skill_set`.
    None,      ///< The agent declares no sets.
};

/// The wire spelling of a source — "explicit" / "selector" / "default" /
/// "none", matching sets.py's SOURCE_* constants exactly.
GAIA_API std::string toString(SkillSetSource source);

/// One entry in a `skills:` list or a `skill_sets:` bundle.
///
/// `version` is a **declaration surface only** in this phase: it is parsed,
/// validated as a non-empty string, and surfaced, but no constraint solving
/// happens until the marketplace phase (#2467) can install versioned skills.
/// It is never silently dropped — Agent::loadSkillSet() emits a loud diagnostic
/// naming the pin and the on-disk version (#2864).
///
/// `required = false` marks an optional enhancement: a missing optional skill
/// is reported and skipped, a missing required one fails the launch.
struct GAIA_API SkillRef {
    std::string name;
    std::optional<std::string> version;
    bool required = true;

    bool operator==(const SkillRef& other) const;
    bool operator!=(const SkillRef& other) const { return !(*this == other); }
};

/// The outcome of SkillSets::resolve().
struct GAIA_API SkillSetResolution {
    /// The active set, or nullopt when the agent declares no sets.
    std::optional<std::string> name;
    /// The always-on list followed by that set's list, in declaration order.
    std::vector<SkillRef> skills;
    /// Which rule chose it.
    SkillSetSource source = SkillSetSource::None;
};

/// A parsed, validated `skills:` + `skill_sets:` declaration.
///
/// Build with parseSkillSets(); the constructor does not validate. An agent
/// that declares neither block gets the empty instance, which is falsy — so
/// every existing agent keeps its current behaviour exactly.
class GAIA_API SkillSets {
public:
    /// A declared set: its name and its skills, in declaration order.
    using Entry = std::pair<std::string, std::vector<SkillRef>>;

    SkillSets() = default;
    SkillSets(std::vector<SkillRef> always, std::vector<Entry> sets,
              std::optional<std::string> defaultSet);

    /// False when the agent declares neither block (Python's __bool__).
    explicit operator bool() const { return !always_.empty() || !sets_.empty(); }
    bool empty() const { return !static_cast<bool>(*this); }

    /// Always-on skills, in declaration order.
    const std::vector<SkillRef>& always() const { return always_; }

    /// Declared set names, in manifest declaration order.
    std::vector<std::string> setNames() const;

    /// Every declared set, in declaration order.
    const std::vector<Entry>& sets() const { return sets_; }

    bool hasSet(const std::string& name) const;

    /// The named set's own skills (not including the always-on list).
    /// @throws SkillSetError if `name` is not a declared set.
    const std::vector<SkillRef>& set(const std::string& name) const;

    const std::optional<std::string>& defaultSet() const { return defaultSet_; }

    /// Always-on skills plus the named set's skills, in declaration order.
    /// Passing nullopt yields the always-on list alone.
    /// @throws SkillSetError if `name` is not a declared set.
    std::vector<SkillRef> skillsFor(const std::optional<std::string>& name) const;

    /// Pick the active set: `requested` -> `selected` -> `default_skill_set`.
    ///
    /// @param requested An explicit choice (a --skill-set flag or config field).
    ///        Highest precedence; an unknown value always throws.
    /// @param selected The agent's selector-hook answer, used only when
    ///        `requested` is empty. An unknown value throws too — a selector
    ///        that computed a name this agent does not declare is a wiring bug,
    ///        not a reason to guess.
    /// @throws SkillSetError on an unknown set name, or a `requested` name on
    ///         an agent that declares no sets.
    SkillSetResolution resolve(
        const std::optional<std::string>& requested = std::nullopt,
        const std::optional<std::string>& selected = std::nullopt) const;

private:
    /// The actionable "not declared" message, listing every valid set.
    std::string unknownSetMessage(const std::string& name,
                                  SkillSetSource source) const;

    std::vector<SkillRef> always_;
    std::vector<Entry> sets_;
    std::optional<std::string> defaultSet_;
};

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

/// Parse and validate the `skills:` / `skill_sets:` blocks of a manifest.
///
/// @param data The full manifest mapping (already YAML-loaded). Keys other than
///        `skills`, `skill_sets`, and `default_skill_set` are ignored.
/// @param where Location suffix for error messages, e.g. " in /path/to.yaml".
/// @return A validated SkillSets. Empty (and falsy) when the manifest declares
///         neither block.
/// @throws SkillValidationError on any malformed entry. Nothing is partially
///         accepted — a manifest either declares a coherent set of skills or
///         fails to load.
GAIA_API SkillSets parseSkillSets(const SkillJson& data, const std::string& where = "");

/// Parse the skill declarations out of an agent's gaia-agent.yaml.
///
/// Reads the YAML directly rather than going through the hub manifest schema, so
/// a custom agent — whose manifest need not carry the hub's publishing fields —
/// declares skills the same way a packaged agent does.
///
/// @throws SkillValidationError if the file cannot be read or parsed, or its
///         skill blocks are malformed. An agent whose own manifest cannot be
///         read is broken, not degraded: an unreadable manifest may be hiding a
///         `skills:` block, so GAIA will not assume the agent declares none.
GAIA_API SkillSets parseSkillSetsFile(const std::string& path);

/// Find the manifest whose `skills:` block applies to an agent living at `dir`.
///
/// Searches `dir` then its parent — the two layouts GAIA ships (a packaged
/// agent keeps the manifest one level above its binary; a source tree keeps it
/// beside the agent). It stops there deliberately: walking further up would
/// eventually claim an unrelated manifest from a sibling or the repo root.
///
/// @return The manifest path, or an empty string when there is none.
GAIA_API std::string findAgentManifest(const std::string& dir);

/// findAgentManifest() against the running executable's own directory — the
/// layout a packaged native agent ships (binary and gaia-agent.yaml side by
/// side, or the manifest one level up).
///
/// @return The manifest path, or an empty string when there is none.
GAIA_API std::string findAgentManifestNearExecutable();

// ---------------------------------------------------------------------------
// The load/unload seam
// ---------------------------------------------------------------------------

/// How Agent::loadSkillSet() actually brings a skill in and takes it out.
///
/// Skill *discovery* (the three roots), permission gating, and prompt injection
/// are P3.3's job (#2800) and are not implemented yet. This interface is the
/// seam between them: skill-set resolution and ownership tracking live here and
/// are complete; #2800 supplies a SkillManager-backed implementation and calls
/// Agent::setSkillLoader(). Until it does, an Agent has no loader, and
/// loadSkillSet() still resolves the set — including throwing on an undeclared
/// name — but registers nothing.
class GAIA_API SkillLoader {
public:
    virtual ~SkillLoader();

    /// Load the named skill.
    ///
    /// @return false when no skill of that name exists in any root.
    ///         loadSkillSet() turns that into a hard failure for a required
    ///         reference and a reported skip for an optional one. Every other
    ///         failure should throw, so it propagates and triggers rollback.
    virtual bool loadSkill(const std::string& name) = 0;

    /// Unload a skill. @return true if it was loaded.
    ///
    /// Should not throw: it runs during rollback of a failed set load, where an
    /// exception would displace the original, actionable failure. A throw is
    /// caught and reported rather than propagated.
    virtual bool unloadSkill(const std::string& name) = 0;

    /// True when the skill is currently loaded.
    virtual bool isLoaded(const std::string& name) const = 0;

    /// The on-disk `version:` of a loaded skill, or empty when it declares
    /// none. Read only to report a declared version pin against reality — see
    /// Agent::loadSkillSet() and #2864.
    virtual std::string loadedVersion(const std::string& name) const = 0;
};

}  // namespace gaia
