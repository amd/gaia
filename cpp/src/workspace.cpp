// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/workspace.h"

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <regex>
#include <set>
#include <sstream>

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
        "/sys", "/dev", "/tmp", "/var/tmp", "/snap", "/nix",
        // Machine facts only: /proc/<pid> holds other programs' environments.
        "/proc/cpuinfo", "/proc/meminfo", "/proc/version", "/proc/uptime", "/proc/loadavg"};
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

// Git Bash spells C:\Users as /c/Users or /cygdrive/c/Users.
std::string fromShellPath(std::string p) {
#ifdef _WIN32
    if (p.rfind("/cygdrive/", 0) == 0) p.erase(0, 9);
    if (p.size() >= 2 && p[0] == '/' && std::isalpha(static_cast<unsigned char>(p[1])) &&
        (p.size() == 2 || p[2] == '/')) {
        p = std::string(1, p[1]) + ":" + (p.size() == 2 ? "/" : p.substr(2));
    }
#endif
    return p;
}

bool inSystemDir(const std::string& abs) {
    return std::any_of(systemDirs().begin(), systemDirs().end(),
                       [&](const std::string& d) { return under(abs, d); });
}

// An absolute path whose top-level folder doesn't exist is a pattern, not a place (grep '/api/v1').
bool looksLikePattern(const std::string& abs) {
    const fs::path ap(abs);
    const fs::path rel = ap.relative_path();
    if (rel.empty()) return false;
    std::error_code ec;
    return !fs::exists(ap.root_path() / *rel.begin(), ec);
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
        if (token.rfind("//", 0) == 0) {
            // After a colon it is the tail of a URL (https://…); otherwise a network share.
            const size_t at = static_cast<size_t>((*it).position(1));
            if (at > 0 && command[at - 1] == ':') continue;
            return refusal(token);
        }
        if (token == "/") {
            // The root only as a standalone word (find / …), not a regex
            // delimiter such as awk's gsub(/ /, …).
            const size_t at = static_cast<size_t>((*it).position(1));
            const char before = at == 0 ? ' ' : command[at - 1];
            const char after = at + 1 < command.size() ? command[at + 1] : ' ';
            const bool word = std::isspace(static_cast<unsigned char>(before)) &&
                              (std::isspace(static_cast<unsigned char>(after)) ||
                               std::string(";|&>").find(after) != std::string::npos);
            if (!word) continue;
            // ...and only as an argument to a command that walks or changes the
            // filesystem; `a / b` in inline code is division.
            static const std::set<std::string> fsCommands = {
                "find", "ls", "du", "cd", "rm", "cp", "mv", "tar", "chmod", "chown", "grep",
                "rg", "fd", "locate", "tree", "stat", "rsync", "zip", "7z", "dir", "Get-ChildItem"};
            const size_t segStart = command.find_last_of(";|&\n(", at);
            std::istringstream seg(command.substr(segStart == std::string::npos ? 0 : segStart + 1,
                                                  at - (segStart == std::string::npos ? 0 : segStart + 1)));
            std::string w, program;
            while (seg >> w) {
                if (program.empty() && w.find('=') == std::string::npos && w != "sudo") program = w;
            }
            if (!fsCommands.count(fs::path(program).filename().string())) continue;
        }
        const std::string expanded = expandHome(fromShellPath(token));
        if (contains(expanded)) continue;
        const std::string abs = normalized(expanded);
        if (inSystemDir(abs) || looksLikePattern(abs)) continue;
        return refusal(token);
    }
    // \\host\share, which the path pattern above does not see.
    static const std::regex uncRe(R"((?:^|[\s'"=(,`])(\\\\[^\s\\'"]+))");
    std::smatch unc;
    if (std::regex_search(command, unc, uncRe)) return refusal(unc[1].str());
    return "";
}

bool Workspace::readsStayInside(const std::vector<std::vector<std::string>>& segments,
                                const std::string& cwd) const {
    auto inside = [this](const std::string& abs) {
        return std::any_of(roots_.begin(), roots_.end(), [&](const std::string& r) { return under(abs, r); });
    };
    // `cd` in a pipeline changes nothing and `cd` after ; or && does, so a
    // later word is checked against every folder the command could be in.
    std::vector<std::string> folders = {normalized(cwd.empty() ? roots_.front() : fromShellPath(cwd))};
    if (!inside(folders.front())) return false;
    for (const auto& words : segments) {
        if (words.empty()) return false;
        const bool cd = words[0] == "cd";
        if (cd && (words.size() != 2 || words[1] == "-")) return false;  // home, or the previous folder
        for (size_t i = 1; i < words.size(); ++i) {
            std::string w = words[i];
            if (w.empty()) continue;
            if (w[0] == '-') {
                const size_t eq = w.find('=');
                if (eq != std::string::npos) w = w.substr(eq + 1);
                else if (w.find_first_of("/\\") != std::string::npos) return false;  // -f/etc/x
                else continue;
                if (w.empty()) continue;
            }
            if (w[0] == '~' && w != "~" && w.rfind("~/", 0) != 0) return false;  // ~other-user
            // A component such as .* can match the parent folder.
            for (size_t at = 0; at < w.size(); ++at) {
                if (w[at] != '.' || (at > 0 && w[at - 1] != '/' && w[at - 1] != '\\')) continue;
                const size_t end = w.find_first_of("/\\", at);
                if (w.substr(at, end - at).find_first_of("*?[") != std::string::npos) return false;
            }
            const fs::path p = expandHome(fromShellPath(w));
            const size_t known = folders.size();
            for (size_t f = 0; f < known; ++f) {
                const std::string abs = normalized(p.is_absolute() ? p : fs::path(folders[f]) / p);
                if (inside(abs)) {
                    if (cd) folders.push_back(abs);
                    continue;
                }
                if (cd) return false;
                if (p.has_root_directory() && (inSystemDir(abs) || looksLikePattern(abs))) continue;
                return false;
            }
        }
    }
    return true;
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
