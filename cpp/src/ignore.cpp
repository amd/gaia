// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/ignore.h"

#include <cctype>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <sstream>

namespace fs = std::filesystem;

namespace gaia {

namespace {

char foldCase(char c, bool caseInsensitive) {
    if (!caseInsensitive) return c;
    return static_cast<char>(
        std::tolower(static_cast<unsigned char>(c)));
}

/// Memoized backtracking glob matcher.
///
/// The memo table is what keeps `*a*a*a*a*b` linear-ish instead of
/// exponential — a search tool must never be able to hang the agent loop on a
/// pattern the model made up.
class GlobEngine {
public:
    GlobEngine(const std::string& pattern,
               const std::string& text,
               const GlobOptions& options)
        : p_(pattern), t_(text), o_(options),
          memo_((pattern.size() + 1) * (text.size() + 1), 0) {}

    bool run() { return match(0, 0); }

private:
    const std::string& p_;
    const std::string& t_;
    const GlobOptions& o_;
    std::vector<uint8_t> memo_;  // 0 = unknown, 1 = true, 2 = false

    bool isSep(size_t ti) const {
        return o_.pathMode && ti < t_.size() && t_[ti] == '/';
    }

    /// Parse a `[...]` class starting at p_[pi] == '['.
    /// Returns false when the class is unterminated (caller treats `[` as a
    /// literal). Otherwise sets `matched` and advances `nextPi` past `]`.
    bool matchClass(size_t pi, char c, bool& matched, size_t& nextPi) const {
        size_t i = pi + 1;
        bool negate = false;
        if (i < p_.size() && (p_[i] == '!' || p_[i] == '^')) {
            negate = true;
            ++i;
        }

        bool found = false;
        bool first = true;
        const char target = foldCase(c, o_.caseInsensitive);

        while (i < p_.size()) {
            if (p_[i] == ']' && !first) {
                matched = negate ? !found : found;
                nextPi = i + 1;
                return true;
            }
            first = false;

            char lo = p_[i];
            if (lo == '\\' && i + 1 < p_.size()) {
                ++i;
                lo = p_[i];
            }

            // Range: a-z (a trailing '-' before ']' is a literal)
            if (i + 2 < p_.size() && p_[i + 1] == '-' && p_[i + 2] != ']') {
                char hi = p_[i + 2];
                size_t consumed = 2;
                if (hi == '\\' && i + 3 < p_.size()) {
                    hi = p_[i + 3];
                    consumed = 3;
                }
                char l = foldCase(lo, o_.caseInsensitive);
                char h = foldCase(hi, o_.caseInsensitive);
                if (target >= l && target <= h) found = true;
                i += consumed + 1;
                continue;
            }

            if (foldCase(lo, o_.caseInsensitive) == target) found = true;
            ++i;
        }

        return false;  // unterminated
    }

    bool match(size_t pi, size_t ti) {
        const size_t key = pi * (t_.size() + 1) + ti;
        if (memo_[key] != 0) return memo_[key] == 1;

        bool result = compute(pi, ti);
        memo_[key] = result ? 1 : 2;
        return result;
    }

    bool compute(size_t pi, size_t ti) {
        if (pi == p_.size()) return ti == t_.size();

        const char pc = p_[pi];

        if (pc == '*') {
            const bool doubleStar =
                o_.pathMode && pi + 1 < p_.size() && p_[pi + 1] == '*';

            if (doubleStar) {
                size_t after = pi + 2;
                if (after < p_.size() && p_[after] == '/') {
                    // `**/` matches zero or more leading directories.
                    if (match(after + 1, ti)) return true;
                    for (size_t k = ti; k < t_.size(); ++k) {
                        if (t_[k] == '/' && match(after + 1, k + 1)) return true;
                    }
                    return false;
                }
                for (size_t k = ti; k <= t_.size(); ++k) {
                    if (match(after, k)) return true;
                }
                return false;
            }

            for (size_t k = ti; k <= t_.size(); ++k) {
                if (match(pi + 1, k)) return true;
                if (isSep(k)) break;  // a single `*` never crosses a separator
            }
            return false;
        }

        if (pc == '?') {
            if (ti >= t_.size() || isSep(ti)) return false;
            return match(pi + 1, ti + 1);
        }

        if (pc == '[') {
            bool matched = false;
            size_t nextPi = pi;
            if (matchClass(pi, ti < t_.size() ? t_[ti] : '\0', matched, nextPi)) {
                if (ti >= t_.size() || isSep(ti) || !matched) return false;
                return match(nextPi, ti + 1);
            }
            // Unterminated class — fall through and treat '[' literally.
        }

        char literal = pc;
        size_t nextPi = pi + 1;
        if (pc == '\\' && pi + 1 < p_.size()) {
            literal = p_[pi + 1];
            nextPi = pi + 2;
        }

        if (ti >= t_.size()) return false;
        if (foldCase(literal, o_.caseInsensitive) !=
            foldCase(t_[ti], o_.caseInsensitive)) {
            return false;
        }
        return match(nextPi, ti + 1);
    }
};

/// Canonicalize to a generic-separator absolute path without requiring the
/// path to exist (weakly_canonical tolerates missing tails).
std::string normalizePath(const std::string& path) {
    std::error_code ec;
    fs::path p = fs::weakly_canonical(fs::path(path), ec);
    if (ec || p.empty()) {
        p = fs::absolute(fs::path(path), ec);
        if (ec) p = fs::path(path);
    }
    std::string s = p.generic_string();
    while (s.size() > 1 && s.back() == '/') s.pop_back();
    return s;
}

bool pathsEqual(const std::string& a, const std::string& b) {
#ifdef _WIN32
    if (a.size() != b.size()) return false;
    for (size_t i = 0; i < a.size(); ++i) {
        if (foldCase(a[i], true) != foldCase(b[i], true)) return false;
    }
    return true;
#else
    return a == b;
#endif
}

/// True when `path` lies strictly under directory `base`; writes the relative
/// remainder to `rel`.
bool relativeTo(const std::string& path, const std::string& base, std::string& rel) {
    if (path.size() <= base.size() + 1) return false;
    if (!pathsEqual(path.substr(0, base.size()), base)) return false;
    if (path[base.size()] != '/') return false;
    rel = path.substr(base.size() + 1);
    return !rel.empty();
}

/// Strip trailing whitespace that is not backslash-escaped (gitignore(5)).
void stripTrailingSpace(std::string& line) {
    while (!line.empty() &&
           (line.back() == ' ' || line.back() == '\t')) {
        size_t backslashes = 0;
        size_t i = line.size() - 1;
        while (i > 0 && line[i - 1] == '\\') {
            ++backslashes;
            --i;
        }
        if (backslashes % 2 == 1) break;  // escaped — keep it
        line.pop_back();
    }
}

} // namespace

// ---------------------------------------------------------------------------
// globMatch
// ---------------------------------------------------------------------------

bool globMatch(const std::string& pattern,
               const std::string& text,
               const GlobOptions& options) {
    GlobEngine engine(pattern, text, options);
    return engine.run();
}

// ---------------------------------------------------------------------------
// GitignoreMatcher
// ---------------------------------------------------------------------------

GitignoreMatcher GitignoreMatcher::forDirectory(const std::string& directory) {
    GitignoreMatcher matcher;

    std::error_code ec;
    fs::path start = fs::weakly_canonical(fs::path(directory), ec);
    if (ec || start.empty()) start = fs::path(directory);

    // Walk up to the enclosing repository root so that searching `repo/src`
    // still honours `repo/.gitignore`.
    std::vector<fs::path> chain;
    fs::path cur = start;
    bool foundRepoRoot = false;
    for (int depth = 0; depth < 64; ++depth) {
        chain.push_back(cur);
        if (fs::exists(cur / ".git", ec)) {
            foundRepoRoot = true;
            break;
        }
        fs::path parent = cur.parent_path();
        if (parent.empty() || parent == cur) break;
        cur = parent;
    }

    if (!foundRepoRoot) {
        // Outside a repository, only the directory's own file applies. Walking
        // to the filesystem root would silently apply a stranger's rules.
        matcher.addFile((start / ".gitignore").string());
        return matcher;
    }

    // Outermost first, so a nested .gitignore overrides its parent.
    for (auto it = chain.rbegin(); it != chain.rend(); ++it) {
        matcher.addFile((*it / ".gitignore").string());
    }
    return matcher;
}

void GitignoreMatcher::addFile(const std::string& gitignorePath) {
    std::ifstream file(gitignorePath);
    if (!file.is_open()) return;

    std::ostringstream buffer;
    buffer << file.rdbuf();

    fs::path baseDir = fs::path(gitignorePath).parent_path();
    if (baseDir.empty()) baseDir = fs::path(".");
    addRules(buffer.str(), baseDir.string());
}

void GitignoreMatcher::addRules(const std::string& contents,
                                const std::string& baseDir) {
    const std::string base = normalizePath(baseDir);

    std::istringstream stream(contents);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        stripTrailingSpace(line);
        if (line.empty()) continue;
        if (line[0] == '#') continue;

        Rule rule;
        rule.baseDir = base;

        if (line[0] == '!') {
            rule.negate = true;
            line.erase(0, 1);
        } else if (line.size() >= 2 && line[0] == '\\' &&
                   (line[1] == '!' || line[1] == '#')) {
            line.erase(0, 1);
        }
        if (line.empty()) continue;

        if (line.back() == '/') {
            rule.dirOnly = true;
            line.pop_back();
        }
        if (line.empty()) continue;

        if (line[0] == '/') {
            rule.anchored = true;
            line.erase(0, 1);
        } else if (line.find('/') != std::string::npos) {
            rule.anchored = true;
        }
        if (line.empty()) continue;

        rule.pattern = line;
        rules_.push_back(std::move(rule));
    }
}

bool GitignoreMatcher::isIgnored(const std::string& path, bool isDirectory) const {
    if (rules_.empty()) return false;

    const std::string abs = normalizePath(path);
    const GlobOptions pathOpts{/*pathMode=*/true, /*caseInsensitive=*/false};
    const GlobOptions nameOpts{/*pathMode=*/false, /*caseInsensitive=*/false};

    bool ignored = false;

    for (const Rule& rule : rules_) {
        std::string rel;
        if (!relativeTo(abs, rule.baseDir, rel)) continue;

        // Evaluate the rule against every ancestor as well as the path itself:
        // a file inside an ignored directory is ignored.
        bool matched = false;
        size_t start = 0;
        while (start <= rel.size() && !matched) {
            size_t slash = rel.find('/', start);
            const bool isLast = (slash == std::string::npos);
            const std::string candidate =
                isLast ? rel : rel.substr(0, slash);
            const bool candidateIsDir = isLast ? isDirectory : true;

            if (!rule.dirOnly || candidateIsDir) {
                if (rule.anchored) {
                    matched = globMatch(rule.pattern, candidate, pathOpts);
                } else {
                    const size_t lastSlash = candidate.find_last_of('/');
                    const std::string name = lastSlash == std::string::npos
                                                 ? candidate
                                                 : candidate.substr(lastSlash + 1);
                    matched = globMatch(rule.pattern, name, nameOpts);
                }
            }

            if (isLast) break;
            start = slash + 1;
        }

        if (matched) ignored = !rule.negate;  // last matching rule wins
    }

    return ignored;
}

} // namespace gaia
