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
#include "gaia/secrets.h"

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

namespace {
bool urlIsLocal(const std::string& url) {
    static const std::regex host(R"(^[A-Za-z][A-Za-z0-9+.\-]*://(?:[^/@?#]*@)?(\[[^\]]*\]|[^/:?#]*))");
    // The host is at the front; a long tail would only deepen the matcher's recursion.
    const std::string head = url.substr(0, 1024);
    std::smatch m;
    if (!std::regex_search(head, m, host)) return false;
    std::string h = lowerAscii(m[1].str());
    if (!h.empty() && h.front() == '[') h = h.substr(1, h.size() - 2);
    if (h == "localhost" || h == "::1") return true;
    static const std::regex loop(R"(^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$)");
    return std::regex_match(h, loop);
}

// Longer than any real command, and std::regex recurses once per character matched.
constexpr size_t kMaxScannedBytes = 64 * 1024;
// The patterns below match within one line, so one line bounds the recursion: a thread's
// stack (512 KB on macOS) holds a few thousand characters of it.
constexpr size_t kMaxScannedLine = 2048;

bool isAsciiLetter(char c) { return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z'); }
bool isSchemeChar(char c) { return isAsciiLetter(c) || (c >= '0' && c <= '9') || c == '+' || c == '.' || c == '-'; }
bool endsUrl(char c) {
    switch (c) {
        case ' ': case '\t': case '\n': case '\v': case '\f': case '\r':
        case '\'': case '"': case '<': case '>': case '`': case '(': case ')':
            return true;
        default:
            return false;
    }
}

// Finds `scheme://rest` from `from` on, in linear time: a backtracking regex
// is quadratic on a long word and MSVC throws error_complexity on one.
bool nextUrl(const std::string& text, size_t from, size_t& begin, size_t& end) {
    for (size_t sep = text.find("://", from); sep != std::string::npos; sep = text.find("://", sep + 1)) {
        size_t start = sep;
        while (start > from && isSchemeChar(text[start - 1])) --start;
        while (start < sep && !isAsciiLetter(text[start])) ++start;
        size_t stop = sep + 3;
        while (stop < text.size() && !endsUrl(text[stop])) ++stop;
        if (start == sep || stop == sep + 3) continue;
        begin = start;
        end = stop;
        return true;
    }
    return false;
}

// Every URL in free text that leaves this PC.
bool mentionsRemoteUrl(const std::string& text) {
    if (text.size() > kMaxScannedBytes) return true;  // too long to scan, so not known to stay local
    size_t begin = 0;
    for (size_t at = 0, end = 0; nextUrl(text, at, begin, end); at = end) {
        if (!urlIsLocal(text.substr(begin, end - begin))) return true;
    }
    return false;
}

// The command with each word reduced to the program name it would run: quotes and cmd.exe's
// ^ escapes dropped, folders and a Windows suffix stripped, lowercased: "C:\Tools\Conda.EXE"
// install and co^nda install are conda install.
// Never longer than the command, line for line, so it is as safe to scan.
std::string programWords(const std::string& command) {
    std::string out, word;
    bool afterWord = false, escaped = false;
    const auto flush = [&] {
        if (word.empty()) return;
        std::string name = lowerAscii(word.substr(word.find_last_of("/\\") + 1));
        for (const char* ext : {".exe", ".cmd", ".bat", ".com", ".ps1"}) {
            const size_t n = std::strlen(ext);
            if (name.size() > n && name.compare(name.size() - n, n, ext) == 0) {
                name.resize(name.size() - n);
                break;
            }
        }
        if (afterWord) out.push_back(' ');
        out += name;
        afterWord = true;
        word.clear();
    };
    char quote = 0;
    for (const char c : command) {
        if (escaped) {
            escaped = false;
            if (c != '\n') word.push_back(c);  // ^ then a line break joins the lines
        } else if (quote) {
            if (c == quote) quote = 0;
            else word.push_back(c);
        } else if (c == '^') {
            escaped = true;
        } else if (c == '\'' || c == '"') {
            quote = c;
        } else if (c == ' ' || c == '\t' || c == '\r') {
            flush();
        } else if (c == '\n' || std::strchr(";|&()<>`", c)) {
            flush();
            out.push_back(c);
            afterWord = false;
        } else {
            word.push_back(c);
        }
    }
    flush();
    return out;
}

// One command that a documented flag keeps off the network: mvn -o, mvn -v, gradle --offline,
// gradle --version, npm ci/install --offline, pip install --no-index. Only those exact flags count.
// Not judged here, so still checked: anything quoted or expanded, a URL, a requirements file, a
// wrapper (mvnw and gradlew download a build tool first), and a goal that publishes.
// Kept in step with _explicitly_offline in src/gaia/agents/base/offline.py.
bool explicitlyOffline(const std::string& segment) {
    // bash opens a socket for a redirect to /dev/tcp/host/port.
    if (segment.find_first_of("'\"$%!`") != std::string::npos || segment.find("://") != std::string::npos ||
        segment.find("/dev/tcp/") != std::string::npos || segment.find("/dev/udp/") != std::string::npos) {
        return false;
    }
    std::vector<std::string> words;
    std::string word;
    for (const char c : segment + " ") {
        if (c == '^') continue;
        if (c == ' ' || c == '\t' || c == '\r') {
            if (!word.empty()) words.push_back(word);
            word.clear();
        } else {
            word.push_back(c);
        }
    }
    if (words.empty()) return false;
    std::string program = lowerAscii(words[0].substr(words[0].find_last_of("/\\") + 1));
    for (const char* ext : {".exe", ".cmd", ".bat", ".com", ".ps1"}) {
        const size_t n = std::strlen(ext);
        if (program.size() > n && program.compare(program.size() - n, n, ext) == 0) program.resize(program.size() - n);
    }
    const auto has = [&words](const char* flag) { return std::find(words.begin() + 1, words.end(), flag) != words.end(); };
    const auto versionOnly = [&words] { return words.size() == 2 && (words[1] == "-v" || words[1] == "--version"); };
    const bool publishes = std::any_of(words.begin() + 1, words.end(), [](const std::string& w) {
        const std::string l = lowerAscii(w);
        for (const char* goal : {"deploy", "publish", "upload", "release", "scm:", "wagon:"}) {
            if (l.find(goal) != std::string::npos) return true;
        }
        return false;
    });
    if (program == "mvn") return versionOnly() || ((has("-o") || has("--offline")) && !publishes);
    if (program == "gradle") return versionOnly() || (has("--offline") && !publishes);
    if (program == "npm") {
        return words.size() > 2 && (words[1] == "ci" || words[1] == "install" || words[1] == "i") && has("--offline");
    }
    size_t sub = 1;
    static const std::regex pip(R"(pip[0-9.]*)");
    static const std::regex python(R"((?:python[0-9.]*|py))");
    if (std::regex_match(program, python) && words.size() > 2 && words[1] == "-m" && lowerAscii(words[2]) == "pip") {
        sub = 3;
    } else if (!std::regex_match(program, pip)) {
        return false;
    }
    if (words.size() <= sub || words[sub] != "install" || !has("--no-index")) return false;
    // A requirements or constraints file can name a URL this check cannot see.
    return std::none_of(words.begin() + 1, words.end(), [](const std::string& w) {
        return w.rfind("-r", 0) == 0 || w.rfind("-c", 0) == 0 || w.rfind("--requirement", 0) == 0 ||
               w.rfind("--constraint", 0) == 0;
    });
}

// The command with every explicitly offline command blanked out, so the checks below skip it.
// Split on every separator, quoted or not: a command that hides one in quotes is not blanked, and
// neither is one beside a backtick substitution.
std::string withoutOfflineSegments(std::string command) {
    size_t start = 0;
    for (size_t i = 0; i <= command.size(); ++i) {
        if (i < command.size() && std::string("\n;|&()").find(command[i]) == std::string::npos) continue;
        if (explicitlyOffline(command.substr(start, i - start))) {
            std::fill(command.begin() + static_cast<std::ptrdiff_t>(start),
                      command.begin() + static_cast<std::ptrdiff_t>(i), ' ');
        }
        start = i + 1;
    }
    return command;
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
// Why a command is too long to be checked, or "" when it can be.
std::string tooLongToCheck(const std::string& asked) {
    if (asked.size() > kMaxScannedBytes) {
        return "a command of " + std::to_string(asked.size()) + " bytes: over " + std::to_string(kMaxScannedBytes) +
               " bytes it cannot be checked for going online. Write long input to a file and have the command "
               "read it.";
    }
    for (size_t start = 0; start <= asked.size();) {
        const size_t end = (std::min)(asked.find('\n', start), asked.size());
        if (end - start > kMaxScannedLine) {
            return "a command with a line of " + std::to_string(end - start) + " bytes: over " +
                   std::to_string(kMaxScannedLine) + " bytes on one line it cannot be checked for going online. "
                   "Break the line up, or write long input to a file and have the command read it.";
        }
        start = end + 1;
    }
    return "";
}

// True when `command` runs curl, wget or a PowerShell web request at anything but this PC.
// `bare` is the command with quotes, backslashes and ^ dropped.
bool fetchLeavesThisPc(const std::string& command, const std::string& bare) {
    static const std::regex fetch(R"(\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b)", std::regex::icase);
    if (!std::regex_search(command, fetch) && !std::regex_search(bare, fetch)) return false;
    // A proxy or a pinned address sends a "localhost" request elsewhere; -x also rides in a
    // flag cluster (-sx) or holds its value (-xhost).
    static const std::regex reroute(
        R"((?:^|\s)(?:-[A-Za-z]*x\S*|--proxy|--preproxy|--socks\S*|--resolve|--connect-to|-e|--execute)(?:[\s=]|$))");
    if (std::regex_search(command, reroute)) return true;
    size_t urlBegin = 0, urlEnd = 0;
    if (!nextUrl(command, 0, urlBegin, urlEnd) || command.find("$(") != std::string::npos ||
        command.find('`') != std::string::npos || command.find("${") != std::string::npos) {
        return true;
    }
    // curl and wget take a bare host as a destination too (curl http://localhost/ example.com),
    // and that has no scheme for the URL scan to find.
    static const std::regex bareHost(R"(^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?::[0-9]+)?(?:[/?#]|$))");
    const auto isFetchProgram = [](const std::string& word) {
        const std::string name = lowerAscii(word.substr(word.find_last_of("/\\") + 1));
        return name == "curl" || name == "wget" || name == "curl.exe" || name == "wget.exe";
    };
    // Flags whose next word is a file or a value, not a destination.
    static const std::set<std::string> takesValue = {
        "-o", "-O", "--output", "--output-document", "-d", "--data", "--data-raw", "--data-binary",
        "--data-urlencode", "-H", "--header", "-T", "--upload-file", "-F", "--form", "-u", "--user",
        "-A", "--user-agent", "-b", "--cookie", "-c", "--cookie-jar", "-K", "--config", "-w",
        "--write-out", "-X", "--request", "-m", "--max-time", "--retry", "--connect-timeout"};
    std::istringstream words(bare);
    bool fetching = false, skipNext = false;
    for (std::string w; words >> w;) {
        if (std::string(";|&<>").find(w[0]) != std::string::npos) {
            fetching = false;  // a redirect target or the next command
            skipNext = w.find_first_not_of("<>&|;") == std::string::npos && (w[0] == '<' || w[0] == '>');
            continue;
        }
        if (skipNext) {
            skipNext = false;
            continue;
        }
        if (isFetchProgram(w)) {
            fetching = true;
            continue;
        }
        if (!fetching) continue;
        if (w[0] == '-') {
            // wget -O takes a file; curl -O takes none, and then the next word is a URL anyway.
            skipNext = takesValue.count(w) > 0;
            continue;
        }
        // Only the head is matched: std::regex recurses once per character of a long word.
        const std::string head = w.substr(0, 300);
        if (head.find("://") != std::string::npos || !std::regex_search(head, bareHost)) continue;
        if (!urlIsLocal("http://" + head)) return true;
    }
    return false;
}

// True when a web-request program is what a command runs, not a word handed to another
// program: `grep curl notes.md` only searches for the name. `bare` is the command with quotes,
// backslashes and ^ dropped, which is how a shell sees `bash -c "curl ..."`.
bool runsFetchProgram(const std::string& command, const std::string& bare) {
    static const std::set<std::string> fetchers = {"curl", "wget", "iwr", "irm", "invoke-webrequest",
                                                   "invoke-restmethod"};
    // Programs that run the words after them; c and k are cmd's /c and /k as programWords leaves them.
    static const std::set<std::string> wrappers = {"sudo", "env", "time", "nohup", "exec", "command", "xargs",
                                                   "bash", "sh", "zsh", "cmd", "powershell", "pwsh", "call",
                                                   "start", "then", "do", "else", "c", "k"};
    const auto scan = [](const std::string& text) {
        bool atCommand = true;
        std::string word;
        for (const char c : text + "\n") {
            const bool space = c == ' ' || c == '\t' || c == '\r';
            if (!space && c != '\n' && !std::strchr(";|&()<>`", c)) {
                word.push_back(c);
                continue;
            }
            if (!word.empty() && atCommand) {
                std::string name = lowerAscii(word.substr(word.find_last_of("/\\") + 1));
                for (const char* ext : {".exe", ".cmd", ".bat", ".com", ".ps1"}) {
                    const size_t n = std::strlen(ext);
                    if (name.size() > n && name.compare(name.size() - n, n, ext) == 0) {
                        name.resize(name.size() - n);
                        break;
                    }
                }
                if (fetchers.count(name)) return true;
                atCommand = wrappers.count(name) || word[0] == '-' || word[0] == '/' ||
                            word.find('=') != std::string::npos;
            }
            word.clear();
            if (!space) atCommand = true;
        }
        return false;
    };
    return scan(programWords(command)) || scan(bare);
}
}  // namespace

std::string webFetchRefusal(const ToolInfo& tool, const json& args) {
    if (tool.name != "bash_execute" || !args.is_object() || !args.contains("command") ||
        !args["command"].is_string()) {
        return "";
    }
    const std::string why = "Web access is off for this run, so GAIA didn't run ";
    const std::string asked = args["command"].get<std::string>();
    const std::string unchecked = tooLongToCheck(asked);
    if (!unchecked.empty()) return why + unchecked;
    std::string bare;
    for (const char c : asked) {
        if (c != '\'' && c != '"' && c != '\\' && c != '^') bare.push_back(c);
    }
    static const std::regex downloader(R"(\b(?:start-bitstransfer\b|bitsadmin\b|certutil\b.*-urlcache|aria2c\b))",
                                       std::regex::icase);
    // Code handed to an interpreter on the command line that opens a connection. A library's name
    // anywhere else is not one: `pip install requests` and `pytest test_requests.py` stay allowed.
    static const std::regex scripted(
        R"((?:^|[\s;&|(`/\\])(?:python[0-9.]*|py|node|deno|bun|perl|ruby|php|pwsh|powershell)(?:\.exe)?\s)"
        R"((?:[^\n]*\s)?(?:-c|-e|-r|-p|--eval|-command|eval)\s[^\n]*)"
        R"((?:socket|urllib|requests|httpx|aiohttp|http\.client|https?\.(?:get|request)|fetch\(|net\.connect)"
        R"(|WebClient|Net::HTTP|LWP|fsockopen|TCPSocket))",
        std::regex::icase);
    const bool socketFile = asked.find("/dev/tcp/") != std::string::npos ||
                            asked.find("/dev/udp/") != std::string::npos;
    const bool download = std::regex_search(asked, downloader) || std::regex_search(bare, downloader);
    const bool script = std::regex_search(asked, scripted) || std::regex_search(bare, scripted);
    const bool fetch = runsFetchProgram(asked, bare) && (mentionsRemoteUrl(asked) || fetchLeavesThisPc(asked, bare));
    if (socketFile || download || script || fetch) {
        return why + "`" + asked.substr(0, 80) + "`, which fetches from the web. Don't fetch it another way: "
               "answer from what is on this PC, or say what you could not read.";
    }
    return "";
}

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
        const std::string asked = args.value("command", "");
        const std::string unchecked = tooLongToCheck(asked);
        if (!unchecked.empty()) return why + unchecked;
        // An explicit offline mode (mvn -o, npm ci --offline) needs no network; the rest of the line is still checked.
        const std::string command = withoutOfflineSegments(asked);
        // Commands that go online with no URL in sight: a remote, a package index, an API.
        // Kept in step with _ONLINE_COMMAND_RE in src/gaia/agents/base/offline.py.
        static const std::regex online(
            R"(\b(?:git\b.*\b(?:clone|fetch|pull|push|ls-remote|submodule\s+update)\b)"
            R"(|git\b.*\bremote\s+update\b)"
            R"(|(?:pip[0-9.]*|uv\s+pip|(?:python[0-9.]*|py)\s+-m\s+pip)\s+(?:install|download|wheel|index|search)\b)"
            R"(|(?:npm|pnpm|yarn|bun)\s+(?:install|add|i|ci|update|upgrade|up|publish|dlx|exec|create|x|audit)"
            R"(|outdated|view|info|search|login)\b|(?:npm|pnpm|yarn|bun)\s+init\s+[^-\s])"
            R"(|(?:npx|uvx|pipx|bunx|pnpx)\s)"
            R"(|uv\s+(?:add|sync|lock|tool\s+(?:install|upgrade)|python\s+install|run\s+--with|self\s+update)\b)"
            R"(|(?:poetry|pdm)\s+(?:install|add|update|lock|sync|publish|self\s+update)\b)"
            R"(|(?:conda|mamba|micromamba)\s+(?:install|create|update|upgrade|search|env\s+(?:create|update))\b)"
            R"(|cargo\s+(?:install|fetch|update|publish|search|add|login)\b|go\s+(?:get|install|mod\s+(?:download|tidy))\b)"
            R"(|gem\s+(?:install|update|fetch|push|search|sources)\b|bundler?\s+(?:install|update|add|outdated|lock)\b)"
            R"(|composer\s+(?:install|update|require|create-project)\b)"
            R"(|dotnet\s+(?:restore|add|new\s+install|tool\s+(?:install|update|restore)|workload|nuget)\b)"
            R"(|nuget\s+(?:install|restore|update|push)\b|(?:docker|podman)\s+(?:pull|push|login|search)\b)"
            R"(|(?:winget|choco|scoop|apt|apt-get|brew|dnf|yum|zypper|apk|snap|flatpak)\s+)"
            R"((?:install|reinstall|upgrade|update|download|search|add|fetch|tap|source|dist-upgrade|full-upgrade)\b)"
            R"(|pacman\s+-S|(?:install|update|find|save)-(?:module|package|script)\b|start-bitstransfer\b|bitsadmin\b)"
            R"(|certutil\b.*-urlcache|aria2c\b|(?:huggingface-cli|hf)\s+(?:download|upload|login)\b|ollama\s+(?:pull|push)\b)"
            R"(|gh\s|ssh\b|scp\b|sftp\b|ftp\b|rsync\b|nc\b|ncat\b|telnet\b|socat\b)"
            R"(|nslookup\b|dig\b|ping\b|traceroute\b|tracert\b|/dev/(?:tcp|udp)/)"
            R"(|(?:https?|all|ftp)_proxy=)"
            R"(|(?:python3?|py|node|deno|bun|perl|ruby|php|pwsh|powershell)\b.*(?:socket|urllib|requests|httpx|aiohttp)"
            R"(|http\.client|https?\.(?:get|request)|fetch\(|net\.connect|WebClient|Net::HTTP|LWP|fsockopen|TCPSocket)))",
            std::regex::icase);
        // Build tools that resolve dependencies from a remote repository on nearly every run.
        static const std::regex buildTool(R"((?:^|[\s;&|(`])(?:mvn|mvnw|gradle|gradlew)(?=[\s;&|)`]|$))",
                                          std::regex::icase);
        // bash opens a socket for a redirect to these names.
        const bool socketFile = asked.find("/dev/tcp/") != std::string::npos ||
                                asked.find("/dev/udp/") != std::string::npos;
        // bash drops quotes and backslashes, and cmd.exe drops ^, before a program is looked up: cu''rl and
        // cu^rl run curl.
        std::string bare;
        for (const char c : command) {
            if (c != '\'' && c != '"' && c != '\\' && c != '^') bare.push_back(c);
        }
        // A full path, a quoted name or a Windows suffix still runs the program: C:\Tools\conda.exe is conda.
        const std::string programs = programWords(command);
        if (socketFile || std::regex_search(command, online) || std::regex_search(bare, online) ||
            std::regex_search(programs, online) || std::regex_search(programs, buildTool) ||
            mentionsRemoteUrl(command)) {
            return why + "`" + asked.substr(0, 80) + "`, which goes online.";
        }
        if (fetchLeavesThisPc(command, bare)) return why + "`" + asked.substr(0, 80) + "`, which goes online.";
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
