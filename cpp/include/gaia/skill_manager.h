// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Skill discovery, the refusal gate, and load/unload/prompt injection — the
// C++ side of Python's SkillManager, refuse_unbridged_permissions(),
// skill_catalog.py and Agent.get_skills_system_prompt().
//
// Roots, highest precedence first; a lower root never overrides a higher one:
//   1. bundled   — skills compiled into the binary (EmbeddedSkill)
//   2. user      — userSkillsDir() (~/.gaia/skills)
//   3. claude-import — ./.claude/skills, then ~/.claude/skills: loadable by
//      name, read-only, never listed in the catalogue.
//
// A skill is refused — discovered, reported with the reason, never loaded —
// when it ships Python code tools (`metadata.gaia.tools`), which a native
// process cannot run, or when it asks for a permission no bridge enforces.

#pragma once

#include <functional>
#include <map>
#include <mutex>
#include <optional>
#include <set>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/skill.h"
#include "gaia/skill_sets.h"
#include "gaia/types.h"

namespace gaia {

class ToolRegistry;

/// A SKILL.md compiled into the binary.
struct GAIA_API EmbeddedSkill {
    const char* directory;  ///< the skill's folder name, which must equal its `name`
    const char* text;       ///< the full SKILL.md
};

struct GAIA_API SkillRoot {
    std::string label;  ///< "user" or "claude-import"
    std::string path;
    bool readOnly = false;
    bool listed = true;  ///< shown in the prompt catalogue
};

struct GAIA_API DiscoveredSkill {
    Skill skill;
    std::string rootLabel;  ///< "bundled", "user" or "claude-import"
    bool listed = true;
    std::string refusal;    ///< why it cannot load here; empty when loadable
};

/// The binaries a `shell:execute:<binary>` grant may name — Python's
/// BINARY_POLICIES keys, so both runtimes accept and refuse the same skills.
GAIA_API const std::set<std::string>& declarableBinaries();

/// Why @p skill cannot load in this runtime, or "" when it can.
GAIA_API std::string skillRefusalReason(const Skill& skill);

class GAIA_API SkillManager {
public:
    SkillManager(std::vector<EmbeddedSkill> embedded, std::vector<SkillRoot> roots,
                 std::set<std::string> excluded = {});

    /// userSkillsDir(), then <projectDir>/.claude/skills and ~/.claude/skills.
    static std::vector<SkillRoot> defaultRoots(const std::string& projectDir);

    /// Re-read every root. Parse failures are kept in invalid(), not thrown.
    /// Safe to call while another thread reads: the accessors return copies.
    void rescan();

    std::map<std::string, DiscoveredSkill> skills() const;
    std::optional<DiscoveredSkill> find(const std::string& name) const;
    /// (location, error) for every SKILL.md that failed to parse.
    std::vector<std::pair<std::string, std::string>> invalid() const;
    /// (name, location) for every copy a higher root shadowed.
    std::vector<std::pair<std::string, std::string>> shadowed() const;
    std::vector<std::string> rootsSearched() const;

    /// The SOURCING rule plus one line per listed, loadable skill, in Python's
    /// get_skill_catalog_system_prompt() format. A skill whose tools_required
    /// names a tool this runtime lacks is left out (see setToolAvailable).
    std::string catalogPrompt() const;

    /// Tells the catalogue which tools exist. Unset, every skill is listed.
    void setToolAvailable(std::function<bool(const std::string&)> available) {
        toolAvailable_ = std::move(available);
    }
    /// tools_required entries of @p skill that this runtime does not have.
    std::vector<std::string> missingTools(const Skill& skill) const;

private:
    struct Scan {
        std::map<std::string, DiscoveredSkill> skills;
        std::vector<std::pair<std::string, std::string>> invalid;
        std::vector<std::pair<std::string, std::string>> shadowed;
    };
    void add(Scan& scan, Skill skill, const std::string& rootLabel, bool listed,
             const std::string& location) const;

    mutable std::mutex mutex_;  // guards the three scan results below
    std::vector<EmbeddedSkill> embedded_;
    std::vector<SkillRoot> roots_;
    std::set<std::string> excluded_;
    std::map<std::string, DiscoveredSkill> skills_;
    std::vector<std::pair<std::string, std::string>> invalid_;
    std::vector<std::pair<std::string, std::string>> shadowed_;
    std::function<bool(const std::string&)> toolAvailable_;
};

/// The loaded set: what the model can see, and the tools that change it.
/// Also the SkillLoader that Agent::loadSkillSet() drives.
class GAIA_API SkillRuntime : public SkillLoader {
public:
    SkillRuntime(SkillManager& manager, const ToolRegistry& registry);

    /// @throws SkillError naming the reason when the skill is refused.
    bool loadSkill(const std::string& name) override;
    bool unloadSkill(const std::string& name) override;
    bool isLoaded(const std::string& name) const override;
    std::string loadedVersion(const std::string& name) const override;

    /// Skills whose bodies render every turn and cannot be unloaded.
    void setAlwaysOn(std::set<std::string> names) { alwaysOn_ = std::move(names); }

    std::vector<std::string> loadedNames() const;

    /// "==== LOADED SKILLS ====" block, or "" when nothing is loaded.
    std::string loadedPrompt() const;

    json listSkillsTool();
    json loadSkillTool(const std::string& name);
    json unloadSkillTool(const std::string& name);
    json skillStatusTool() const;

    /// Register list_skills, load_skill, unload_skill and skill_status.
    /// @p onChange runs after any load or unload (rebuild the system prompt).
    /// A newly loaded skill's instructions are also in load_skill's result,
    /// since the agent keeps one system prompt for the whole turn.
    void registerTools(ToolRegistry& registry, std::function<void()> onChange);

private:
    std::string bodyFor(const Skill& skill) const;
    std::vector<std::string> companions(const std::string& name) const;

    SkillManager& manager_;
    const ToolRegistry& registry_;
    mutable std::mutex mutex_;
    std::vector<std::string> order_;
    std::map<std::string, Skill> loaded_;
    std::set<std::string> alwaysOn_;
};

}  // namespace gaia
