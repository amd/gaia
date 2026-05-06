// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Bash-specific tool callbacks for the GAIA BashAgent.
// Provides shell execution and environment inspection tools.

#pragma once

#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

/// Bash-specific tool callbacks for the BashAgent.
///
/// Provides two tools:
///   - bash_execute: Run a shell command with timeout and output capture
///   - env_inspect: Inspect the shell environment, OS, PATH, and installed tools
///
/// Usage:
///   BashTools::registerAll(agent.toolRegistry());
class GAIA_API BashTools {
public:
    /// Register all bash tools with the given registry.
    static void registerAll(ToolRegistry& registry);

    /// bash_execute: Execute a shell command.
    /// Args: {"command": string, "timeout_ms"?: int (default 30000)}
    /// Policy: CONFIRM (user must approve each command)
    /// Returns: {"stdout": string, "stderr": string, "exit_code": int, "timed_out": bool}
    /// On error: {"error": string}
    static ToolInfo bashExecute();

    /// env_inspect: Inspect the shell environment.
    /// Args: {} (no args)
    /// Policy: ALLOW (read-only inspection)
    /// Returns: {"shell": string, "os": string, "path": [string], "tools": {"name": bool}}
    static ToolInfo envInspect();

private:
    // Implementation callbacks
    static json doBashExecute(const json& args);
    static json doEnvInspect(const json& args);

    /// Detect the best available shell on this system.
    /// Returns the shell command prefix (e.g. "bash", "sh", "/usr/bin/bash").
    /// On Windows, checks for bash (WSL, Git Bash, MSYS2) then falls back to sh.
    static std::string detectShell();

    /// Check if a command is available on PATH.
    /// Uses "which" on POSIX, "where" on Windows.
    static bool isToolAvailable(const std::string& toolName);

    /// Maximum output size before truncation (32 KB).
    static constexpr size_t MAX_OUTPUT_BYTES = 32768;

    /// Default command timeout in milliseconds.
    static constexpr int DEFAULT_TIMEOUT_MS = 30000;
};

} // namespace gaia
