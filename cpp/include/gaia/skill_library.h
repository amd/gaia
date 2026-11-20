// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// SkillLibrary: discovers SKILL.md skills, lists them in the system prompt,
// and loads their instructions on demand — the native counterpart of Python's
// SkillManager + skill catalog + load_skill (progressive disclosure).
//
// Roots, in precedence order (an earlier root's skill shadows a same-named
// later one, and the shadowing is reported, never silent):
//   1. agent-bundled  <exe dir>/skills/<name>/
//   2. user           ~/.gaia/skills/<name>/   ($GAIA_HOME/skills when set)
//   3. claude-project ./.claude/skills/<name>/ (loadable, not listed)
//   4. claude-import  ~/.claude/skills/<name>/ (loadable, not listed)
//
// Skills that ship their own tools (tools.py) need the Python runtime; here
// they load as instructions only, and load_skill says so.

#pragma once

#include <map>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/skill.h"
#include "gaia/skill_sets.h"
#include "gaia/tool_registry.h"

namespace gaia {

struct GAIA_API SkillRoot {
    std::string label;  // agent-bundled | user | claude-project | claude-import
    std::string path;
    bool listed = true;     // shown in the catalog
    bool readOnly = false;  // imported from another host
};

class GAIA_API SkillLibrary : public SkillLoader {
public:
    /// The default roots for this process (see the file comment).
    static std::vector<SkillRoot> defaultRoots();

    explicit SkillLibrary(std::vector<SkillRoot> roots = defaultRoots());

    /// Scan every root again. Parse failures are kept in failures(), not thrown.
    void refresh();

    /// Name -> skill metadata (body not loaded), winners of precedence only.
    const std::map<std::string, Skill>& skills() const { return skills_; }

    /// "path: reason" for each SKILL.md that failed to parse.
    const std::vector<std::string>& failures() const { return failures_; }

    /// "name (root) shadowed by root" for each same-named skill that lost.
    const std::vector<std::string>& shadowed() const { return shadowed_; }

    /// The system-prompt catalog ("==== SKILLS ====" + one line per listed
    /// skill), or "" when there are none. Bodies of skills loaded through the
    /// SkillLoader seam follow it.
    std::string renderPrompt() const;

    /// Full instructions for `name`, parsed from disk. Throws SkillError when
    /// no such skill exists or it no longer parses.
    Skill loadFull(const std::string& name) const;

    /// list_skills and load_skill.
    void registerTools(ToolRegistry& registry);

    /// The user root (~/.gaia/skills, or $GAIA_HOME/skills).
    static std::string userRoot();

    /// Copy the skill folder `source` (holding SKILL.md) into `destRoot`,
    /// replacing an installed copy of the same name. The skill must validate
    /// and its folder must be named after it. Returns the installed folder.
    /// Throws SkillError / std::runtime_error naming what was wrong.
    ///
    /// A signed bundle (SIGNATURE.json) must verify, or the install is refused
    /// as tampered. A skill that is unsigned, or signed by a key the trust store
    /// doesn't know, is experimental and installs only with allowExperimental —
    /// the same gate as `gaia skill install --allow-experimental`. `tierOut`
    /// receives verified / community / experimental.
    static std::string installFromFolder(const std::string& source,
                                         const std::string& destRoot = userRoot(),
                                         bool allowExperimental = false,
                                         std::string* tierOut = nullptr);

    /// The skill hub origin: $GAIA_HUB_URL, else https://hub.amd-gaia.ai.
    static std::string hubUrl();

    /// Download `name` (exact `version`, or the latest when empty) from the
    /// hub, check the bundle's SHA-256 against the hub manifest, unpack it and
    /// install it through installFromFolder (signature check and tier gate).
    static std::string installFromHub(const std::string& name, const std::string& version = "",
                                      const std::string& destRoot = userRoot(),
                                      bool allowExperimental = false,
                                      std::string* tierOut = nullptr);

    /// Remove an installed skill from `root`. Returns false when absent.
    static bool removeInstalled(const std::string& name, const std::string& root = userRoot());

    // ---- SkillLoader (manifest skill sets) ----
    bool loadSkill(const std::string& name) override;
    bool unloadSkill(const std::string& name) override;
    bool isLoaded(const std::string& name) const override;
    std::string loadedVersion(const std::string& name) const override;

private:
    std::vector<SkillRoot> roots_;
    std::map<std::string, Skill> skills_;
    std::vector<std::string> failures_;
    std::vector<std::string> shadowed_;

    mutable std::mutex loadedMutex_;
    std::map<std::string, Skill> loaded_;  // full skills loaded via the seam
};

} // namespace gaia
