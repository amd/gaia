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
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/security.h"
#include "gaia/types.h"

namespace gaia {

class GAIA_API ToolRegistry {
public:
    /// Register a tool with the registry.
    /// @param info Complete tool information including callback.
    /// @throws std::runtime_error if a tool with the same name is already registered.
    void registerTool(ToolInfo info);

    /// Register a tool with individual parameters (convenience overload).
    /// If policy is omitted, the registry's defaultPolicy() is used.
    void registerTool(const std::string& name,
                      const std::string& description,
                      ToolCallback callback,
                      std::vector<ToolParameter> params = {},
                      bool atomic = false,
                      std::optional<ToolPolicy> policy = std::nullopt);

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
    /// Enforces policy, argument validation, and confirmation before invoking.
    /// @return Tool execution result as JSON.
    json executeTool(const std::string& name, const json& args);

    // ---- Enable / disable ----

    /// Enable or disable a tool by name.
    /// Disabled tools are hidden from formatForPrompt() and rejected in executeTool().
    /// When used through an Agent, call Agent::rebuildSystemPrompt() afterward to
    /// flush the cached system prompt so the change is reflected in the next LLM call.
    /// @return false if the tool is not registered.
    bool setEnabled(const std::string& name, bool enabled);

    /// Return whether a tool is enabled (returns false if not registered).
    bool isEnabled(const std::string& name) const;

    /// Return names of all currently enabled tools (in registration order).
    std::vector<std::string> enabledTools() const;

    // ---- Security configuration ----

    /// Set the confirmation callback used when a tool's policy is CONFIRM.
    /// If no callback is set and policy is CONFIRM, the tool is denied (fail-closed).
    void setConfirmCallback(ToolConfirmCallback cb);

    /// Set the default policy for tools registered without an explicit policy.
    void setDefaultPolicy(ToolPolicy policy);

    /// Get the current default policy.
    ToolPolicy defaultPolicy() const;

    /// Inject a shared AllowedToolsStore.
    /// The store is checked before calling the confirm callback.
    void setAllowedToolsStore(std::shared_ptr<AllowedToolsStore> store);

private:
    std::map<std::string, ToolInfo> tools_;
    ToolConfirmCallback confirmCallback_;
    ToolPolicy defaultPolicy_ = ToolPolicy::ALLOW;
    std::shared_ptr<AllowedToolsStore> allowedStore_;

    /// Convert string to lowercase for case-insensitive matching.
    static std::string toLower(const std::string& s);
};

} // namespace gaia
