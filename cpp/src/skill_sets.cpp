// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// C++ port of src/gaia/skills/sets.py. See include/gaia/skill_sets.h.
//
// Every error string here is a transcription of its Python counterpart, not a
// paraphrase: #2805 asserts the two runtimes refuse the same manifest with the
// same words, so a skill author gets one answer regardless of which runtime
// read the file.

#include "gaia/skill_sets.h"

#include "skill_yaml.h"

#include <yaml-cpp/yaml.h>

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <map>
#include <regex>
#include <set>
#include <sstream>

#if defined(_WIN32)
#  ifndef WIN32_LEAN_AND_MEAN
#    define WIN32_LEAN_AND_MEAN
#  endif
#  include <windows.h>
#elif defined(__APPLE__)
#  include <mach-o/dyld.h>
#endif

namespace fs = std::filesystem;

namespace gaia {

using detail::pyRepr;
using detail::pyTypeName;
using detail::yamlToJson;

namespace {

/// Set names use the same slug shape as skill names so a set can be typed on a
/// command line without quoting: lowercase alphanumeric with internal hyphens.
const std::regex& setNamePattern() {
    static const std::regex re(R"(^[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$)");
    return re;
}

/// The skill-name shape, shared with the SKILL.md parser (format.py's
/// NAME_PATTERN).
const std::regex& skillNamePattern() {
    static const std::regex re(R"(^[a-z0-9]+(-[a-z0-9]+)*$)");
    return re;
}

/// Recognized keys in the mapping form of a skill reference.
const std::set<std::string>& refKeys() {
    static const std::set<std::string> keys{"name", "required", "version"};
    return keys;
}

std::string joinStrings(const std::vector<std::string>& items, const char* sep) {
    std::string out;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i) out += sep;
        out += items[i];
    }
    return out;
}

std::string stripWhitespace(const std::string& text) {
    const char* ws = " \t\r\n\f\v";
    const size_t begin = text.find_first_not_of(ws);
    if (begin == std::string::npos) return "";
    const size_t end = text.find_last_not_of(ws);
    return text.substr(begin, end - begin + 1);
}

/// Normalize an optional string the way sets.py does: blank means unset.
std::optional<std::string> blankToNullopt(const std::optional<std::string>& value) {
    if (!value.has_value()) return std::nullopt;
    const std::string trimmed = stripWhitespace(*value);
    if (trimmed.empty()) return std::nullopt;
    return trimmed;
}

const SkillJson& at(const SkillJson& obj, const char* key) {
    static const SkillJson kNull = nullptr;
    const auto it = obj.find(key);
    return it == obj.end() ? kNull : *it;
}

/// The trailing "See <url>." every message ends with.
std::string seeDocs() { return std::string(" See ") + SETS_DOCS_URL + "."; }

std::string validatedName(const SkillJson& value, const std::string& fieldName,
                          const std::string& where) {
    if (!value.is_string() || stripWhitespace(value.get<std::string>()).empty()) {
        throw SkillValidationError("'" + fieldName + "'" + where +
                                   " is missing a skill name. Every entry needs a "
                                   "non-empty 'name'." +
                                   seeDocs());
    }
    const std::string name = stripWhitespace(value.get<std::string>());
    if (!std::regex_match(name, skillNamePattern())) {
        throw SkillValidationError("'" + fieldName + "'" + where + " names skill " +
                                   pyRepr(name) +
                                   ", which is not a valid skill name. Use lowercase "
                                   "letters, digits, and internal hyphens (e.g. "
                                   "'inbox-triage')." +
                                   seeDocs());
    }
    return name;
}

/// Parse one reference: `"name"` or `{name, version?, required?}`.
SkillRef parseRef(const SkillJson& entry, const std::string& fieldName,
                  const std::string& where) {
    if (entry.is_string()) {
        SkillRef ref;
        ref.name = validatedName(entry, fieldName, where);
        return ref;
    }

    if (!entry.is_object()) {
        throw SkillValidationError("'" + fieldName + "'" + where +
                                   " must be a skill name or a mapping with a 'name' "
                                   "key, got " +
                                   pyTypeName(entry) + "." + seeDocs());
    }

    std::vector<std::string> unknown;
    for (auto it = entry.begin(); it != entry.end(); ++it) {
        // Rendered as Python would render the key it actually loaded, so a bare
        // `on:` reads `True` in both runtimes rather than `on` in one of them.
        if (refKeys().count(it.key()) == 0) {
            unknown.push_back(detail::pyStr(detail::resolvePlainScalar(it.key())));
        }
    }
    if (!unknown.empty()) {
        // sorted() in Python; the valid-key list is sorted too.
        std::sort(unknown.begin(), unknown.end());
        throw SkillValidationError(
            "'" + fieldName + "'" + where + " has unrecognized key(s): " +
            joinStrings(unknown, ", ") + ". Valid keys: " +
            joinStrings({"name", "required", "version"}, ", ") + "." + seeDocs());
    }

    SkillRef ref;
    ref.name = validatedName(at(entry, "name"), fieldName, where);

    const SkillJson& version = at(entry, "version");
    if (!version.is_null()) {
        if (!version.is_string() ||
            stripWhitespace(version.get<std::string>()).empty()) {
            throw SkillValidationError("'" + fieldName + ".version'" + where +
                                       " must be a non-empty string (a version or "
                                       "range, e.g. '>=1.0.0'), got " +
                                       pyRepr(version) + "." + seeDocs());
        }
        ref.version = stripWhitespace(version.get<std::string>());
    }

    // Python reads `entry.get("required", True)`, so a *present* `required:`
    // must be a bool — an explicit `required: null` is an error, an absent key
    // is the True default. `at()` cannot tell those apart; find() can.
    const auto requiredIt = entry.find("required");
    if (requiredIt != entry.end()) {
        if (!requiredIt->is_boolean()) {
            throw SkillValidationError("'" + fieldName + ".required'" + where +
                                       " must be true or false, got " +
                                       pyRepr(*requiredIt) + "." + seeDocs());
        }
        ref.required = requiredIt->get<bool>();
    }

    return ref;
}

/// Parse a list of skill references (plain strings and/or mappings).
std::vector<SkillRef> parseRefList(const SkillJson& raw, const std::string& fieldName,
                                   const std::string& where, bool allowEmpty) {
    // A bare `work:` key with nothing indented under it parses as null. For a
    // set that is the same authoring mistake as an empty list — checked before
    // the null shortcut, or the emptiness error below is unreachable.
    if (raw.is_null() && allowEmpty) return {};
    if (raw.is_null()) {
        const size_t dot = fieldName.rfind('.');
        const std::string leaf =
            dot == std::string::npos ? fieldName : fieldName.substr(dot + 1);
        throw SkillValidationError("'" + fieldName + ":'" + where +
                                   " names no skills. A skill set must list at least "
                                   "one skill (a bare '" +
                                   leaf +
                                   ":' key with nothing under it parses as empty), or "
                                   "be removed." +
                                   seeDocs());
    }
    if (!raw.is_array()) {
        throw SkillValidationError("'" + fieldName + ":'" + where +
                                   " must be a list of skill names (or mappings with "
                                   "'name'), got " +
                                   pyTypeName(raw) + "." + seeDocs());
    }
    if (raw.empty() && !allowEmpty) {
        throw SkillValidationError("'" + fieldName + ":'" + where +
                                   " is empty. A skill set must name at least one "
                                   "skill, or be removed." +
                                   seeDocs());
    }

    std::vector<SkillRef> refs;
    std::map<std::string, size_t> seen;
    for (size_t index = 0; index < raw.size(); ++index) {
        const SkillRef ref =
            parseRef(raw[index], fieldName + "[" + std::to_string(index) + "]", where);
        const auto previous = seen.find(ref.name);
        if (previous != seen.end()) {
            throw SkillValidationError(
                "'" + fieldName + ":'" + where + " lists skill " + pyRepr(ref.name) +
                " twice (entries " + std::to_string(previous->second) + " and " +
                std::to_string(index) + "). Remove the duplicate." + seeDocs());
        }
        seen.emplace(ref.name, index);
        refs.push_back(ref);
    }
    return refs;
}

}  // namespace

// ---------------------------------------------------------------------------
// SkillSetSource
// ---------------------------------------------------------------------------

std::string toString(SkillSetSource source) {
    switch (source) {
        case SkillSetSource::Explicit: return "explicit";
        case SkillSetSource::Selector: return "selector";
        case SkillSetSource::Default:  return "default";
        case SkillSetSource::None:     return "none";
    }
    return "none";
}

// ---------------------------------------------------------------------------
// SkillRef
// ---------------------------------------------------------------------------

bool SkillRef::operator==(const SkillRef& other) const {
    return name == other.name && version == other.version && required == other.required;
}

// ---------------------------------------------------------------------------
// SkillSets
// ---------------------------------------------------------------------------

SkillSets::SkillSets(std::vector<SkillRef> always, std::vector<Entry> sets,
                     std::optional<std::string> defaultSet)
    : always_(std::move(always)), sets_(std::move(sets)),
      defaultSet_(std::move(defaultSet)) {}

std::vector<std::string> SkillSets::setNames() const {
    std::vector<std::string> names;
    names.reserve(sets_.size());
    for (const auto& entry : sets_) names.push_back(entry.first);
    return names;
}

bool SkillSets::hasSet(const std::string& name) const {
    for (const auto& entry : sets_) {
        if (entry.first == name) return true;
    }
    return false;
}

const std::vector<SkillRef>& SkillSets::set(const std::string& name) const {
    for (const auto& entry : sets_) {
        if (entry.first == name) return entry.second;
    }
    throw SkillSetError(unknownSetMessage(name, SkillSetSource::Explicit));
}

std::vector<SkillRef> SkillSets::skillsFor(
    const std::optional<std::string>& name) const {
    if (!name.has_value()) return always_;
    const std::vector<SkillRef>& own = set(*name);  // throws when undeclared
    std::vector<SkillRef> combined = always_;
    combined.insert(combined.end(), own.begin(), own.end());
    return combined;
}

SkillSetResolution SkillSets::resolve(const std::optional<std::string>& requested,
                                      const std::optional<std::string>& selected) const {
    const std::optional<std::string> want = blankToNullopt(requested);
    const std::optional<std::string> pick = blankToNullopt(selected);

    if (sets_.empty()) {
        if (want.has_value()) {
            throw SkillSetError("Skill set " + pyRepr(*want) +
                                " was requested but this agent declares no "
                                "'skill_sets:' block, so there is nothing to select. "
                                "Drop the --skill-set argument, or add a "
                                "'skill_sets:' block to its gaia-agent.yaml." +
                                seeDocs());
        }
        return SkillSetResolution{std::nullopt, always_, SkillSetSource::None};
    }

    const std::pair<std::optional<std::string>, SkillSetSource> candidates[] = {
        {want, SkillSetSource::Explicit},
        {pick, SkillSetSource::Selector},
        {defaultSet_, SkillSetSource::Default},
    };
    for (const auto& candidate : candidates) {
        if (!candidate.first.has_value()) continue;
        if (!hasSet(*candidate.first)) {
            throw SkillSetError(unknownSetMessage(*candidate.first, candidate.second));
        }
        return SkillSetResolution{*candidate.first, skillsFor(*candidate.first),
                                  candidate.second};
    }

    // Unreachable via parseSkillSets (a non-empty skill_sets block requires
    // default_skill_set), but a hand-built SkillSets could get here.
    throw SkillSetError("No skill set could be resolved: nothing was requested, the "
                        "selector returned nothing, and no 'default_skill_set' is "
                        "declared. Declared sets: " +
                        joinStrings(setNames(), ", ") + ". Set '" + DEFAULT_SET_KEY +
                        ":' in gaia-agent.yaml." + seeDocs());
}

std::string SkillSets::unknownSetMessage(const std::string& name,
                                         SkillSetSource source) const {
    const std::vector<std::string> names = setNames();
    const std::string valid = names.empty() ? "(none)" : joinStrings(names, ", ");
    std::string origin;
    switch (source) {
        case SkillSetSource::Selector:
            origin = "The agent's skill-set selector returned skill set";
            break;
        case SkillSetSource::Default:
            origin = std::string("'") + DEFAULT_SET_KEY + "' names skill set";
            break;
        default:
            origin = "Skill set";
            break;
    }
    return origin + " " + pyRepr(name) + " is not declared by this agent. Valid sets: " +
           valid + ". Pass one of those names, or add " + pyRepr(name) + " to the '" +
           SKILL_SETS_KEY + ":' block in gaia-agent.yaml." + seeDocs();
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

SkillSets parseSkillSets(const SkillJson& data, const std::string& where) {
    if (data.is_null()) return SkillSets();
    if (!data.is_object()) {
        throw SkillValidationError("Cannot read skill declarations" + where +
                                   ": expected a mapping, got " + pyTypeName(data) +
                                   "." + seeDocs());
    }

    std::vector<SkillRef> always =
        parseRefList(at(data, SKILLS_KEY), SKILLS_KEY, where, /*allowEmpty=*/true);
    std::set<std::string> alwaysNames;
    for (const auto& ref : always) alwaysNames.insert(ref.name);

    std::vector<SkillSets::Entry> sets;
    const SkillJson& rawSets = at(data, SKILL_SETS_KEY);
    if (!rawSets.is_null()) {
        if (!rawSets.is_object()) {
            throw SkillValidationError(std::string("'") + SKILL_SETS_KEY + ":'" + where +
                                       " must be a mapping of set name → list of "
                                       "skills, got " +
                                       pyTypeName(rawSets) + "." + seeDocs());
        }
        for (auto it = rawSets.begin(); it != rawSets.end(); ++it) {
            const std::string setName = it.key();
            // PyYAML resolves a bare `on:` / `no:` / `5:` key to a bool or int,
            // which sets.py refuses via its isinstance(str) guard. yamlToJson
            // has already flattened the key to text, so recover what Python
            // would have seen — otherwise a manifest that launches natively
            // hard-fails on the Python runtime. (A *quoted* "on": is a string to
            // PyYAML; the quoting is lost by then, so it is refused here too —
            // loud and renameable, unlike the silent accept it replaces.)
            const SkillJson asPython = detail::resolvePlainScalar(setName);
            if (!asPython.is_string() || !std::regex_match(setName, setNamePattern())) {
                throw SkillValidationError(
                    std::string("'") + SKILL_SETS_KEY + ":'" + where +
                    " has invalid set name " + pyRepr(asPython) +
                    ". Use lowercase letters, digits, and internal hyphens (1–32 "
                    "chars), e.g. 'work'." +
                    seeDocs());
            }
            std::vector<SkillRef> refs =
                parseRefList(it.value(), std::string(SKILL_SETS_KEY) + "." + setName,
                             where, /*allowEmpty=*/false);

            std::vector<std::string> clash;
            for (const auto& ref : refs) {
                if (alwaysNames.count(ref.name)) clash.push_back(ref.name);
            }
            if (!clash.empty()) {
                std::sort(clash.begin(), clash.end());
                throw SkillValidationError(
                    std::string("'") + SKILL_SETS_KEY + "." + setName + "'" + where +
                    " re-declares skill(s) already in the always-on '" + SKILLS_KEY +
                    ":' list: " + joinStrings(clash, ", ") +
                    ". An always-on skill loads for every set — remove it from the "
                    "set, or from '" +
                    SKILLS_KEY + ":' if it is set-specific." + seeDocs());
            }
            sets.emplace_back(setName, std::move(refs));
        }
    }

    const SkillJson& rawDefault = at(data, DEFAULT_SET_KEY);
    if (!rawDefault.is_null() && !rawDefault.is_string()) {
        throw SkillValidationError(std::string("'") + DEFAULT_SET_KEY + ":'" + where +
                                   " must be a string naming one of the declared skill "
                                   "sets, got " +
                                   pyTypeName(rawDefault) + "." + seeDocs());
    }
    std::optional<std::string> defaultSet;
    if (rawDefault.is_string()) {
        defaultSet = blankToNullopt(rawDefault.get<std::string>());
    }

    std::vector<std::string> declared;
    for (const auto& entry : sets) declared.push_back(entry.first);

    if (!sets.empty() && !defaultSet.has_value()) {
        throw SkillValidationError(
            std::string("'") + SKILL_SETS_KEY + ":'" + where + " declares set(s) " +
            joinStrings(declared, ", ") + " but no '" + DEFAULT_SET_KEY + ":'. Add '" +
            DEFAULT_SET_KEY +
            ": <name>' so a launch that selects nothing still resolves a set "
            "explicitly." +
            seeDocs());
    }
    if (defaultSet.has_value()) {
        bool found = false;
        for (const auto& entry : sets) {
            if (entry.first == *defaultSet) found = true;
        }
        if (!found) {
            const std::string valid =
                declared.empty() ? "(none declared)" : joinStrings(declared, ", ");
            throw SkillValidationError(std::string("'") + DEFAULT_SET_KEY + ": " +
                                       *defaultSet + "'" + where +
                                       " does not name a declared skill set. Valid "
                                       "sets: " +
                                       valid + "." + seeDocs());
        }
    }

    return SkillSets(std::move(always), std::move(sets), std::move(defaultSet));
}

SkillSets parseSkillSetsFile(const std::string& path) {
    std::ifstream file(fs::u8path(path), std::ios::binary);
    if (!file) {
        throw SkillValidationError(
            "Could not read the agent manifest at " + path +
            ": no such file, or it is not readable. Fix the path — an unreadable "
            "manifest may be hiding a 'skills:' block, so GAIA will not assume the "
            "agent declares none." +
            seeDocs());
    }
    std::ostringstream buffer;
    buffer << file.rdbuf();
    std::string text = buffer.str();

    // Tolerate a UTF-8 BOM, as the SKILL.md parser does.
    if (text.size() >= 3 && static_cast<unsigned char>(text[0]) == 0xEF &&
        static_cast<unsigned char>(text[1]) == 0xBB &&
        static_cast<unsigned char>(text[2]) == 0xBF) {
        text.erase(0, 3);
    }

    YAML::Node root;
    try {
        root = YAML::Load(text);
    } catch (const YAML::Exception& exc) {
        throw SkillValidationError(
            "Could not read the agent manifest at " + path + ": " + exc.what() +
            ". Fix the YAML — an unreadable manifest may be hiding a 'skills:' block, "
            "so GAIA will not assume the agent declares none." +
            seeDocs());
    }

    const SkillJson data = yamlToJson(root, path, "agent manifest", SETS_DOCS_URL);
    if (data.is_null()) return SkillSets();
    return parseSkillSets(data, " in " + path);
}

SkillLoader::~SkillLoader() = default;

std::string findAgentManifest(const std::string& dir) {
    if (dir.empty()) return "";
    std::error_code ec;
    const fs::path start = fs::u8path(dir);
    const fs::path candidates[] = {start, start.parent_path()};
    for (const fs::path& candidate : candidates) {
        if (candidate.empty()) continue;
        const fs::path manifest = candidate / AGENT_MANIFEST_FILENAME;
        if (fs::is_regular_file(manifest, ec)) return manifest.string();
    }
    return "";
}

std::string findAgentManifestNearExecutable() {
#if defined(_WIN32)
    std::wstring buffer(MAX_PATH, L'\0');
    for (;;) {
        const DWORD written =
            ::GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (written == 0) return "";
        if (written < buffer.size()) {
            buffer.resize(written);
            break;
        }
        buffer.resize(buffer.size() * 2);  // truncated — retry with more room
    }
    const fs::path exe(buffer);
#elif defined(__APPLE__)
    uint32_t size = 0;
    _NSGetExecutablePath(nullptr, &size);  // sets `size`; always returns -1
    std::string buffer(size, '\0');
    if (_NSGetExecutablePath(buffer.data(), &size) != 0) return "";
    buffer.resize(std::strlen(buffer.c_str()));
    std::error_code ec;
    const fs::path exe = fs::weakly_canonical(fs::path(buffer), ec);
#else
    std::error_code ec;
    const fs::path exe = fs::read_symlink("/proc/self/exe", ec);
    if (ec) return "";
#endif
    if (exe.empty()) return "";
    return findAgentManifest(exe.parent_path().string());
}

}  // namespace gaia
