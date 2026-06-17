// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Pre-built file I/O tool callbacks for GAIA agents.
// Provides read, write, edit, and search tools that any agent can register
// to give the LLM file manipulation capabilities.

#pragma once

#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

/// Pre-built file I/O tool callbacks for agents.
/// Each static method returns a ToolInfo ready for ToolRegistry::registerTool().
///
/// Usage:
///   auto& reg = agent.toolRegistry();
///   reg.registerTool(FileIOTools::fileRead());
///   reg.registerTool(FileIOTools::fileWrite());
///   reg.registerTool(FileIOTools::fileEdit());
///   reg.registerTool(FileIOTools::fileSearch());
///
/// Or register all at once:
///   FileIOTools::registerAll(agent.toolRegistry());
class GAIA_API FileIOTools {
public:
    /// Register all file I/O tools with the given registry.
    static void registerAll(ToolRegistry& registry);

    /// file_read: Read file contents with optional line range.
    /// Args: {"path": string, "start_line"?: int, "end_line"?: int}
    /// Returns: {"content": string, "lines": int, "path": string}
    /// On error: {"error": string}
    static ToolInfo fileRead();

    /// file_write: Write content to a file (creates parent dirs).
    /// Args: {"path": string, "content": string}
    /// Returns: {"success": true, "path": string, "bytes_written": int}
    /// On error: {"error": string}
    static ToolInfo fileWrite();

    /// file_edit: Surgical string replacement in a file.
    /// Args: {"path": string, "old_string": string, "new_string": string}
    /// Returns: {"success": true, "path": string, "replacements": int}
    /// On error: {"error": string}
    static ToolInfo fileEdit();

    /// file_search: Search for files by glob pattern and/or content pattern.
    /// Args: {"pattern": string, "path"?: string, "content_pattern"?: string, "max_results"?: int}
    /// Returns: {"matches": [{"path": string, "line"?: int, "context"?: string}], "total": int}
    /// On error: {"error": string}
    static ToolInfo fileSearch();

private:
    // Implementation callbacks
    static json doFileRead(const json& args);
    static json doFileWrite(const json& args);
    static json doFileEdit(const json& args);
    static json doFileSearch(const json& args);

    /// Simple glob-style pattern matching (supports * and ? wildcards).
    static bool matchGlob(const std::string& pattern, const std::string& text);
};

} // namespace gaia
