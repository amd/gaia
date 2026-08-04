// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Cross-platform process execution utility for the GAIA C++ agent framework.
// Replaces the ad-hoc runShell() pattern in example agents with a proper
// library function that handles timeouts, output capping, working directory,
// and environment variables.

#pragma once

#include <map>
#include <memory>
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
/// only when cwd and env are both empty (the default). Use ShellSession
/// below when you need either: it applies both inside the child shell and
/// so never mutates the calling process.
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

/// A shell whose working directory and environment survive between commands.
///
/// `ProcessRunner::run()` is one-shot: `cd build` in one call is invisible to
/// the next, and so is `export CC=clang`. That is materially worse than a
/// human terminal for the build/test loop an agent spends most of its time in,
/// and no system prompt can fix it — the state simply is not there to observe.
/// A ShellSession keeps it.
///
/// The security model is unchanged. This class only preserves state; it does
/// not decide what may run. The `bash_execute` tool that uses it keeps its
/// `ToolPolicy::CONFIRM` policy and its output cap, both of which apply per
/// command exactly as before.
///
/// Thread-safety, and why this is better than ProcessRunner::run(cwd, env):
/// `run()` implements cwd and env with process-wide `chdir`/`setenv`, so two
/// concurrent calls with different values corrupt each other. A ShellSession
/// never mutates the parent process — the working directory and the variables
/// are applied inside the child shell — so distinct sessions are safe to use
/// concurrently. Calls on the *same* session are serialized by an internal
/// mutex, because they share one logical shell state.
///
/// How the state round-trips: each call runs a small generated script that
/// restores the session's cwd and variables, sources the command from its own
/// file, then writes the resulting `pwd` and environment to a side file. The
/// side file — rather than the command's own output — is what keeps the output
/// cap honest and the captured stdout free of bookkeeping noise. A command
/// that calls `exit` terminates the script before that bookkeeping runs, so
/// cwd and environment changes from that one command are not captured; the
/// session keeps its previous state instead of guessing.
///
/// Windows without a POSIX shell: commands run as a cmd.exe batch script,
/// because keeping `cd` and `set` requires running in the same interpreter and
/// cmd only offers that to a script. `%VAR%` expansion is identical to a
/// prompt, but a `for` loop variable is written `%%i` rather than `%i`, and
/// `%1`-`%9` are the (empty) script arguments rather than literal text. When a
/// bash is present — Git Bash, MSYS, WSL, which `BashTools::detectShell()`
/// prefers — none of this applies and commands run as ordinary shell script.
///
/// Two further properties of the generated script are worth knowing:
///   - stdin is `/dev/null`. An interactive command (`git commit` opening an
///     editor, `sudo`, `npm login`) returns immediately instead of sitting
///     until the timeout, because a tool call has no way to answer it.
///   - On POSIX a timeout kills the command's whole process group, not just
///     the shell — a build or test command spawns children, and leaving them
///     running is what makes a timed-out build loop worse than useless.
///     Windows has no equivalent yet; it would need a Job Object.
///
/// Example:
/// @code
///   gaia::ShellSession shell;
///   shell.run("cd /tmp && export BUILD=release");
///   auto result = shell.run("pwd && echo $BUILD");  // /tmp, release
/// @endcode
class GAIA_API ShellSession {
public:
    /// @param startCwd  Initial working directory (empty = process cwd)
    /// @param shell     Shell to run commands with. Empty means `/bin/sh` on
    ///                  POSIX and cmd.exe on Windows. Naming a POSIX shell on
    ///                  Windows (Git Bash, MSYS, WSL) makes the session
    ///                  generate a shell script for it instead of a batch file.
    explicit ShellSession(const std::string& startCwd = "",
                          const std::string& shell = "");
    ~ShellSession();

    ShellSession(const ShellSession&) = delete;
    ShellSession& operator=(const ShellSession&) = delete;
    ShellSession(ShellSession&&) noexcept;
    ShellSession& operator=(ShellSession&&) noexcept;

    /// Run a command in the session, then absorb any cwd/environment change
    /// it made. Output capping and timeout semantics match ProcessRunner::run.
    ///
    /// @param command         Shell command to execute
    /// @param timeoutMs       Timeout in milliseconds (0 = no timeout)
    /// @param maxOutputBytes  Maximum bytes captured per stream
    ProcessResult run(const std::string& command,
                      int timeoutMs = 30000,
                      size_t maxOutputBytes = 65536);

    /// Current working directory of the session.
    std::string cwd() const;

    /// Set the working directory. Returns false when the path is not an
    /// existing directory (and leaves the session unchanged).
    bool setCwd(const std::string& dir);

    /// Variables the session has diverged from the parent process
    /// environment — what a command exported, minus what was inherited.
    std::map<std::string, std::string> environment() const;

    /// Set a variable for subsequent commands in this session.
    void setEnv(const std::string& name, const std::string& value);

    /// Forget every environment change and return to the starting directory.
    void reset();

    /// Process-wide default session, so a stateless tool callback can still
    /// participate in one continuous shell.
    static ShellSession& shared();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace gaia
