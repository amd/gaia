// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Common types for the GAIA C++ agent framework.
// Ported from Python: src/gaia/agents/base/agent.py, tools.py

#pragma once

#include <functional>
#include <map>
#include <optional>
#include <string>
#include <variant>
#include <vector>

#include <nlohmann/json.hpp>

namespace gaia {

using json = nlohmann::json;

// ---- Agent States ----
// Mirrors Python Agent.STATE_* constants

enum class AgentState {
    PLANNING,
    EXECUTING_PLAN,
    DIRECT_EXECUTION,
    ERROR_RECOVERY,
    COMPLETION
};

inline std::string agentStateToString(AgentState s) {
    switch (s) {
        case AgentState::PLANNING:         return "PLANNING";
        case AgentState::EXECUTING_PLAN:   return "EXECUTING_PLAN";
        case AgentState::DIRECT_EXECUTION: return "DIRECT_EXECUTION";
        case AgentState::ERROR_RECOVERY:   return "ERROR_RECOVERY";
        case AgentState::COMPLETION:       return "COMPLETION";
    }
    return "UNKNOWN";
}

// ---- Message Types ----

enum class MessageRole {
    SYSTEM,
    USER,
    ASSISTANT,
    TOOL
};

inline std::string roleToString(MessageRole r) {
    switch (r) {
        case MessageRole::SYSTEM:    return "system";
        case MessageRole::USER:      return "user";
        case MessageRole::ASSISTANT: return "assistant";
        case MessageRole::TOOL:      return "tool";
    }
    return "unknown";
}

struct Message {
    MessageRole role;
    std::string content;
    std::optional<std::string> name;       // Tool name (for role=TOOL)
    std::optional<std::string> toolCallId; // Tool call ID (for role=TOOL)

    json toJson() const {
        json j;
        j["role"] = roleToString(role);
        j["content"] = content;
        if (name.has_value()) j["name"] = name.value();
        if (toolCallId.has_value()) j["tool_call_id"] = toolCallId.value();
        return j;
    }
};

// ---- Tool Types ----

enum class ToolParamType {
    STRING,
    INTEGER,
    NUMBER,
    BOOLEAN,
    ARRAY,
    OBJECT,
    UNKNOWN
};

inline std::string paramTypeToString(ToolParamType t) {
    switch (t) {
        case ToolParamType::STRING:  return "string";
        case ToolParamType::INTEGER: return "integer";
        case ToolParamType::NUMBER:  return "number";
        case ToolParamType::BOOLEAN: return "boolean";
        case ToolParamType::ARRAY:   return "array";
        case ToolParamType::OBJECT:  return "object";
        case ToolParamType::UNKNOWN: return "unknown";
    }
    return "unknown";
}

struct ToolParameter {
    std::string name;
    ToolParamType type = ToolParamType::UNKNOWN;
    bool required = true;
    std::string description;
};

// Callback type for tool functions.
// Takes JSON arguments, returns JSON result.
using ToolCallback = std::function<json(const json&)>;

struct ToolInfo {
    std::string name;
    std::string description;
    std::vector<ToolParameter> parameters;
    ToolCallback callback;
    bool atomic = false;

    // MCP metadata (populated when tool comes from MCP server)
    std::optional<std::string> mcpServer;
    std::optional<std::string> mcpToolName;
};

// ---- Parsed LLM Response ----

struct ParsedResponse {
    std::string thought;
    std::string goal;

    // Exactly one of these should be set:
    std::optional<std::string> answer;        // Final answer text
    std::optional<std::string> toolName;      // Tool to call
    std::optional<json>        toolArgs;      // Arguments for tool
    std::optional<json>        plan;          // Multi-step plan (array)
};

// ---- Agent Configuration ----

struct AgentConfig {
    std::string baseUrl = "http://localhost:8000/api/v1";
    std::string modelId = "Qwen3-4B-GGUF";
    int maxSteps = 20;
    int maxPlanIterations = 3;
    int maxConsecutiveRepeats = 4;
    int maxHistoryMessages = 40; // Max messages kept between processQuery() calls (0 = unlimited)
    int contextSize = 16384;    // LLM context window size in tokens (n_ctx)
    bool debug = false;
    bool showPrompts = false;
    bool streaming = false;
    bool silentMode = false;
};

} // namespace gaia
