// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Pre-built read-only git tool callbacks for GAIA agents.
// Provides status, diff, log, and show tools that any agent can register
// to give the LLM git inspection capabilities.

#pragma once

#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

/// Pre-built read-only git tool callbacks for agents.
/// All tools use ALLOW policy (read-only operations).
///
/// Each static method returns a ToolInfo ready for ToolRegistry::registerTool().
///
/// Usage:
///   auto& reg = agent.toolRegistry();
///   reg.registerTool(GitTools::gitStatus());
///   reg.registerTool(GitTools::gitDiff());
///   reg.registerTool(GitTools::gitLog());
///   reg.registerTool(GitTools::gitShow());
///
/// Or register all at once:
///   GitTools::registerAll(agent.toolRegistry());
class GAIA_API GitTools {
public:
    /// Register all git tools with the given registry.
    static void registerAll(ToolRegistry& registry);

    /// git_status: Get working tree status.
    /// Args: {} (no args)
    /// Returns: {"status": string, "clean": bool}
    /// On error: {"error": string}
    static ToolInfo gitStatus();

    /// git_diff: Show changes in working tree or between refs.
    /// Args: {"path"?: string, "staged"?: bool, "ref"?: string}
    /// Returns: {"diff": string, "files_changed": int}
    /// On error: {"error": string}
    static ToolInfo gitDiff();

    /// git_log: Show recent commit history.
    /// Args: {"count"?: int (default 10), "oneline"?: bool (default true), "path"?: string}
    /// Returns: {"log": string, "commits": int}
    /// On error: {"error": string}
    static ToolInfo gitLog();

    /// git_show: Show a specific commit or object.
    /// Args: {"ref": string (default "HEAD")}
    /// Returns: {"content": string, "ref": string}
    /// On error: {"error": string}
    static ToolInfo gitShow();

private:
    // Implementation callbacks
    static json doGitStatus(const json& args);
    static json doGitDiff(const json& args);
    static json doGitLog(const json& args);
    static json doGitShow(const json& args);
};

} // namespace gaia
