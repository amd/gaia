// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// run_checks: runs a project's tests and reports what they actually did, so an
// answer can be held to a real result rather than the model's word for it.

#pragma once

#include <filesystem>
#include <memory>
#include <string>
#include <vector>

#include <gaia/process.h>
#include <gaia/types.h>
#include <gaia/workspace.h>

namespace gaia::code {

/// What one test run did. A count the runner doesn't print is -1.
struct CheckResult {
    std::string command;
    int exitCode = -1;
    bool timedOut = false;
    int passed = -1;
    int failed = -1;
    int skipped = -1;
    std::vector<std::string> failures;  // failing test names, when the runner names them
    /// The command was the project's detected runner, or reads as a test command; only
    /// such a run can back a claim that the tests pass.
    bool testRun = false;

    /// Ran, exited 0, ran at least one test and none failed.
    bool green() const { return !timedOut && exitCode == 0 && passed > 0 && failed <= 0; }
};

/// The command that runs the tests of the project in `dir`, or "" when no runner is recognised.
std::string detectCheckCommand(const std::filesystem::path& dir);

/// Fill `r`'s counts and failure names from a test runner's output (pytest, unittest, go,
/// cargo, jest/vitest, ctest). Leaves -1 for what the output doesn't state.
void parseCheckOutput(const std::string& output, CheckResult& r);

/// Whether a shell command runs a test suite, for a test run made through bash_execute.
bool looksLikeTestCommand(const std::string& command);

/// The shell's folder as a native path: Git Bash on Windows reports /c/Users/....
std::string nativeFolder(const std::string& cwd);

/// The run_checks tool. `session` is the agent's shell (its cwd and environment); `ws`
/// confines `path` (null = full access).
ToolInfo runChecksTool(std::shared_ptr<ShellSession> session, std::shared_ptr<const Workspace> ws);

}  // namespace gaia::code
