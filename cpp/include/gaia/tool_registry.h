// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Tool registry for agent tools.
// Ported from Python: src/gaia/agents/base/tools.py
//
// In Python, tools are registered via @tool decorator into a global dict.
// In C++, we use a ToolRegistry class with explicit registerTool() calls.

#pragma once

#include <map>
#include <optional>
#include <string>
#include <vector>

#include "types.h"

namespace gaia {

class ToolRegistry {
public:
    /// Register a tool with the registry.
    /// @param info Complete tool information including callback.
    /// @throws std::runtime_error if a tool with the same name is already registered.
    void registerTool(ToolInfo info);

    /// Register a tool with individual parameters (convenience overload).
    void registerTool(const std::string& name,
                      const std::string& description,
                      ToolCallback callback,
                      std::vector<ToolParameter> params = {},
                      bool atomic = false);

    /// Look up a tool by exact name.
    /// @return Pointer to ToolInfo if found, nullptr otherwise.
    const ToolInfo* findTool(const std::string& name) const;

    /// Resolve an unrecognized tool name to a registered one.
    /// Handles common LLM mistakes (unprefixed MCP names, case-insensitive match).
    /// Mirrors Python Agent._resolve_tool_name().
    /// @return Resolved name, or empty string if no unique match.
    std::string resolveName(const std::string& name) const;

    /// Check if a tool is registered.
    bool hasTool(const std::string& name) const;

    /// Remove a tool from the registry.
    bool removeTool(const std::string& name);

    /// Get all registered tools (ordered by name).
    const std::map<std::string, ToolInfo>& allTools() const;

    /// Get the number of registered tools.
    size_t size() const;

    /// Clear all registered tools.
    void clear();

    /// Format tools as a string for LLM system prompt.
    /// Mirrors Python Agent._format_tools_for_prompt().
    std::string formatForPrompt() const;

    /// Execute a tool by name.
    /// Resolves the name if not found directly.
    /// @return Tool execution result as JSON.
    /// @throws std::runtime_error if tool not found after resolution.
    json executeTool(const std::string& name, const json& args) const;

private:
    std::map<std::string, ToolInfo> tools_;

    /// Convert string to lowercase for case-insensitive matching.
    static std::string toLower(const std::string& s);
};

} // namespace gaia
