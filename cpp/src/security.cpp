// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/security.h"

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <set>
#include <sstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include "gaia/clean_console.h"
#include "gaia/secrets.h"
#include "lower_ascii.h"

#ifdef _WIN32
#   include <windows.h>
#   include <direct.h>
#else
#   include <climits>
#   include <cstdlib>
#   include <sys/stat.h>
#endif

#include <nlohmann/json.hpp>

namespace gaia {

using json = nlohmann::json;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

namespace {

/// Create all missing directories along the given path.
void mkdirp(const std::string& path) {
#ifdef _WIN32
    // Walk path segments and create each one.
    for (size_t i = 0; i < path.size(); ++i) {
        if ((path[i] == '\\' || path[i] == '/') && i > 0) {
            std::string sub = path.substr(0, i);
            ::CreateDirectoryA(sub.c_str(), nullptr);
        }
    }
    ::CreateDirectoryA(path.c_str(), nullptr);
#else
    for (size_t i = 1; i < path.size(); ++i) {
        if (path[i] == '/') {
            std::string sub = path.substr(0, i);
            ::mkdir(sub.c_str(), 0755);
        }
    }
    ::mkdir(path.c_str(), 0755);
#endif
}

std::string canonicalize(const std::string& path) {
#ifdef _WIN32
    char buf[MAX_PATH];
    if (::GetFullPathNameA(path.c_str(), MAX_PATH, buf, nullptr) == 0) {
        return "";
    }
    // Require existence to match realpath() semantics on POSIX.
    if (::GetFileAttributesA(buf) == INVALID_FILE_ATTRIBUTES) {
        return "";
    }
    return std::string(buf);
#else
    char buf[PATH_MAX];
    if (::realpath(path.c_str(), buf) == nullptr) {
        return "";
    }
    return std::string(buf);
#endif
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// validatePath
// ---------------------------------------------------------------------------

bool validatePath(const std::string& basePath, const std::string& requestedPath) {
    if (basePath.empty() || requestedPath.empty()) {
        return false;
    }

    std::string base = canonicalize(basePath);
    std::string req  = canonicalize(requestedPath);

    if (base.empty() || req.empty()) {
        return false;
    }

#ifdef _WIN32
    // GetFullPathNameA returns backslashes; normalize to forward slash so the
    // prefix comparison below works regardless of which separator appears.
    std::replace(base.begin(), base.end(), '\\', '/');
    std::replace(req.begin(),  req.end(),  '\\', '/');
#endif

    // Ensure base ends with separator for prefix comparison
    if (base.back() != '/') {
        base += '/';
    }

    return req.substr(0, base.size()) == base || req == base.substr(0, base.size() - 1);
}

// ---------------------------------------------------------------------------
// isSafeShellArg
// ---------------------------------------------------------------------------

bool isSafeShellArg(const std::string& arg) {
    if (arg.empty()) {
        return false;
    }
    // Reject characters with special meaning in POSIX or Windows shells.
    // Backslash is intentionally excluded: it is a path separator on Windows
    // and must be accepted for paths like "C:\Users\foo". Callers that target
    // POSIX shells exclusively should add their own backslash check.
    static const std::string kUnsafe = " \t\n\r;|&<>$`\"'!{}()[]~*?#^%=";
    return arg.find_first_of(kUnsafe) == std::string::npos;
}

// ---------------------------------------------------------------------------
// makeStdinConfirmCallback
// ---------------------------------------------------------------------------

ToolConfirmCallback makeStdinConfirmCallback() {
    return [](const std::string& toolName, const json& /*args*/) -> ToolConfirmResult {
        std::cerr << "\n"
                  << "  \"" << toolName << "\" requires confirmation. Allow this tool to run?\n"
                  << color::CYAN
                  << "  ============================================\n"
                  << color::RESET
                  << color::YELLOW << "  [1] " << color::RESET << color::WHITE << "Allow once\n"    << color::RESET
                  << color::YELLOW << "  [2] " << color::RESET << color::WHITE << "Always allow\n"  << color::RESET
                  << color::YELLOW << "  [3] " << color::RESET << color::WHITE << "Deny\n"          << color::RESET
                  << color::CYAN
                  << "  ============================================\n"
                  << color::RESET
                  << "  Choice: " << std::flush;

        std::string input;
        if (!std::getline(std::cin, input)) return ToolConfirmResult::DENY;
        if (input == "1" || input == "o" || input == "O") return ToolConfirmResult::ALLOW_ONCE;
        if (input == "2" || input == "a" || input == "A") return ToolConfirmResult::ALWAYS_ALLOW;
        return ToolConfirmResult::DENY;  // 3, d, D, or anything else
    };
}

// ---------------------------------------------------------------------------
// AllowedToolsStore
// ---------------------------------------------------------------------------

std::string gaiaHome() {
    const std::string chosen = getEnvVar("GAIA_HOME");
    if (!chosen.empty()) return chosen;
#ifdef _WIN32
    const std::string home = getEnvVar("USERPROFILE");
    const char* sep = "\\";
#else
    const std::string home = getEnvVar("HOME");
    const char* sep = "/";
#endif
    return home.empty() ? "" : home + sep + ".gaia";
}

std::string AllowedToolsStore::defaultConfigDir() {
#ifdef _WIN32
    const char* name = "USERPROFILE";
    const char* sep = "\\";
#else
    const char* name = "HOME";
    const char* sep = "/";
#endif
    const std::string home = gaiaHome();
    if (home.empty()) {
        // A shared folder such as /tmp would let another account pre-approve tools.
        throw std::runtime_error(std::string("Neither GAIA_HOME nor ") + name +
                                 " is set, so there is no private folder for saved tool approvals. Set " + name +
                                 " to your home folder, or pass a folder to AllowedToolsStore.");
    }
    return home + sep + "security";
}

AllowedToolsStore::AllowedToolsStore()
    : AllowedToolsStore(defaultConfigDir()) {}

AllowedToolsStore::AllowedToolsStore(const std::string& dir) {
    if (dir.empty()) return;  // memory only: nothing loaded or saved
    mkdirp(dir);
#ifdef _WIN32
    filePath_ = dir + "\\allowed_tools.json";
#else
    filePath_ = dir + "/allowed_tools.json";
#endif
    load();
}

bool AllowedToolsStore::isAlwaysAllowed(const std::string& toolName) const {
    std::lock_guard<std::mutex> lock(mu_);
    return allowed_.count(toolName) > 0;
}

void AllowedToolsStore::addAlwaysAllowed(const std::string& toolName) {
    std::lock_guard<std::mutex> lock(mu_);
    allowed_.insert(toolName);
    save();
}

void AllowedToolsStore::removeAlwaysAllowed(const std::string& toolName) {
    std::lock_guard<std::mutex> lock(mu_);
    allowed_.erase(toolName);
    save();
}

void AllowedToolsStore::clearAll() {
    std::lock_guard<std::mutex> lock(mu_);
    allowed_.clear();
    save();
}

std::vector<std::string> AllowedToolsStore::allAllowed() const {
    std::lock_guard<std::mutex> lock(mu_);
    return std::vector<std::string>(allowed_.begin(), allowed_.end());
}

void AllowedToolsStore::load() {
    if (filePath_.empty()) return;
    std::ifstream f(filePath_);
    if (!f.is_open()) {
        return; // File doesn't exist yet — start empty
    }
    try {
        json j = json::parse(f);
        if (j.contains("allowed_tools") && j["allowed_tools"].is_array()) {
            for (const auto& item : j["allowed_tools"]) {
                if (item.is_string()) {
                    allowed_.insert(item.get<std::string>());
                }
            }
        }
    } catch (const std::exception& e) {
        // An unreadable file means no "always" answers; say so rather than hide it.
        std::cerr << "[gaia] warning: ignoring unreadable " << filePath_ << ": " << e.what() << "\n";
        allowed_.clear();
    }
}

void AllowedToolsStore::save() const {
    if (filePath_.empty()) return;
    json j;
    j["version"] = 1;
    j["allowed_tools"] = json::array();
    for (const auto& name : allowed_) {
        j["allowed_tools"].push_back(name);
    }
    // Write beside the file and rename, so a crash never leaves half a list.
    const std::string tmp = filePath_ + ".tmp";
    {
        std::ofstream f(tmp, std::ios::trunc);
        f << j.dump(2) << "\n";
        if (!f.flush()) {
            std::cerr << "[gaia] warning: could not write allowed_tools store: " << tmp << "\n";
            return;
        }
    }
    std::error_code ec;
    std::filesystem::rename(tmp, filePath_, ec);
    if (ec) {
        std::cerr << "[gaia] warning: could not replace " << filePath_ << ": " << ec.message() << "\n";
        std::filesystem::remove(tmp, ec);
    }
}

using detail::lowerAscii;

bool splitShellCommand(const std::string& line, bool posixShell, std::vector<std::vector<std::string>>& segments) {
    segments.assign(1, {});
    std::string word;
    bool inWord = false;
    char quote = 0;
    auto endWord = [&] {
        if (inWord) segments.back().push_back(word);
        word.clear();
        inWord = false;
    };
    for (size_t i = 0; i < line.size(); ++i) {
        const char c = line[i];
        if (c == '`' || c == '$' || c == '%' || c == '^' || c == '!' || c == '\n' || c == '\r') return false;
        // bash removes a backslash everywhere but inside '...'; cmd.exe keeps it.
        if (posixShell && c == '\\' && quote != '\'') return false;
        if (quote) {
            if (c == quote) quote = 0;
            else word += c;
            continue;
        }
        // cmd.exe has one quote character; ' is an ordinary letter there.
        if (c == '"' || (posixShell && c == '\'')) {
            // A quoted program name is resolved differently by each shell.
            if (segments.back().empty()) return false;
            quote = c;
            inWord = true;
            continue;
        }
        if (c == ' ' || c == '\t') {
            endWord();
            continue;
        }
        const bool dbl = i + 1 < line.size() && line[i + 1] == c;
        if (c == ';' || c == '|' || (c == '&' && dbl)) {
            endWord();
            if (segments.back().empty()) return false;  // `ls ||`, `; ls`
            segments.emplace_back();
            if (dbl) ++i;
            continue;
        }
        if (c == '&' || c == '>' || c == '<' || c == '(' || c == ')' || c == '{' || c == '}') return false;
        word += c;
        inWord = true;
    }
    if (quote) return false;
    endWord();
    return !segments.back().empty();
}

bool isReadOnlyShellCommand(const std::string& command, bool posixShell) {
    if (command.find_first_not_of(" \t") == std::string::npos) return false;
    // Throwing away output or merging stderr writes nothing. Each shell has its
    // own null device; the other shell's name would be a real file.
    std::string cmd = " " + command + " ";
    const std::vector<const char*> quiet = posixShell
        ? std::vector<const char*>{" 2>/dev/null ", " >/dev/null ", " 2>&1 "}
        : std::vector<const char*>{" 2>nul ", " >nul ", " 2>&1 "};
    for (const char* q : quiet) {
        for (size_t at; (at = cmd.find(q)) != std::string::npos;) cmd.replace(at, std::strlen(q), " ");
    }
    std::vector<std::vector<std::string>> segments;
    if (!splitShellCommand(cmd, posixShell, segments)) return false;
    // Only programs with no flag that writes, deletes or runs code. Under
    // cmd.exe only built-ins: cmd runs a find.bat in the current folder before
    // the real find.
    static const std::set<std::string> posixReaders = {
        "ls", "dir", "cat", "head", "tail", "wc", "find", "grep", "pwd", "echo", "cd",
        "which", "whoami", "hostname", "uname", "stat", "du", "df", "basename", "dirname", "realpath"};
    static const std::set<std::string> cmdReaders = {"dir", "type", "cd", "echo"};
    const auto& readers = posixShell ? posixReaders : cmdReaders;
    for (const auto& argv : segments) {
        // cmd.exe ignores case; bash runs a different program for LS.
        const std::string program = posixShell ? argv[0] : lowerAscii(argv[0]);
        if (!readers.count(program)) return false;
        for (size_t i = 1; i < argv.size(); ++i) {
            const std::string& a = argv[i];
            if (a.empty()) continue;
            // \\host\share and //host/share read from the network.
            if (a.rfind("//", 0) == 0 || a.rfind("\\\\", 0) == 0) return false;
            // A `..` step can climb out of the chat's folders, which the folder check doesn't follow.
            for (size_t dots = a.find(".."); dots != std::string::npos; dots = a.find("..", dots + 1)) {
                const size_t next = dots + 2;
                if (next == a.size() || a[next] == '/' || a[next] == '\\') return false;
                // cmd.exe also ends an argument at a comma, a semicolon or an equals sign.
                if (!posixShell && (a[next] == ',' || a[next] == ';' || a[next] == '=')) return false;
            }
            // file_read refuses a key or .env file; reading one through the shell at least asks.
            if (a[0] != '-' && isCredentialFileName(a.substr(a.find_last_of("/\\") + 1))) return false;
            // hostname with a name sets it.
            if (program == "hostname" && a[0] != '-') return false;
            if (program != "find") continue;
            for (const char* bad : {"-exec", "-ok", "-delete", "-fprint", "-fls"}) {
                if (a.rfind(bad, 0) == 0) return false;
            }
        }
    }
    return true;
}

// internetRefusal and webFetchRefusal are in internet_refusal.cpp.

std::string shellCommandScope(const std::string& command) {
    const size_t start = command.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "exact:";
    const std::string cmd = command.substr(start, command.find_last_not_of(" \t\r\n") - start + 1);
    // Anything that runs a second thing or hides a word is approved only as written.
    // cmd.exe expands %VAR% and !VAR! and drops ^ before the program sees its words.
    for (const char* op : {";", "&", "|", "`", "$", ">", "<", "\n", "(", "\\", "\"", "'", "%", "^", "!"}) {
        if (cmd.find(op) != std::string::npos) return "exact:" + cmd;
    }
    std::istringstream words(cmd);
    std::string program, sub;
    words >> program;
#ifdef _WIN32
    program = lowerAscii(program);
    if (program.size() > 4 && program.compare(program.size() - 4, 4, ".exe") == 0) program.resize(program.size() - 4);
#endif
    // Tools whose subcommands differ in risk: the grant covers one subcommand.
    static const std::set<std::string> withSubcommand = {
        "git", "npm", "pnpm", "yarn", "pip", "pip3", "uv", "cargo", "go", "docker",
        "kubectl", "gh", "make", "dotnet", "brew", "apt", "apt-get", "winget"};
    // Programs that do one thing, whatever their arguments.
    static const std::set<std::string> plain = {
        "ls", "dir", "cat", "type", "head", "tail", "wc", "grep", "rg", "pwd", "echo", "cd", "which",
        "stat", "du", "df", "tree", "diff", "sort", "uniq", "mkdir", "md", "touch", "cp", "copy",
        "mv", "move", "ren", "rm", "del", "rmdir", "chmod", "pytest", "ctest", "cmake", "ninja", "tsc"};
    if (withSubcommand.count(program)) {
        // A flag before the subcommand (git -C elsewhere ...) changes what runs.
        if (!(words >> sub) || sub[0] == '-') return "exact:" + cmd;
        return "cmd:" + program + " " + sub;
    }
    if (plain.count(program)) {
        // A flag that runs another program or writes a file is approved only as written.
        for (std::string w; words >> w;) {
            // A grant for a program covers work in the folder, not a path that climbs out of it or names home.
            if (w[0] == '~') return "exact:" + cmd;
            // A wildcard in an absolute path (/*, /h*/me) can match any folder on the machine.
            const bool absolute = w[0] == '/' || (w.size() > 1 && w[1] == ':');
            if (absolute && w.find_first_of("*?[{") != std::string::npos) return "exact:" + cmd;
            for (size_t dots = w.find(".."); dots != std::string::npos; dots = w.find("..", dots + 1)) {
                // C:..\x climbs from the drive's current folder.
                const bool starts = dots == 0 || w[dots - 1] == '/' || w[dots - 1] == '\\' || w[dots - 1] == '=' ||
                                    w[dots - 1] == ':';
                const size_t next = dots + 2;
                if (starts && (next == w.size() || w[next] == '/' || w[next] == '\\')) return "exact:" + cmd;
            }
            if (w.size() < 2) continue;
#ifdef _WIN32
            if (program == "sort" && w[0] == '/' && (w[1] == 'o' || w[1] == 'O')) return "exact:" + cmd;
#endif
            if (w[0] != '-') continue;
            const bool longOpt = w[1] == '-';
            const std::string name = w.substr(0, w.find('='));
            // GNU tools accept any unambiguous prefix of a long option.
            const auto abbreviates = [&](const std::string& option) {
                return longOpt && name.size() > 2 && option.compare(0, name.size(), name) == 0;
            };
            bool risky = false;
            if (program == "rg") {
                risky = w.rfind("--pre", 0) == 0 || w.rfind("--hostname-bin", 0) == 0;
            } else if (program == "sort") {
                risky = (!longOpt && w.find('o') != std::string::npos) || abbreviates("--output") ||
                        abbreviates("--compress-program");
            } else if (program == "tree") {
                risky = (!longOpt && w.find('o') != std::string::npos) || abbreviates("--output");
            } else if (program == "cmake") {
                risky = !longOpt && (w[1] == 'P' || w[1] == 'E' || w[1] == 'C');
            }
            if (risky) return "exact:" + cmd;
        }
        return "cmd:" + program;
    }
    // Interpreters, wrappers (env, xargs, sudo), a path to a program, VAR=x cmd.
    return "exact:" + cmd;
}

} // namespace gaia
