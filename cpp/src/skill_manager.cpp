// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/skill_manager.h"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <sstream>

#include "gaia/flagship_config.h"
#include "gaia/tool_registry.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

const char* const kFormatDocs = "https://amd-gaia.ai/docs/plans/skill-format";

const char* const kGroundingRule =
    "==== SOURCING ====\n"
    "Facts about live external state — a repository, an inbox, a calendar, a web page, a file "
    "on disk — must come from a tool call in THIS turn. Memory, training, and earlier turns are "
    "not sources for them. If no registered tool can fetch what was asked, say you cannot and "
    "name what is missing. Never answer from recollection, and never present an example as real "
    "data.";

const char* const kCatalogHeader =
    "==== SKILLS ====\n"
    "Installed skills. Each carries instructions for a kind of work; some unlock a CLI (such as "
    "gh) that is refused until the skill is loaded. Skills combine: call load_skill for EVERY "
    "skill whose description matches any part of the request — what it reads (files, data, logs, "
    "notes, the web) and what it produces (a deck, workbook, document, report). When several "
    "match, load each one before starting, even if they overlap — also when you realise it "
    "partway through. A skill under LOADED SKILLS is loaded; do not load it again.";

constexpr std::size_t kCatalogDescriptionChars = 480;
constexpr std::size_t kCatalogPairsShown = 3;

// Python's " ".join(s.split()), then the 480-character cap with an ellipsis.
std::string catalogDescription(const std::string& description) {
    std::string text;
    std::istringstream in(description);
    for (std::string word; in >> word;) {
        if (!text.empty()) text += ' ';
        text += word;
    }
    // Python counts characters; count UTF-8 lead bytes to match.
    std::size_t chars = 0, cut = std::string::npos;
    for (std::size_t i = 0; i < text.size(); ++i) {
        if ((static_cast<unsigned char>(text[i]) & 0xC0) != 0x80) {
            if (chars == kCatalogDescriptionChars) {
                cut = i;
                break;
            }
            ++chars;
        }
    }
    if (cut == std::string::npos) return text;
    text.resize(cut);
    while (!text.empty() && std::isspace(static_cast<unsigned char>(text.back()))) text.pop_back();
    return text + "…";
}

std::vector<std::string> pairsWith(const Skill& skill) {
    std::vector<std::string> out;
    auto it = skill.gaia.extra.find("pairs_with");
    if (it == skill.gaia.extra.end() || !it->is_array()) return out;
    for (const auto& v : *it) {
        if (v.is_string()) out.push_back(v.get<std::string>());
    }
    return out;
}

std::string joined(const std::vector<std::string>& items, const std::string& sep) {
    std::string out;
    for (const auto& s : items) {
        if (!out.empty()) out += sep;
        out += s;
    }
    return out;
}

std::string shellBinary(const std::string& permission) {
    const std::string prefix = "shell:execute:";
    if (permission.compare(0, prefix.size(), prefix) != 0) return "";
    std::string scope = permission.substr(prefix.size());
    std::transform(scope.begin(), scope.end(), scope.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return scope;
}

}  // namespace

const std::set<std::string>& declarableBinaries() {
    static const std::set<std::string> names = {"black", "gh",  "git",    "go",      "isort", "npm",
                                                "pip",   "pytest", "python", "python3", "ruff",  "uv"};
    return names;
}

std::string skillRefusalReason(const Skill& skill) {
    if (!skill.gaia.tools.empty()) {
        return "Skill '" + skill.name +
               "' ships Python tools (metadata.gaia.tools / tools.py), which this native runtime "
               "cannot run, so it is refused rather than loaded with tools that would never "
               "exist. Use it from the Python GAIA agent, or use an instruction-only skill.";
    }
    std::vector<std::string> wholeShell, local;
    for (const auto& raw : skill.gaia.permissions) {
        const auto first = raw.find(':');
        if (first == std::string::npos) continue;  // the parser rejects this shape already
        const std::string domain = raw.substr(0, first);
        const auto second = raw.find(':', first + 1);
        const std::string level = raw.substr(first + 1, second == std::string::npos
                                                             ? std::string::npos
                                                             : second - first - 1);
        if (level == "none" || domain == "network" || domain == "mcp") continue;
        if (domain == "shell") {
            const std::string binary = shellBinary(raw);
            if (binary.empty()) {
                wholeShell.push_back(raw);
                continue;
            }
            if (!declarableBinaries().count(binary)) {
                return "Skill '" + skill.name + "' declares 'shell:execute:" + binary +
                       "', but GAIA ships no command policy for '" + binary +
                       "', so the grant cannot be enforced and the skill is refused rather than "
                       "loaded unenforced. Declarable binaries: " +
                       joined({declarableBinaries().begin(), declarableBinaries().end()}, ", ") +
                       ". See " + kFormatDocs + "#permission-model";
            }
            continue;
        }
        local.push_back(raw);
    }
    if (!wholeShell.empty()) {
        return "Skill '" + skill.name + "' declares " + joined(wholeShell, ", ") +
               ", which asks for the whole shell. GAIA grants shell access one binary at a time: "
               "scope it to the CLI the skill actually runs, e.g. 'shell:execute:gh'. See " +
               kFormatDocs + "#permission-model and https://github.com/amd/gaia/issues/1019 (Phase 2).";
    }
    if (!local.empty()) {
        return "Skill '" + skill.name + "' declares local-capability permission(s): " +
               joined(local, ", ") +
               ". GAIA's skills runtime bridges connector-backed permissions (mcp, network) and "
               "scoped binary grants (shell:execute:<binary>); the sandbox that enforces database, "
               "desktop, env, filesystem is deferred to a later phase, so this skill is refused "
               "rather than loaded without enforcement. See " +
               std::string(kFormatDocs) + "#permission-model and https://github.com/amd/gaia/issues/1019 (Phase 2).";
    }
    return "";
}

SkillManager::SkillManager(std::vector<EmbeddedSkill> embedded, std::vector<SkillRoot> roots,
                           std::set<std::string> excluded)
    : embedded_(std::move(embedded)), roots_(std::move(roots)), excluded_(std::move(excluded)) {
    rescan();
}

std::vector<SkillRoot> SkillManager::defaultRoots(const std::string& projectDir) {
    std::vector<SkillRoot> roots;
    roots.push_back({"user", userSkillsDir(), false, true});
    if (!projectDir.empty()) {
        roots.push_back({"claude-import", (fs::u8path(projectDir) / ".claude" / "skills").u8string(), true, false});
    }
    try {
        const std::string home = (fs::u8path(userHomeDir()) / ".claude" / "skills").u8string();
        std::error_code ec;
        const bool same = !projectDir.empty() &&
                          fs::weakly_canonical(fs::u8path(projectDir) / ".claude" / "skills", ec) ==
                              fs::weakly_canonical(fs::u8path(home), ec);
        if (!same) roots.push_back({"claude-import", home, true, false});
    } catch (const std::runtime_error&) {
        // No home directory: the user root above already named the problem.
    }
    return roots;
}

void SkillManager::add(Scan& scan, Skill skill, const std::string& rootLabel, bool listed,
                       const std::string& location) const {
    if (excluded_.count(skill.name)) return;
    if (scan.skills.count(skill.name)) {
        scan.shadowed.emplace_back(skill.name, location);
        return;
    }
    DiscoveredSkill d;
    d.refusal = skillRefusalReason(skill);
    d.skill = std::move(skill);
    d.rootLabel = rootLabel;
    d.listed = listed;
    const std::string name = d.skill.name;
    scan.skills.emplace(name, std::move(d));
}

void SkillManager::rescan() {
    // Parsed off the lock, then swapped in whole, so a reader never sees a half scan.
    Scan scan;
    for (const auto& e : embedded_) {
        const std::string location = std::string("bundled:") + e.directory + "/SKILL.md";
        try {
            Skill s = parseSkill(e.text, location);
            if (s.name != e.directory) {
                throw SkillValidationError(location + ": name '" + s.name +
                                           "' must match its folder '" + e.directory + "'.");
            }
            s.root = "bundled";
            add(scan, std::move(s), "bundled", true, location);
        } catch (const SkillError& ex) {
            scan.invalid.emplace_back(location, ex.what());
        }
    }
    for (const auto& root : roots_) {
        std::error_code ec;
        const fs::path dir = fs::u8path(root.path);
        if (!fs::is_directory(dir, ec)) continue;
        std::vector<fs::path> entries;
        for (const auto& entry : fs::directory_iterator(dir, ec)) {
            if (entry.is_directory(ec)) entries.push_back(entry.path());
        }
        std::sort(entries.begin(), entries.end());
        for (const auto& sub : entries) {
            const fs::path file = sub / "SKILL.md";
            if (!fs::is_regular_file(file, ec)) continue;
            try {
                Skill s = parseSkillFile(file.u8string(), root.label, root.readOnly, true);
                add(scan, std::move(s), root.label, root.listed, file.u8string());
            } catch (const SkillError& ex) {
                scan.invalid.emplace_back(file.u8string(), ex.what());
            }
        }
    }
    std::lock_guard<std::mutex> lock(mutex_);
    skills_ = std::move(scan.skills);
    invalid_ = std::move(scan.invalid);
    shadowed_ = std::move(scan.shadowed);
}

std::map<std::string, DiscoveredSkill> SkillManager::skills() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return skills_;
}

std::optional<DiscoveredSkill> SkillManager::find(const std::string& name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = skills_.find(name);
    if (it == skills_.end()) return std::nullopt;
    return it->second;
}

std::vector<std::pair<std::string, std::string>> SkillManager::invalid() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return invalid_;
}

std::vector<std::pair<std::string, std::string>> SkillManager::shadowed() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return shadowed_;
}

std::vector<std::string> SkillManager::rootsSearched() const {
    std::vector<std::string> out = {"bundled (compiled in)"};
    for (const auto& r : roots_) out.push_back(r.path);
    return out;
}

std::vector<std::string> SkillManager::missingTools(const Skill& skill) const {
    std::vector<std::string> missing;
    if (!toolAvailable_) return missing;
    for (const auto& t : skill.gaia.toolsRequired) {
        if (!toolAvailable_(t)) missing.push_back(t);
    }
    return missing;
}

std::string SkillManager::catalogPrompt() const {
    const auto all = skills();
    std::vector<std::string> lines;
    for (const auto& [name, d] : all) {  // std::map: sorted by name
        if (!d.listed || !d.refusal.empty() || !missingTools(d.skill).empty()) continue;
        // Pairing is mutual: own declared order first, then skills naming it, sorted.
        std::vector<std::string> paired;
        for (const auto& other : pairsWith(d.skill)) {
            const auto t = all.find(other);
            if (t != all.end() && t->second.listed && other != name &&
                std::find(paired.begin(), paired.end(), other) == paired.end()) {
                paired.push_back(other);
            }
        }
        for (const auto& [otherName, other] : all) {
            if (!other.listed || otherName == name) continue;
            const auto theirs = pairsWith(other.skill);
            if (std::find(theirs.begin(), theirs.end(), name) != theirs.end() &&
                std::find(paired.begin(), paired.end(), otherName) == paired.end()) {
                paired.push_back(otherName);
            }
        }
        std::string note;
        if (!paired.empty()) {
            std::vector<std::string> shown(paired.begin(),
                                           paired.begin() + std::min(paired.size(), kCatalogPairsShown));
            note = " (pairs with: " + joined(shown, ", ");
            if (paired.size() > kCatalogPairsShown) {
                note += ", +" + std::to_string(paired.size() - kCatalogPairsShown) + " more";
            }
            note += ")";
        }
        lines.push_back("- " + name + ": " + catalogDescription(d.skill.description) + note);
    }
    if (lines.empty()) return kGroundingRule;
    return std::string(kGroundingRule) + "\n\n" + kCatalogHeader + "\n" + joined(lines, "\n");
}

// ---------------------------------------------------------------------------
// SkillRuntime
// ---------------------------------------------------------------------------

SkillRuntime::SkillRuntime(SkillManager& manager, const ToolRegistry& registry)
    : manager_(manager), registry_(registry) {}

bool SkillRuntime::loadSkill(const std::string& name) {
    const auto d = manager_.find(name);
    if (!d) return false;
    if (!d->refusal.empty()) throw SkillError(d->refusal);
    std::lock_guard<std::mutex> lock(mutex_);
    if (!loaded_.count(name)) {
        loaded_.emplace(name, d->skill);
        order_.push_back(name);
    }
    return true;
}

bool SkillRuntime::unloadSkill(const std::string& name) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!loaded_.erase(name)) return false;
    order_.erase(std::remove(order_.begin(), order_.end(), name), order_.end());
    return true;
}

bool SkillRuntime::isLoaded(const std::string& name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    return loaded_.count(name) > 0;
}

std::string SkillRuntime::loadedVersion(const std::string& name) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = loaded_.find(name);
    return (it == loaded_.end() || !it->second.version) ? "" : *it->second.version;
}

std::vector<std::string> SkillRuntime::loadedNames() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return order_;
}

std::string SkillRuntime::bodyFor(const Skill& skill) const {
    std::vector<std::string> missing;
    for (const auto& p : skill.gaia.permissions) {
        const std::string binary = shellBinary(p);
        if (!binary.empty() && findExecutable(binary).empty()) missing.push_back(binary);
    }
    if (missing.empty()) return skill.body;
    std::string note;
    for (const auto& m : missing) {
        if (!note.empty()) note += " ";
        note += "`" + m + "` is not installed, so commands that need it will fail.";
    }
    return skill.body + "\n\nOn this machine: " + note;
}

std::string SkillRuntime::loadedPrompt() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::string> sections;
    for (const auto& name : order_) {
        const Skill& s = loaded_.at(name);
        if (s.body.empty()) continue;
        sections.push_back("--- SKILL: " + s.name + " ---\n" + bodyFor(s));
    }
    if (sections.empty()) return "";
    return "==== LOADED SKILLS ====\n" + joined(sections, "\n\n");
}

std::vector<std::string> SkillRuntime::companions(const std::string& name) const {
    std::vector<std::string> out;
    const auto d = manager_.find(name);
    if (!d) return out;
    for (const auto& other : pairsWith(d->skill)) {
        const auto t = manager_.find(other);
        if (t && t->listed && t->refusal.empty() && other != name) out.push_back(other);
    }
    for (const auto& [otherName, other] : manager_.skills()) {
        if (!other.listed || !other.refusal.empty() || otherName == name) continue;
        const auto theirs = pairsWith(other.skill);
        if (std::find(theirs.begin(), theirs.end(), name) != theirs.end() &&
            std::find(out.begin(), out.end(), otherName) == out.end()) {
            out.push_back(otherName);
        }
    }
    return out;
}

json SkillRuntime::listSkillsTool() {
    manager_.rescan();
    json skills = json::array();
    json refused = json::array();
    for (const auto& [name, d] : manager_.skills()) {
        json entry = {{"name", name},
                      {"version", d.skill.version.value_or("")},
                      {"tier", d.skill.securityTier()},
                      {"source", d.rootLabel},
                      {"loaded", isLoaded(name)}};
        if (!d.refusal.empty()) {
            refused.push_back({{"name", name}, {"reason", d.refusal}});
            continue;
        }
        const auto missing = manager_.missingTools(d.skill);
        if (!missing.empty()) entry["missing_tools"] = missing;
        skills.push_back(entry);
    }
    json out = {{"status", "success"},
                {"count", skills.size()},
                {"skills", skills},
                {"loaded", loadedNames()},
                {"roots_searched", manager_.rootsSearched()}};
    if (!refused.empty()) out["refused"] = refused;
    if (!manager_.invalid().empty()) {
        json invalid = json::array();
        for (const auto& [where, why] : manager_.invalid()) invalid.push_back({{"path", where}, {"error", why}});
        out["invalid"] = invalid;
        out["warning"] = "Some SKILL.md files could not be read; see invalid.";
    }
    if (skills.empty()) {
        out["hint"] = "No skills are installed. Put a skill folder with a SKILL.md in " + userSkillsDir() + ".";
    }
    return out;
}

json SkillRuntime::loadSkillTool(const std::string& name) {
    if (name.empty()) return {{"status", "error"}, {"error", "name is required"}};
    if (!manager_.find(name)) manager_.rescan();
    const auto d = manager_.find(name);
    if (!d) {
        std::vector<std::string> names;
        for (const auto& [n, s] : manager_.skills()) {
            if (s.listed && s.refusal.empty()) names.push_back(n);
        }
        return {{"status", "error"},
                {"error", "No skill named '" + name + "' is installed. Installed skills: " +
                              (names.empty() ? std::string("(none)") : joined(names, ", ")) + "."}};
    }
    const bool already = isLoaded(name);
    try {
        loadSkill(name);
    } catch (const SkillError& e) {
        return {{"status", "error"}, {"name", name}, {"error", e.what()}};
    }
    std::vector<std::string> unmet;
    for (const auto& t : d->skill.gaia.toolsRequired) {
        if (!registry_.hasTool(t)) unmet.push_back(t);
    }
    std::vector<std::string> unavailable;
    for (const auto& p : d->skill.gaia.permissions) {
        const std::string binary = shellBinary(p);
        if (!binary.empty() && findExecutable(binary).empty()) unavailable.push_back(binary);
    }
    json out = {{"status", "success"},
                {"name", name},
                {"already_loaded", already},
                {"security_tier", d->skill.securityTier()},
                {"directory", d->skill.directory()},
                {"registered_tools", json::array()},
                {"loaded_skills", loadedNames()},
                {"prompt_tokens_estimate", (d->skill.body.size() + 3) / 4}};
    const auto paired = companions(name);
    if (!paired.empty()) {
        out["companion_skills"] = paired;
        out["companion_hint"] = "These skills pair with " + name +
                                "; load any whose description matches the rest of the request.";
    }
    if (!unmet.empty()) {
        out["unmet_tools_required"] = unmet;
        out["warning"] = "This runtime has no " + joined(unmet, ", ") +
                         " tool; steps of the skill that need it cannot run here — say so instead of "
                         "pretending they ran.";
    }
    if (!unavailable.empty()) out["unavailable_commands"] = unavailable;
    return out;
}

json SkillRuntime::unloadSkillTool(const std::string& name) {
    if (alwaysOn_.count(name)) {
        return {{"status", "error"},
                {"error", "'" + name + "' is always on for this agent and cannot be unloaded."}};
    }
    if (!unloadSkill(name)) {
        return {{"status", "error"}, {"error", "Skill '" + name + "' is not loaded."}};
    }
    std::size_t tokens = 0;
    for (const auto& n : loadedNames()) {
        if (const auto d = manager_.find(n)) tokens += (d->skill.body.size() + 3) / 4;
    }
    return {{"status", "success"}, {"name", name}, {"loaded_skills", loadedNames()},
            {"prompt_tokens_estimate", tokens}};
}

json SkillRuntime::skillStatusTool() const {
    json loaded = json::array();
    std::size_t total = 0;
    std::set<std::string> loadedSet;
    for (const auto& n : loadedNames()) {
        loadedSet.insert(n);
        const auto d = manager_.find(n);
        const std::size_t tokens = d ? (d->skill.body.size() + 3) / 4 : 0;
        total += tokens;
        loaded.push_back({{"name", n},
                          {"description", d ? d->skill.description : ""},
                          {"security_tier", d ? d->skill.securityTier() : ""},
                          {"prompt_tokens_estimate", tokens},
                          {"active_this_turn", true}});
    }
    json notLoaded = json::array();
    for (const auto& [n, d] : manager_.skills()) {
        if (d.listed && d.refusal.empty() && !loadedSet.count(n)) notLoaded.push_back(n);
    }
    return {{"status", "success"},
            {"loaded_count", loaded.size()},
            {"loaded", loaded},
            {"total_prompt_tokens_estimate", total},
            {"installed_not_loaded", notLoaded}};
}

void SkillRuntime::registerTools(ToolRegistry& registry, std::function<void()> onChange) {
    auto changed = [onChange] {
        if (onChange) onChange();
    };
    ToolInfo list;
    list.name = "list_skills";
    list.description =
        "List the skills installed on this machine, which are loaded, and any that cannot load "
        "here and why.";
    list.callback = [this](const json&) { return listSkillsTool(); };
    registry.registerTool(std::move(list));

    ToolInfo load;
    load.name = "load_skill";
    load.description =
        "Load an installed skill by name so its instructions apply to this task. Call it for "
        "every skill in the SKILLS list whose description matches part of the request.";
    load.parameters = {{"name", ToolParamType::STRING, true, "The skill's name"}};
    load.callback = [this, changed](const json& args) {
        json r = loadSkillTool(args.value("name", ""));
        if (r.value("status", "") == "success") changed();
        return r;
    };
    registry.registerTool(std::move(load));

    ToolInfo unload;
    unload.name = "unload_skill";
    unload.description = "Unload a loaded skill so its instructions stop applying.";
    unload.parameters = {{"name", ToolParamType::STRING, true, "The skill's name"}};
    unload.callback = [this, changed](const json& args) {
        json r = unloadSkillTool(args.value("name", ""));
        if (r.value("status", "") == "success") changed();
        return r;
    };
    registry.registerTool(std::move(unload));

    ToolInfo status;
    status.name = "skill_status";
    status.description = "Show which skills are loaded and roughly how much of the prompt they use.";
    status.callback = [this](const json&) { return skillStatusTool(); };
    registry.registerTool(std::move(status));
}

}  // namespace gaia
