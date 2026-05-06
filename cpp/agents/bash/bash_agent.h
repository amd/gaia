// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// BashAgent — a GAIA agent specialized for bash/shell scripting.
// Combines file I/O, git, and bash execution tools with a system prompt
// tuned for POSIX-correct, shellcheck-clean shell code.

#pragma once

#include <gaia/agent.h>
#include <gaia/file_tools.h>
#include <gaia/git_tools.h>

namespace gaia {

/// Bash coding agent — writes, executes, and debugs shell scripts.
///
/// Registers:
///   - File I/O tools (read, write, edit, search)
///   - Git tools (status, diff, log, show)
///   - bash_execute (run commands with timeout)
///   - env_inspect (detect shell, OS, installed tools)
///
/// System prompt enforces:
///   - POSIX-first coding style
///   - set -euo pipefail in non-trivial scripts
///   - Proper variable quoting
///   - Confirmation for destructive operations
class GAIA_API BashAgent : public Agent {
public:
    explicit BashAgent(const AgentConfig& config = {});

protected:
    void registerTools() override;
    std::string getSystemPrompt() const override;
};

} // namespace gaia
