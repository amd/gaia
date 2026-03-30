// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Common types for the GAIA C++ agent framework.
// Ported from Python: src/gaia/agents/base/agent.py, tools.py

#pragma once

#include <cstdlib>
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

// Cross-platform environment variable helper.
// On MSVC uses _dupenv_s (safe); on GCC/Clang (including MinGW) uses std::getenv.
inline std::string getEnvVar(const char* name, const std::string& defaultValue = "") {
#ifdef _MSC_VER
    char* value = nullptr;
    size_t len = 0;
    if (_dupenv_s(&value, &len, name) == 0 && value) {
        std::string result(value);
        free(value);
        return result;
    }
    return defaultValue;
#else
    const char* value = std::getenv(name);
    return value ? std::string(value) : defaultValue;
#endif
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

// Callback invoked for each token as it arrives during streaming inference.
using StreamCallback = std::function<void(const std::string& token)>;

// ---- Security Types ----

enum class ToolPolicy { ALLOW, CONFIRM, DENY };

enum class ToolConfirmResult { ALLOW_ONCE, ALWAYS_ALLOW, DENY };

// Returns sanitized args or throws std::invalid_argument to reject the call.
using ToolValidateCallback = std::function<json(const std::string& toolName, const json& args)>;

// Returns ALLOW_ONCE, ALWAYS_ALLOW, or DENY.
using ToolConfirmCallback = std::function<ToolConfirmResult(const std::string& toolName, const json& args)>;


struct ToolInfo {
    std::string name;
    std::string description;
    std::vector<ToolParameter> parameters;
    ToolCallback callback;
    bool atomic = false;
    ToolPolicy policy = ToolPolicy::ALLOW;                // default = backwards-compatible
    bool enabled = true;                                  // false = hidden from prompt + rejected on execute
    std::optional<ToolValidateCallback> validateArgs;     // per-tool argument validator

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

/// Return the default streaming setting, honoring the GAIA_STREAMING
/// environment variable if set (1 = enabled, anything else = disabled).
inline bool defaultStreaming() {
    return getEnvVar("GAIA_STREAMING") == "1";
}

/// Return the default LLM base URL, honoring the LEMONADE_BASE_URL
/// environment variable if set (matching the Python CLI behavior).
inline std::string defaultBaseUrl() {
    return getEnvVar("LEMONADE_BASE_URL", "http://localhost:8000/api/v1");
}

// ---- Decision Support ----

/// A user-facing choice presented after an LLM yes/no confirmation prompt.
struct Decision {
    std::string label;       // display text: "Yes", "No"
    std::string value;       // sent to LLM: "yes", "no"
    std::string description; // hint: "Confirm and proceed"
};

struct AgentConfig {
    std::string baseUrl = defaultBaseUrl();
    std::string modelId = "Qwen3-4B-GGUF";
    int maxSteps = 20;
    int maxPlanIterations = 3;
    int maxConsecutiveRepeats = 4;
    int maxHistoryMessages = 40; // Max messages kept between processQuery() calls (0 = unlimited)
    int contextSize = 16384;    // LLM context window size in tokens (n_ctx)
    int maxTokens = 4096;       // Max tokens in LLM response
    bool debug = false;
    bool showPrompts = false;
    bool streaming = defaultStreaming();  // also controlled by GAIA_STREAMING=1
    bool silentMode = false;
    double temperature = 0.7;  // LLM sampling temperature (0.0 = deterministic)

    /// Validate config fields; throws std::invalid_argument on violation.
    void validate() const;

    /// Construct from a JSON object. Missing fields retain defaults.
    /// Throws std::invalid_argument if any field is out of range.
    static AgentConfig fromJson(const json& j);

    /// Load config from a JSON file. All fields are optional.
    /// Throws std::runtime_error on file/parse error, std::invalid_argument on invalid values.
    static AgentConfig fromJsonFile(const std::string& path);

    /// Serialize all fields to JSON (round-trips through fromJson).
    json toJson() const;
};

} // namespace gaia
