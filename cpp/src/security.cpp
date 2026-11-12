// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/security.h"

#include <algorithm>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <regex>
#include <mutex>
#include <set>
#include <sstream>
#include <iostream>
#include <stdexcept>
#include <string>

#include "gaia/clean_console.h"

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

namespace {
std::string lowerAscii(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}
}  // namespace

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

namespace {
bool urlIsLocal(const std::string& url) {
    static const std::regex host(R"(^[A-Za-z][A-Za-z0-9+.\-]*://(?:[^/@?#]*@)?(\[[^\]]*\]|[^/:?#]*))");
    std::smatch m;
    if (!std::regex_search(url, m, host)) return false;
    std::string h = lowerAscii(m[1].str());
    if (!h.empty() && h.front() == '[') h = h.substr(1, h.size() - 2);
    if (h == "localhost" || h == "::1") return true;
    static const std::regex loop(R"(^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$)");
    return std::regex_match(h, loop);
}

// Every URL in free text that leaves this PC.
bool mentionsRemoteUrl(const std::string& text) {
    static const std::regex url(R"([A-Za-z][A-Za-z0-9+.\-]*://[^\s'"<>`()]+)");
    for (std::sregex_iterator it(text.begin(), text.end(), url), end; it != end; ++it) {
        if (!urlIsLocal(it->str())) return true;
    }
    return false;
}

bool anyRemoteUrl(const json& v) {
    if (v.is_string()) return mentionsRemoteUrl(v.get<std::string>());
    if (v.is_array() || v.is_object()) {
        for (const auto& x : v) {
            if (anyRemoteUrl(x)) return true;
        }
    }
    return false;
}
}  // namespace

std::string internetRefusal(const ToolInfo& tool, const json& args) {
    const std::string& name = tool.name;
    const std::string why = "The internet is off for this chat, so GAIA didn't run ";
    if (tool.mcpServer) return why + "the " + *tool.mcpServer + " connector. Turn the internet on to use it.";
    if (name == "web_fetch") {
        const std::string url = args.value("url", "");
        if (!urlIsLocal(url)) return why + "web_fetch. Turn the internet on to read web pages.";
        return "";
    }
    if (name == "bash_execute") {
        const std::string command = args.value("command", "");
        // Commands that go online with no URL in sight: a remote, a package index, an API.
        static const std::regex online(
            R"(\b(?:git\b.*\b(?:clone|fetch|pull|push|ls-remote|submodule\s+update)\b)"
            R"(|(?:pip3?|uv\s+pip|python3?\s+-m\s+pip)\s+(?:install|download)\b)"
            R"(|(?:npm|pnpm|yarn|bun)\s+(?:install|add|i|update|publish)\b|(?:npx|uvx|pipx|bunx)\s)"
            R"(|uv\s+(?:add|sync|tool\s+install)\b|cargo\s+(?:install|fetch|update)\b)"
            R"(|go\s+(?:get|install|mod\s+download)\b|docker\s+(?:pull|push|login)\b)"
            R"(|(?:winget|choco|scoop|apt|apt-get|brew|dnf|yum)\s+(?:install|upgrade|update)\b)"
            R"(|gh\s|ssh\b|scp\b|sftp\b|ftp\b|rsync\b|nc\b|ncat\b|telnet\b|socat\b)"
            R"(|nslookup\b|dig\b|ping\b|traceroute\b|tracert\b|/dev/(?:tcp|udp)/)"
            R"(|(?:https?|all|ftp)_proxy=)"
            R"(|(?:python3?|py|node|deno|bun|perl|ruby|php|pwsh|powershell)\b.*(?:socket|urllib|requests|httpx|aiohttp)"
            R"(|http\.client|https?\.(?:get|request)|fetch\(|net\.connect|WebClient|Net::HTTP|LWP|fsockopen|TCPSocket)))",
            std::regex::icase);
        static const std::regex fetch(R"(\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b)", std::regex::icase);
        // bash opens a socket for a redirect to these names.
        const bool socketFile = command.find("/dev/tcp/") != std::string::npos ||
                                command.find("/dev/udp/") != std::string::npos;
        if (socketFile || std::regex_search(command, online) || mentionsRemoteUrl(command)) {
            return why + "`" + command.substr(0, 80) + "`, which goes online.";
        }
        // A fetch stays local only when every destination it names is this PC.
        if (std::regex_search(command, fetch)) {
            static const std::regex anyUrl(R"([A-Za-z][A-Za-z0-9+.\-]*://)");
            // A proxy or a pinned address sends a "localhost" request elsewhere.
            static const std::regex reroute(
                R"((?:^|\s)(?:-x|--proxy|--preproxy|--socks\S*|--resolve|--connect-to|-e|--execute)(?:[\s=]|$))");
            if (std::regex_search(command, reroute)) return why + "`" + command.substr(0, 80) + "`, which goes online.";
            if (!std::regex_search(command, anyUrl) || command.find("$(") != std::string::npos ||
                command.find('`') != std::string::npos || command.find("${") != std::string::npos) {
                return why + "`" + command.substr(0, 80) + "`, which goes online.";
            }
        }
        return "";
    }
    // Tools that fetch whatever URL they're handed.
    if ((name == "transcribe_audio" || name == "analyze_image") && anyRemoteUrl(args)) {
        return why + name + ", which would download from outside this PC.";
    }
    return "";
}

std::string shellCommandScope(const std::string& command) {
    const size_t start = command.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "exact:";
    const std::string cmd = command.substr(start, command.find_last_not_of(" \t\r\n") - start + 1);
    // Anything that runs a second thing or hides a word is approved only as written.
    for (const char* op : {";", "&", "|", "`", "$", ">", "<", "\n", "(", "\\", "\"", "'"}) {
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
    if (plain.count(program)) return "cmd:" + program;
    // Interpreters, wrappers (env, xargs, sudo), a path to a program, VAR=x cmd.
    return "exact:" + cmd;
}

} // namespace gaia
