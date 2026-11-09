// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/workspace.h"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <regex>

namespace fs = std::filesystem;

namespace gaia {

namespace {

std::string normalized(const fs::path& p) {
    std::error_code ec;
    fs::path abs = fs::weakly_canonical(fs::absolute(p, ec), ec);
    if (ec) abs = fs::absolute(p).lexically_normal();
    std::string s = abs.generic_string();
#ifdef _WIN32
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
#endif
    while (s.size() > 1 && s.back() == '/') s.pop_back();
    return s;
}

bool under(const std::string& path, const std::string& root) {
    if (path == root) return true;
    if (root == "/") return true;
    return path.size() > root.size() && path.compare(0, root.size(), root) == 0 &&
           path[root.size()] == '/';
}

// Places programs and scratch space live; commands may name them freely.
const std::vector<std::string>& systemDirs() {
#ifdef _WIN32
    static const std::vector<std::string> dirs = {};
#else
    static const std::vector<std::string> dirs = {
        "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/opt", "/etc",
        "/proc", "/sys", "/dev", "/tmp", "/var/tmp", "/run", "/snap", "/nix"};
#endif
    return dirs;
}

std::string expandHome(const std::string& p) {
    if (p.size() >= 1 && p[0] == '~' && (p.size() == 1 || p[1] == '/')) {
#ifdef _WIN32
        const std::string home = getEnvVar("USERPROFILE");
#else
        const std::string home = getEnvVar("HOME");
#endif
        if (!home.empty()) return home + p.substr(1);
    }
    return p;
}

}  // namespace

Workspace::Workspace(std::vector<std::string> roots) {
    if (roots.empty()) {
        std::error_code ec;
        roots.push_back(fs::current_path(ec).string());
    }
    for (const auto& r : roots) roots_.push_back(normalized(expandHome(r)));
}

bool Workspace::contains(const std::string& path) const {
    const fs::path p = expandHome(path);
    const std::string abs =
        normalized(p.is_absolute() ? p : fs::path(roots_.front()) / p);
    return std::any_of(roots_.begin(), roots_.end(),
                       [&](const std::string& r) { return under(abs, r); });
}

std::string Workspace::refusal(const std::string& path) const {
    std::string list;
    for (const auto& r : roots_) list += (list.empty() ? "" : ", ") + r;
    return "Refused: " + path + " is outside the folders this agent may use (" + list +
           "). Work inside them. If the user needs another folder, say so: they can start "
           "the agent with --allow-dir <folder>.";
}

std::string Workspace::checkPath(const std::string& path) const {
    if (path.empty() || contains(path)) return "";
    return refusal(path);
}

std::string Workspace::checkCommand(const std::string& command) const {
    // Absolute POSIX paths, ~/ paths and Windows drive paths, as standalone words.
    static const std::regex pathRe(
        R"((?:^|[\s'"=(:,`])((?:/|~/|[A-Za-z]:[\\/])[^\s'"`;|&()<>,]*))");
    for (auto it = std::sregex_iterator(command.begin(), command.end(), pathRe);
         it != std::sregex_iterator(); ++it) {
        std::string token = (*it)[1].str();
        if (token.rfind("//", 0) == 0) continue;  // the tail of a URL (https://…)
        const std::string expanded = expandHome(token);
        if (contains(expanded)) continue;
        const std::string abs = normalized(expanded);
        const bool system = std::any_of(systemDirs().begin(), systemDirs().end(),
                                        [&](const std::string& d) { return under(abs, d); });
        if (system) continue;
        // A path whose top-level folder doesn't exist is a pattern, not a place (grep '/api/v1').
        const fs::path ap(abs);
        const fs::path rel = ap.relative_path();
        if (!rel.empty()) {
            std::error_code ec;
            if (!fs::exists(ap.root_path() / *rel.begin(), ec)) continue;
        }
        return refusal(token);
    }
    return "";
}

ToolInfo Workspace::confine(ToolInfo tool, std::shared_ptr<const Workspace> ws,
                            std::vector<std::string> pathArgs) {
    ToolCallback inner = std::move(tool.callback);
    tool.callback = [inner, ws, pathArgs](const json& args) -> json {
        for (const auto& name : pathArgs) {
            if (!args.contains(name) || !args[name].is_string()) continue;
            const std::string why = ws->checkPath(args[name].get<std::string>());
            if (!why.empty()) return json{{"status", "error"}, {"error", why}};
        }
        return inner(args);
    };
    return tool;
}

ToolInfo Workspace::confineShell(ToolInfo tool, std::shared_ptr<const Workspace> ws,
                                 std::string commandArg) {
    ToolCallback inner = std::move(tool.callback);
    tool.callback = [inner, ws, commandArg](const json& args) -> json {
        if (args.contains(commandArg) && args[commandArg].is_string()) {
            const std::string why = ws->checkCommand(args[commandArg].get<std::string>());
            if (!why.empty()) return json{{"status", "error"}, {"error", why}};
        }
        return inner(args);
    };
    return tool;
}

}  // namespace gaia
