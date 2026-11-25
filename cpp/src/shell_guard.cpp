// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/shell_guard.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <filesystem>
#include <optional>
#include <set>

#include "gaia/types.h"
#include "gaia/workspace.h"

namespace fs = std::filesystem;

namespace gaia {

namespace {

constexpr size_t kMaxCommandBytes = 64 * 1024;
constexpr int kMaxDepth = 6;
constexpr size_t kMaxAlternatives = 32;
constexpr size_t kMaxMatches = 4096;
constexpr size_t kMaxDirEntries = 20000;
constexpr size_t kMaxFolders = 16;

// Stand-ins inside an expanded word: a field break, and text known only when the command runs.
constexpr char kBreak = '\x1F';
constexpr char kUnknown = '\x1E';

// ---------------------------------------------------------------------------
// Lexer: bash's words, quotes, expansions, operators and redirections.
// ---------------------------------------------------------------------------

struct Part {
    enum Kind { Lit, Var, Sub, Unknown } kind = Lit;
    std::string text;     // literal text, variable name, or substitution body
    bool quoted = false;  // inside quotes: no splitting, no wildcard
};

struct Word {
    std::vector<Part> parts;

    void lit(char c, bool quoted) {
        if (parts.empty() || parts.back().kind != Part::Lit || parts.back().quoted != quoted) {
            parts.push_back({Part::Lit, "", quoted});
        }
        parts.back().text += c;
    }
    void lit(const std::string& s, bool quoted) {
        if (s.empty()) {
            if (quoted) parts.push_back({Part::Lit, "", true});
            return;
        }
        for (const char c : s) lit(c, quoted);
    }
    // The word when it is one unquoted literal (a keyword, an option), else "".
    std::string bare() const {
        return parts.size() == 1 && parts[0].kind == Part::Lit && !parts[0].quoted ? parts[0].text : "";
    }
};

struct Token {
    enum Kind { WordT, Op, Redir } kind = WordT;
    Word word;
    std::string text;      // the operator
    std::string heredoc;   // body, for << and <<-
    bool hasBody = false;
    bool bodyExpands = false;  // an unquoted delimiter: the shell expands $ and ` in the body
};

bool isNameStart(char c) { return std::isalpha(static_cast<unsigned char>(c)) || c == '_'; }
bool isNameChar(char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '_'; }
bool isName(const std::string& s) {
    return !s.empty() && isNameStart(s[0]) && std::all_of(s.begin(), s.end(), isNameChar);
}

class Lexer {
public:
    explicit Lexer(const std::string& s) : s_(s) {}

    // False when the quoting can't be read (an unclosed quote or bracket).
    bool run(std::vector<Token>& out) {
        struct Pending {
            size_t token;
            std::string delimiter;
            bool stripTabs;
        };
        std::vector<Pending> pending;
        size_t i = 0;
        const size_t n = s_.size();
        while (i < n) {
            const char c = s_[i];
            if (c == ' ' || c == '\t' || c == '\r') {
                ++i;
            } else if (c == '\\' && i + 1 < n && s_[i + 1] == '\n') {
                i += 2;
            } else if (c == '\n') {
                out.push_back({Token::Op, {}, "\n", "", false});
                ++i;
                for (const auto& p : pending) {
                    std::string body;
                    while (i < n) {
                        const size_t eol = (std::min)(s_.find('\n', i), n);
                        std::string line = s_.substr(i, eol - i);
                        i = eol < n ? eol + 1 : n;
                        std::string probe = line;
                        if (p.stripTabs) probe.erase(0, probe.find_first_not_of('\t'));
                        if (probe == p.delimiter) break;
                        body += line + "\n";
                    }
                    out[p.token].heredoc = body;
                    out[p.token].hasBody = true;
                }
                pending.clear();
            } else if (c == '#') {
                i = (std::min)(s_.find('\n', i), n);
            } else if (c == '&' && i + 1 < n && s_[i + 1] == '>') {
                const bool append = i + 2 < n && s_[i + 2] == '>';
                out.push_back({Token::Redir, {}, append ? "&>>" : "&>", "", false});
                i += append ? 3 : 2;
            } else if (c == ';' || c == '|' || c == '&' || c == '(' || c == ')') {
                std::string op(1, c);
                if (i + 1 < n && ((c == '&' && s_[i + 1] == '&') || (c == '|' && s_[i + 1] == '|') ||
                                  (c == ';' && s_[i + 1] == ';') || (c == '|' && s_[i + 1] == '&'))) {
                    op += s_[i + 1];
                }
                i += op.size();
                out.push_back({Token::Op, {}, op, "", false});
            } else if ((c == '<' || c == '>') && !(i + 1 < n && s_[i + 1] == '(')) {
                std::string op(1, c);
                for (const char* longer : {"<<<", "<<-", "<<", "<>", "<&", ">>", ">|", ">&"}) {
                    if (s_.compare(i, std::char_traits<char>::length(longer), longer) == 0) {
                        op = longer;
                        break;
                    }
                }
                i += op.size();
                out.push_back({Token::Redir, {}, op, "", false});
                if (op == "<<" || op == "<<-") {
                    while (i < n && (s_[i] == ' ' || s_[i] == '\t')) ++i;
                    Word delimiter;
                    if (!word(i, delimiter)) return false;
                    std::string text;
                    out.back().bodyExpands = true;
                    for (const auto& part : delimiter.parts) {
                        text += part.text;
                        if (part.quoted || part.kind != Part::Lit) out.back().bodyExpands = false;
                    }
                    pending.push_back({out.size() - 1, text, op == "<<-"});
                }
            } else {
                // A file descriptor before a redirection: 2>file, 3<&0.
                size_t digits = i;
                while (digits < n && std::isdigit(static_cast<unsigned char>(s_[digits]))) ++digits;
                if (digits > i && digits < n && (s_[digits] == '<' || s_[digits] == '>')) {
                    i = digits;
                    continue;
                }
                Token t;
                if (!word(i, t.word)) return false;
                out.push_back(std::move(t));
            }
        }
        return true;
    }

private:
    // Index of the bracket closing the one before `from`, quotes and nesting respected; npos when unclosed.
    size_t closing(size_t from, char open, char close) const {
        int depth = 1;
        for (size_t i = from; i < s_.size(); ++i) {
            const char c = s_[i];
            if (c == '\\') {
                ++i;
            } else if (c == '\'') {
                i = s_.find('\'', i + 1);
                if (i == std::string::npos) return i;
            } else if (c == '"') {
                for (++i; i < s_.size() && s_[i] != '"'; ++i) {
                    if (s_[i] == '\\') ++i;
                }
            } else if (c == open) {
                ++depth;
            } else if (c == close && --depth == 0) {
                return i;
            }
        }
        return std::string::npos;
    }

    bool backtick(size_t& i, Word& w, bool quoted) {
        std::string body;
        for (++i; i < s_.size() && s_[i] != '`'; ++i) {
            if (s_[i] == '\\' && i + 1 < s_.size()) ++i;
            body += s_[i];
        }
        if (i >= s_.size()) return false;
        ++i;
        w.parts.push_back({Part::Sub, body, quoted});
        return true;
    }

    // $'...': ANSI-C quoting is decoded before the path is judged.
    bool ansiString(size_t& i, Word& w) {
        std::string out;
        for (++i; i < s_.size() && s_[i] != '\''; ++i) {
            if (s_[i] != '\\' || i + 1 >= s_.size()) {
                out += s_[i];
                continue;
            }
            const char e = s_[++i];
            const auto number = [&](int base, int maxDigits) {
                int value = 0, used = 0;
                while (used < maxDigits && i + 1 < s_.size() && std::isxdigit(static_cast<unsigned char>(s_[i + 1])) &&
                       (base == 16 || (s_[i + 1] >= '0' && s_[i + 1] <= '7'))) {
                    const char d = s_[++i];
                    value = value * base + (std::isdigit(static_cast<unsigned char>(d)) ? d - '0' : (std::tolower(d) - 'a' + 10));
                    ++used;
                }
                return static_cast<char>(value);
            };
            switch (e) {
                case 'n': out += '\n'; break;
                case 't': out += '\t'; break;
                case 'r': out += '\r'; break;
                case 'a': out += '\a'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'v': out += '\v'; break;
                case 'e': case 'E': out += '\x1B'; break;
                case 'x': out += number(16, 2); break;
                case 'u': case 'U': case 'c':
                    w.parts.push_back({Part::Unknown, "", true});
                    break;
                default:
                    if (e >= '0' && e <= '7') {
                        --i;
                        out += number(8, 3);
                    } else {
                        out += e;
                    }
            }
        }
        if (i >= s_.size()) return false;
        ++i;
        w.lit(out, true);
        return true;
    }

    bool dollar(size_t& i, Word& w, bool quoted) {
        const size_t n = s_.size();
        const char next = i + 1 < n ? s_[i + 1] : '\0';
        if (next == '(' && i + 2 < n && s_[i + 2] == '(') {
            const size_t end = closing(i + 3, '(', ')');
            if (end == std::string::npos || end + 1 >= n) return false;
            // Arithmetic is a number, which can't climb a folder, but evaluating it can run a
            // substitution: one written in it, or one held in a variable it names.
            w.parts.push_back({Part::Unknown, "arith:" + s_.substr(i + 3, end - i - 3), quoted});
            i = end + 2;
            return true;
        }
        if (next == '(') {
            const size_t end = closing(i + 2, '(', ')');
            if (end == std::string::npos) return false;
            w.parts.push_back({Part::Sub, s_.substr(i + 2, end - i - 2), quoted});
            i = end + 1;
            return true;
        }
        if (next == '{') {
            const size_t end = closing(i + 2, '{', '}');
            if (end == std::string::npos) return false;
            const std::string body = s_.substr(i + 2, end - i - 2);
            w.parts.push_back({isName(body) ? Part::Var : Part::Unknown, body, quoted});
            i = end + 1;
            return true;
        }
        if (next == '\'' && !quoted) {
            ++i;
            return ansiString(i, w);
        }
        // $"..." is a translated string: the $ is dropped and the quotes read as usual.
        if (next == '"' && !quoted) {
            ++i;
            return true;
        }
        if (isNameStart(next)) {
            size_t end = i + 1;
            while (end < n && isNameChar(s_[end])) ++end;
            w.parts.push_back({Part::Var, s_.substr(i + 1, end - i - 1), quoted});
            i = end;
            return true;
        }
        if (std::isdigit(static_cast<unsigned char>(next)) || std::string("@*#?$!-").find(next) != std::string::npos) {
            if (next != '\0') {
                w.parts.push_back({Part::Unknown, std::string(1, next), quoted});
                i += 2;
                return true;
            }
        }
        w.lit('$', quoted);
        ++i;
        return true;
    }

    bool word(size_t& i, Word& w) {
        const size_t n = s_.size();
        while (i < n) {
            const char c = s_[i];
            if (c == ' ' || c == '\t' || c == '\n' || c == '\r' || c == ';' || c == '|' || c == '&' || c == ')') break;
            if (c == '(' && !w.parts.empty()) break;
            if ((c == '<' || c == '>') && i + 1 < n && s_[i + 1] == '(') {
                // Process substitution: the command runs, and the word is a pipe's name.
                const size_t end = closing(i + 2, '(', ')');
                if (end == std::string::npos) return false;
                w.parts.push_back({Part::Sub, s_.substr(i + 2, end - i - 2), true});
                w.parts.back().kind = Part::Sub;
                w.lit("/dev/fd/63", true);
                processSubstitution = true;
                i = end + 1;
                continue;
            }
            if (c == '<' || c == '>' || c == '(') break;
            if (c == '\\') {
                if (i + 1 >= n) {
                    ++i;
                    break;
                }
                if (s_[i + 1] != '\n') w.lit(s_[i + 1], true);
                i += 2;
            } else if (c == '\'') {
                const size_t end = s_.find('\'', i + 1);
                if (end == std::string::npos) return false;
                w.lit(s_.substr(i + 1, end - i - 1), true);
                i = end + 1;
            } else if (c == '"') {
                ++i;
                w.lit("", true);
                while (i < n && s_[i] != '"') {
                    const char q = s_[i];
                    if (q == '\\' && i + 1 < n && std::string("$`\"\\\n").find(s_[i + 1]) != std::string::npos) {
                        if (s_[i + 1] != '\n') w.lit(s_[i + 1], true);
                        i += 2;
                    } else if (q == '$') {
                        if (!dollar(i, w, true)) return false;
                    } else if (q == '`') {
                        if (!backtick(i, w, true)) return false;
                    } else {
                        w.lit(q, true);
                        ++i;
                    }
                }
                if (i >= n) return false;
                ++i;
            } else if (c == '$') {
                if (!dollar(i, w, false)) return false;
            } else if (c == '`') {
                if (!backtick(i, w, false)) return false;
            } else {
                w.lit(c, false);
                ++i;
            }
        }
        return true;
    }

public:
    bool processSubstitution = false;

    // Text the shell expands like the inside of double quotes (a heredoc body): its $ and ` parts.
    bool quotedText(Word& w) {
        for (size_t i = 0; i < s_.size();) {
            if (s_[i] == '\\' && i + 1 < s_.size()) {
                w.lit(s_[i + 1], true);
                i += 2;
            } else if (s_[i] == '$') {
                if (!dollar(i, w, true)) return false;
            } else if (s_[i] == '`') {
                if (!backtick(i, w, true)) return false;
            } else {
                w.lit(s_[i++], true);
            }
        }
        return true;
    }

private:
    const std::string& s_;
};

// ---------------------------------------------------------------------------
// Patterns: a field keeps `\` before every character that is not a live wildcard.
// ---------------------------------------------------------------------------

std::string escaped(const std::string& s) {
    std::string out;
    for (const char c : s) {
        if (std::string("*?[]{},\\~").find(c) != std::string::npos) out += '\\';
        out += c;
    }
    return out;
}

std::string unescaped(const std::string& pattern) {
    std::string out;
    for (size_t i = 0; i < pattern.size(); ++i) {
        if (pattern[i] == '\\' && i + 1 < pattern.size()) ++i;
        out += pattern[i];
    }
    return out;
}

bool hasWildcard(const std::string& pattern) {
    for (size_t i = 0; i < pattern.size(); ++i) {
        if (pattern[i] == '\\') ++i;
        else if (pattern[i] == '*' || pattern[i] == '?' || pattern[i] == '[') return true;
    }
    return false;
}

// fnmatch for one path component: * ? [set] and \ escapes.
bool matches(const std::string& p, size_t pi, const std::string& s, size_t si) {
    size_t starP = std::string::npos, starS = 0;
    while (si < s.size()) {
        bool ok = false;
        size_t advance = 1;
        if (pi < p.size()) {
            const char c = p[pi];
            if (c == '*') {
                starP = pi++;
                starS = si;
                continue;
            }
            if (c == '?') {
                ok = true;
            } else if (c == '[') {
                size_t j = pi + 1;
                const bool negate = j < p.size() && (p[j] == '!' || p[j] == '^');
                if (negate) ++j;
                bool hit = false, closed = false;
                for (size_t first = j; j < p.size(); ++j) {
                    if (p[j] == ']' && j > first) {
                        closed = true;
                        break;
                    }
                    char lo = p[j];
                    if (lo == '\\' && j + 1 < p.size()) lo = p[++j];
                    char hi = lo;
                    if (j + 2 < p.size() && p[j + 1] == '-' && p[j + 2] != ']') {
                        j += 2;
                        hi = p[j];
                        if (hi == '\\' && j + 1 < p.size()) hi = p[++j];
                    }
                    if (s[si] >= lo && s[si] <= hi) hit = true;
                }
                if (closed) {
                    ok = hit != negate;
                    advance = j - pi + 1;
                } else {
                    ok = s[si] == '[';
                }
            } else if (c == '\\' && pi + 1 < p.size()) {
                ok = p[pi + 1] == s[si];
                advance = 2;
            } else {
                ok = c == s[si];
            }
        }
        if (ok) {
            pi += advance;
            ++si;
        } else if (starP != std::string::npos) {
            pi = starP + 1;
            si = ++starS;
        } else {
            return false;
        }
    }
    while (pi < p.size() && p[pi] == '*') ++pi;
    return pi == p.size();
}

// {a,b} alternatives of a pattern (live braces only). False when there are too many.
bool expandBraces(const std::string& pattern, std::vector<std::string>& out) {
    size_t open = std::string::npos;
    for (size_t i = 0; i < pattern.size(); ++i) {
        if (pattern[i] == '\\') {
            ++i;
        } else if (pattern[i] == '{') {
            open = i;
            break;
        }
    }
    if (open == std::string::npos) {
        out.push_back(pattern);
        return out.size() <= kMaxAlternatives;
    }
    int depth = 0;
    std::vector<size_t> commas;
    size_t close = std::string::npos;
    for (size_t i = open; i < pattern.size(); ++i) {
        if (pattern[i] == '\\') {
            ++i;
        } else if (pattern[i] == '{') {
            ++depth;
        } else if (pattern[i] == '}' && --depth == 0) {
            close = i;
            break;
        } else if (pattern[i] == ',' && depth == 1) {
            commas.push_back(i);
        }
    }
    if (close == std::string::npos || commas.empty()) {
        // Not a list: the brace is a letter. Step past it and keep looking.
        std::vector<std::string> rest;
        if (!expandBraces(pattern.substr(open + 1), rest)) return false;
        for (const auto& r : rest) out.push_back(pattern.substr(0, open) + "\\{" + r);
        return out.size() <= kMaxAlternatives;
    }
    commas.push_back(close);
    size_t from = open + 1;
    for (const size_t comma : commas) {
        const std::string candidate = pattern.substr(0, open) + pattern.substr(from, comma - from) + pattern.substr(close + 1);
        if (!expandBraces(candidate, out)) return false;
        from = comma + 1;
    }
    return out.size() <= kMaxAlternatives;
}

// ---------------------------------------------------------------------------
// What each program does with its words.
// ---------------------------------------------------------------------------

using Names = std::set<std::string>;

// Touch no file their words name.
const Names& inertPrograms() {
    static const Names names = {":", "true", "false", "echo", "printf", "pwd", "exit", "return", "break", "continue",
                                "shift", "wait", "times", "umask", "ulimit", "set", "shopt", "jobs", "fg", "bg",
                                "disown", "kill", "sleep", "seq", "date", "whoami", "id", "groups", "hostname", "uname",
                                "arch", "nproc", "uptime", "tty", "basename", "dirname", "expr", "tr", "cal",
                                "printenv", "which", "type", "getconf", "sw_vers", "logname", "clear"};
    return names;
}

// Read or change exactly the files their words name.
const Names& pathPrograms() {
    static const Names names = {
        "ls", "dir", "cat", "head", "tail", "wc", "stat", "file", "du", "df", "touch", "mkdir", "rmdir", "rm",
        "cp", "mv", "ln", "chmod", "chown", "chgrp", "tee", "sort", "uniq", "cut", "nl", "tac", "rev", "fold",
        "fmt", "comm", "paste", "join", "diff", "cmp", "md5", "md5sum", "shasum", "sha1sum", "sha256sum",
        "sha512sum", "cksum", "sum", "base64", "xxd", "od", "hexdump", "strings", "column", "expand", "unexpand",
        "realpath", "readlink", "tree", "grep", "egrep", "fgrep", "rg", "zcat", "gzcat", "gzip", "gunzip", "bzip2",
        "bunzip2", "xz", "unxz", "zip", "unzip", "tar", "truncate", "dd", "sed", "awk", "gawk", "mawk", "nawk",
        "find", "split", "csplit", "iconv", "pr", "shuf", "tsort", "look", "mktemp", "install", "unlink", "link",
        "b2sum", "numfmt"};
    return names;
}

// Change a file in place, so one with a second name elsewhere must not be among their words.
const Names& inPlaceWriters() {
    static const Names names = {"tee", "chmod", "chown", "chgrp", "truncate"};
    return names;
}

// Change the files their words name, or make new ones.
const Names& changingPrograms() {
    static const Names names = {"touch", "mkdir", "rmdir", "rm", "cp", "mv", "ln", "chmod", "chown", "chgrp", "tee",
                                "truncate", "dd", "sed", "split", "csplit", "mktemp", "install", "unlink", "link",
                                "gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "zip", "unzip", "tar"};
    return names;
}

// Run by the shell itself, never looked up as a file.
const Names& shellBuiltins() {
    static const Names names = {":", "true", "false", "echo", "printf", "pwd", "exit", "return", "break", "continue",
                                "shift", "wait", "times", "umask", "ulimit", "set", "shopt", "jobs", "fg", "bg",
                                "disown", "kill", "test", "[", "[[", "cd", "pushd", "popd", "export", "declare",
                                "typeset", "local", "readonly", "unset", "read", "mapfile", "readarray", "getopts",
                                "eval", "trap", "source", ".", "alias", "type", "hash", "let", "command", "builtin",
                                "exec", "enable", "chdir"};
    return names;
}

// True for a variable that changes which program runs, how words are read, or what a checked
// program does beyond its own words (tar, zip, rg and git all take options or settings from one).
bool steers(const std::string& name) {
    static const Names names = {
        "PATH", "IFS", "BASH_ENV", "ENV", "LD_PRELOAD", "LD_LIBRARY_PATH",
        "LD_AUDIT", "DYLD_INSERT_LIBRARIES", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "GCONV_PATH",
        "PROMPT_COMMAND", "PS4", "GLOBIGNORE", "SHELLOPTS", "BASHOPTS", "POSIXLY_CORRECT", "PYTHONSTARTUP",
        "PERL5OPT", "NODE_OPTIONS", "RUBYOPT", "PAGER", "EDITOR", "VISUAL", "LESSOPEN", "LESSCLOSE", "TAR_OPTIONS",
        "GREP_OPTIONS", "RIPGREP_CONFIG_PATH", "ZIPOPT", "UNZIP", "UNZIPOPT", "ZIPINFO", "GZIP", "BZIP2", "BZIP",
        "XZ_OPT", "XZ_DEFAULTS", "TMPDIR"};
    std::string upper = name;
    std::transform(upper.begin(), upper.end(), upper.begin(), [](unsigned char c) { return static_cast<char>(std::toupper(c)); });
    return names.count(upper) > 0 || upper.rfind("GIT_", 0) == 0;
}

// Run the words after their own options as a command.
const Names& wrapperPrograms() {
    static const Names names = {"nohup", "nice", "ionice", "timeout", "gtimeout", "time", "stdbuf", "setsid", "chrt",
                                "taskset", "caffeinate", "sudo", "doas", "unbuffer", "command", "builtin", "exec"};
    return names;
}

const Names& shellPrograms() {
    static const Names names = {"sh", "bash", "zsh", "dash", "ksh", "ash", "mksh"};
    return names;
}

// The language of a program that runs code it is handed, or "".
std::string interpreterLanguage(const std::string& name) {
    static const std::vector<std::pair<const char*, const char*>> prefixes = {
        {"python", "python"}, {"pypy", "python"}, {"node", "node"},     {"deno", "node"},    {"bun", "node"},
        {"perl", "perl"},     {"ruby", "ruby"},   {"php", "php"},       {"lua", "lua"},      {"rscript", "r"},
        {"osascript", "applescript"}, {"swift", "swift"}, {"tclsh", "tcl"}, {"wish", "tcl"}, {"julia", "julia"},
        {"pwsh", "powershell"}, {"powershell", "powershell"}, {"fish", "fish"}, {"csh", "csh"}, {"tcsh", "csh"},
        {"groovy", "groovy"}, {"jshell", "java"}, {"irb", "ruby"}, {"ipython", "python"}};
    if (name == "py") return "python";
    for (const auto& [prefix, language] : prefixes) {
        const std::string p = prefix;
        if (name.compare(0, p.size(), p) != 0) continue;
        // python3.12, lua5.4, node18: a version may follow, a longer word is another program.
        const std::string rest = name.substr(p.size());
        if (rest.find_first_not_of("0123456789.") == std::string::npos) return language;
    }
    return "";
}

// Reach another computer whatever their words are (a local target aside).
const Names& networkPrograms() {
    static const Names names = {
        "nc", "ncat", "netcat", "socat", "telnet", "ssh", "scp", "sftp", "ftp", "tftp", "lftp", "rsync", "rclone",
        "mosh", "nmap", "ping", "ping6", "traceroute", "traceroute6", "tracepath", "mtr", "dig", "nslookup", "host",
        "whois", "drill", "gh", "glab", "hub", "yt-dlp", "youtube-dl", "aria2c", "axel", "lynx", "links", "w3m",
        "elinks", "http", "https", "xh", "curlie", "websocat", "grpcurl", "smbclient", "mail", "mailx", "sendmail",
        "msmtp", "nscurl", "dns-sd", "iperf", "iperf3", "aws", "gcloud", "az", "gsutil", "s3cmd", "kubectl",
        "wget", "wget2", "curl", "fetch"};
    return names;
}

// The fetchers whose target decides: the caller judges their command line, with its flags.
const Names& fetchPrograms() {
    static const Names names = {"curl", "wget", "wget2", "fetch"};
    return names;
}

bool isLocalHost(std::string host) {
    const size_t scheme = host.find("://");
    if (scheme != std::string::npos) host.erase(0, scheme + 3);
    host = host.substr(0, host.find_first_of("/?#"));
    const size_t user = host.rfind('@');
    if (user != std::string::npos) host.erase(0, user + 1);
    if (!host.empty() && host[0] == '[') {
        host = host.substr(1, host.find(']') == std::string::npos ? std::string::npos : host.find(']') - 1);
    } else if (std::count(host.begin(), host.end(), ':') == 1) {
        host.erase(host.find(':'));
    }
    std::transform(host.begin(), host.end(), host.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return host == "localhost" || host == "::1" || host == "0.0.0.0" || host.rfind("127.", 0) == 0;
}

// True for sed commands that only edit the text: no w, r or e, which write, read or run something else.
bool sedScriptIsPlain(const std::string& script) {
    size_t i = 0;
    const size_t n = script.size();
    const auto skipDelimited = [&](char delimiter) {
        for (; i < n && script[i] != delimiter; ++i) {
            if (script[i] == '\\') {
                ++i;
            } else if (script[i] == '[') {
                size_t j = i + 1;
                if (j < n && script[j] == '^') ++j;
                if (j < n && script[j] == ']') ++j;
                while (j < n && script[j] != ']') ++j;
                if (j < n) i = j;
            } else if (script[i] == '\n') {
                return false;
            }
        }
        if (i >= n) return false;
        ++i;
        return true;
    };
    const auto address = [&] {
        if (i < n && (std::isdigit(static_cast<unsigned char>(script[i])) || script[i] == '$')) {
            while (i < n && (std::isdigit(static_cast<unsigned char>(script[i])) || script[i] == '$' || script[i] == '~')) ++i;
            return true;
        }
        if (i < n && script[i] == '/') {
            ++i;
            if (!skipDelimited('/')) return false;
        } else if (i + 1 < n && script[i] == '\\') {
            const char d = script[i + 1];
            i += 2;
            if (!skipDelimited(d)) return false;
        } else {
            return true;
        }
        while (i < n && (script[i] == 'I' || script[i] == 'M')) ++i;
        return true;
    };
    while (i < n) {
        while (i < n && (script[i] == ' ' || script[i] == '\t' || script[i] == ';' || script[i] == '\n')) ++i;
        if (i >= n) break;
        if (!address()) return false;
        while (i < n && script[i] == ' ') ++i;
        if (i < n && script[i] == ',') {
            ++i;
            if (i < n && (script[i] == '+' || script[i] == '~')) ++i;
            if (!address()) return false;
        }
        while (i < n && (script[i] == ' ' || script[i] == '!')) ++i;
        if (i >= n) return false;
        const char command = script[i++];
        if (command == 's' || command == 'y') {
            if (i >= n) return false;
            const char d = script[i++];
            if (d == '\\' || d == '\n' || !skipDelimited(d) || !skipDelimited(d)) return false;
            for (; i < n && script[i] != ';' && script[i] != '}' && script[i] != '\n'; ++i) {
                if (std::string("gpIiMm0123456789 ").find(script[i]) == std::string::npos) return false;
            }
        } else if (std::string("dDgGhHnNpPxz=lqQ").find(command) != std::string::npos) {
            while (i < n && (std::isdigit(static_cast<unsigned char>(script[i])) || script[i] == ' ')) ++i;
            if (i < n && script[i] != ';' && script[i] != '}' && script[i] != '\n') return false;
        } else if (command == '{' || command == '}') {
            continue;
        } else if (command == 'a' || command == 'i' || command == 'c' || command == '#' || command == 'b' ||
                   command == 't' || command == 'T' || command == ':') {
            const bool text = command == 'a' || command == 'i' || command == 'c' || command == '#';
            while (i < n && script[i] != '\n' && (text || script[i] != ';')) {
                if (script[i] == '\\' && i + 1 < n) ++i;
                ++i;
            }
        } else {
            return false;
        }
    }
    return true;
}

// True for an awk program that only reads its input and prints: no command, no file of its own choosing.
bool awkProgramIsPlain(const std::string& program) {
    bool printing = false;
    char previous = '{';
    for (size_t i = 0; i < program.size(); ++i) {
        const char c = program[i];
        if (c == '"') {
            for (++i; i < program.size() && program[i] != '"'; ++i) {
                if (program[i] == '\\') ++i;
            }
            previous = '"';
            continue;
        }
        if (c == '/' && std::string("(,{};~!&|=\n").find(previous) != std::string::npos) {
            for (++i; i < program.size() && program[i] != '/'; ++i) {
                if (program[i] == '\\') ++i;
            }
            previous = '/';
            continue;
        }
        if (c == '#') {
            i = (std::min)(program.find('\n', i), program.size());
            continue;
        }
        if (c == ';' || c == '{' || c == '}' || c == '\n') printing = false;
        if (c == '@') return false;
        if (c == '|' && !(i + 1 < program.size() && program[i + 1] == '|') && !(i > 0 && program[i - 1] == '|')) return false;
        if (c == '>' && printing) return false;
        if (isNameStart(c) && !(i > 0 && isNameChar(program[i - 1]))) {
            size_t end = i;
            while (end < program.size() && isNameChar(program[end])) ++end;
            const std::string name = program.substr(i, end - i);
            for (const char* bad : {"system", "getline", "ARGV", "ARGC", "close", "ENVIRON", "PROCINFO"}) {
                if (name == bad) return false;
            }
            if (name == "print" || name == "printf") printing = true;
            i = end - 1;
            previous = 'a';
            continue;
        }
        if (c != ' ' && c != '\t') previous = c;
    }
    return true;
}

std::string lowered(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

// A program's name for a message a person reads: a plain name as written, anything else (a path
// with spaces, a name with a hidden character in it) as "this program".
std::string named(const std::string& program) {
    static const std::string plain = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-.+/";
    return !program.empty() && program.size() <= 80 && program.find_first_not_of(plain) == std::string::npos
               ? program
               : "this program";
}

std::string quotedForShell(const std::string& word) {
    if (!word.empty() && word.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                                                "_-./:=@%+,") == std::string::npos) {
        return word;
    }
    std::string out = "'";
    for (const char c : word) out += c == '\'' ? std::string("'\\''") : std::string(1, c);
    return out + "'";
}

// ---------------------------------------------------------------------------
// The judge: walks the command the way the shell would run it.
// ---------------------------------------------------------------------------

struct Value {
    std::vector<std::string> values;  // every value the variable may hold when read
    bool unknown = false;
};

struct State {
    std::vector<std::string> folders;  // every folder the shell may be standing in
    bool folderUnknown = false;
    bool movedSurely = false;  // the last cd named a folder that is there, so it will not fail
    std::map<std::string, Value> vars;
};

std::vector<std::string> merged(std::vector<std::string> a, const std::vector<std::string>& b) {
    for (const auto& f : b) {
        if (std::find(a.begin(), a.end(), f) == a.end()) a.push_back(f);
    }
    return a;
}

struct Field {
    std::string pattern;
    bool unknown = false;
    bool wildcard = false;
    std::string text() const { return unescaped(pattern); }
};

class Judge {
public:
    Judge(const Workspace* ws, const std::map<std::string, std::string>& env, ShellJudgement& out)
        : ws_(ws), env_(env), out_(out) {
        // The folders programs are looked up in, as this process will hand them to the shell.
        const std::string path = getEnvVar("PATH");
        for (size_t from = 0; from <= path.size();) {
            const size_t colon = (std::min)(path.find(':', from), path.size());
            searchPath_.push_back(path.substr(from, colon - from));
            from = colon + 1;
        }
    }

    void script(const std::string& text, State& st, int depth) {
        if (depth > kMaxDepth) {
            hide("it nests commands too deep to read");
            return;
        }
        if (depth > 0) out_.compound = true;
        std::vector<Token> tokens;
        Lexer lexer(text);
        if (!lexer.run(tokens)) {
            hide("its quoting can't be read");
            return;
        }
        if (lexer.processSubstitution) out_.compound = true;
        depth_ = depth;
        size_t i = 0;
        list(tokens, i, st, false);
        depth_ = depth;
    }

private:
    // --- findings ---------------------------------------------------------

    void opaque(const std::string& why) {
        if (out_.opaque.empty()) out_.opaque = why;
    }
    void hide(const std::string& why) {
        if (out_.hidden.empty()) out_.hidden = why;
        opaque(why);
    }
    void network(const std::string& program) {
        if (out_.network.empty()) out_.network = program;
    }
    void outside(const std::string& word, const std::string& real, const std::string& why = "") {
        if (!out_.outsideWord.empty()) return;
        out_.outsideWord = word;
        out_.outsidePath = real;
        out_.outsideWhy = why;
    }

    // --- variables --------------------------------------------------------

    Value lookup(const std::string& name, const State& st) const {
        const auto it = st.vars.find(name);
        if (it != st.vars.end()) return it->second;
        if (name == "PWD") return st.folderUnknown ? Value{{}, true} : Value{st.folders, false};
        for (const char* number : {"RANDOM", "SECONDS", "LINENO", "UID", "EUID", "PPID", "BASHPID", "SRANDOM",
                                   "EPOCHSECONDS", "COLUMNS", "LINES", "SHLVL"}) {
            if (name == number) return {{"0"}, false};
        }
        // bash sets these itself: `_` is the last word of the command before.
        if (name == "_" || name == "OLDPWD" || name == "REPLY" || name == "IFS" || name == "PIPESTATUS" ||
            name.rfind("BASH", 0) == 0) {
            return {{}, true};
        }
        const auto mine = env_.find(name);
        if (mine != env_.end()) return {{mine->second}, false};
        // Not set anywhere this check can see: the shell or the command may give it a value.
        const char* inherited = std::getenv(name.c_str());
        return inherited ? Value{{inherited}, false} : Value{{}, true};
    }

    // True when evaluating arithmetic text could run a command: a substitution in it, or in a variable it names.
    bool arithmeticRuns(const std::string& text, const State& st) const {
        if (text.find("$(") != std::string::npos || text.find('`') != std::string::npos) return true;
        for (size_t i = 0; i < text.size(); ++i) {
            if (!isNameStart(text[i]) || (i > 0 && isNameChar(text[i - 1]))) continue;
            size_t end = i;
            while (end < text.size() && isNameChar(text[end])) ++end;
            const auto it = st.vars.find(text.substr(i, end - i));
            const auto exported = env_.find(text.substr(i, end - i));
            std::vector<std::string> values;
            if (it != st.vars.end()) {
                if (it->second.unknown) return true;
                values = it->second.values;
            } else if (exported != env_.end()) {
                values.push_back(exported->second);
            } else if (const char* inherited = std::getenv(text.substr(i, end - i).c_str())) {
                values.push_back(inherited);
            }
            for (const auto& v : values) {
                if (v.find_first_of("$`[") != std::string::npos) return true;
            }
            i = end;
        }
        return false;
    }

    void assign(State& st, const std::string& name, const Value& value) {
        if (steers(name)) opaque("it sets " + name + ", which changes what its later commands do");
        auto it = st.vars.find(name);
        if (it == st.vars.end()) {
            st.vars[name] = value;
            return;
        }
        // An assignment may be skipped (a && x=1), so the old value stays possible.
        it->second.unknown = it->second.unknown || value.unknown;
        for (const auto& v : value.values) {
            if (std::find(it->second.values.begin(), it->second.values.end(), v) == it->second.values.end()) {
                it->second.values.push_back(v);
            }
        }
        if (it->second.values.size() > kMaxAlternatives) it->second = {{}, true};
    }

    // --- expansion --------------------------------------------------------

    // The fields a word becomes. `subs` collects the commands it runs on the way.
    std::vector<Field> expand(const Word& word, const State& st, std::vector<std::string>& subs, bool split = true) {
        std::vector<std::string> alternatives = {""};
        bool anyQuoted = false;
        for (size_t p = 0; p < word.parts.size(); ++p) {
            const Part& part = word.parts[p];
            anyQuoted = anyQuoted || part.quoted;
            std::vector<std::string> pieces;
            if (part.kind == Part::Lit) {
                std::string text = part.quoted ? escaped(part.text) : part.text;
                // ~ and ~/x at the start of a word are the home folder.
                if (p == 0 && !part.quoted && !text.empty() && text[0] == '~' && (text.size() == 1 || text[1] == '/')) {
                    const Value home = lookup("HOME", st);
                    if (home.unknown || home.values.empty() || home.values[0].empty()) {
                        pieces.push_back(std::string(1, kUnknown));
                    } else {
                        for (const auto& h : home.values) pieces.push_back(escaped(h) + text.substr(1));
                    }
                } else {
                    pieces.push_back(text);
                }
            } else if (part.kind == Part::Var) {
                const Value value = lookup(part.text, st);
                if (value.unknown) pieces.push_back(std::string(1, kUnknown));
                for (const auto& v : value.values) {
                    if (part.quoted || !split) {
                        pieces.push_back(escaped(v));
                        continue;
                    }
                    // Unquoted: the value is split at spaces and its wildcards are live.
                    std::string piece;
                    for (const char c : v) {
                        if (c == ' ' || c == '\t' || c == '\n') {
                            if (piece.empty() || piece.back() != kBreak) piece += kBreak;
                        } else if (c == '\\' || c == '{' || c == '}' || c == ',' || c == '~') {
                            piece += '\\';
                            piece += c;
                        } else {
                            piece += c;
                        }
                    }
                    pieces.push_back(piece);
                }
                if (pieces.empty()) pieces.push_back("");
            } else if (part.kind == Part::Unknown && part.text.rfind("arith:", 0) == 0) {
                if (arithmeticRuns(part.text.substr(6), st)) hide("it does arithmetic on text that can run a command");
                pieces.push_back("0");
            } else {
                if (part.kind == Part::Sub) subs.push_back(part.text);
                else if (part.text.find("$(") != std::string::npos || part.text.find('`') != std::string::npos) {
                    hide("it uses an expansion that runs a command GAIA can't read");
                }
                pieces.push_back(std::string(1, kUnknown));
            }
            std::vector<std::string> next;
            for (const auto& a : alternatives) {
                for (const auto& piece : pieces) {
                    if (next.size() > kMaxAlternatives) break;
                    next.push_back(a + piece);
                }
            }
            alternatives = std::move(next);
        }
        std::vector<Field> fields;
        if (alternatives.size() > kMaxAlternatives) {
            fields.push_back({"", true, false});
            return fields;
        }
        for (const auto& alternative : alternatives) {
            std::vector<std::string> braced;
            if (!expandBraces(alternative, braced)) {
                fields.push_back({"", true, false});
                continue;
            }
            for (const auto& b : braced) {
                size_t from = 0;
                for (;;) {
                    const size_t stop = b.find(kBreak, from);
                    const std::string field = b.substr(from, stop == std::string::npos ? stop : stop - from);
                    if (!field.empty() || anyQuoted) {
                        fields.push_back({field, field.find(kUnknown) != std::string::npos, hasWildcard(field)});
                    }
                    if (stop == std::string::npos) break;
                    from = stop + 1;
                }
            }
        }
        multi_ = alternatives.size() > 1;
        return fields;
    }

    // --- paths ------------------------------------------------------------

    // `text` as a path from `base`, spelled without . and .. (links are not followed).
    static std::string joined(const std::string& base, const std::string& text) {
        const fs::path p = fs::u8path(text);
        return (p.has_root_directory() ? p : fs::u8path(base) / p).lexically_normal().generic_u8string();
    }

    static bool isAbsolute(const std::string& p) {
        return !p.empty() && (p[0] == '/' || p[0] == '\\' ||
                              (p.size() > 2 && std::isalpha(static_cast<unsigned char>(p[0])) && p[1] == ':'));
    }

    // Every existing path a wildcard pattern matches under `base`. False when there are too many to check.
    bool glob(const std::string& base, const std::string& pattern, std::vector<std::string>& found) {
        std::vector<std::string> components;
        std::string current;
        for (size_t i = 0; i < pattern.size(); ++i) {
            if (pattern[i] == '\\' && i + 1 < pattern.size()) {
                current += pattern[i];
                current += pattern[++i];
            } else if (pattern[i] == '/') {
                components.push_back(current);
                current.clear();
            } else {
                current += pattern[i];
            }
        }
        components.push_back(current);
        std::vector<std::string> at = {isAbsolute(unescaped(pattern)) ? std::string("/") : base};
        for (size_t c = 0; c < components.size(); ++c) {
            const std::string& component = components[c];
            if (component.empty()) continue;
            std::vector<std::string> next;
            if (!hasWildcard(component)) {
                for (const auto& dir : at) next.push_back((fs::u8path(dir) / fs::u8path(unescaped(component))).u8string());
            } else {
                for (const auto& dir : at) {
                    std::error_code ec;
                    // .* also matches the folder above.
                    if (component[0] == '.' && matches(component, 0, "..", 0)) next.push_back(dir + "/..");
                    for (fs::directory_iterator it(fs::u8path(dir), fs::directory_options::skip_permission_denied, ec), end;
                         !ec && it != end; it.increment(ec)) {
                        if (++entriesSeen_ > kMaxDirEntries) return false;
                        const std::string name = it->path().filename().u8string();
                        if (name[0] == '.' && component[0] != '.') continue;
                        if (matches(component, 0, name, 0)) next.push_back((fs::u8path(dir) / fs::u8path(name)).u8string());
                    }
                    if (next.size() > kMaxMatches) return false;
                }
            }
            at = std::move(next);
        }
        found.insert(found.end(), at.begin(), at.end());
        return found.size() <= kMaxMatches;
    }

    // True when `folder` is one the shell looks programs up in.
    bool onSearchPath(const std::string& folder, const std::string& base) const {
        std::error_code ec;
        for (const auto& entry : searchPath_) {
            const fs::path dir = entry.empty() || !isAbsolute(entry) ? fs::u8path(base) / fs::u8path(entry) : fs::u8path(entry);
            if (fs::equivalent(dir, fs::u8path(folder), ec) && !ec) return true;
        }
        return false;
    }

    // True when the shell would run a file inside the allowed folders for this bare program name.
    bool runsFromWorkspace(const std::string& program, const State& st) const {
        std::error_code ec;
        for (const auto& entry : searchPath_) {
            const bool relative = entry.empty() || !isAbsolute(entry);
            for (const auto& base : relative ? st.folders : std::vector<std::string>{""}) {
                const fs::path dir = relative ? fs::u8path(base) / fs::u8path(entry) : fs::u8path(entry);
                if (!fs::exists(dir / fs::u8path(program), ec)) continue;
                const Workspace::Place where = ws_->locate((dir / fs::u8path(program)).u8string(), "");
                return where.allowed && !where.system;
            }
        }
        return false;
    }

    // A project venv's python: a link in <venv>/bin beside pyvenv.cfg, inside the folders,
    // to an interpreter outside them. It is run like a program on PATH, never opened.
    bool venvPython(const std::string& program, const State& st) const {
        if (!ws_ || st.folderUnknown) return false;
        const fs::path name = fs::u8path(program).filename();
        if (name.u8string().rfind("python", 0) != 0) return false;
        const bool absolute = isAbsolute(program);
        const std::vector<std::string> bases = absolute ? std::vector<std::string>{""} : st.folders;
        if (bases.empty()) return false;
        std::error_code ec;
        for (const auto& base : bases) {
            const fs::path link = absolute ? fs::u8path(program) : fs::u8path(base) / fs::u8path(program);
            if (!fs::is_symlink(link, ec)) return false;
            const Workspace::Place bin = ws_->locate(link.parent_path().u8string(), "");
            if (!bin.allowed || bin.system || bin.real.empty()) return false;
            const fs::path binReal = fs::u8path(bin.real);
            if (binReal.filename() != "bin" || !fs::is_regular_file(binReal.parent_path() / "pyvenv.cfg", ec)) return false;
            const fs::file_status target = fs::status(link, ec);
            if (ec || !fs::is_regular_file(target) || (target.permissions() & fs::perms::owner_exec) == fs::perms::none) {
                return false;
            }
        }
        return true;
    }

    void place(const std::string& path, const std::string& base, const std::string& word, bool onePlace, bool write) {
        const Workspace::Place where = ws_->locate(path, base);
        if (!where.allowed) {
            outside(word, onePlace ? where.real : "");
            return;
        }
        // A file put where programs are looked up becomes the program a later, checked command runs.
        if ((write || changing_) && !where.system) {
            const fs::path real = fs::u8path(where.real);
            if (onSearchPath(real.u8string(), base) || onSearchPath(real.parent_path().u8string(), base)) {
                opaque("it changes a folder that programs are run from");
            }
        }
        if (!write || where.system || !out_.outsideWord.empty()) return;
        const std::string shared = ws_->sharedFileRefusal(where.real);
        if (shared.empty()) return;
        outside(word, "");
        out_.refusal = ws_->sharedFileRefusal(word);
    }

    // A word a program treats as a file or folder.
    void path(const Field& field, const State& st, bool write = false) {
        if (write && !field.unknown && !field.wildcard && out_.written.size() < 64) {
            for (const auto& base : st.folders) out_.written.push_back(joined(base, field.text()));
        }
        if (!ws_) return;
        if (field.unknown) {
            opaque("a file name in it is worked out while the command runs");
            return;
        }
        const std::string text = field.text();
        if (text.empty() || text == "-" || text.find("://") != std::string::npos) return;
        if (text[0] == '~' && field.pattern[0] != '\\') {
            outside(text, "", "It names another user's home folder.");
            return;
        }
        const bool absolute = isAbsolute(text);
        if (!absolute && st.folderUnknown) {
            opaque("it changes folder to one worked out while the command runs");
            return;
        }
        const std::vector<std::string> bases = absolute ? std::vector<std::string>{""} : st.folders;
        for (const auto& base : bases) {
            if (!field.wildcard) {
                place(text, base, text, true, write);
                continue;
            }
            // The folder the wildcard is read from, then everything it matches now.
            std::string prefix = field.pattern.substr(0, field.pattern.find_first_of("*?["));
            const size_t slash = prefix.rfind('/');
            prefix = slash == std::string::npos ? std::string(".") : unescaped(prefix.substr(0, slash + 1));
            place(prefix, base, text, false, false);
            std::vector<std::string> found;
            if (!glob(base, field.pattern, found)) {
                opaque("a wildcard in it matches too many files to check");
                return;
            }
            for (const auto& match : found) place(match, base, text, false, write);
        }
    }

    // True when a walk that follows links from `field` (a folder) stays inside.
    void followedTree(const Field& field, const State& st) {
        if (!ws_ || field.unknown) return;
        for (const auto& base : st.folders) {
            const Workspace::Place where = ws_->locate(field.text(), base);
            if (where.allowed && !where.system && !ws_->linksStayInside(where.real)) {
                outside(field.text(), "", "It holds a link that leads outside those folders, and this command follows links.");
            }
        }
    }

    // --- cd ---------------------------------------------------------------

    void changeFolder(const std::vector<Field>& fields, State& st) {
        std::vector<Field> operands;
        for (size_t i = 1; i < fields.size(); ++i) {
            const std::string t = fields[i].text();
            if (!fields[i].unknown && t.size() > 1 && t[0] == '-' && operands.empty()) continue;  // -L, -P
            operands.push_back(fields[i]);
        }
        if (operands.empty()) {
            const Value home = lookup("HOME", st);
            if (home.unknown || home.values.empty()) {
                operands.push_back({"", true, false});
            } else {
                operands.push_back({escaped(home.values[0]), false, false});
            }
        }
        const std::string target = operands[0].text();
        // Unset is the usual case, and then `cd name` means the folder here.
        const bool cdPathSet = st.vars.count("CDPATH") || env_.count("CDPATH") || std::getenv("CDPATH") != nullptr;
        const Value cdPath = cdPathSet ? lookup("CDPATH", st) : Value{{""}, false};
        const bool searched = cdPath.unknown || (!cdPath.values.empty() && !cdPath.values[0].empty());
        if (operands.size() > 1 || operands[0].unknown || target == "-" ||
            (searched && !isAbsolute(target) && target.rfind("./", 0) != 0 && target.rfind("../", 0) != 0)) {
            opaque("it changes folder to one worked out while the command runs");
            st.folderUnknown = true;
            return;
        }
        if (!ws_) return;
        // The folder an attached file sits in: the shell may stand there, and every
        // word is still judged as a file of its own, so nothing else there is opened.
        if (!operands[0].wildcard) {
            const std::vector<std::string> from = isAbsolute(target) ? std::vector<std::string>{""} : st.folders;
            std::vector<std::string> beside;
            for (const auto& base : from) {
                const Workspace::Place where = ws_->locate(target, base);
                const std::string folder = where.allowed ? "" : ws_->attachedFileFolder(where.real);
                if (folder.empty()) {
                    beside.clear();
                    break;
                }
                beside = merged(std::move(beside), {folder});
            }
            if (!beside.empty()) {
                st.folders = std::move(beside);
                st.movedSurely = true;
                return;
            }
        }
        const bool wasOutside = !out_.outsideWord.empty();
        path(operands[0], st);
        if (!wasOutside && !out_.outsideWord.empty() && out_.outsideWhy.empty()) {
            out_.outsideWhy = "Changing folder there would leave them, and every later command would run outside.";
        }
        // Where the shell stands if the cd works; list() adds back where it stands if it doesn't.
        std::vector<std::string> next;
        bool allThere = !operands[0].wildcard;
        const std::vector<std::string> bases = isAbsolute(target) ? std::vector<std::string>{""} : st.folders;
        for (const auto& base : bases) {
            std::vector<std::string> found;
            if (operands[0].wildcard) {
                if (!glob(base, operands[0].pattern, found)) st.folderUnknown = true;
            } else {
                found.push_back(target);
            }
            for (const auto& f : found) {
                const Workspace::Place where = ws_->locate(f, base);
                std::error_code ec;
                if (!where.allowed) continue;
                if (!fs::is_directory(fs::u8path(where.real), ec)) allThere = false;
                next = merged(std::move(next), {where.real});
            }
        }
        if (next.size() > kMaxFolders) st.folderUnknown = true;
        if (next.empty()) return;
        st.folders = std::move(next);
        st.movedSurely = allThere;
    }

    // --- programs ---------------------------------------------------------

    struct Call {
        std::set<std::string> prefixEnv;  // NAME=value words before the program
        bool pipedIn = false;
        bool stdinFromFile = false;
        // What the command reads on stdin, when that can be told: a heredoc, a here-string or
        // `echo text |` as text, `< file` or `cat file |` as a file.
        const std::string* stdinText = nullptr;
        const Field* stdinFile = nullptr;
    };

    static bool isOption(const Field& f) { return !f.unknown && f.pattern.size() > 1 && f.pattern[0] == '-'; }

    // Paths in the words of a program GAIA has a rule for: operands, and values attached to options.
    void operands(const std::vector<Field>& fields, size_t from, const State& st, bool write, bool skipFirst = false) {
        bool onlyOperands = false;
        for (size_t i = from; i < fields.size(); ++i) {
            const Field& f = fields[i];
            if (!onlyOperands && isOption(f)) {
                const std::string t = f.text();
                if (t == "--") {
                    onlyOperands = true;
                    continue;
                }
                const size_t eq = t.find('=');
                if (t[1] == '-') {
                    if (eq != std::string::npos && eq + 1 < t.size()) path({escaped(t.substr(eq + 1)), false, false}, st, write);
                    continue;
                }
                // A value written against its flag (-t../out, -Clink): every tail that looks like a path
                // or names something that exists is checked.
                for (size_t cut = 2; cut < t.size() && cut < 64; ++cut) {
                    const std::string value = t.substr(cut);
                    bool pathLike = value.size() > 1 && (value[0] == '/' || value.rfind("~/", 0) == 0 ||
                                                         value.rfind("../", 0) == 0 || value == ".." ||
                                                         value.find("/../") != std::string::npos);
                    std::error_code ec;
                    for (const auto& base : st.folders) {
                        pathLike = pathLike || fs::symlink_status(fs::u8path(base) / fs::u8path(value), ec).type() != fs::file_type::not_found;
                    }
                    if (pathLike) path({escaped(value), false, false}, st, write);
                }
                continue;
            }
            if (skipFirst) {
                skipFirst = false;
                continue;
            }
            path(f, st, write);
        }
    }

    // Words of a program GAIA has no rule for: checked where they look like paths, and code is searched for them.
    void looseOperands(const std::vector<Field>& fields, size_t from, const State& st) {
        for (size_t i = from; i < fields.size(); ++i) {
            if (fields[i].unknown) continue;
            const std::string t = fields[i].text();
            if (isOption(fields[i])) {
                const size_t eq = t.find('=');
                if (eq != std::string::npos && eq + 1 < t.size()) pathIfLikely(t.substr(eq + 1), st);
                continue;
            }
            if (t.find_first_of(" \t\n;()'\"") != std::string::npos) {
                pathsInCode(t, st);
            } else if (fields[i].wildcard) {
                path(fields[i], st);
            } else {
                pathIfLikely(t, st);
            }
        }
    }

    void pathIfLikely(const std::string& t, const State& st) {
        if (t.size() < 2 || t.find("://") != std::string::npos) return;
        const bool climbs = t == ".." || t.rfind("../", 0) == 0 || t.find("/../") != std::string::npos ||
                            (t.size() > 3 && t.compare(t.size() - 3, 3, "/..") == 0);
        if (isAbsolute(t) || t.rfind("~/", 0) == 0 || climbs || t.find('/') != std::string::npos) {
            Field f{escaped(t), false, false};
            if (t.rfind("~/", 0) == 0) {
                const Value home = lookup("HOME", st);
                if (home.unknown || home.values.empty()) return;
                f.pattern = escaped(home.values[0] + t.substr(1));
            }
            path(f, st);
        }
    }

    // Path-shaped text inside a script given on the command line.
    void pathsInCode(const std::string& code, const State& st) {
        static const std::string kBreaks = " \t\r\n'\"`;|&()<>=,[]{}+:";
        size_t checked = 0;
        for (size_t start = code.find_first_not_of(kBreaks); start != std::string::npos && checked < 256;) {
            const size_t end = code.find_first_of(kBreaks, start);
            const std::string token = code.substr(start, end == std::string::npos ? end : end - start);
            start = end == std::string::npos ? end : code.find_first_not_of(kBreaks, end);
            // `..` alone is a range in several languages, so only a path that climbs counts.
            const bool climbs = token.rfind("../", 0) == 0 || token.find("/../") != std::string::npos ||
                                (token.size() > 3 && token.compare(token.size() - 3, 3, "/..") == 0);
            if (token.size() < 2 || token.size() > 1024) continue;
            if (!(token[0] == '/' || token.rfind("~/", 0) == 0 || climbs)) continue;
            ++checked;
            pathIfLikely(token, st);
        }
    }

    // A command as the shell will see its words, for the caller's checks of one command line.
    void resolved(const std::vector<Field>& fields, size_t from) {
        if (out_.resolvedLines.size() >= 64) return;
        std::string line;
        for (size_t i = from; i < fields.size(); ++i) {
            if (fields[i].unknown) return;
            line += (line.empty() ? "" : " ") + quotedForShell(fields[i].text());
        }
        out_.resolvedLines.push_back(line);
    }

    void run(std::vector<Field> fields, State& st, const Call& call, size_t from = 0) {
        if (fields.size() <= from) return;
        if (++words_ > 4000) {
            opaque("it is too long to check");
            return;
        }
        const Field& first = fields[from];
        if (first.unknown || (first.wildcard && first.pattern != "[" && first.pattern != "[[")) {
            hide("the program it runs is worked out while the command runs");
            looseOperands(fields, from + 1, st);
            return;
        }
        std::string name = first.text();
        if (name.find('/') != std::string::npos) {
            static const std::vector<std::string> trusted = {"/bin/", "/sbin/", "/usr/bin/", "/usr/sbin/"};
            const bool system = std::any_of(trusted.begin(), trusted.end(),
                                            [&](const std::string& d) { return name.rfind(d, 0) == 0; }) &&
                                name.find("..") == std::string::npos;
            if (!system) {
                if (!venvPython(name, st)) pathIfLikely(name, st);
                opaque("it runs the program file " + named(name) + ", which GAIA can't inspect");
                noteScript("file", first, st);
                looseOperands(fields, from + 1, st);
                return;
            }
        }
        name = lowered(name.substr(name.find_last_of('/') == std::string::npos ? 0 : name.find_last_of('/') + 1));
        if (name.size() > 4 && name.compare(name.size() - 4, 4, ".exe") == 0) name.resize(name.size() - 4);

        for (const auto& variable : call.prefixEnv) {
            // Set for one command only, cd reads these too, and their values are not followed here.
            static const Names folderVariables = {"HOME", "CDPATH", "PWD", "OLDPWD"};
            if (steers(variable) || folderVariables.count(variable)) {
                opaque("it sets " + variable + ", which changes what its programs do");
            }
        }
        if (ws_ && first.pattern.find('/') == std::string::npos && !shellBuiltins().count(name) && runsFromWorkspace(name, st)) {
            opaque("the " + named(name) + " it runs is a file in a folder this agent may write to, not the system's program");
        }

        const auto text = [&](size_t i) { return fields[i].unknown ? std::string() : fields[i].text(); };

        // --- shell builtins that carry state --------------------------------
        if (name == "cd" || name == "pushd" || name == "chdir") {
            changeFolder(std::vector<Field>(fields.begin() + static_cast<long>(from), fields.end()), st);
            return;
        }
        if (name == "popd") {
            opaque("it changes folder to one worked out while the command runs");
            st.folderUnknown = true;
            return;
        }
        if (name == "export" || name == "declare" || name == "typeset" || name == "local" || name == "readonly") {
            for (size_t i = from + 1; i < fields.size(); ++i) {
                // -n makes a name stand for another variable, -i evaluates the value: neither is followed here.
                if (isOption(fields[i]) || (!fields[i].unknown && fields[i].pattern[0] == '+')) {
                    if (text(i).find_first_not_of("-xrgp") != std::string::npos) hide(name + " " + named(text(i)) + " changes how a variable is read");
                    continue;
                }
                const std::string t = fields[i].pattern;
                const size_t eq = t.find('=');
                const std::string var = t.substr(0, eq);
                if (!isName(var)) opaque("it sets a variable whose name is worked out while the command runs");
                else if (eq != std::string::npos) assign(st, var, fields[i].unknown ? Value{{}, true} : Value{{unescaped(t.substr(eq + 1))}, false});
            }
            return;
        }
        // A name such as a[$(cmd)] makes the shell run cmd to find the element.
        const auto subscripted = [&](size_t i) {
            return fields[i].unknown || text(i).find_first_of("$`[") != std::string::npos;
        };
        if (name == "unset") {
            for (size_t i = from + 1; i < fields.size(); ++i) {
                if (subscripted(i)) hide("unset is given a name that can run a command");
                else if (isName(text(i))) st.vars[text(i)] = {{}, true};
            }
            return;
        }
        if (name == "read" || name == "mapfile" || name == "readarray" || name == "getopts") {
            for (size_t i = from + 1; i < fields.size(); ++i) {
                if (isOption(fields[i])) continue;
                if (subscripted(i)) hide(name + " is given a name that can run a command");
                else if (isName(text(i))) st.vars[text(i)] = {{}, true};
            }
            st.vars["REPLY"] = {{}, true};
            return;
        }
        if (name == "printf" && fields.size() > from + 2 && text(from + 1) == "-v") {
            if (subscripted(from + 2)) hide("printf -v is given a name that can run a command");
            else st.vars[text(from + 2)] = {{}, true};
            return;
        }
        if (name == "let" || name == "hash") {
            hide(name == "let" ? "let evaluates text that can run a command" : "hash changes which program a name runs");
            return;
        }
        if (name == "test" || name == "[" || name == "[[") {
            static const Names unary = {"-e", "-f", "-d", "-r", "-w", "-x", "-s", "-L", "-h", "-O", "-G", "-N", "-b",
                                        "-c", "-p", "-S", "-u", "-g", "-k"};
            static const Names binary = {"-nt", "-ot", "-ef"};
            for (size_t i = from + 1; i < fields.size(); ++i) {
                // [[ x -eq y ]] and -v evaluate their words, so a[$(cmd)] in one runs cmd.
                if (name == "[[" && subscripted(i) && text(i) != "[[" && text(i) != "]]") {
                    hide("[[ ]] is given a word that can run a command");
                }
                if (fields[i].unknown) continue;
                if (unary.count(text(i)) && i + 1 < fields.size()) path(fields[i + 1], st);
                if (binary.count(text(i)) && i + 1 < fields.size()) {
                    path(fields[i - 1], st);
                    path(fields[i + 1], st);
                }
            }
            return;
        }
        if (name == "eval") {
            std::string joined;
            for (size_t i = from + 1; i < fields.size(); ++i) {
                if (fields[i].unknown) {
                    hide("eval runs text put together while the command runs");
                    return;
                }
                joined += (joined.empty() ? "" : " ") + text(i);
            }
            nested(joined, st, true);
            return;
        }
        if (name == "trap") {
            if (fields.size() > from + 1 && !isOption(fields[from + 1])) {
                if (fields[from + 1].unknown) hide("trap runs text put together while the command runs");
                else nested(text(from + 1), st, false);
            }
            return;
        }
        if (name == "source" || name == ".") {
            opaque("it runs the commands in a file, which GAIA does not read");
            if (fields.size() > from + 1) {
                path(fields[from + 1], st);
                noteScript("sh", fields[from + 1], st);
            }
            return;
        }
        if (name == "alias" || name == "enable" || name == "bind" || name == "complete" || name == "coproc") {
            opaque("it changes what later words in the shell mean");
            return;
        }
        if (name == "env") {
            size_t i = from + 1;
            Call inner = call;
            for (; i < fields.size(); ++i) {
                const std::string t = text(i);
                if (fields[i].unknown) break;
                if (t == "-u" || t == "--unset") {
                    ++i;
                } else if (t == "-S" || t.rfind("--split-string", 0) == 0 || t == "-C" || t.rfind("--chdir", 0) == 0) {
                    hide("env is told to split a string into a command, or to change folder");
                    return;
                } else if (t == "-" || t == "-i" || t == "--" || t.rfind("--ignore-environment", 0) == 0 ||
                           (t.size() > 1 && t[0] == '-')) {
                    continue;
                } else if (t.find('=') != std::string::npos && isName(t.substr(0, t.find('=')))) {
                    inner.prefixEnv.insert(t.substr(0, t.find('=')));
                } else {
                    break;
                }
            }
            run(fields, st, inner, i);
            return;
        }
        if (name == "command" || name == "builtin") {
            if (fields.size() > from + 1 && (text(from + 1) == "-v" || text(from + 1) == "-V")) return;
        }
        if (wrapperPrograms().count(name)) {
            static const std::map<std::string, Names> valued = {
                {"nice", {"-n", "--adjustment"}}, {"ionice", {"-c", "-n", "-p", "--class", "--classdata"}},
                {"timeout", {"-s", "--signal", "-k", "--kill-after"}}, {"gtimeout", {"-s", "--signal", "-k", "--kill-after"}},
                {"sudo", {"-u", "-g", "-h", "-p", "-C", "-T", "-r", "-t", "-U", "--user", "--group"}},
                {"doas", {"-u", "-C"}}, {"caffeinate", {"-t", "-w"}}, {"exec", {"-a"}}, {"stdbuf", {"-i", "-o", "-e"}},
                {"taskset", {"-p"}}, {"chrt", {"-p"}}};
            size_t i = from + 1;
            const auto found = valued.find(name);
            for (; i < fields.size(); ++i) {
                if (fields[i].unknown || !isOption(fields[i])) break;
                const std::string t = text(i);
                if (t == "--") {
                    ++i;
                    break;
                }
                if (name == "sudo" && (t == "-D" || t == "-R" || t == "--chdir" || t == "--chroot" || t == "-E" ||
                                       t == "-i" || t == "-s" || t == "-e")) {
                    opaque("sudo is told to change folder, shell or environment");
                }
                if (found != valued.end() && found->second.count(t)) ++i;
            }
            // timeout 5, taskset 0x3, chrt 10: the wrapper's own first word.
            if ((name == "timeout" || name == "gtimeout" || name == "taskset" || name == "chrt") && i < fields.size()) ++i;
            run(fields, st, call, i);
            return;
        }
        if (inertPrograms().count(name)) {
            // The shell fills a wildcard in before the program runs, which reads the folder it names.
            for (size_t i = from + 1; i < fields.size(); ++i) {
                if (fields[i].wildcard && !fields[i].unknown) path(fields[i], st);
            }
            return;
        }

        // --- programs that run other commands -------------------------------
        if (shellPrograms().count(name)) {
            shell(fields, from, st, call, name);
            return;
        }
        if (name == "xargs" || name == "parallel") {
            if (name == "parallel") {
                opaque("parallel builds commands from its input");
                return;
            }
            static const Names valued = {"-n", "-I", "-L", "-P", "-d", "-E", "-s", "-a", "-J", "-R", "-S",
                                         "--max-args", "--replace", "--max-lines", "--max-procs", "--delimiter",
                                         "--arg-file", "--max-chars", "--eof"};
            size_t i = from + 1;
            for (; i < fields.size() && isOption(fields[i]); ++i) {
                const std::string t = text(i);
                if (t == "-a" || t == "--arg-file") path(fields[i + 1 < fields.size() ? i + 1 : i], st);
                if (valued.count(t)) ++i;
            }
            // What xargs reads becomes words nobody can see here.
            fields.push_back({"", true, false});
            if (i + 1 >= fields.size()) return;  // xargs alone echoes
            run(fields, st, call, i);
            return;
        }
        if (name == "find") {
            find(fields, from, st, call);
            return;
        }
        if (name == "flock" || name == "watch" || name == "script" || name == "expect" || name == "at" ||
            name == "batch" || name == "crontab" || name == "launchctl" || name == "systemctl" || name == "screen" ||
            name == "tmux" || name == "open" || name == "xdg-open" || name == "make" || name == "gmake") {
            if (name == "open" || name == "xdg-open") {
                for (size_t i = from + 1; i < fields.size(); ++i) {
                    if (text(i).find("://") != std::string::npos && !isLocalHost(text(i)) && text(i).rfind("file:", 0) != 0) {
                        network(name);
                    }
                }
            }
            opaque(name + " starts other programs, which GAIA can't follow");
            looseOperands(fields, from + 1, st);
            return;
        }

        // --- interpreters ---------------------------------------------------
        const std::string language = interpreterLanguage(name);
        if (!language.empty()) {
            resolved(fields, from);
            interpreter(fields, from, st, call, name, language);
            return;
        }
        if (name == "jq") {
            operands(fields, from + 1, st, false, true);
            return;
        }

        // --- programs that reach another computer -----------------------------
        if (name == "git") {
            git(fields, from, st);
            return;
        }
        if (name == "openssl") {
            for (size_t i = from + 1; i < fields.size(); ++i) {
                if (text(i) == "s_client" || text(i) == "s_time" || text(i) == "ocsp") network("openssl " + text(i));
            }
        }
        if (networkPrograms().count(name)) {
            bool info = fields.size() == from + 2 &&
                        (text(from + 1) == "--version" || text(from + 1) == "--help" || text(from + 1) == "-V" ||
                         text(from + 1) == "-h");
            if (name == "rsync") {
                // Local unless a word names a host (host:path, rsync://) or a remote shell.
                info = true;
                for (size_t i = from + 1; i < fields.size(); ++i) {
                    const std::string t = text(i);
                    const size_t colon = t.find(':');
                    if (fields[i].unknown || t == "-e" || t.rfind("--rsh", 0) == 0 || t.find("://") != std::string::npos ||
                        (colon != std::string::npos && colon > 0 && t.find('/') > colon && !isOption(fields[i]))) {
                        info = false;
                    }
                }
            }
            // Only this PC named: nc -z localhost 8080.
            size_t local = 0, remote = 0;
            for (size_t i = from + 1; i < fields.size(); ++i) {
                const std::string t = text(i);
                if (fields[i].unknown) ++remote;
                else if (isOption(fields[i]) || t.find_first_not_of("0123456789") == std::string::npos) continue;
                else if (isLocalHost(t)) ++local;
                else if (!fetchPrograms().count(name) || t.find("://") != std::string::npos) ++remote;
            }
            if (fetchPrograms().count(name)) {
                // The caller's fetch check reads the flags, so it is handed the line.
                resolved(fields, from);
            } else if (!info && !(local > 0 && remote == 0)) {
                network(name);
            }
            if (info && name != "rsync") return;  // curl --version prints and exits
            opaque(name + " can copy files to and from another computer");
            looseOperands(fields, from + 1, st);
            return;
        }

        // --- programs with a rule ---------------------------------------------
        if (pathPrograms().count(name)) {
            bounded(fields, from, st, name);
            return;
        }
        opaque("GAIA has no rule for what " + named(name) + " reads or writes");
        looseOperands(fields, from + 1, st);
    }

    void noteScript(const std::string& language, const Field& file, const State& st) {
        if (file.unknown || file.wildcard) return;
        for (const auto& base : st.folders) out_.scripts.emplace_back(language, joined(base, file.text()));
    }

    void nested(const std::string& text, State& st, bool sameShell) {
        const int depth = depth_;
        if (sameShell) {
            script(text, st, depth + 1);
        } else {
            State copy = st;
            script(text, copy, depth + 1);
        }
        depth_ = depth;
    }

    void shell(const std::vector<Field>& fields, size_t from, State& st, const Call& call, const std::string& name) {
        size_t i = from + 1;
        bool command = false, fromStdin = false;
        for (; i < fields.size(); ++i) {
            if (fields[i].unknown || !isOption(fields[i])) break;
            const std::string t = fields[i].text();
            if (t == "--") {
                ++i;
                break;
            }
            if (t == "-o" || t == "-O" || t == "+o" || t == "+O") {
                ++i;
            } else if (t.rfind("--", 0) == 0) {
                if (t == "--rcfile" || t == "--init-file") opaque(name + " is told to run a start-up file");
            } else {
                if (t.find('c') != std::string::npos) command = true;
                if (t.find('s') != std::string::npos) fromStdin = true;
                if (t.find('i') != std::string::npos || t.find('l') != std::string::npos) {
                    opaque(name + " is started as a login or interactive shell, which runs the user's start-up files");
                }
            }
        }
        if (command) {
            if (i >= fields.size() || fields[i].unknown) {
                hide(name + " -c runs text put together while the command runs");
                return;
            }
            nested(fields[i].text(), st, false);
            return;
        }
        if (i < fields.size() && !fromStdin) {
            path(fields[i], st);
            noteScript("sh", fields[i], st);
            opaque(name + " runs a script file, which GAIA does not read");
            return;
        }
        if (call.stdinText) {
            nested(*call.stdinText, st, false);
        } else if (call.stdinFile) {
            path(*call.stdinFile, st);
            noteScript("sh", *call.stdinFile, st);
            opaque(name + " runs a script file, which GAIA does not read");
        } else if (call.pipedIn || call.stdinFromFile) {
            hide(name + " reads its commands from input GAIA can't see");
        }
    }

    void interpreter(const std::vector<Field>& fields, size_t from, const State& st, const Call& call,
                     const std::string& name, const std::string& language) {
        opaque(name + " runs code, which can read and write anywhere you can");
        bool codeGiven = false, fileGiven = false;
        for (size_t i = from + 1; i < fields.size(); ++i) {
            if (fields[i].unknown) continue;
            const std::string t = fields[i].text();
            if (!fileGiven && !codeGiven && isOption(fields[i])) {
                const bool cluster = t[1] != '-';
                bool takesCode = false;
                if (language == "python") takesCode = cluster && t.find('c') != std::string::npos;
                else if (language == "node") takesCode = t == "-e" || t == "--eval" || t == "-p" || t == "--print" || t == "-pe";
                else if (language == "perl" || language == "ruby") takesCode = cluster && t.find_first_of("eE") != std::string::npos;
                else if (language == "php") takesCode = t == "-r";
                else if (language == "powershell") takesCode = false;  // its switches are read by the web check
                else takesCode = t == "-e" || t == "-c" || t == "-E" || t == "--eval";
                if (takesCode && i + 1 < fields.size()) {
                    codeGiven = true;
                    const Field& code = fields[++i];
                    if (code.unknown) {
                        hide(name + " runs code put together while the command runs");
                    } else {
                        pathsInCode(code.text(), st);
                        if (language == "applescript") shellInAppleScript(code.text(), st);
                    }
                } else if (t == "-m" && language == "python" && i + 1 < fields.size()) {
                    // Standard modules that are a network client when run as a program.
                    static const Names clients = {"webbrowser", "urllib.request", "http.client", "ftplib", "telnetlib",
                                                  "smtplib", "poplib", "imaplib", "nntplib", "xmlrpc.client"};
                    if (!fields[i + 1].unknown && clients.count(fields[i + 1].text())) network("python -m " + fields[i + 1].text());
                    ++i;
                } else if (t == "-m" || t == "-W" || t == "-X" || t == "-I" || t == "-l" || t == "-s") {
                    ++i;
                } else {
                    const size_t eq = t.find('=');
                    if (eq != std::string::npos) pathIfLikely(t.substr(eq + 1), st);
                }
                continue;
            }
            if (!codeGiven && !fileGiven && t != "-" && language != "powershell") {
                fileGiven = true;
                path(fields[i], st);
                noteScript(language, fields[i], st);
                continue;
            }
            if (t.find_first_of(" \t\n;()'\"") != std::string::npos) pathsInCode(t, st);
            else pathIfLikely(t, st);
        }
        if (codeGiven || fileGiven || language == "powershell") return;
        if (call.stdinText) {
            pathsInCode(*call.stdinText, st);
            if (language == "applescript") shellInAppleScript(*call.stdinText, st);
        } else if (call.stdinFile) {
            path(*call.stdinFile, st);
            noteScript(language, *call.stdinFile, st);
        } else if (call.pipedIn || call.stdinFromFile) {
            hide(name + " reads its code from input GAIA can't see");
        }
    }

    // do shell script "..." hands a line to /bin/sh, and a web address is opened by whatever handles it.
    void shellInAppleScript(const std::string& code, const State& st) {
        static const std::string marker = "do shell script";
        const std::string lower = lowered(code);
        for (size_t at = code.find("://"); at != std::string::npos; at = code.find("://", at + 1)) {
            size_t begin = at, end = at;
            while (begin > 0 && isNameChar(code[begin - 1])) --begin;
            while (end < code.size() && std::string(" \t\n\"'<>").find(code[end]) == std::string::npos) ++end;
            const std::string url = code.substr(begin, end - begin);
            if (url.rfind("file:", 0) != 0 && !isLocalHost(url)) network("osascript");
        }
        for (size_t at = lower.find(marker); at != std::string::npos; at = lower.find(marker, at + 1)) {
            size_t i = code.find_first_not_of(" \t", at + marker.size());
            if (i == std::string::npos || code[i] != '"') {
                hide("osascript runs a shell command put together while the script runs");
                continue;
            }
            std::string line;
            for (++i; i < code.size() && code[i] != '"'; ++i) {
                if (code[i] == '\\' && i + 1 < code.size()) ++i;
                line += code[i];
            }
            // More text joined on (&) means the line is not all here.
            const size_t after = code.find_first_not_of(" \t", i + 1);
            if (after != std::string::npos && code[after] == '&') {
                hide("osascript runs a shell command put together while the script runs");
            }
            State copy = st;
            nested(line, copy, false);
        }
    }

    void find(std::vector<Field> fields, size_t from, State& st, const Call& call) {
        size_t i = from + 1;
        bool follows = false;
        for (; i < fields.size() && isOption(fields[i]); ++i) {
            const std::string t = fields[i].text();
            if (t == "-L" || t == "-H") follows = true;
            else if (t == "-f") ++i;
            else if (t != "-P" && t != "-E" && t != "-X" && t != "-d" && t != "-s" && t != "-x" && t != "-O") break;
        }
        std::vector<Field> roots;
        for (; i < fields.size(); ++i) {
            const std::string t = fields[i].unknown ? "" : fields[i].text();
            if (!fields[i].unknown && (t[0] == '-' || t == "(" || t == ")" || t == "!" || t == ",")) break;
            roots.push_back(fields[i]);
            path(fields[i], st);
        }
        if (roots.empty()) roots.push_back({".", false, false});
        for (; i < fields.size(); ++i) {
            const std::string t = fields[i].unknown ? "" : fields[i].text();
            if (t == "-follow") follows = true;
            if ((t == "-newer" || t == "-anewer" || t == "-cnewer" || t == "-samefile" || t == "-fprint" ||
                 t == "-fprint0" || t == "-fls" || t == "-fprintf" || t == "-newermt" || t == "-files0-from") &&
                i + 1 < fields.size()) {
                if (t != "-newermt") path(fields[i + 1], st, t[1] == 'f' && t != "-files0-from");
                ++i;
                continue;
            }
            if (t != "-exec" && t != "-execdir" && t != "-ok" && t != "-okdir") continue;
            // The command runs once per match, with {} standing for a path under the roots.
            std::vector<Field> inner;
            for (++i; i < fields.size(); ++i) {
                const std::string w = fields[i].unknown ? "" : fields[i].text();
                if (!fields[i].unknown && (w == ";" || w == "+")) break;
                if (w == "{}") {
                    for (const auto& r : roots) inner.push_back(r);
                } else if (w.find("{}") != std::string::npos) {
                    inner.push_back({"", true, false});
                } else {
                    inner.push_back(fields[i]);
                }
            }
            State copy = st;
            run(inner, copy, call, 0);
        }
        if (follows) {
            for (const auto& r : roots) followedTree(r, st);
        }
    }

    void git(const std::vector<Field>& fields, size_t from, State& st) {
        size_t i = from + 1;
        for (; i < fields.size() && isOption(fields[i]); ++i) {
            const std::string t = fields[i].text();
            if (t == "-C" || t == "--git-dir" || t == "--work-tree") {
                if (i + 1 < fields.size()) path(fields[++i], st);
                opaque("git is pointed at another folder");
            } else if (t == "-c" || t == "--config-env" || t.rfind("--exec-path", 0) == 0) {
                opaque("git is given a setting that can name a program to run");
                if (t == "-c" || t == "--config-env") ++i;
            } else if (t.rfind("--git-dir=", 0) == 0 || t.rfind("--work-tree=", 0) == 0) {
                path({escaped(t.substr(t.find('=') + 1)), false, false}, st);
                opaque("git is pointed at another folder");
            }
        }
        if (i >= fields.size() || fields[i].unknown) {
            if (i < fields.size()) hide("the git command is worked out while the command runs");
            return;
        }
        const std::string sub = fields[i].text();
        const std::string second = i + 1 < fields.size() && !fields[i + 1].unknown ? fields[i + 1].text() : "";
        static const Names remote = {"clone", "fetch", "pull", "push", "ls-remote", "send-email", "request-pull", "svn",
                                     "archimport", "cvsimport", "p4", "daemon", "http-fetch", "http-push", "fetch-pack",
                                     "send-pack", "upload-pack", "receive-pack", "imap-send"};
        static const Names remoteSecond = {"update", "show", "prune", "set-head"};
        bool online = remote.count(sub) > 0 || (sub == "remote" && remoteSecond.count(second)) ||
                      (sub == "submodule" && (second == "update" || second == "add" || second == "sync" || second == "init")) ||
                      (sub == "lfs" && (second == "fetch" || second == "pull" || second == "push" || second == "clone")) ||
                      (sub == "bundle" && second == "fetch");
        for (size_t j = i + 1; j < fields.size(); ++j) {
            const std::string t = fields[j].unknown ? "" : fields[j].text();
            if (sub == "archive" && t.rfind("--remote", 0) == 0) online = true;
            // A repository in a folder on this PC is not another computer: a path, not a host or a saved name.
            const bool written = t.rfind("/", 0) == 0 || t.rfind("./", 0) == 0 || t.rfind("../", 0) == 0 ||
                                 t.rfind("~/", 0) == 0 || t == "." || t == "..";
            if (online && (sub == "clone" || sub == "fetch" || sub == "pull" || sub == "ls-remote") &&
                !isOption(fields[j]) && written && t.find("://") == std::string::npos) {
                online = false;
            }
            if (t.rfind("file://", 0) == 0) online = false;
        }
        if (online) network("git " + sub + (sub == "remote" || sub == "submodule" || sub == "lfs" ? " " + named(second) : ""));
        // Only plumbing that runs nothing the repository's own settings name; the rest asks first.
        static const Names reads = {"branch", "rev-parse", "rev-list", "ls-tree", "merge-base", "name-rev", "remote",
                                    "version", "--version"};
        bool plain = reads.count(sub) > 0 && !online;
        for (size_t j = i + 1; j < fields.size(); ++j) {
            const std::string t = fields[j].unknown ? "" : fields[j].text();
            if (!isOption(fields[j])) continue;
            // A patch or a pickaxe search converts file contents with the repository's programs.
            static const Names showsContent = {"-p", "-u", "--patch", "-c", "--cc", "-m", "--raw", "--stat",
                                               "--numstat", "--shortstat", "--dirstat", "--summary", "--check"};
            if (showsContent.count(t) || t.rfind("-S", 0) == 0 || t.rfind("-G", 0) == 0 || t.rfind("-L", 0) == 0 ||
                t.rfind("--ext-diff", 0) == 0 || t.rfind("--textconv", 0) == 0 || t.rfind("--exec", 0) == 0 ||
                t.rfind("--upload-pack", 0) == 0 || t.rfind("--open-files-in-pager", 0) == 0 || t == "-O" ||
                t.rfind("--output", 0) == 0 || t == "-F" || t == "--file" || t == "-e" || t == "--edit" ||
                t == "--edit-description" || t.rfind("--show-signature", 0) == 0 || t.rfind("--verify", 0) == 0 ||
                t.find("%G") != std::string::npos) {
                plain = false;
            }
        }
        if (!plain) opaque("git " + named(sub) + " can run programs the repository's own settings name");
        operands(fields, i + 1, st, false);
    }

    void bounded(const std::vector<Field>& fields, size_t from, const State& st, const std::string& name) {
        const auto text = [&](size_t i) { return fields[i].unknown ? std::string() : fields[i].text(); };
        changing_ = changingPrograms().count(name) > 0;
        std::string shortFlags;
        bool dereference = false, expression = false, scriptFile = false;
        for (size_t i = from + 1; i < fields.size(); ++i) {
            if (!isOption(fields[i])) continue;
            const std::string t = text(i);
            if (t == "--") break;
            // Options that run a program, or take their file names from a file or from input.
            for (const char* runs : {"--pre", "--hostname-bin", "--compress-program", "--filter", "--unzip-command",
                                     "--files0-from", "--files-from", "--to-file", "--from-file", "--exclude-from",
                                     "--include-from", "--reference-file"}) {
                if (t.rfind(runs, 0) == 0) opaque(name + " is given " + std::string(runs) + ", which names a program or a list of files");
            }
            if ((name == "tar" && (t == "-T" || t == "-X")) || (name == "zip" && (t == "-@" || t == "-TT" || t == "-i@")) ||
                (name == "file" && t == "-f")) {
                opaque(name + " " + t + " takes its file names from a file or from input");
            }
            if (t[1] != '-') shortFlags += t.substr(1);
            if (t == "--dereference" || t == "--dereference-recursive" || t == "--follow" || t == "--recursive-dereference") dereference = true;
            if (t == "-e" || t == "-f" || t.rfind("--regexp", 0) == 0 || t.rfind("--file", 0) == 0 || t.rfind("--expression", 0) == 0) expression = true;
            if (t == "-f" || t.rfind("--file", 0) == 0) scriptFile = true;
        }
        const auto flag = [&](char c) { return shortFlags.find(c) != std::string::npos; };
        const bool grepLike = name == "grep" || name == "egrep" || name == "fgrep" || name == "rg";
        bool follows = dereference;
        if (grepLike) follows = follows || (name != "rg" && flag('R')) || (name == "rg" && flag('L'));
        else if (name == "zip") follows = follows || (flag('r') && !flag('y'));
        else if (name == "diff") follows = follows || flag('r');
        else if (name == "tar") follows = follows || flag('h') || flag('L');
        else if (name == "cp" || name == "du" || name == "ls" || name == "chmod" || name == "chown" || name == "chgrp" ||
                 name == "stat" || name == "file" || name == "tree") {
            follows = follows || flag('L') || (name != "ls" && name != "stat" && name != "file" && flag('H')) ||
                      (name == "tree" && flag('l'));
        }

        size_t first = from + 1;
        bool skipFirst = false;
        if (grepLike) {
            skipFirst = !expression;
        } else if (name == "sed") {
            const bool inPlace = flag('i') || std::any_of(fields.begin(), fields.end(), [](const Field& f) {
                return !f.unknown && f.text().rfind("--in-place", 0) == 0;
            });
            std::vector<std::string> scripts;
            size_t operandAt = std::string::npos;
            for (size_t i = from + 1; i < fields.size(); ++i) {
                const std::string t = text(i);
                if (fields[i].unknown) {
                    opaque("a sed script or file name is worked out while the command runs");
                    return;
                }
                if (t == "-e" || t == "--expression") {
                    if (i + 1 < fields.size()) scripts.push_back(text(++i));
                } else if (t.rfind("--expression=", 0) == 0) {
                    scripts.push_back(t.substr(13));
                } else if (t == "-f" || t.rfind("--file", 0) == 0) {
                    opaque("sed runs a script file, which GAIA does not read");
                    if (t == "-f" && i + 1 < fields.size()) path(fields[++i], st);
                } else if (isOption(fields[i])) {
                    continue;
                } else if (scripts.empty() && operandAt == std::string::npos && !scriptFile) {
                    scripts.push_back(t);
                    operandAt = i;
                } else {
                    path(fields[i], st, inPlace);
                }
            }
            for (const auto& s : scripts) {
                if (sedScriptIsPlain(s)) continue;
                opaque("the sed script can write, read or run something else (w, r, e)");
                // `e command` runs the rest of the line in a shell.
                for (size_t at = 0; at < s.size(); ++at) {
                    if (s[at] != 'e' || (at > 0 && std::isalpha(static_cast<unsigned char>(s[at - 1]))) || at + 1 >= s.size() || s[at + 1] != ' ') continue;
                    State copy = st;
                    nested(s.substr(at + 2, s.find('\n', at) == std::string::npos ? std::string::npos : s.find('\n', at) - at - 2), copy, false);
                }
            }
            return;
        } else if (name == "awk" || name == "gawk" || name == "mawk" || name == "nawk") {
            bool programSeen = false;
            for (size_t i = from + 1; i < fields.size(); ++i) {
                const std::string t = text(i);
                if (fields[i].unknown) {
                    opaque("an awk program or file name is worked out while the command runs");
                    return;
                }
                if (!programSeen && (t == "-f" || t == "-i" || t == "-l" || t.rfind("--file", 0) == 0 ||
                                     t.rfind("--include", 0) == 0 || t.rfind("--load", 0) == 0 || t == "-E" || t == "--exec")) {
                    opaque("awk runs a program file, which GAIA does not read");
                    programSeen = true;
                    if (i + 1 < fields.size()) path(fields[++i], st);
                } else if (!programSeen && (t == "-v" || t == "-F" || t == "--assign" || t == "--field-separator")) {
                    ++i;
                } else if (!programSeen && isOption(fields[i])) {
                    continue;
                } else if (!programSeen) {
                    programSeen = true;
                    if (!awkProgramIsPlain(t)) {
                        opaque("the awk program can run a command or choose its own files");
                        commandsInAwk(t, st);
                    }
                } else if (t.find('=') == std::string::npos || !isName(t.substr(0, t.find('=')))) {
                    path(fields[i], st);
                }
            }
            return;
        } else if (name == "dd") {
            for (size_t i = from + 1; i < fields.size(); ++i) {
                const std::string t = fields[i].pattern;
                if (fields[i].unknown) opaque("a file name in it is worked out while the command runs");
                else if (t.rfind("if=", 0) == 0) path({t.substr(3), false, hasWildcard(t.substr(3))}, st);
                else if (t.rfind("of=", 0) == 0) path({t.substr(3), false, hasWildcard(t.substr(3))}, st, true);
            }
            return;
        } else if (name == "tar") {
            // tar xzf a.tgz: the first word is the flags.
            if (first < fields.size() && !isOption(fields[first]) && !fields[first].unknown) {
                shortFlags += text(first);
                ++first;
            }
            for (size_t i = from + 1; i < fields.size(); ++i) {
                const std::string t = text(i);
                if (t.rfind("--to-command", 0) == 0 || t.rfind("--use-compress-program", 0) == 0 || t == "-I" ||
                    t.rfind("--checkpoint-action", 0) == 0 || t.rfind("--rsh-command", 0) == 0 ||
                    t.rfind("--absolute-names", 0) == 0 || t.rfind("--to-stdout", 0) == 0 || t.rfind("--transform", 0) == 0 ||
                    t.rfind("--xform", 0) == 0 || t == "-s" || t.rfind("--strip", 0) == 0) {
                    opaque("tar is given an option that runs a program or changes where files land");
                }
            }
            if (shortFlags.find('P') != std::string::npos) opaque("tar keeps absolute paths (-P)");
            if (shortFlags.find('x') != std::string::npos || std::any_of(fields.begin(), fields.end(), [](const Field& f) {
                    return !f.unknown && (f.text() == "--extract" || f.text() == "--get");
                })) {
                unpacks_ = true;
            }
        } else if (name == "unzip" || name == "gunzip" || name == "bunzip2" || name == "unxz") {
            if (name == "unzip") unpacks_ = true;
        } else if (name == "ln") {
            link(fields, from, st, flag('s'));
            return;
        } else if (name == "cp" || name == "mv" || name == "install") {
            // A link that is moved or copied as a link points somewhere new.
            const bool keepsLinks = name == "mv" || flag('R') || flag('r') || flag('a') || flag('P') || flag('d');
            if (ws_ && keepsLinks) {
                for (size_t i = first; i < fields.size(); ++i) {
                    if (fields[i].unknown || isOption(fields[i])) continue;
                    for (const auto& base : st.folders) {
                        const std::string real = (fs::u8path(base) / fs::u8path(fields[i].text())).u8string();
                        if (Workspace::holdsLinks(isAbsolute(fields[i].text()) ? fields[i].text() : real)) movesLinks_ = true;
                    }
                }
            }
        }
        operands(fields, first, st, inPlaceWriters().count(name) > 0, skipFirst);
        if (name == "sort" || name == "shuf" || name == "tree" || name == "iconv") {
            // -o FILE is written in place.
            for (size_t i = first; i + 1 < fields.size(); ++i) {
                if (text(i) == "-o" || text(i) == "--output") path(fields[i + 1], st, true);
            }
            for (size_t i = first; i < fields.size(); ++i) {
                if (text(i).rfind("--output=", 0) == 0) path({escaped(text(i).substr(9)), false, false}, st, true);
            }
        }
        if (name == "cp" || name == "install") {
            // Copying over a file writes into it, so the last word is checked as a write.
            for (size_t i = fields.size(); i-- > first;) {
                if (fields[i].unknown || isOption(fields[i])) continue;
                path(fields[i], st, true);
                break;
            }
        }
        if (!follows) return;
        bool named = false;
        for (size_t i = first; i < fields.size(); ++i) {
            if (fields[i].unknown || isOption(fields[i])) continue;
            named = true;
            followedTree(fields[i], st);
        }
        if (!named) followedTree({".", false, false}, st);
    }

    // The command lines an awk program hands to a shell: system("..."), "..." | getline, print | "...".
    void commandsInAwk(const std::string& program, const State& st) {
        for (size_t i = 0; i < program.size(); ++i) {
            if (program[i] != '"') continue;
            std::string text;
            const size_t open = i;
            for (++i; i < program.size() && program[i] != '"'; ++i) {
                if (program[i] == '\\' && i + 1 < program.size()) ++i;
                text += program[i];
            }
            const size_t before = program.find_last_not_of(" \t", open == 0 ? 0 : open - 1);
            const size_t after = program.find_first_not_of(" \t", i + 1);
            const bool piped = (before != std::string::npos && open > 0 && program[before] == '|') ||
                               (after != std::string::npos && program[after] == '|');
            const bool called = before != std::string::npos && open > 0 && program[before] == '(' && before >= 6 &&
                                program.compare(before - 6, 6, "system") == 0;
            if (!piped && !called) continue;
            State copy = st;
            nested(text, copy, false);
        }
    }

    // ln: a link made here must not lead outside, since a later word on the same line can read through it.
    void link(const std::vector<Field>& fields, size_t from, const State& st, bool symbolic) {
        std::vector<Field> names;
        for (size_t i = from + 1; i < fields.size(); ++i) {
            if (fields[i].unknown) {
                opaque("a file name in it is worked out while the command runs");
                return;
            }
            if (!isOption(fields[i])) names.push_back(fields[i]);
        }
        if (names.empty()) return;
        if (!symbolic) {
            for (const auto& n : names) path(n, st);
            return;
        }
        // The last word is the link (or the folder it goes in); a relative target is read from there.
        const Field where = names.size() > 1 ? names.back() : Field{".", false, false};
        path(where, st);
        if (!ws_) return;
        for (size_t i = 0; i + (names.size() > 1 ? 1 : 0) < names.size(); ++i) {
            const std::string target = names[i].text();
            if (isAbsolute(target) || target[0] == '~') path(names[i], st);
            for (const auto& base : st.folders) {
                std::error_code ec;
                fs::path linkDir = fs::u8path(isAbsolute(where.text()) ? where.text() : (fs::u8path(base) / fs::u8path(where.text())).u8string());
                if (!fs::is_directory(linkDir, ec)) linkDir = linkDir.parent_path();
                if (!isAbsolute(target)) place(target, linkDir.u8string(), target, true, false);
            }
        }
    }

    // --- grammar ----------------------------------------------------------

    static bool isOp(const std::vector<Token>& t, size_t i, const char* op) {
        return i < t.size() && t[i].kind == Token::Op && t[i].text == op;
    }

    void list(const std::vector<Token>& t, size_t& i, State& st, bool untilParen) {
        // Folders the shell is still in if a cd earlier in this && chain failed or was skipped.
        std::vector<std::string> fallback;
        std::string before;  // the operator before this command
        while (i < t.size()) {
            if (isOp(t, i, ")")) {
                if (untilParen) break;
                ++i;
                continue;
            }
            if (t[i].kind == Token::Op) {
                if (t[i].text != "\n") out_.compound = true;
                before = t[i].text;
                if (before != "&&") {
                    st.folders = merged(std::move(st.folders), fallback);
                    fallback.clear();
                }
                ++i;
                continue;
            }
            // A pipeline: each part runs in its own shell, so nothing it sets or changes lasts.
            bool piped = false;
            for (;;) {
                State part = st;
                part.movedSurely = false;
                // What this command reads from the pipe, when the command before it shows that.
                pipeText_ = piped ? outText_ : std::nullopt;
                pipeFile_ = piped ? outFile_ : std::nullopt;
                outText_.reset();
                outFile_.reset();
                command(t, i, part, piped);
                const bool more = isOp(t, i, "|") || isOp(t, i, "|&");
                if (!more && !piped) {
                    const std::vector<std::string> was = st.folders;
                    st = part;
                    if (st.folders != was) {
                        // The cd is certain when nothing could skip it and its folder is there. Otherwise the
                        // commands after && run only where it worked; anything later may run where it didn't.
                        const bool certain = st.movedSurely && !control_ && before != "&&" && before != "||";
                        if (!certain && isOp(t, i, "&&") && before != "||") fallback = merged(std::move(fallback), was);
                        else if (!certain) st.folders = merged(std::move(st.folders), was);
                    }
                }
                if (!more) break;
                piped = true;
                out_.compound = true;
                ++i;
            }
        }
        st.folders = merged(std::move(st.folders), fallback);
        if (st.folders.size() > kMaxFolders) st.folderUnknown = true;
    }

    void command(const std::vector<Token>& t, size_t& i, State& st, bool pipedIn) {
        if (i >= t.size()) return;
        if (isOp(t, i, "(")) {
            ++i;
            out_.compound = true;
            State inner = st;
            list(t, i, inner, true);
            if (isOp(t, i, ")")) ++i;
        } else if (t[i].kind == Token::WordT) {
            const std::string keyword = t[i].word.bare();
            static const Names opens = {"{", "!", "time", "if", "then", "elif", "else", "while", "until", "do"};
            static const Names closes = {"fi", "done", "}", "esac", "]]"};
            if (opens.count(keyword)) {
                ++i;
                if (keyword != "!" && keyword != "time") out_.compound = control_ = true;
                if (i < t.size() && t[i].kind != Token::Op) command(t, i, st, pipedIn);
                return;
            }
            if (closes.count(keyword)) {
                ++i;
            } else if (keyword == "for" || keyword == "select") {
                forLoop(t, i, st);
                return;
            } else if (keyword == "case" || keyword == "function") {
                opaque(keyword == "case" ? "it branches on a value worked out while the command runs"
                                         : "it defines a shell function");
                out_.compound = true;
                while (i < t.size() && t[i].kind != Token::Op) ++i;
                return;
            } else if (isOp(t, i + 1, "(") && isOp(t, i + 2, ")")) {
                opaque("it defines a shell function");
                out_.compound = true;
                i += 3;
                return;
            }
        }
        std::vector<std::pair<std::string, Word>> assigns;
        std::vector<Word> words;
        std::vector<const Token*> redirs;
        std::vector<Word> targets;
        while (i < t.size() && t[i].kind != Token::Op) {
            if (t[i].kind == Token::Redir) {
                const Token* redir = &t[i++];
                Word target;
                if (redir->text != "<<" && redir->text != "<<-" && i < t.size() && t[i].kind == Token::WordT) target = t[i++].word;
                redirs.push_back(redir);
                targets.push_back(target);
                continue;
            }
            const Word& w = t[i++].word;
            if (words.empty() && !w.parts.empty() && w.parts[0].kind == Part::Lit && !w.parts[0].quoted) {
                const std::string& head = w.parts[0].text;
                size_t eq = head.find('=');
                const std::string name = eq == std::string::npos ? "" : head.substr(0, eq > 0 && head[eq - 1] == '+' ? eq - 1 : eq);
                if (isName(name)) {
                    Word value = w;
                    value.parts[0].text = head.substr(eq + 1);
                    assigns.emplace_back(name, value);
                    continue;
                }
            }
            words.push_back(w);
        }
        simple(assigns, words, redirs, targets, st, pipedIn);
    }

    void forLoop(const std::vector<Token>& t, size_t& i, State& st) {
        out_.compound = control_ = true;
        ++i;
        const std::string name = i < t.size() && t[i].kind == Token::WordT ? t[i].word.bare() : "";
        if (i < t.size() && t[i].kind == Token::WordT) ++i;
        Value value;
        if (!isName(name) || i >= t.size() || t[i].kind != Token::WordT || t[i].word.bare() != "in") {
            opaque("it loops over values worked out while the command runs");
            value.unknown = true;
        } else {
            for (++i; i < t.size() && t[i].kind == Token::WordT; ++i) {
                std::vector<std::string> subs;
                const std::vector<Field> listed = expand(t[i].word, st, subs);
                for (const auto& s : subs) nested(s, st, false);
                for (const auto& f : listed) {
                    if (f.unknown) {
                        value.unknown = true;
                    } else if (!f.wildcard) {
                        value.values.push_back(f.text());
                    } else {
                        // The names the shell fills in: read from a folder, so that folder is checked.
                        path(f, st);
                        for (const auto& base : st.folders) {
                            std::vector<std::string> found;
                            if (!glob(base, f.pattern, found)) value.unknown = true;
                            for (const auto& m : found) {
                                value.values.push_back(m);
                                if (f.pattern.find('/') == std::string::npos) value.values.push_back(fs::u8path(m).filename().u8string());
                            }
                        }
                    }
                }
            }
            if (value.values.size() > kMaxAlternatives) value = {{}, true};
        }
        if (isName(name)) st.vars[name] = value;
    }

    void simple(const std::vector<std::pair<std::string, Word>>& assigns, const std::vector<Word>& words,
                const std::vector<const Token*>& redirs, const std::vector<Word>& targets, State& st, bool pipedIn) {
        if (++commands_ > 1) out_.compound = true;
        changing_ = false;
        std::vector<std::string> subs;
        std::vector<Field> fields;
        bool programMulti = false;
        for (size_t w = 0; w < words.size(); ++w) {
            const std::vector<Field> expanded = expand(words[w], st, subs);
            if (w == 0) programMulti = multi_;
            fields.insert(fields.end(), expanded.begin(), expanded.end());
        }
        Call call;
        call.pipedIn = pipedIn;
        std::string stdinText;
        Field stdinFile;
        if (pipedIn && pipeText_) {
            stdinText = *pipeText_;
            call.stdinText = &stdinText;
        } else if (pipedIn && pipeFile_) {
            stdinFile = *pipeFile_;
            call.stdinFile = &stdinFile;
        }
        for (size_t r = 0; r < redirs.size(); ++r) {
            const std::string& op = redirs[r]->text;
            if (op == "<<" || op == "<<-") {
                if (!redirs[r]->hasBody) {
                    call.stdinFromFile = true;
                    continue;
                }
                stdinText = redirs[r]->heredoc;
                call.stdinText = &stdinText;
                if (redirs[r]->bodyExpands) {
                    // The shell runs every $( ) in the body before the command reads it.
                    Word body;
                    Lexer lexer(redirs[r]->heredoc);
                    if (!lexer.quotedText(body)) {
                        hide("a heredoc's quoting can't be read");
                    } else {
                        const std::vector<Field> text = expand(body, st, subs);
                        if (text.size() == 1 && !text[0].unknown) stdinText = text[0].text();
                        else call.stdinText = nullptr, call.stdinFromFile = true;
                    }
                }
                continue;
            }
            const std::vector<Field> target = expand(targets[r], st, subs);
            if (op == "<<<") {
                if (target.size() == 1 && !target[0].unknown) {
                    stdinText = target[0].text();
                    call.stdinText = &stdinText;
                } else {
                    call.stdinFromFile = true;
                }
                continue;
            }
            if (op == "<" || op == "<>") {
                call.stdinFromFile = true;
                call.stdinText = nullptr;
                if (target.size() == 1 && !target[0].unknown && !target[0].wildcard) {
                    stdinFile = target[0];
                    call.stdinFile = &stdinFile;
                }
            }
            for (const auto& f : target) {
                const std::string text = f.unknown ? "" : f.text();
                if ((op == ">&" || op == "<&") && !f.unknown &&
                    (text == "-" || text.find_first_not_of("0123456789") == std::string::npos)) {
                    continue;
                }
                path(f, st, op[0] == '>' || op[0] == '&' || op == "<>");
            }
        }
        for (const auto& [name, word] : assigns) {
            const std::vector<Field> value = expand(word, st, subs, false);
            Value v;
            for (const auto& f : value) {
                if (f.unknown) v.unknown = true;
                else v.values.push_back(f.text());
            }
            if (value.empty()) v.values.push_back("");
            if (words.empty()) assign(st, name, v);
            else call.prefixEnv.insert(name);
        }
        for (const auto& body : subs) nested(body, st, false);
        if (fields.empty()) return;
        if (programMulti) {
            hide("the program it runs is worked out while the command runs");
            return;
        }
        // What a plain echo or cat hands the next command in a pipe.
        if (!fields[0].unknown) {
            const std::string program = fields[0].text();
            std::vector<const Field*> named;
            bool known = true;
            for (size_t i = 1; i < fields.size(); ++i) {
                known = known && !fields[i].unknown && !fields[i].wildcard;
                if (!isOption(fields[i])) named.push_back(&fields[i]);
            }
            if (program == "echo" && known && named.size() + 1 == fields.size()) {
                std::string text;
                for (const Field* f : named) text += (text.empty() ? "" : " ") + f->text();
                outText_ = text;
            } else if (program == "cat" && known && named.size() == 1) {
                outFile_ = *named[0];
            } else if (program == "cat" && named.empty() && call.stdinText) {
                outText_ = *call.stdinText;
            } else if (program == "cat" && named.empty() && call.stdinFile) {
                outFile_ = *call.stdinFile;
            }
        }
        run(fields, st, call, 0);
    }

    const Workspace* ws_;
    const std::map<std::string, std::string>& env_;
    ShellJudgement& out_;
    int depth_ = 0;
    int commands_ = 0;
    size_t words_ = 0;
    size_t entriesSeen_ = 0;
    bool multi_ = false;
    bool control_ = false;  // an if, a loop or a group was seen: a command may be skipped or repeated
    bool changing_ = false;  // the program being judged makes or changes the files it names
    std::vector<std::string> searchPath_;
    std::optional<std::string> outText_, pipeText_;
    std::optional<Field> outFile_, pipeFile_;

public:
    bool unpacks_ = false;     // an archive is unpacked: links it holds appear mid-line
    bool movesLinks_ = false;  // a link is moved or copied as a link
};

}  // namespace

ShellJudgement judgeShellCommand(const std::string& command, const std::string& cwd,
                                 const std::map<std::string, std::string>& env, const Workspace* ws) {
    ShellJudgement out;
    if (command.size() > kMaxCommandBytes) {
        out.opaque = out.hidden = "it is too long to check";
        return out;
    }
    State st;
    std::string start = cwd;
    if (start.empty()) {
        if (ws && !ws->roots().empty()) {
            start = ws->roots().front();
        } else {
            std::error_code ec;
            start = fs::current_path(ec).u8string();
        }
    }
    st.folders.push_back(start);
    // The shell drops a NUL byte, so the command would not run as it reads here.
    if (command.find('\0') != std::string::npos) {
        out.outsideWord = "a NUL byte";
        out.refusal = "Refused: the command holds a NUL byte, which the shell drops before it runs the line, so the "
                      "line can't be checked as written. Send the command without it.";
        return out;
    }
    for (const auto& [name, value] : env) {
        if (steers(name)) out.opaque = "an earlier command changed " + named(name) + ", which decides what later commands do";
        (void)value;
    }
    Judge judge(ws, env, out);
    judge.script(command, st, 0);
    // A link that appears part-way through a line can't be followed by a check made before the line runs.
    if (out.compound && (judge.unpacks_ || judge.movesLinks_) && out.opaque.empty()) {
        out.opaque = judge.unpacks_ ? "it unpacks an archive and then runs more commands, and the archive may hold links"
                                    : "it moves a link and then runs more commands";
    }
    return out;
}

}  // namespace gaia
