// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Cross-platform process execution utility for the GAIA C++ agent framework.
// Replaces the ad-hoc runShell() pattern in example agents with a proper
// library function that handles timeouts, output capping, working directory,
// and environment variables.

#pragma once

#include <map>
#include <stdexcept>
#include <string>

#include "gaia/export.h"

namespace gaia {

/// Result of a process execution.
struct GAIA_API ProcessResult {
    std::string stdout_output;  ///< Captured stdout
    std::string stderr_output;  ///< Captured stderr
    int exitCode = -1;          ///< Process exit code (-1 if not started)
    bool timedOut = false;      ///< True if process was killed due to timeout
};

/// Cross-platform process execution utility.
///
/// Provides static methods to run shell commands and capture their output,
/// with support for timeouts, output capping, working directory override,
/// and environment variable injection.
///
/// @note NOT fully thread-safe when `cwd` or `env` parameters are used.
/// Working directory (chdir) and environment variables (setenv) are
/// process-wide on both POSIX and Windows. Concurrent calls with
/// different cwd/env values will interfere. Safe for concurrent use
/// only when cwd and env are both empty (the default).
///
/// Example:
/// @code
///   auto result = gaia::ProcessRunner::run("echo hello", 5000);
///   if (result.exitCode == 0) {
///       std::cout << result.stdout_output;
///   }
/// @endcode
class GAIA_API ProcessRunner {
public:
    /// Run a command and capture output.
    ///
    /// @param command         Shell command string to execute
    /// @param timeoutMs       Timeout in milliseconds (0 = no timeout, default 30000)
    /// @param cwd             Working directory (empty = inherit current)
    /// @param env             Additional environment variables (merged with current)
    /// @param maxOutputBytes  Maximum bytes to capture per stream (default 64 KB)
    /// @return ProcessResult with captured output and exit code
    static ProcessResult run(
        const std::string& command,
        int timeoutMs = 30000,
        const std::string& cwd = "",
        const std::map<std::string, std::string>& env = {},
        size_t maxOutputBytes = 65536
    );

    /// Convenience: run and return stdout only, throw on non-zero exit.
    ///
    /// @param command    Shell command string to execute
    /// @param timeoutMs  Timeout in milliseconds (0 = no timeout, default 30000)
    /// @param cwd        Working directory (empty = inherit current)
    /// @return Captured stdout on success
    /// @throws std::runtime_error on non-zero exit, timeout, or execution failure
    static std::string runOrThrow(
        const std::string& command,
        int timeoutMs = 30000,
        const std::string& cwd = ""
    );
};

} // namespace gaia
