// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Apart from security.cpp so a static link pulls in the shell parser and its patterns only for a
// program that calls internetRefusal or webFetchRefusal (gaiacpp), not for every user of validatePath.

#include "gaia/security.h"

#include <algorithm>
#include <cstring>
#include <map>
#include <regex>
#include <set>
#include <sstream>
#include <string>
#include <vector>

#include "lower_ascii.h"

#include <nlohmann/json.hpp>

namespace gaia {

using json = nlohmann::json;
using detail::lowerAscii;

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
    // curl -K and wget -i read their destinations from a file the URL scan can't see.
    static const std::regex fromFile(R"((?:^|\s)(?:-K\S*|--config|--input-file)(?:[\s=]|$))");
    if (std::regex_search(command, fromFile)) return true;
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
    bool fetching = false, skipNext = false, wget = false;
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
            wget = lowerAscii(w).find("wget") != std::string::npos;
            continue;
        }
        if (!fetching) continue;
        // wget -i (alone or in a cluster such as -qi) reads its destinations from a file.
        if (wget && w.size() > 1 && w[0] == '-' && w[1] != '-' && w.find('i') != std::string::npos) return true;
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

// Programs and shell words that run the words after them as a command. c and k are cmd's /c and
// /k read as a program name.
const std::set<std::string>& commandWrappers() {
    static const std::set<std::string> wrappers = {
        "sudo", "doas", "env", "time", "nohup", "exec", "command", "xargs", "timeout", "nice", "ionice",
        "stdbuf", "taskset", "chrt", "watch", "if", "elif", "then", "while", "until", "do", "else", "{", "!",
        "cmd", "call", "start", "c", "k"};
    return wrappers;
}

// True when `flag` makes `wrapper` read the next word as its value: sudo -u alice, nice -n 5.
// "run" stands for uv run, poetry run, pipenv run and pdm run.
bool wrapperFlagTakesValue(const std::string& wrapper, const std::string& flag) {
    static const std::map<std::string, std::set<std::string>> valued = {
        {"sudo", {"-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-D", "-R", "-T", "--user", "--group", "--host",
                  "--prompt", "--chdir", "--role", "--type", "--other-user", "--close-from", "--command-timeout"}},
        {"doas", {"-u", "-C"}},
        {"env", {"-u", "-C", "--unset", "--chdir"}},
        {"timeout", {"-s", "-k", "--signal", "--kill-after"}},
        {"nice", {"-n", "--adjustment"}},
        {"ionice", {"-c", "-n", "-p", "-P", "-u", "--class", "--classdata"}},
        {"stdbuf", {"-i", "-o", "-e"}},
        {"watch", {"-n", "--interval"}},
        {"xargs", {"-I", "-n", "-P", "-L", "-d", "-E", "-s", "-a", "--max-args", "--max-procs", "--delimiter",
                   "--arg-file"}},
        {"time", {"-f", "-o", "--format", "--output"}},
        {"exec", {"-a"}},
        {"run", {"--with", "--with-requirements", "--python", "-p", "--package", "--directory", "--project",
                 "--env-file", "--group", "--extra", "--index", "--from"}}};
    const auto found = valued.find(wrapper);
    return found != valued.end() && found->second.count(flag) > 0;
}

// A wrapper's bare number: timeout 10, timeout 5s, chrt 10, taskset 0x3.
bool isWrapperNumber(const std::string& wrapper, const std::string& word) {
    if (wrapper != "timeout" && wrapper != "taskset" && wrapper != "chrt" && wrapper != "nice" && wrapper != "ionice") {
        return false;
    }
    return !word.empty() && word[0] >= '0' && word[0] <= '9' &&
           word.find_first_not_of("0123456789abcdefABCDEFxX.,:-smh") == std::string::npos;
}

// Whether the next word of a command names the program that runs, past wrappers such as
// sudo -u alice, timeout 10, nice -n 5, if, {, ! and uv run.
class CommandStart {
public:
    bool at() const { return at_; }
    void reset() { *this = CommandStart(); }
    void stop() { at_ = false; }
    // Reads a word at a command position that isn't a program being looked for. `name` is the
    // word as a program name; `alsoWrappers` are more programs to see through.
    void pass(const std::string& word, const std::string& name, const std::set<std::string>* alsoWrappers) {
        if (skipValue_) {
            skipValue_ = false;
            return;
        }
        if (runner_) {
            if (word[0] == '-') return;
            runner_ = false;
            at_ = name == "run";
            if (at_) wrapper_ = "run";
            return;
        }
        if (commandWrappers().count(name) || (alsoWrappers && alsoWrappers->count(name))) {
            wrapper_ = name;
            return;
        }
        if (name == "uv" || name == "poetry" || name == "pipenv" || name == "pdm") {
            runner_ = true;
            return;
        }
        // command -v and -V look a program up rather than run it.
        if (wrapper_ == "command" && (word == "-v" || word == "-V")) {
            at_ = false;
            return;
        }
        // A word starting with / is a wrapper's switch (cmd /s /c); before any wrapper it is a program's path.
        if (word[0] == '-' || (word[0] == '/' && !wrapper_.empty())) {
            skipValue_ = wrapperFlagTakesValue(wrapper_, word);
            return;
        }
        if (word.find('=') != std::string::npos || isWrapperNumber(wrapper_, word)) return;
        at_ = false;
    }

private:
    bool at_ = true;
    bool runner_ = false;
    bool skipValue_ = false;
    std::string wrapper_;
};

// Programs that fetch whatever address they are given.
const std::set<std::string>& fetchPrograms() {
    static const std::set<std::string> fetchers = {"curl", "wget", "wget2", "iwr", "irm", "invoke-webrequest",
                                                   "invoke-restmethod", "http", "https", "xh", "xhs", "lynx",
                                                   "w3m", "links", "elinks"};
    return fetchers;
}

// True when a web-request program is what a command runs, not a word handed to another
// program: `grep curl notes.md` only searches for the name. `bare` is the command with quotes,
// backslashes and ^ dropped, which is how a shell sees `bash -c "curl ..."`.
bool runsFetchProgram(const std::string& command, const std::string& bare) {
    static const std::set<std::string>& fetchers = fetchPrograms();
    // With quotes dropped, a shell's command string is the words after it.
    static const std::set<std::string> shells = {"bash", "sh", "zsh", "dash", "ksh", "powershell", "pwsh"};
    const auto scan = [](const std::string& text) {
        CommandStart start;
        std::string word;
        for (const char c : text + "\n") {
            const bool space = c == ' ' || c == '\t' || c == '\r';
            if (!space && c != '\n' && !std::strchr(";|&()<>`", c)) {
                word.push_back(c);
                continue;
            }
            if (!word.empty() && start.at()) {
                std::string name = lowerAscii(word.substr(word.find_last_of("/\\") + 1));
                for (const char* ext : {".exe", ".cmd", ".bat", ".com", ".ps1"}) {
                    const size_t n = std::strlen(ext);
                    if (name.size() > n && name.compare(name.size() - n, n, ext) == 0) {
                        name.resize(name.size() - n);
                        break;
                    }
                }
                if (fetchers.count(name)) return true;
                start.pass(word, name, &shells);
            }
            word.clear();
            if (!space) start.reset();
        }
        return false;
    };
    return scan(programWords(command)) || scan(bare);
}

// A command line split the way a shell reads it: quotes group a word and are dropped, cmd's ^
// escape is dropped, and ; | & ( ) ` and a newline outside quotes end a command. A redirection
// is a token of its own, and the lines of a heredoc are one token, kept as written.
struct ShellToken {
    std::string text;
    bool separator = false;
    bool redirect = false;   // <, >>, 2>&1, <<EOF, <<<
    bool takesWord = false;  // a redirect the next word belongs to: a file, a delimiter or a here-string
    bool body = false;       // a heredoc's lines
    size_t bodyAt = std::string::npos;  // on a heredoc redirect, the index of its body
};

bool isHeredoc(const ShellToken& t) {
    return t.redirect && t.text.find("<<") != std::string::npos && t.text.find("<<<") == std::string::npos;
}

std::vector<ShellToken> shellTokens(const std::string& line) {
    std::vector<ShellToken> out;
    std::string word;
    bool inWord = false;
    char quote = 0;
    struct Pending {
        std::string delimiter;
        bool stripTabs;
        size_t redirect;
    };
    std::vector<Pending> pending;
    size_t heredocAt = std::string::npos;  // a heredoc redirect still waiting for its delimiter
    const auto flush = [&] {
        if (inWord) {
            if (heredocAt != std::string::npos) {
                pending.push_back({word, out[heredocAt].text.back() == '-', heredocAt});
                heredocAt = std::string::npos;
            }
            ShellToken t;
            t.text = word;
            out.push_back(t);
        }
        word.clear();
        inWord = false;
    };
    for (size_t i = 0; i < line.size(); ++i) {
        const char c = line[i];
        const char next = i + 1 < line.size() ? line[i + 1] : '\0';
        if (quote != 0) {
            if (c == quote) {
                quote = 0;
            } else if (quote == '"' && c == '\\' && (next == '"' || next == '\\' || next == '$' || next == '`')) {
                word.push_back(next);  // bash -c "python3 -c \"...\"" nests code in escaped quotes
                ++i;
            } else {
                word.push_back(c);
            }
            continue;
        }
        if (c == '\'' || c == '"') {
            quote = c;
            inWord = true;
        } else if (c == '^') {
            inWord = true;
        } else if (c == ' ' || c == '\t' || c == '\r') {
            flush();
        } else if (c == '<' || c == '>' || (c == '&' && next == '>')) {
            ShellToken op;
            op.redirect = true;
            // A file descriptor written against the operator (2>) belongs to it.
            if (inWord && !word.empty() && word.find_first_not_of("0123456789") == std::string::npos) {
                op.text = word;
                word.clear();
                inWord = false;
            } else {
                flush();
            }
            size_t k = i;
            if (line[k] == '&') op.text += line[k++];
            const char dir = line[k];
            size_t run = 0;
            for (; k < line.size() && line[k] == dir && run < (dir == '<' ? 3u : 2u); ++run) op.text += line[k++];
            if (dir == '<' && run == 2 && k < line.size() && line[k] == '-') {
                op.text += line[k++];  // <<- strips leading tabs
            } else if (k < line.size() && (line[k] == '|' || (dir == '<' && line[k] == '>'))) {
                op.text += line[k++];  // >| and <>
            }
            bool duplicate = false;
            if (k < line.size() && line[k] == '&') {  // 2>&1, <&0 and >&- duplicate; >&file names a file
                op.text += line[k++];
                while (k < line.size() && ((line[k] >= '0' && line[k] <= '9') || line[k] == '-')) {
                    op.text += line[k++];
                    duplicate = true;
                }
            }
            op.takesWord = !duplicate;
            i = k - 1;
            out.push_back(op);
            if (isHeredoc(op)) heredocAt = out.size() - 1;
        } else if (c == '\n' || std::strchr(";|&()`", c)) {
            flush();
            heredocAt = std::string::npos;
            ShellToken sep;
            sep.separator = true;
            sep.text = std::string(1, c);
            if ((c == '|' || c == '&') && next == c) {
                sep.text += next;
                ++i;
            } else if (c == '|' && next == '&') {
                sep.text += next;
                ++i;
            }
            out.push_back(sep);
            if (c != '\n' || pending.empty()) continue;
            // Heredocs opened on this line take the lines after it, each up to its delimiter.
            size_t pos = i + 1;
            for (const Pending& p : pending) {
                ShellToken body;
                body.body = true;
                while (pos < line.size()) {
                    const size_t eol = (std::min)(line.find('\n', pos), line.size());
                    std::string text = line.substr(pos, eol - pos);
                    pos = eol + 1;
                    if (!text.empty() && text.back() == '\r') text.pop_back();
                    if (p.stripTabs) text.erase(0, text.find_first_not_of('\t'));
                    if (text == p.delimiter) break;
                    body.text += text + "\n";
                }
                out[p.redirect].bodyAt = out.size();
                out.push_back(body);
            }
            pending.clear();
            out.push_back(sep);
            i = pos - 1;
        } else {
            word.push_back(c);
            inWord = true;
        }
    }
    flush();
    return out;
}

// The program a word runs: folders and a Windows suffix dropped, lowercased.
std::string programName(const std::string& word) {
    std::string name = lowerAscii(word.substr(word.find_last_of("/\\") + 1));
    for (const char* ext : {".exe", ".cmd", ".bat", ".com"}) {
        const size_t n = std::strlen(ext);
        if (name.size() > n && name.compare(name.size() - n, n, ext) == 0) {
            name.resize(name.size() - n);
            break;
        }
    }
    return name;
}

// The language an interpreter runs, or "" when `name` is not one.
std::string interpreterLanguage(const std::string& name) {
    static const std::regex python(R"((?:python|pypy)[0-9.]*|py)");
    if (name.size() <= 32 && std::regex_match(name, python)) return "python";
    if (name == "node" || name == "nodejs" || name == "bun" || name == "deno") return "node";
    if (name == "perl" || name == "ruby" || name == "php") return name;
    if (name == "powershell" || name == "pwsh") return "powershell";
    return "";
}

// True when a line of PowerShell code (lowercase, quotes dropped) has Start-Process launch a web-request
// program, given with -FilePath or as its first positional word, past any number of switches.
bool startProcessRunsFetcher(const std::string& text) {
    static const std::regex launcher(R"((?:^|[^a-z0-9-])(?:start-process|saps|start)\s+)");
    // Start-Process's switches that take a value, and the common ones.
    static const std::set<std::string> valued = {
        "argumentlist", "args", "credential", "workingdirectory", "windowstyle", "verb", "environment",
        "redirectstandarderror", "redirectstandardinput", "redirectstandardoutput", "rse", "rsi", "rso",
        "erroraction", "ea", "errorvariable", "ev", "warningaction", "wa", "warningvariable", "wv",
        "informationaction", "infa", "informationvariable", "iv", "outvariable", "ov", "outbuffer", "ob",
        "pipelinevariable", "pv"};
    const auto takesValue = [](const std::string& name) {
        if (name.size() < 2) return name == "a" || name == "c" || name == "v";  // unique one-letter prefixes
        for (const std::string& full : valued) {
            if (full.rfind(name, 0) == 0 && (full.size() > 3 || full == name)) return true;
        }
        return false;
    };
    const auto launchesFetcher = [](std::string word) {
        word = word.substr(0, word.find_first_of(",;|})"));
        return fetchPrograms().count(programName(word)) > 0;
    };
    for (std::sregex_iterator m(text.begin(), text.end(), launcher), done; m != done; ++m) {
        std::istringstream in(text.substr(static_cast<size_t>(m->position() + m->length())));
        bool programNext = false, skipNext = false;
        for (std::string w; in >> w;) {
            if (std::strchr(";|}&)", w[0])) break;
            if (programNext) {
                if (launchesFetcher(w)) return true;
                break;
            }
            if (skipNext) {
                skipNext = false;
            } else if (w[0] == '-') {
                std::string name = w.substr(1);
                const size_t colon = name.find(':');
                const bool attached = colon != std::string::npos;
                const std::string value = attached ? name.substr(colon + 1) : "";
                name = name.substr(0, colon);
                const bool filePath = !name.empty() && std::string("filepath").rfind(name, 0) == 0;
                if (filePath || name == "path" || name == "pspath") {
                    if (attached) {
                        if (launchesFetcher(value)) return true;
                        break;
                    }
                    programNext = true;
                } else if (!attached && takesValue(name)) {
                    skipNext = true;
                }
            } else {
                if (launchesFetcher(w)) return true;
                break;
            }
            if (w.find_first_of(";|}") != std::string::npos) break;
        }
    }
    return false;
}

// True when code in `language` calls something that reaches another computer, or loads a network
// library with a remote address in sight (`urlNearby`: a remote URL elsewhere on its command).
// Loading a library alone connects to nothing: `import requests; print(requests.__version__)`.
// With `anyWord`, PowerShell's web-request commands count anywhere, as in text piped into it;
// otherwise only where a command starts, so `Select-String -Pattern curl` is a search.
bool codeGoesOnline(const std::string& language, const std::string& code, bool urlNearby, bool anyWord = false) {
    // Matched with quotes and whitespace dropped: requests.get ( url ) is requests.get(url.
    static const std::map<std::string, std::vector<std::string>> calls = {
        {"python", {"urlopen(", "urlretrieve(", "requests.get(", "requests.post(", "requests.put(", "requests.patch(",
                    "requests.delete(", "requests.head(", "requests.options(", "requests.request(",
                    "requests.session(", "httpx.get(", "httpx.post(", "httpx.put(", "httpx.patch(", "httpx.delete(",
                    "httpx.head(", "httpx.stream(", "httpx.request(", "httpx.client(", "httpx.asyncclient(",
                    "clientsession(", "httpconnection(", "httpsconnection(", "create_connection(", ".connect((",
                    "pycurl.curl(", "ftplib.ftp(", "ftp_tls(", "smtplib.smtp(", "smtp_ssl(", "telnetlib.telnet(",
                    "websockets.connect(", "poolmanager(", "urllib3.request("}},
        {"node", {"fetch(", "http.get(", "https.get(", "http.request(", "https.request(", "net.connect(",
                  "createconnection(", "tls.connect(", ").get(http", ").request(http", "axios(", "axios.get(",
                  "axios.post(", "axios.request(", "websocket(", "xmlhttprequest(", "dgram.createsocket(",
                  "deno.connect(", "undici.request(", "undici.fetch("}},
        {"perl", {"getprint", "getstore", "lwp::simple::get(", "lwp::useragent->new", "http::tiny->new",
                  "io::socket::inet->new", "io::socket::ip->new", "net::ftp->new", "net::http->new",
                  "mojo::useragent->new", "www::mechanize->new"}},
        {"ruby", {"net::http.get", "net::http.start", "net::http.new", "net::http.post", "uri.open", "open(http",
                  "tcpsocket.new", "tcpsocket.open", "socket.tcp(", "httparty.get", "httparty.post", "faraday.get",
                  "faraday.post", "faraday.new", "restclient.get", "restclient.post", "httpclient.new",
                  "httpclient.get"}},
        {"php", {"file_get_contents(http", "fopen(http", "readfile(http", "file(http", "copy(http", "fsockopen(",
                 "curl_init(", "curl_exec(", "stream_socket_client(", "socket_connect(", "get_headers(",
                 "simplexml_load_file(http"}},
        {"powershell", {"net.webclient", "http.httpclient", "sockets.tcpclient", "sockets.udpclient",
                        "downloadstring(", "downloadfile(", "downloaddata(", "uploadstring(", "uploadfile(",
                        "uploaddata(", "openread(", "webrequest]::create", "httpwebrequest", "start-bitstransfer"}}};
    // Network libraries: loading one counts only beside a remote address.
    static const std::map<std::string, std::vector<std::string>> libraries = {
        {"python", {"requests", "httpx", "aiohttp", "urllib.request", "urllib2", "urllib3", "http.client", "httplib",
                    "socket", "pycurl", "ftplib", "smtplib", "telnetlib", "websocket"}},
        {"node", {"require(http", "require(net", "require(tls", "require(dgram", "require(axios", "require(undici",
                  "require(node-fetch", "node:http", "node:net", "node:tls", "node:dgram", "from http", "from net",
                  "from axios", "from undici", "from node-fetch"}},
        {"perl", {"lwp", "io::socket", "http::tiny", "net::http", "mojo::useragent", "www::mechanize", "net::ftp",
                  "socket"}},
        {"ruby", {"net/http", "net::http", "open-uri", "socket", "httparty", "faraday", "rest-client", "httpclient"}}};
    const auto found = calls.find(language);
    if (found == calls.end()) return false;
    std::string lower, squeezed;
    for (const char c : code) {
        if (c == '\'' || c == '"') continue;
        const char l = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        lower.push_back(l);
        if (l != ' ' && l != '\t' && l != '\r' && l != '\n') squeezed.push_back(l);
    }
    for (const auto& call : found->second) {
        if (squeezed.find(call) != std::string::npos) return true;
    }
    const auto libs = libraries.find(language);
    if (libs != libraries.end()) {
        for (const auto& library : libs->second) {
            if (lower.find(library) != std::string::npos) return urlNearby || mentionsRemoteUrl(code);
        }
    }
    if (language != "powershell") return false;
    // A command starts at the line's start, after ; | { ( & = or $(, or as what iex, Start-Process
    // or Invoke-Command runs (with at most one switch before it). The program may carry a folder:
    // & c:\tools\curl.exe, .\curl.exe, ./curl.
    static const std::regex atStart(
        R"((?:^|[;|{(&=]|\$\(|(?:^|[^a-z0-9-])(?:iex|invoke-expression|start-process|saps|start|invoke-command|icm))"
        R"((?:\s+-[a-z]+)?\s)\s*(?:[^\s;|&(){}=]*[\\/])?)"
        R"((?:iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod)(?:[^a-z0-9-]|$))");
    static const std::regex anywhere(
        R"((?:^|[^a-z0-9-])(?:iwr|irm|curl|wget|invoke-webrequest|invoke-restmethod)(?:[^a-z0-9-]|$))");
    // The call operator runs a quoted path: & 'C:\Program Files\curl\curl.exe'.
    for (size_t at = code.find('&'); at != std::string::npos; at = code.find('&', at + 1)) {
        size_t k = at + 1;
        while (k < code.size() && (code[k] == ' ' || code[k] == '\t')) ++k;
        if (k >= code.size() || (code[k] != '\'' && code[k] != '"')) continue;
        // A path is short; a bounded look keeps a line of many & linear.
        const size_t close = code.substr(k + 1, 512).find(code[k]);
        if (close != std::string::npos && fetchPrograms().count(programName(code.substr(k + 1, close)))) {
            return true;
        }
    }
    // One line at a time bounds the matcher's recursion; a line too long to read whole is not known
    // to stay local.
    for (size_t start = 0; start < lower.size();) {
        const size_t end = (std::min)(lower.find('\n', start), lower.size());
        if (end - start > kMaxScannedLine) return true;
        const std::string text = lower.substr(start, end - start);
        if (std::regex_search(text, anyWord ? anywhere : atStart) || startProcessRunsFetcher(text)) return true;
        start = end + 1;
    }
    return false;
}

// What an interpreter does with one word of its options: run it (or the next word) as code, read
// code from a file or module, take the next word as a value, read code from stdin, or print
// something and exit without running code.
enum class InterpreterWord { Code, Script, TakesValue, Stdin, Exits, Option };

InterpreterWord interpreterWord(const std::string& language, const std::string& word) {
    if (word == "-") return InterpreterWord::Stdin;
    if (word.empty() || word[0] != '-') return InterpreterWord::Script;
    // Flags are short; the head alone is enough with code attached (python -c"...").
    const std::string w = lowerAscii(word.substr(0, 64));
    // Flags that take nothing, clustered before the one that does: python -uc, perl -ne.
    const auto cluster = [&w](const char* plain, char last) {
        size_t k = 1;
        while (k < w.size() && w[k] != '\0' && std::strchr(plain, w[k])) ++k;
        return k < w.size() && w[k] == last;
    };
    if (language == "python") {
        if (word == "-V" || word == "-VV" || w == "--version" || w == "-h" || w == "--help" || w == "-?") {
            return InterpreterWord::Exits;
        }
        if (cluster("bdehiioqsuvr", 'c')) return InterpreterWord::Code;
        if (w == "-w" || w == "-x") return InterpreterWord::TakesValue;
        if (cluster("bdehiioqsuvr", 'm')) return InterpreterWord::Script;
    } else if (language == "node") {
        if (w == "-v" || w == "--version" || w == "-h" || w == "--help" || w == "--v8-options") {
            return InterpreterWord::Exits;
        }
        if (w == "-e" || w == "--eval" || w == "-p" || w == "--print" || w == "-pe") return InterpreterWord::Code;
        if (w == "-r" || w == "--require" || w == "--import" || w == "--loader" || w == "--experimental-loader" ||
            w == "--conditions") {
            return InterpreterWord::TakesValue;
        }
    } else if (language == "perl" || language == "ruby") {
        // ruby -v runs a script given after it; perl -v and -V only print.
        if ((language == "perl" && (word == "-v" || word == "-V")) || w == "--version" || w == "-h" ||
            w == "--help") {
            return InterpreterWord::Exits;
        }
        if (w.rfind("-m", 0) != 0 && w.rfind("-i", 0) != 0 && w.rfind("-r", 0) != 0 &&
            cluster("0123456789abcdfghjklnopqstuvwxyz", 'e')) {
            return InterpreterWord::Code;
        }
        if (w == "-i" || w == "-r") return InterpreterWord::TakesValue;
    } else if (language == "php") {
        // Case matters: -r, -R, -B and -E run code, while lowercase -b and -e are unrelated options.
        if (word == "-r" || word == "-R" || word == "-B" || word == "-E") return InterpreterWord::Code;
        if (word == "-v" || word == "-h" || word == "-i" || word == "-m" || w == "--version" || w == "--help" ||
            w == "--info" || w == "--modules") {
            return InterpreterWord::Exits;
        }
        if (word == "-f") return InterpreterWord::Script;
        if (word == "-c" || word == "-d" || word == "-z") return InterpreterWord::TakesValue;
    }
    return InterpreterWord::Option;
}

// How powershell.exe and pwsh read one of their own switches.
enum class PowerShellSwitch { NotSwitch, Encoded, Command, File, Stdin, Exits, TakesValue, Option };

// PowerShell takes -, -- or / before a switch (a typographic dash too), in any case, and any
// prefix of its name: -e, -en and -enc all mean -EncodedCommand, while -ex and -ep are
// -ExecutionPolicy.
PowerShellSwitch powershellSwitch(const std::string& word) {
    const std::string w = word.substr(0, 64);
    const auto dashAt = [&w](size_t at) -> size_t {
        if (at < w.size() && w[at] == '-') return 1;
        // En dash, em dash and horizontal bar in UTF-8.
        if (at + 2 < w.size() && w[at] == '\xE2' && w[at + 1] == '\x80' &&
            (w[at + 2] == '\x93' || w[at + 2] == '\x94' || w[at + 2] == '\x95')) {
            return 3;
        }
        return 0;
    };
    size_t skip = dashAt(0);
    if (skip == 0 && !w.empty() && w[0] == '/') skip = 1;
    if (skip == 0) return PowerShellSwitch::NotSwitch;
    skip += dashAt(skip);
    std::string name = lowerAscii(w.substr(skip));
    // -ExecutionPolicy:Bypass carries its value, so the next word is not it.
    const bool valueAttached = name.find(':') != std::string::npos;
    name = name.substr(0, name.find(':'));
    if (name.empty()) return PowerShellSwitch::Stdin;
    const auto abbreviates = [&name](const char* full, size_t shortest) {
        return name.size() >= shortest && std::string(full).rfind(name, 0) == 0;
    };
    if (name == "ec" || abbreviates("encodedcommand", 1)) return PowerShellSwitch::Encoded;
    if (abbreviates("command", 1)) return PowerShellSwitch::Command;
    if (abbreviates("file", 1)) return PowerShellSwitch::File;
    if (name == "?" || abbreviates("help", 1)) return PowerShellSwitch::Exits;
    // -Version only prints in pwsh; Windows PowerShell 5.1 takes a version and goes on to run.
    static const std::vector<std::pair<const char*, size_t>> valued = {
        {"executionpolicy", 2}, {"ep", 2}, {"windowstyle", 1}, {"outputformat", 1}, {"of", 2},
        {"inputformat", 3}, {"if", 2}, {"workingdirectory", 2}, {"wd", 2}, {"configurationname", 3},
        {"configurationfile", 3}, {"psconsolefile", 2}, {"settingsfile", 2}, {"custompipename", 2},
        {"encodedarguments", 8}, {"ea", 2}, {"version", 1}};
    for (const auto& [full, shortest] : valued) {
        if (abbreviates(full, shortest)) return valueAttached ? PowerShellSwitch::Option : PowerShellSwitch::TakesValue;
    }
    return PowerShellSwitch::Option;
}

// What a command's redirects feed its stdin: heredoc lines and here-strings, or a file.
struct StdinFeed {
    std::string code;
    bool given = false;
    bool fromFile = false;
};

bool isWordToken(const std::vector<ShellToken>& tokens, size_t at) {
    return at < tokens.size() && !tokens[at].separator && !tokens[at].redirect && !tokens[at].body;
}

// Reads the redirect at tokens[j] into `feed` and moves j onto its target word, if it has one.
void readRedirect(const std::vector<ShellToken>& tokens, size_t& j, StdinFeed& feed) {
    const ShellToken& r = tokens[j];
    const bool hasWord = r.takesWord && isWordToken(tokens, j + 1);
    if (isHeredoc(r)) {
        if (r.bodyAt < tokens.size()) {
            feed.code += tokens[r.bodyAt].text + "\n";
            feed.given = true;
        }
    } else if (r.text.find("<<<") != std::string::npos) {
        if (hasWord) {
            feed.code += tokens[j + 1].text + "\n";
            feed.given = true;
        }
    } else if (r.takesWord && r.text.find('<') != std::string::npos) {
        feed.fromFile = true;
    }
    if (hasWord) ++j;
}

// Shared by one check of a command line: the full line, and what reading all of it as one
// language's code found, so many interpreters on a line don't each re-read it.
struct CodeScan {
    explicit CodeScan(const std::string& line) : whole(line) {}
    bool wholeGoesOnline(const std::string& language) {
        const auto seen = checked.find(language);
        if (seen != checked.end()) return seen->second;
        return checked[language] = codeGoesOnline(language, whole, false, true);
    }
    const std::string& whole;
    std::map<std::string, bool> checked;
};

// True when a word from `from` up to the next separator names a URL that leaves this PC.
bool urlInSegment(const std::vector<ShellToken>& tokens, size_t from) {
    for (size_t k = from; k < tokens.size(); ++k) {
        if (tokens[k].separator) break;
        if (isWordToken(tokens, k) && mentionsRemoteUrl(tokens[k].text)) return true;
    }
    return false;
}

bool commandLineGoesOnline(const std::string& line, CodeScan& scan, int depth, bool pipedIn = false);

// True when text on a line piped into a shell, read as shell code, reaches another computer. What
// the pipe carries is unknown, so each quoted word, each command's arguments and each heredoc is
// read as a command line: echo "curl https://..." | sh.
bool pipedShellTextGoesOnline(const std::vector<ShellToken>& tokens, CodeScan& scan, int depth) {
    if (depth >= 4) return true;  // nested past any real use: not known to stay local
    std::string arguments;
    bool first = true;
    for (size_t k = 0; k <= tokens.size(); ++k) {
        if (k == tokens.size() || tokens[k].separator) {
            if (!arguments.empty() && commandLineGoesOnline(arguments, scan, depth + 1)) return true;
            arguments.clear();
            first = true;
            continue;
        }
        const ShellToken& t = tokens[k];
        if (t.redirect) continue;
        if (t.body || t.text.find_first_of(" \t\n") != std::string::npos) {
            if (commandLineGoesOnline(t.text, scan, depth + 1)) return true;
        }
        if (t.body) continue;
        if (first) {
            first = false;
            continue;
        }
        if (!arguments.empty()) arguments += ' ';
        arguments += t.text;
    }
    return false;
}

// True when an interpreter is given code that reaches another computer, on its command line or on
// stdin: python -c, perl -MLWP -e, powershell's bare command, node <<EOF, python3 <<< "...", or code
// piped in. Code in a script file is not read. `pipedIn` says the line's first command reads a pipe.
bool inlineCodeGoesOnline(const std::string& line, CodeScan& scan, int depth, bool pipedIn) {
    if (depth > 4) return true;  // nested past any real use: not known to stay local
    const std::vector<ShellToken> tokens = shellTokens(line);
    CommandStart start;
    bool piped = pipedIn;
    size_t powershellCodeEnd = 0;  // tokens before this were read as a powershell's code
    int pipedText = -1;            // pipedShellTextGoesOnline for this line, read once
    const auto pipedTextGoesOnline = [&] {
        if (pipedText < 0) pipedText = pipedShellTextGoesOnline(tokens, scan, depth) ? 1 : 0;
        return pipedText == 1;
    };
    for (size_t i = 0; i < tokens.size(); ++i) {
        const ShellToken& token = tokens[i];
        if (token.separator) {
            start.reset();
            piped = token.text == "|" || token.text == "|&";
            continue;
        }
        // A heredoc's lines may be commands (bash <<EOF) or data; read them as commands either way.
        // Data such as C++'s `cout << x` reads as more heredocs, so past the depth limit a body is
        // skipped rather than refused: only bash -c and bash <<EOF below run nested lines.
        if (token.body) {
            if (depth < 4 && inlineCodeGoesOnline(token.text, scan, depth + 1, false)) return true;
            continue;
        }
        if (token.redirect) {
            StdinFeed unused;
            readRedirect(tokens, i, unused);
            continue;
        }
        const std::string& word = token.text;
        if (!start.at() || word.empty()) continue;
        const std::string name = programName(word);
        // bash -c "python -c ..." runs the string as a command line of its own, and so is a here-string.
        // -c is a flag: the command string is the first word after the options, which end at -- or -,
        // and -o/-O/+o/+O take an option name. With no -c and no script, the shell runs its stdin.
        if (name == "bash" || name == "sh" || name == "zsh" || name == "dash" || name == "ksh") {
            StdinFeed feed;
            bool options = true, commandString = false, readsStdin = false, operand = false;
            size_t j = i + 1;
            for (; j < tokens.size() && !tokens[j].separator; ++j) {
                if (tokens[j].redirect) {
                    readRedirect(tokens, j, feed);
                    continue;
                }
                const std::string& arg = tokens[j].text;
                if (operand || tokens[j].body) continue;
                if (options && (arg == "--" || arg == "-")) {
                    options = false;
                    continue;
                }
                if (options && arg.size() > 1 && (arg[0] == '-' || arg[0] == '+')) {
                    if (arg[1] == '-') {
                        if ((arg == "--rcfile" || arg == "--init-file") && isWordToken(tokens, j + 1)) ++j;
                        continue;
                    }
                    const std::string flags = arg.substr(1, 64);
                    if (arg[0] == '-' && flags.find('c') != std::string::npos) commandString = true;
                    if (arg[0] == '-' && flags.find('s') != std::string::npos) readsStdin = true;
                    for (const char f : flags) {
                        if ((f == 'o' || f == 'O') && isWordToken(tokens, j + 1)) ++j;
                    }
                    continue;
                }
                operand = true;
                if (commandString && commandLineGoesOnline(arg, scan, depth + 1, piped)) return true;
            }
            if (feed.given && commandLineGoesOnline(feed.code, scan, depth + 1)) return true;
            const bool runsStdin = !commandString && (readsStdin || !operand);
            if (runsStdin && piped && !feed.given && !feed.fromFile && pipedTextGoesOnline()) return true;
            start.stop();
            i = j - 1;
            continue;
        }
        // cmd /c "..." and /k run the string after them as a command line of their own.
        if (name == "cmd") {
            for (size_t j = i + 1; isWordToken(tokens, j) && tokens[j].text.size() > 1 && tokens[j].text[0] == '/';
                 ++j) {
                const std::string flag = lowerAscii(tokens[j].text);
                if (flag != "/c" && flag != "/k") continue;
                if (isWordToken(tokens, j + 1) && commandLineGoesOnline(tokens[j + 1].text, scan, depth + 1, piped)) {
                    return true;
                }
                break;
            }
        }
        const std::string language = interpreterLanguage(name);
        if (language.empty()) {
            start.pass(word, name, nullptr);
            continue;
        }
        start.stop();
        StdinFeed feed;
        bool readsStdin = false;
        size_t j = i + 1;
        if (language == "powershell") {
            // Its command runs on, flag or not, through ( ) and ; (cmd.exe passes ; along), up to a
            // pipe, an & or the line's end; -File runs a script instead. Words a powershell before it
            // already read as its code are read up to the next separator only, so a line of many
            // powershells isn't read once per powershell.
            const bool covered = i < powershellCodeEnd;
            bool urlNearby = false;
            std::string code;
            bool inCommand = false, done = false, afterSeparator = false;
            for (; j < tokens.size(); ++j) {
                const ShellToken& t = tokens[j];
                if (t.separator && (covered || t.text == "\n" || t.text.find_first_of("|&") != std::string::npos)) {
                    break;
                }
                if (t.body) continue;
                if (t.redirect) {
                    readRedirect(tokens, j, feed);
                    continue;
                }
                if (!t.separator && !urlNearby && mentionsRemoteUrl(t.text)) urlNearby = true;
                if (done) continue;
                if (!inCommand && !t.separator) {
                    switch (powershellSwitch(t.text)) {
                        case PowerShellSwitch::Encoded:
                            return true;  // can't be read
                        case PowerShellSwitch::File:
                            // -File - reads the script from stdin.
                            if (isWordToken(tokens, j + 1) && tokens[j + 1].text == "-") readsStdin = true;
                            done = true;
                            continue;
                        case PowerShellSwitch::Exits:
                            done = true;
                            continue;
                        case PowerShellSwitch::Command:
                            inCommand = true;
                            continue;
                        case PowerShellSwitch::Stdin:
                            readsStdin = true;
                            done = true;
                            continue;
                        case PowerShellSwitch::TakesValue:
                            if (isWordToken(tokens, j + 1)) ++j;
                            continue;
                        case PowerShellSwitch::Option:
                            continue;
                        case PowerShellSwitch::NotSwitch:
                            break;
                    }
                }
                if (inCommand && code.empty() && t.text == "-") {
                    readsStdin = true;  // -Command -
                    done = true;
                    continue;
                }
                inCommand = true;
                // One space between words and none beside a separator, so the code is never longer than its line.
                if (!code.empty() && !t.separator && !afterSeparator) code += ' ';
                code += t.text;
                afterSeparator = t.separator;
            }
            if (!done || readsStdin) {
                if (codeGoesOnline(language, code, urlNearby)) return true;
                if (code.empty() || readsStdin) {
                    if (feed.given) {
                        if (codeGoesOnline(language, feed.code, urlNearby)) return true;
                    } else if (!feed.fromFile && piped && scan.wholeGoesOnline(language)) {
                        return true;
                    }
                }
            }
            powershellCodeEnd = (std::max)(powershellCodeEnd, j);
            // Under bash a ; ends the powershell command, so what follows is judged as commands too.
            while (i + 1 < tokens.size() && !tokens[i + 1].separator) ++i;
            continue;
        }
        const bool urlNearby = urlInSegment(tokens, j);
        std::string options, code;
        bool decided = false;
        for (; j < tokens.size() && !tokens[j].separator; ++j) {
            if (tokens[j].body) continue;
            if (tokens[j].redirect) {
                readRedirect(tokens, j, feed);
                continue;
            }
            const std::string& arg = tokens[j].text;
            if (decided || arg.empty()) continue;
            switch (interpreterWord(language, arg)) {
                case InterpreterWord::Code:
                    // The code is the next word, or the rest of this one (python -c"...").
                    code = arg;
                    if (isWordToken(tokens, j + 1)) code += " " + tokens[++j].text;
                    decided = true;
                    break;
                case InterpreterWord::Script:
                    // deno eval <code> names its code with a word, not a flag.
                    if (name == "deno" && lowerAscii(arg) == "eval" && isWordToken(tokens, j + 1)) {
                        code = tokens[++j].text;
                    }
                    decided = true;
                    break;
                case InterpreterWord::Exits:
                    decided = true;
                    break;
                case InterpreterWord::Stdin:
                    readsStdin = decided = true;
                    break;
                case InterpreterWord::TakesValue:
                    options += " " + arg;
                    if (isWordToken(tokens, j + 1)) options += " " + tokens[++j].text;
                    break;
                case InterpreterWord::Option:
                    options += " " + arg;
                    break;
            }
        }
        // perl -MLWP::Simple loads its library with an option.
        if (codeGoesOnline(language, options + " " + code, urlNearby)) return true;
        // No code and no script: it runs what stdin holds.
        if (!decided || readsStdin) {
            if (feed.given) {
                if (codeGoesOnline(language, feed.code, urlNearby)) return true;
            } else if (!feed.fromFile && piped && scan.wholeGoesOnline(language)) {
                return true;
            }
        }
        i = j - 1;
    }
    return false;
}

// True when git copies a repository from another computer named on the line.
bool clonesRemoteRepository(const std::string& command, int depth = 0) {
    const std::vector<ShellToken> tokens = shellTokens(command);
    for (size_t i = 0; i < tokens.size(); ++i) {
        // Heredoc lines, read as commands; past a few levels they are data such as `cout << x`.
        if (tokens[i].body && depth < 4 && clonesRemoteRepository(tokens[i].text, depth + 1)) return true;
        if (!isWordToken(tokens, i) || programName(tokens[i].text) != "git") continue;
        bool cloning = false;
        for (size_t j = i + 1; j < tokens.size() && !tokens[j].separator; ++j) {
            if (!isWordToken(tokens, j)) continue;
            const std::string& arg = tokens[j].text;
            if (arg == "clone" || arg == "submodule" || arg == "fetch" || arg == "pull" || arg == "ls-remote") {
                cloning = true;
                continue;
            }
            if (!cloning) continue;
            // A URL that leaves this PC, or scp-style host:path (git@github.com:org/repo).
            if (mentionsRemoteUrl(arg)) return true;
            static const std::regex scpLike(R"(^[^/\\:@\s]+@[A-Za-z0-9.-]+:.*)");
            if (arg.size() < 300 && std::regex_match(arg, scpLike)) return true;
        }
    }
    return false;
}

// The command with every `<fetch program> --version` (or -V, --help, -h) blanked out: it prints
// and exits without a destination.
std::string withoutFetchInfoSegments(std::string command) {
    size_t start = 0;
    for (size_t i = 0; i <= command.size(); ++i) {
        if (i < command.size() && std::string("\n;|&()").find(command[i]) == std::string::npos) continue;
        const std::string segment = command.substr(start, i - start);
        std::istringstream in(segment);
        std::vector<std::string> words;
        for (std::string w; words.size() < 3 && in >> w;) words.push_back(w);
        if (words.size() == 2 && segment.find_first_of("'\"$`\\^%!") == std::string::npos &&
            fetchPrograms().count(programName(words[0])) &&
            (words[1] == "--version" || words[1] == "-V" || words[1] == "--help" || words[1] == "-h")) {
            std::fill(command.begin() + static_cast<std::ptrdiff_t>(start),
                      command.begin() + static_cast<std::ptrdiff_t>(i), ' ');
        }
        start = i + 1;
    }
    return command;
}

// The --no-web checks on one command line: a socket path, a download tool, interpreter code that
// connects, a web-request program aimed off this PC, or a remote git repository.
bool commandLineGoesOnline(const std::string& line, CodeScan& scan, int depth, bool pipedIn) {
    if (line.find("/dev/tcp/") != std::string::npos || line.find("/dev/udp/") != std::string::npos) return true;
    const auto withoutQuotes = [](const std::string& text) {
        std::string bare;
        for (const char c : text) {
            if (c != '\'' && c != '"' && c != '\\' && c != '^') bare.push_back(c);
        }
        return bare;
    };
    static const std::regex downloader(R"(\b(?:start-bitstransfer\b|bitsadmin\b|certutil\b.*-urlcache|aria2c\b))",
                                       std::regex::icase);
    const std::string bare = withoutQuotes(line);
    if (std::regex_search(line, downloader) || std::regex_search(bare, downloader)) return true;
    // Code handed to an interpreter that opens a connection. A library's name anywhere else is not
    // one: `pip install requests` and `pytest test_requests.py` stay allowed.
    if (inlineCodeGoesOnline(line, scan, depth, pipedIn)) return true;
    const std::string fetchable = withoutFetchInfoSegments(line);
    const std::string bareFetchable = withoutQuotes(fetchable);
    if (runsFetchProgram(fetchable, bareFetchable) &&
        (mentionsRemoteUrl(fetchable) || fetchLeavesThisPc(fetchable, bareFetchable))) {
        return true;
    }
    return clonesRemoteRepository(line, depth);
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
    CodeScan scan(asked);
    if (commandLineGoesOnline(asked, scan, 0)) {
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

} // namespace gaia
