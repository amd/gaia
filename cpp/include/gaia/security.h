// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Security utilities for the GAIA C++ agent framework.
// Provides path validation, shell argument safety checks, and a persistent
// "always allow" store for tool confirmation.

#pragma once

#include <memory>
#include <mutex>
#include <set>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

// ---------------------------------------------------------------------------
// Path and shell argument utilities
// ---------------------------------------------------------------------------

/// Validate that requestedPath is inside basePath (prevents path traversal).
/// Uses platform realpath/GetFullPathName to canonicalize both paths before
/// comparing. Returns false on resolution failure or if requestedPath escapes.
GAIA_API bool validatePath(const std::string& basePath, const std::string& requestedPath);

/// Check whether arg is safe to pass to a shell command without quoting.
/// Returns false for empty strings and strings containing shell metacharacters.
/// Generalizes the ad-hoc checks in wifi_agent.cpp / test_tool_integration.cpp.
GAIA_API bool isSafeShellArg(const std::string& arg);

/// What an "always allow" for a shell command covers: the program for a short
/// list of single-purpose programs ("pytest", "mkdir"), program + subcommand
/// for tools whose subcommands differ in risk ("git status", "npm test"), and
/// the exact command for everything else: interpreters, wrappers, unknown
/// programs, and anything that chains, pipes, quotes, substitutes or redirects.
GAIA_API std::string shellCommandScope(const std::string& command);

/// Split a command line into segments at ; | && || and each segment into words
/// with quotes removed, the way `posixShell` (bash) or cmd.exe would. False
/// when the line holds anything that could run or write something else: & or
/// a redirection, an expansion, a backslash bash would consume, an unbalanced
/// quote, or a quoted program name.
GAIA_API bool splitShellCommand(const std::string& line, bool posixShell,
                                std::vector<std::vector<std::string>>& segments);

/// True for a command that only reads: programs from a short list that have
/// no flag that writes or runs code (ls, cat, grep, find without
/// -exec/-delete...), piped or chained, no expansion, no network share, and no
/// redirection except discarding output to that shell's own null device.
/// Under cmd.exe (`posixShell` false) only the built-ins dir, type, cd and
/// echo. It says nothing about where the command reads; see
/// Workspace::readsStayInside(). Anything uncertain is false.
GAIA_API bool isReadOnlyShellCommand(const std::string& command, bool posixShell = true);

struct ToolInfo;

/// Why `tool` must not run while the internet is off for a chat, or "" when
/// the call stays on this PC: web_fetch to another host, any MCP tool, a shell
/// command that goes online, or a fetch of a remote URL.
GAIA_API std::string internetRefusal(const ToolInfo& tool, const json& args);

/// The folder GAIA keeps this user's files in: $GAIA_HOME when set, else
/// .gaia under the home folder (USERPROFILE on Windows, HOME elsewhere).
/// Empty when neither is set. Every per-user default path starts here.
GAIA_API std::string gaiaHome();

/// Create a terminal-based confirm callback. Prompts on stderr, reads stdin.
/// Auto-installed by Agent for interactive (non-silent) agents.
GAIA_API ToolConfirmCallback makeStdinConfirmCallback();

// ---------------------------------------------------------------------------
// AllowedToolsStore — persistent "always allow" permissions
// ---------------------------------------------------------------------------

/// Persists the set of tool names that a user has permanently approved.
///
/// Storage: ~/.gaia/security/allowed_tools.json (POSIX)
///          %USERPROFILE%\.gaia\security\allowed_tools.json (Windows)
///
/// The store is global to all GAIA instances on the machine. An Agent creates
/// one instance and passes it to ToolRegistry via setAllowedToolsStore().
class GAIA_API AllowedToolsStore {
public:
    /// Construct with the default config directory (~/.gaia/security/).
    AllowedToolsStore();

    /// Construct with an explicit directory path. An empty path keeps the
    /// answers in memory only (one chat's "always").
    explicit AllowedToolsStore(const std::string& dir);

    /// Check whether toolName has been permanently allowed.
    bool isAlwaysAllowed(const std::string& toolName) const;

    /// Add toolName to the always-allowed set and persist to disk.
    void addAlwaysAllowed(const std::string& toolName);

    /// Remove toolName from the always-allowed set and persist to disk.
    void removeAlwaysAllowed(const std::string& toolName);

    /// Remove all permanently-allowed tools and persist to disk.
    void clearAll();

    /// Return all permanently-allowed tool names.
    std::vector<std::string> allAllowed() const;

private:
    std::string filePath_;
    std::set<std::string> allowed_;
    mutable std::mutex mu_;

    void load();
    void save() const;
    static std::string defaultConfigDir();
};

} // namespace gaia
