// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Glob matching and .gitignore evaluation for the GAIA C++ agent framework.
//
// Searching a real repository without ignore awareness returns build output,
// vendored dependencies, and VCS internals. That noise poisons an agent's
// context far more effectively than a missing result would hurt it, so every
// file-walking tool in the framework routes through this header.
//
// Reused by `FileIOTools::fileSearch()`. A future `lsp` tool (clangd) and the
// code index both need the same "which files belong to this project" answer;
// they should call GitignoreMatcher rather than re-deriving it, so a project
// that hides a directory hides it from every tool at once.

#pragma once

#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia {

/// Options controlling how globMatch() interprets a pattern.
struct GAIA_API GlobOptions {
    /// When true, `*` and `?` do not match the path separator `/`, and `**`
    /// spans directories. When false the whole text is treated as one
    /// segment (the right mode for matching a bare file name).
    bool pathMode = false;

    /// ASCII case-insensitive comparison.
    bool caseInsensitive = false;
};

/// Match `text` against a glob `pattern`.
///
/// Supported syntax:
///   - `*`     any run of characters (not `/` in path mode)
///   - `?`     exactly one character (not `/` in path mode)
///   - `**`    any run of characters including `/` (path mode only);
///             `**/` also matches zero directories, so `**/x` matches `x`
///   - `[abc]` `[a-z]` `[!a-z]` `[^a-z]` character classes with ranges and negation
///   - `\x`    escapes the next character
///
/// Runs in O(pattern * text) via memoized backtracking, so a pathological
/// pattern such as `*a*a*a*a*b` cannot hang the agent.
GAIA_API bool globMatch(const std::string& pattern,
                        const std::string& text,
                        const GlobOptions& options = {});

/// Evaluates `.gitignore` rules against paths.
///
/// Implements the subset of gitignore(5) that matters for source trees:
/// comments, blank lines, escapes, trailing-space stripping, `!` negation
/// (last matching rule wins), `dir/` directory-only rules, rules anchored by
/// a leading or embedded `/`, and unanchored rules that match at any depth.
///
/// Two deliberate deviations, both in the direction of showing more rather
/// than fewer files — a search that hides a file the user asked for is worse
/// than one that shows an extra:
///   - `.git/info/exclude`, the global core.excludesFile, and skip-worktree
///     bits are not consulted; they live outside the working tree.
///   - git refuses to re-include a file whose parent directory is excluded.
///     Here the last matching rule simply wins, so `!keep.log` re-includes
///     even under an ignored directory.
class GAIA_API GitignoreMatcher {
public:
    GitignoreMatcher() = default;

    /// Build a matcher governing `directory`: collects every `.gitignore`
    /// from the enclosing git repository root down to `directory` itself, so
    /// searching `repo/src` still honours `repo/.gitignore`. When `directory`
    /// is not inside a git repository, only its own `.gitignore` is read.
    ///
    /// This covers everything *above* the starting directory. A caller that
    /// walks downward must fold in each subdirectory's own `.gitignore` with
    /// addFile() as it descends — rules are scoped to their base directory,
    /// so adding them late is safe and cannot affect siblings.
    static GitignoreMatcher forDirectory(const std::string& directory);

    /// Add the rules in the `.gitignore` file at `gitignorePath`. Rules are
    /// interpreted relative to the file's own directory. A missing or
    /// unreadable file adds nothing.
    void addFile(const std::string& gitignorePath);

    /// Add rules from in-memory `.gitignore` text anchored at `baseDir`.
    void addRules(const std::string& contents, const std::string& baseDir);

    /// True when `path` is ignored. `path` may be absolute or relative;
    /// it is canonicalized against the rule base directories. `isDirectory`
    /// selects whether `dir/`-style rules apply to the final component.
    bool isIgnored(const std::string& path, bool isDirectory) const;

    /// True when no rules were loaded (every path is then un-ignored).
    bool empty() const { return rules_.empty(); }

    /// Number of loaded rules — surfaced so a tool can report *why* a search
    /// came back thin instead of leaving the caller guessing.
    size_t ruleCount() const { return rules_.size(); }

private:
    struct Rule {
        std::string pattern;   ///< pattern with leading `!`/`/` and trailing `/` stripped
        std::string baseDir;   ///< generic-format absolute directory the rule is anchored to
        bool negate = false;   ///< `!pattern` — re-includes a previously ignored path
        bool dirOnly = false;  ///< `pattern/` — matches directories only
        bool anchored = false; ///< contains `/` other than a trailing one
    };

    std::vector<Rule> rules_;
};

} // namespace gaia
