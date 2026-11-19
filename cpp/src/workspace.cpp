// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/workspace.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <functional>
#include <regex>
#include <set>
#include <sstream>

namespace fs = std::filesystem;

namespace gaia {

namespace {

// Folders and paths are UTF-8 strings here, as a model and the UI write them.
std::string normalized(const fs::path& p, int depth = 0) {
    std::error_code ec;
    // A link to a file not there yet keeps its own name in weakly_canonical; a write lands on its target.
    const fs::path given = fs::absolute(p, ec);
    if (!ec && depth < 8 && fs::is_symlink(given, ec) && !fs::exists(given, ec)) {
        const fs::path target = fs::read_symlink(given, ec);
        if (!ec) return normalized(target.is_absolute() ? target : given.parent_path() / target, depth + 1);
    }
    fs::path abs = fs::weakly_canonical(fs::absolute(p, ec), ec);
    if (ec) abs = fs::absolute(p).lexically_normal();
    std::string s = abs.generic_u8string();
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

// True for a \\host\share, //host/share, \\?\ or \\.\ path that lies under no root. Decided
// on the spelling alone: resolving such a path makes Windows contact the host.
bool outsideNetworkPath(const std::string& path, const std::vector<std::string>& roots) {
#ifdef _WIN32
    const auto slash = [](char c) { return c == '\\' || c == '/'; };
    if (path.size() < 2 || !slash(path[0]) || !slash(path[1])) return false;
    std::string s = fs::u8path(path).lexically_normal().generic_u8string();
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    while (s.size() > 1 && s.back() == '/') s.pop_back();
    return std::none_of(roots.begin(), roots.end(), [&](const std::string& r) { return under(s, r); });
#else
    (void)path;
    (void)roots;
    return false;
#endif
}

// Longer than any real command, and std::regex recurses once per character matched.
constexpr size_t kMaxCommandBytes = 64 * 1024;

// Places programs and scratch space live; commands may name them freely.
const std::vector<std::string>& systemDirs() {
#ifdef _WIN32
    static const std::vector<std::string> dirs = {};
#else
    static const std::vector<std::string> dirs = {
        "/usr", "/bin", "/sbin", "/lib", "/lib32", "/lib64", "/libx32", "/opt", "/etc",
        "/sys", "/dev", "/tmp", "/var/tmp", "/snap", "/nix",
#ifdef __APPLE__
        // macOS keeps these under /private and links to them; a path is judged after links are followed.
        "/private/etc", "/private/tmp", "/private/var/tmp", "/private/var/folders",
#endif
        // Machine facts only: /proc/<pid> holds other programs' environments.
        "/proc/cpuinfo", "/proc/meminfo", "/proc/version", "/proc/uptime", "/proc/loadavg"};
#endif
    return dirs;
}

std::string expandHome(const std::string& p) {
    if (p.size() >= 1 && p[0] == '~' && (p.size() == 1 || p[1] == '/')) {
#ifdef _WIN32
        // Read wide: the narrow environment is in the code page, not UTF-8.
        const wchar_t* profile = _wgetenv(L"USERPROFILE");
        const std::string home = profile ? fs::path(profile).u8string() : std::string();
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
    // /proc is a place wherever it would be read, including on a system that has none.
    if (abs.compare(0, 6, "/proc/") == 0) return false;
    std::error_code ec;
    return !fs::exists(ap.root_path() / *rel.begin(), ec);
}

}  // namespace

Workspace::Workspace(std::vector<std::string> roots) {
    if (roots.empty()) {
        std::error_code ec;
        roots.push_back(fs::current_path(ec).u8string());
    }
    for (const auto& r : roots) roots_.push_back(normalized(fs::u8path(expandHome(r))));
}

bool Workspace::contains(const std::string& path) const {
    const std::string expanded = expandHome(path);
    if (outsideNetworkPath(expanded, roots_)) return false;
    // Against the working directory, which is where a tool opens a relative path.
    const std::string abs = normalized(fs::u8path(expanded));
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

namespace {
// /s, /b, /a:d, /-c, /? : a cmd.exe switch rather than a place.
bool looksLikeCmdSwitch(const std::string& token) {
    static const std::regex switchRe(R"(/-?[A-Za-z?](?::[^/\\]*)?)");
    return token.size() <= 64 && std::regex_match(token, switchRe);
}
} // namespace

bool Workspace::isGitBashDevicePath(const std::string& path) {
    if (path.rfind("/proc/", 0) == 0) return true;  // /proc/registry is the Windows registry
    if (path.rfind("/dev/", 0) != 0) return false;
    const std::string name = path.substr(5);
    for (const char* harmless : {"null", "zero", "stdin", "stdout", "stderr", "tty", "random", "urandom"}) {
        if (name == harmless) return false;
    }
    return name.rfind("fd/", 0) != 0;
}

std::string Workspace::checkCommand(const std::string& command, bool posixShell) const {
    if (command.size() > kMaxCommandBytes) {
        return "Refused: the command is " + std::to_string(command.size()) + " bytes long and the limit is " +
               std::to_string(kMaxCommandBytes) + ". Write long input to a file and have the command read it.";
    }
    // Absolute POSIX paths, ~/ paths and Windows drive paths, as standalone words. Found by hand:
    // std::regex recurses once per character, and a 60,000-character word overflows the stack.
    std::vector<std::pair<size_t, std::string>> pathWords;
    for (size_t i = 0; i < command.size(); ++i) {
        static const std::string kBefore = " \t\n\v\f\r'\"=(:,`", kEnds = " \t\n\v\f\r'\"`;|&()<>,";
        if (i > 0 && kBefore.find(command[i - 1]) == std::string::npos) continue;
        const char c = command[i];
        const char next = i + 1 < command.size() ? command[i + 1] : '\0';
        const char third = i + 2 < command.size() ? command[i + 2] : '\0';
        const bool starts = c == '/' || (c == '~' && next == '/') ||
                            (std::isalpha(static_cast<unsigned char>(c)) && next == ':' &&
                             (third == '\\' || third == '/'));
        if (!starts) continue;
        const size_t end = (std::min)(command.find_first_of(kEnds, i), command.size());
        pathWords.emplace_back(i, command.substr(i, end - i));
        i = end;  // the character that ended this word may open the next
    }
    for (const auto& [tokenAt, tokenText] : pathWords) {
        std::string token = tokenText;
        if (!posixShell && looksLikeCmdSwitch(token)) continue;
        if (token.rfind("//", 0) == 0) {
            // After a colon it is the tail of a URL (https://…); otherwise a network share.
            const size_t at = tokenAt;
            if (at > 0 && command[at - 1] == ':') continue;
            return refusal(token);
        }
        if (token == "/") {
            // The root only as a standalone word (find / …), not a regex
            // delimiter such as awk's gsub(/ /, …).
            const size_t at = tokenAt;
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
            if (!fsCommands.count(fs::u8path(program).filename().u8string())) continue;
        }
#ifdef _WIN32
        // Not files: no folder check can place them, and they reach the clipboard and the registry.
        if (posixShell && isGitBashDevicePath(token)) return refusal(token);
#endif
        const std::string expanded = expandHome(fromShellPath(token));
        if (contains(expanded)) continue;
        const std::string abs = normalized(fs::u8path(expanded));
        if (inSystemDir(abs) || looksLikePattern(abs)) continue;
        if (looksLikeCmdSwitch(token)) {
            return refusal(token) + " Commands here run in bash, where " + token +
                   " is a path, not a switch: use bash commands such as ls -R or find "
                   "instead of cmd.exe switches.";
        }
        return refusal(token);
    }
    // \\host\share, which the path pattern above does not see.
    for (size_t at = command.find("\\\\"); at != std::string::npos; at = command.find("\\\\", at + 1)) {
        static const std::string kBefore = " \t\n\v\f\r'\"=(,`", kEnds = " \t\n\v\f\r\\'\"";
        if (at > 0 && kBefore.find(command[at - 1]) == std::string::npos) continue;
        const size_t end = (std::min)(command.find_first_of(kEnds, at + 2), command.size());
        if (end > at + 2) return refusal(command.substr(at, end - at));
    }
    // A relative name that exists here but resolves elsewhere is a link out of the folder.
    static const std::string kBreaks = " \t\r\n'\"`;|&()<>=,";
    std::error_code cwdError;
    const fs::path here = fs::current_path(cwdError);
    const std::string hereResolved = cwdError ? "" : normalized(here);
    size_t checked = 0;
    for (size_t start = command.find_first_not_of(kBreaks);
         start != std::string::npos && checked < 256 && !hereResolved.empty();) {
        const size_t end = command.find_first_of(kBreaks, start);
        const std::string word = command.substr(start, end == std::string::npos ? end : end - start);
        start = end == std::string::npos ? end : command.find_first_not_of(kBreaks, end);
        if (word.empty() || word.size() > 1024 || word[0] == '-' || word[0] == '~') continue;
        // The program being run: a virtual environment's python is a link to the real one.
        const size_t wordAt = end == std::string::npos ? command.size() - word.size() : end - word.size();
        const size_t before = wordAt == 0 ? std::string::npos : command.find_last_not_of(" \t'\"", wordAt - 1);
        if (before == std::string::npos || std::string(";|&\n").find(command[before]) != std::string::npos) continue;
        const fs::path rel = fs::u8path(fromShellPath(word));
        if (rel.has_root_directory() || rel.has_root_name()) continue;
        ++checked;
        // Only a name that stays inside as written: `..` is not this check's business.
        std::string written = (fs::u8path(hereResolved) / rel).lexically_normal().generic_u8string();
        while (written.size() > 1 && written.back() == '/') written.pop_back();
        if (!std::any_of(roots_.begin(), roots_.end(), [&](const std::string& r) { return under(written, r); })) {
            continue;
        }
        const std::string abs = normalized(here / rel);
        if (std::any_of(roots_.begin(), roots_.end(), [&](const std::string& r) { return under(abs, r); }) ||
            inSystemDir(abs)) {
            continue;
        }
        return refusal(word) + " It is a link that leads to " + abs + ".";
    }
    return "";
}

namespace {
using Inside = std::function<bool(const std::string&)>;

// The shell expands a wildcard and reads through a link it matches, and the word never names that
// link. False when the wildcard's folder holds a link that leads out, or can't be told to hold none.
bool wildcardStaysInside(const fs::path& folder, const std::string& word, const Inside& inside) {
    const fs::path pattern = fs::u8path(fromShellPath(word));
    const fs::path parent = pattern.parent_path();
    if (parent.u8string().find_first_of("*?[") != std::string::npos) return false;
    std::error_code ec;
    size_t seen = 0;
    for (fs::directory_iterator it(pattern.is_absolute() ? parent : folder / parent,
                                   fs::directory_options::skip_permission_denied, ec), end;
         !ec && it != end; it.increment(ec)) {
        if (++seen > 5000) return false;
        if (it->is_symlink(ec) && !inside(normalized(it->path()))) return false;
    }
    return true;
}

// For a command that follows links as it walks (grep -R, find -L): false when the tree holds a
// link that leads out, or is too large to tell.
bool treeStaysInside(const fs::path& root, const Inside& inside) {
    std::error_code ec;
    size_t seen = 0;
    for (fs::recursive_directory_iterator it(root, fs::directory_options::skip_permission_denied, ec), end;
         !ec && it != end; it.increment(ec)) {
        if (++seen > 20000) return false;
        if (it->is_symlink(ec) && !inside(normalized(it->path()))) return false;
    }
    return true;
}

bool followsLinks(const std::vector<std::string>& words) {
    const std::string& program = words[0];
    for (size_t i = 1; i < words.size(); ++i) {
        const std::string& w = words[i];
        if (w.size() < 2 || w[0] != '-') continue;
        if (w == "--dereference" || w == "--dereference-recursive" || w == "--follow" || w == "-follow") return true;
        if (w[1] == '-') continue;
        if (program == "find") {
            if (w == "-L") return true;
        } else if (program == "grep") {
            if (w.find('R') != std::string::npos) return true;  // -L lists files; -R follows links
        } else if (w.find('L') != std::string::npos) {
            return true;
        }
    }
    return false;
}
}  // namespace

bool Workspace::readsStayInside(const std::vector<std::vector<std::string>>& segments,
                                const std::string& cwd) const {
    const Inside inside = [this](const std::string& abs) {
        return std::any_of(roots_.begin(), roots_.end(), [&](const std::string& r) { return under(abs, r); });
    };
    // `cd` in a pipeline changes nothing and `cd` after ; or && does, so a
    // later word is checked against every folder the command could be in.
    std::vector<std::string> folders = {normalized(fs::u8path(cwd.empty() ? roots_.front() : fromShellPath(cwd)))};
    if (!inside(folders.front())) return false;
    for (const auto& words : segments) {
        if (words.empty()) return false;
        const bool cd = words[0] == "cd";
        if (cd && (words.size() != 2 || words[1] == "-")) return false;  // home, or the previous folder
        // With CDPATH set, `cd name` can land in a folder that is not under this one.
        const char* cdPath = std::getenv("CDPATH");
        if (cd && cdPath != nullptr && *cdPath != '\0') return false;
        if (followsLinks(words)) {
            bool named = false;
            for (size_t i = 1; i < words.size(); ++i) {
                if (words[i].empty() || words[i][0] == '-') continue;
                for (const auto& folder : folders) {
                    const fs::path p = fs::u8path(expandHome(fromShellPath(words[i])));
                    const fs::path dir = p.is_absolute() ? p : fs::u8path(folder) / p;
                    std::error_code ec;
                    if (!fs::is_directory(dir, ec)) continue;
                    named = true;
                    if (!treeStaysInside(dir, inside)) return false;
                }
            }
            for (const auto& folder : folders) {
                if (!named && !treeStaysInside(fs::u8path(folder), inside)) return false;
            }
        }
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
            if (outsideNetworkPath(w, roots_)) return false;
            // A component such as .* can match the parent folder.
            for (size_t at = 0; at < w.size(); ++at) {
                if (w[at] != '.' || (at > 0 && w[at - 1] != '/' && w[at - 1] != '\\')) continue;
                const size_t end = w.find_first_of("/\\", at);
                if (w.substr(at, end - at).find_first_of("*?[") != std::string::npos) return false;
            }
            if (w.find_first_of("*?[") != std::string::npos) {
                for (const auto& folder : folders) {
                    if (!wildcardStaysInside(fs::u8path(folder), w, inside)) return false;
                }
            }
            const fs::path p = fs::u8path(expandHome(fromShellPath(w)));
            const size_t known = folders.size();
            for (size_t f = 0; f < known; ++f) {
                const std::string abs = normalized(p.is_absolute() ? p : fs::u8path(folders[f]) / p);
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
                                 std::string commandArg, bool posixShell) {
    ToolCallback inner = std::move(tool.callback);
    tool.callback = [inner, ws, commandArg, posixShell](const json& args) -> json {
        if (args.contains(commandArg) && args[commandArg].is_string()) {
            const std::string why =
                ws->checkCommand(args[commandArg].get<std::string>(), posixShell);
            if (!why.empty()) return json{{"status", "error"}, {"error", why}};
        }
        return inner(args);
    };
    return tool;
}

}  // namespace gaia
