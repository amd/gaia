// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/skill_library.h"

#include <algorithm>
#include <filesystem>
#include <fstream>
#include <sstream>

#include "gaia/http_client.h"
#include "gaia/process.h"
#include "gaia/skill_signing.h"
#include "gaia/types.h"

#ifdef _WIN32
#include <windows.h>
#elif defined(__APPLE__)
#include <mach-o/dyld.h>
#include <climits>
#else
#include <unistd.h>
#include <climits>
#endif

namespace fs = std::filesystem;

namespace gaia {

namespace {

// Mirrors Python's skill_catalog.CATALOG_HEADER in intent.
constexpr const char* kCatalogHeader =
    "==== SKILLS ====\n"
    "Installed skills. Each carries instructions for a kind of work. Call load_skill "
    "for every skill whose description matches any part of the request before you "
    "start that part, then follow its instructions.";

constexpr size_t kCatalogDescriptionChars = 240;

std::string executableDir() {
#ifdef _WIN32
    wchar_t buf[MAX_PATH];
    const DWORD n = GetModuleFileNameW(nullptr, buf, MAX_PATH);
    if (n == 0 || n == MAX_PATH) return "";
    return fs::path(std::wstring(buf, n)).parent_path().string();
#elif defined(__APPLE__)
    char buf[PATH_MAX];
    uint32_t size = sizeof(buf);
    if (_NSGetExecutablePath(buf, &size) != 0) return "";
    return fs::path(buf).parent_path().string();
#else
    char buf[PATH_MAX];
    const ssize_t n = readlink("/proc/self/exe", buf, sizeof(buf) - 1);
    if (n <= 0) return "";
    return fs::path(std::string(buf, static_cast<size_t>(n))).parent_path().string();
#endif
}

std::string homeDir() {
#ifdef _WIN32
    return getEnvVar("USERPROFILE");
#else
    return getEnvVar("HOME");
#endif
}

std::string catalogDescription(const std::string& d) {
    std::string one;
    for (char c : d) one.push_back(c == '\n' || c == '\r' ? ' ' : c);
    if (one.size() <= kCatalogDescriptionChars) return one;
    size_t cut = kCatalogDescriptionChars;
    while (cut > 0 && (static_cast<unsigned char>(one[cut]) & 0xC0) == 0x80) --cut;
    return one.substr(0, cut) + "…";
}

} // namespace

std::vector<SkillRoot> SkillLibrary::defaultRoots() {
    std::vector<SkillRoot> roots;
    const std::string exe = executableDir();
    if (!exe.empty()) roots.push_back({"agent-bundled", (fs::path(exe) / "skills").string(), true, false});
    const std::string gaiaHome = getEnvVar("GAIA_HOME");
    const std::string home = homeDir();
    if (!gaiaHome.empty()) {
        roots.push_back({"user", (fs::path(gaiaHome) / "skills").string(), true, false});
    } else if (!home.empty()) {
        roots.push_back({"user", (fs::path(home) / ".gaia" / "skills").string(), true, false});
    }
    std::error_code ec;
    roots.push_back({"claude-project", (fs::current_path(ec) / ".claude" / "skills").string(), false, true});
    if (!home.empty()) {
        roots.push_back({"claude-import", (fs::path(home) / ".claude" / "skills").string(), false, true});
    }
    return roots;
}

SkillLibrary::SkillLibrary(std::vector<SkillRoot> roots) : roots_(std::move(roots)) { refresh(); }

void SkillLibrary::refresh() {
    skills_.clear();
    failures_.clear();
    shadowed_.clear();
    for (const auto& root : roots_) {
        std::error_code ec;
        if (!fs::is_directory(root.path, ec)) continue;
        std::vector<fs::path> dirs;
        for (const auto& entry : fs::directory_iterator(root.path, ec)) {
            if (entry.is_directory(ec) && fs::is_regular_file(entry.path() / "SKILL.md", ec)) {
                dirs.push_back(entry.path());
            }
        }
        std::sort(dirs.begin(), dirs.end());
        for (const auto& dir : dirs) {
            try {
                Skill s = parseSkillMetadata(dir.string(), root.label, root.readOnly);
                auto it = skills_.find(s.name);
                if (it != skills_.end()) {
                    shadowed_.push_back(s.name + " (" + root.label + ") shadowed by " + it->second.root);
                    continue;
                }
                skills_.emplace(s.name, std::move(s));
            } catch (const std::exception& e) {
                failures_.push_back((dir / "SKILL.md").string() + ": " + e.what());
            }
        }
    }
}

std::string SkillLibrary::renderPrompt() const {
    std::ostringstream out;
    bool any = false;
    for (const auto& [name, skill] : skills_) {
        const bool listed = std::any_of(roots_.begin(), roots_.end(), [&](const SkillRoot& r) {
            return r.label == skill.root && r.listed;
        });
        if (!listed) continue;
        if (!any) out << kCatalogHeader << "\n";
        any = true;
        out << "- " << name << ": " << catalogDescription(skill.description) << "\n";
    }
    std::lock_guard<std::mutex> lock(loadedMutex_);
    for (const auto& [name, skill] : loaded_) {
        out << "\n==== SKILL: " << name << " (loaded) ====\n";
        if (!skill.directory().empty()) {
            out << "Skill directory: " << skill.directory()
                << " (resolve relative paths in these instructions against it)\n";
        }
        out << skill.body << "\n";
    }
    return out.str();
}

Skill SkillLibrary::loadFull(const std::string& name) const {
    auto it = skills_.find(name);
    if (it == skills_.end()) {
        std::string known;
        for (const auto& [n, _] : skills_) known += (known.empty() ? "" : ", ") + n;
        throw SkillError("No installed skill named '" + name + "'. Installed: " +
                         (known.empty() ? "none" : known) + ".");
    }
    return parseSkillFile(it->second.directory(), it->second.root, it->second.readOnly);
}

void SkillLibrary::registerTools(ToolRegistry& registry) {
    ToolInfo list;
    list.name = "list_skills";
    list.description =
        "List the skills installed on this machine (name, description, where it is installed, "
        "loaded or not), plus any SKILL.md that failed to parse.";
    list.policy = ToolPolicy::ALLOW;
    list.callback = [this](const json&) -> json {
        json items = json::array();
        for (const auto& [name, s] : skills_) {
            items.push_back({{"name", name},
                             {"description", s.description},
                             {"root", s.root},
                             {"version", s.version ? json(*s.version) : json(nullptr)},
                             {"own_tools", s.toolNames()},
                             {"loaded", isLoaded(name)}});
        }
        return json{{"status", "success"}, {"skills", items}, {"failed", failures_},
                    {"shadowed", shadowed_}};
    };
    registry.registerTool(std::move(list));

    ToolInfo load;
    load.name = "load_skill";
    load.description =
        "Load an installed skill and return its full instructions. Do this before the work "
        "the skill covers, then follow the instructions.";
    load.parameters = {{"name", ToolParamType::STRING, true, "Skill name, as listed."}};
    load.policy = ToolPolicy::ALLOW;
    load.callback = [this](const json& args) -> json {
        const std::string name = args.value("name", "");
        Skill s;
        try {
            s = loadFull(name);
        } catch (const std::exception& e) {
            return json{{"status", "error"}, {"error", e.what()}};
        }
        json out = {{"status", "success"},
                    {"name", s.name},
                    {"directory", s.directory()},
                    {"tier", s.securityTier()},
                    {"instructions", s.body}};
        if (!s.isInstructionOnly()) {
            out["note"] = "This skill also ships Python tools (" +
                          json(s.toolNames()).dump() +
                          "), which need the Python GAIA runtime. Follow the instructions with "
                          "the tools you have.";
        }
        return out;
    };
    registry.registerTool(std::move(load));
}

std::string SkillLibrary::userRoot() {
    const std::string gaiaHome = getEnvVar("GAIA_HOME");
    if (!gaiaHome.empty()) return (fs::path(gaiaHome) / "skills").string();
    const std::string home = homeDir();
    if (home.empty()) throw SkillError("Can't find the user skill folder: HOME is not set");
    return (fs::path(home) / ".gaia" / "skills").string();
}

std::string SkillLibrary::installFromFolder(const std::string& source, const std::string& destRoot,
                                            bool allowExperimental, std::string* tierOut) {
    std::error_code ec;
    fs::path src = fs::absolute(source, ec);
    if (fs::is_regular_file(src, ec) && src.filename() == "SKILL.md") src = src.parent_path();
    if (!fs::is_regular_file(src / "SKILL.md", ec)) {
        throw SkillError("No SKILL.md in " + src.string() + ". Point at the skill's folder.");
    }
    // Validates the frontmatter and that the folder is named after the skill.
    const Skill skill = parseSkillFile(src.string(), "user", false, true);
    validateSkill(skill, (src / "SKILL.md").string());
    const SkillSignature sig =
        verifySkillBundle(src.string(), skill.name, skill.version.value_or(""), destRoot);
    if (tierOut) *tierOut = sig.tier();
    if (sig.tier() == "experimental" && !allowExperimental) {
        throw SkillError(
            std::string("'") + skill.name + "' is " +
            (sig.signedBundle ? "signed by a key you haven't trusted (" + sig.keyId + ")"
                              : "unsigned") +
            ", so it is an experimental skill. Install it only if you trust its source: "
            "rerun with --allow-experimental.");
    }

    const fs::path root(destRoot);
    fs::create_directories(root, ec);
    if (ec) throw SkillError("Can't create " + root.string() + ": " + ec.message());
    const fs::path target = root / skill.name;
    if (fs::equivalent(src, target, ec)) return target.string();  // already in place
    const fs::path staging = root / ("." + skill.name + ".installing");
    fs::remove_all(staging, ec);
    fs::copy(src, staging, fs::copy_options::recursive | fs::copy_options::copy_symlinks, ec);
    if (ec) {
        std::error_code ignore;
        fs::remove_all(staging, ignore);
        throw SkillError("Copying " + src.string() + " failed: " + ec.message());
    }
    const fs::path retired = root / ("." + skill.name + ".old");
    fs::remove_all(retired, ec);
    if (fs::exists(target, ec)) fs::rename(target, retired, ec);
    fs::rename(staging, target, ec);
    if (ec) {
        std::error_code ignore;
        if (fs::exists(retired, ignore)) fs::rename(retired, target, ignore);
        throw SkillError("Installing into " + target.string() + " failed: " + ec.message());
    }
    fs::remove_all(retired, ec);
    return target.string();
}

std::string SkillLibrary::hubUrl() {
    std::string url = getEnvVar("GAIA_HUB_URL", "https://hub.amd-gaia.ai");
    while (!url.empty() && url.back() == '/') url.pop_back();
    return url;
}

std::string SkillLibrary::installFromHub(const std::string& name, const std::string& version,
                                         const std::string& destRoot, bool allowExperimental,
                                         std::string* tierOut) {
    if (name.empty() || name.find_first_of("/\\.") != std::string::npos) {
        throw SkillError("Not a skill name: '" + name + "'");
    }
    if (version.find_first_of("^~*<>= ,") != std::string::npos) {
        throw SkillError("gaiacpp installs exact versions only (e.g. " + name +
                         "@1.0.3); got the range '" + version + "'.");
    }
    HttpClientConfig hc;
    hc.baseUrl = hubUrl();
    hc.timeoutSec = 120;
    HttpClient http(hc);
    json manifest;
    try {
        manifest = json::parse(http.get("/skills/" + name + "/manifest.json").body);
    } catch (const HttpError& e) {
        if (e.status() == 404) throw SkillError("The skill hub has no skill named '" + name + "'.");
        throw SkillError("Couldn't reach the skill hub at " + hubUrl() + ": " + e.what() +
                         " Check your network, or set GAIA_HUB_URL.");
    }
    const std::string ver = version.empty() ? manifest.value("latest_version", "") : version;
    const json versions = manifest.value("versions", json::object());
    if (ver.empty() || !versions.contains(ver)) {
        std::string published;
        for (auto& [v, _] : versions.items()) published += (published.empty() ? "" : ", ") + v;
        throw SkillError("Skill '" + name + "' has no published version '" + ver +
                         "'. Published: " + (published.empty() ? "none" : published) + ".");
    }
    json artifact = versions[ver].value("artifact", json());
    if (!artifact.is_object() && versions[ver].contains("artifacts") &&
        !versions[ver]["artifacts"].empty()) {
        artifact = versions[ver]["artifacts"][0];
    }
    const std::string filename = artifact.is_object() ? artifact.value("filename", "") : "";
    const std::string expected = artifact.is_object() ? artifact.value("sha256", "") : "";
    if (filename.empty() || expected.empty() || filename.find_first_of("/\\") != std::string::npos) {
        throw SkillError("The hub manifest for '" + name + "' " + ver +
                         " has no artifact with a filename and sha256, so it can't be verified. "
                         "This is a hub-side problem; report it.");
    }
    const std::string bytes = http.get("/skills/" + name + "/" + ver + "/" + filename).body;
    const std::string actual = skillSha256Hex(bytes);
    if (actual != expected) {
        throw SkillError("The downloaded bundle for '" + name + "' " + ver + " hashes to " + actual +
                         ", but the hub lists " + expected + ". Refusing to install.");
    }

    std::error_code ec;
    const fs::path work = fs::temp_directory_path() / ("gaia-skill-" + name + "-" + actual.substr(0, 12));
    fs::remove_all(work, ec);
    fs::create_directories(work / "x");
    const fs::path zip = work / filename;
    std::ofstream(zip, std::ios::binary) << bytes;
#ifdef _WIN32
    const std::string cmd = "tar -xf \"" + zip.string() + "\" -C \"" + (work / "x").string() + "\"";
#else
    const std::string cmd = "unzip -q -o '" + zip.string() + "' -d '" + (work / "x").string() + "'";
#endif
    const ProcessResult r = ProcessRunner::run(cmd, 60000);
    if (r.exitCode != 0) {
        fs::remove_all(work, ec);
        throw SkillError("Couldn't unpack " + filename + " (" +
                         (r.exitCode == 127 ? std::string("unzip is not installed")
                                            : r.stderr_output.substr(0, 200)) + ").");
    }
    fs::path skillDir = work / "x" / name;
    if (!fs::is_regular_file(skillDir / "SKILL.md", ec)) skillDir = work / "x";
    try {
        const std::string where = installFromFolder(skillDir.string(), destRoot, allowExperimental, tierOut);
        fs::remove_all(work, ec);
        return where;
    } catch (...) {
        fs::remove_all(work, ec);
        throw;
    }
}

bool SkillLibrary::removeInstalled(const std::string& name, const std::string& root) {
    if (name.empty() || name.find_first_of("/\\") != std::string::npos || name[0] == '.') {
        throw SkillError("Not a skill name: '" + name + "'");
    }
    const fs::path target = fs::path(root) / name;
    std::error_code ec;
    if (!fs::is_regular_file(target / "SKILL.md", ec)) return false;
    fs::remove_all(target, ec);
    if (ec) throw SkillError("Removing " + target.string() + " failed: " + ec.message());
    return true;
}

bool SkillLibrary::loadSkill(const std::string& name) {
    if (skills_.find(name) == skills_.end()) return false;
    Skill s = loadFull(name);
    std::lock_guard<std::mutex> lock(loadedMutex_);
    loaded_[name] = std::move(s);
    return true;
}

bool SkillLibrary::unloadSkill(const std::string& name) {
    std::lock_guard<std::mutex> lock(loadedMutex_);
    return loaded_.erase(name) > 0;
}

bool SkillLibrary::isLoaded(const std::string& name) const {
    std::lock_guard<std::mutex> lock(loadedMutex_);
    return loaded_.count(name) > 0;
}

std::string SkillLibrary::loadedVersion(const std::string& name) const {
    std::lock_guard<std::mutex> lock(loadedMutex_);
    auto it = loaded_.find(name);
    return it == loaded_.end() || !it->second.version ? "" : *it->second.version;
}

} // namespace gaia
