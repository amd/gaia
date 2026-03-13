// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Security utilities for the GAIA C++ agent framework.
// Provides path validation, shell argument safety checks, and a persistent
// "always allow" store for tool confirmation.

#pragma once

#include <memory>
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

    /// Construct with an explicit directory path (for testing).
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

    void load();
    void save() const;
    static std::string defaultConfigDir();
};

} // namespace gaia
