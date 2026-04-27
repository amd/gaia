// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Common types for the GAIA C++ agent framework.
// Ported from Python: src/gaia/agents/base/agent.py, tools.py

#pragma once

#include <cstdint>
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

// ---- VLM / Image Content Support ----
//
// Mirrors OpenAI's vision chat completion schema:
//   {"type":"text","text":"..."}
//   {"type":"image_url","image_url":{"url":"data:<mime>;base64,<b64>"}}

/// Maximum image size accepted by Image::fromFile (default 20 MiB).
/// Override by defining GAIA_MAX_IMAGE_BYTES at compile time.
#ifndef GAIA_MAX_IMAGE_BYTES
#define GAIA_MAX_IMAGE_BYTES (20u * 1024u * 1024u)
#endif

/// Detect image MIME type from magic bytes.
/// Supported formats: PNG, JPEG, GIF (87a/89a), WebP (RIFF+WEBP at offset 8), BMP.
/// Returns "image/png" for null pointers or buffers shorter than 12 bytes
/// (safe fallback against OOB access on header stubs — AC-15e).
/// Returns "" (empty string) for full-sized (>= 12 byte) buffers with
/// unrecognized magic. Callers must handle empty by throwing or requiring
/// the caller to supply an explicit mimeType.
std::string detectImageMimeType(const std::uint8_t* data, std::size_t size);

/// A single piece of message content — either text or a base64 data-URI image.
struct ContentPart {
    enum class Kind { TEXT, IMAGE_URL };

    Kind kind = Kind::TEXT;
    std::string text;        // populated when kind == TEXT
    std::string imageUrl;    // populated when kind == IMAGE_URL ("data:...;base64,...")

    json toJson() const;

    static ContentPart makeText(std::string t);
    static ContentPart makeImageUrl(std::string url);
};

/// Image bytes plus a MIME type, carrying everything needed to compose a
/// vision content part. Store is the raw bytes (not base64) — base64 encoding
/// happens lazily in toContentPart().
class Image {
public:
    /// Construct from raw image bytes. MIME type is auto-detected from magic
    /// bytes unless explicitly provided.
    /// Throws std::invalid_argument if bytes are empty, or if an explicit
    /// MIME type is outside the whitelist (image/{png,jpeg,gif,webp,bmp}).
    static Image fromBytes(std::vector<std::uint8_t> bytes,
                           const std::string& mimeType = "");

    /// Load an image from a regular file on disk.
    /// Throws std::runtime_error if the path can't be opened.
    /// Throws std::invalid_argument for: non-regular files (directory/symlink/
    /// FIFO/device), zero-byte files, or files exceeding GAIA_MAX_IMAGE_BYTES.
    static Image fromFile(const std::string& path);

    const std::vector<std::uint8_t>& bytes() const { return bytes_; }
    const std::string& mimeType() const { return mimeType_; }
    std::size_t size() const { return bytes_.size(); }

    /// Produce a ContentPart{IMAGE_URL} with a data:<mime>;base64,<...> URI.
    ContentPart toContentPart() const;

    /// Produce the raw data URI string (same value as toContentPart().imageUrl).
    std::string toDataUri() const;

private:
    Image() = default;
    std::vector<std::uint8_t> bytes_;
    std::string mimeType_;
};

/// RFC 4648 standard-alphabet base64 encoder (with '=' padding).
std::string base64Encode(const std::uint8_t* data, std::size_t size);
inline std::string base64Encode(const std::vector<std::uint8_t>& v) {
    return base64Encode(v.data(), v.size());
}

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

    /// When present, `parts` supersedes `content` on serialization:
    /// toJson() emits content as a JSON array of ContentPart. `content` is
    /// left untouched but ignored. This is additive/source-compatible —
    /// existing aggregate initialization continues to work.
    std::optional<std::vector<ContentPart>> parts;

    /// Serialize to OpenAI-compatible chat message JSON.
    /// Defined out-of-line in types.cpp to avoid ODR hazards for consumers
    /// linked against a prebuilt gaia_core.
    json toJson() const;

    /// Factory: build a user message with optional images. Text is placed
    /// first, followed by image parts in the order supplied. When `text`
    /// is empty and images are provided, the content array contains only
    /// image parts (no empty-text stub). When both are empty, the message
    /// has empty string content.
    static Message fromUser(const std::string& text,
                            const std::vector<Image>& images = {});
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
