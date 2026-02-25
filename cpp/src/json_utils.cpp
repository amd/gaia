// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/json_utils.h"

#include <algorithm>
#include <regex>

namespace gaia {

std::string extractFirstJsonObject(const std::string& text) {
    auto startIdx = text.find('{');
    if (startIdx == std::string::npos) {
        return "";
    }

    int bracketCount = 0;
    bool inString = false;
    bool escapeNext = false;

    for (size_t i = startIdx; i < text.size(); ++i) {
        char c = text[i];

        if (escapeNext) {
            escapeNext = false;
            continue;
        }
        if (c == '\\') {
            escapeNext = true;
            continue;
        }
        if (c == '"' && !escapeNext) {
            inString = !inString;
        }
        if (!inString) {
            if (c == '{') {
                ++bracketCount;
            } else if (c == '}') {
                --bracketCount;
                if (bracketCount == 0) {
                    return text.substr(startIdx, i - startIdx + 1);
                }
            }
        }
    }

    return "";
}

std::string fixCommonJsonErrors(const std::string& text) {
    std::string fixed = text;

    // Remove trailing commas before } or ]
    fixed = std::regex_replace(fixed, std::regex(R"(,\s*\})"), "}");
    fixed = std::regex_replace(fixed, std::regex(R"(,\s*\])"), "]");

    // Fix single quotes to double quotes (only if no double quotes present)
    if (fixed.find('"') == std::string::npos && fixed.find('\'') != std::string::npos) {
        std::replace(fixed.begin(), fixed.end(), '\'', '"');
    }

    // Remove text before first '{' or '['
    auto bracePos = fixed.find('{');
    auto bracketPos = fixed.find('[');
    size_t startPos = std::string::npos;

    if (bracePos != std::string::npos && bracketPos != std::string::npos) {
        startPos = std::min(bracePos, bracketPos);
    } else if (bracePos != std::string::npos) {
        startPos = bracePos;
    } else if (bracketPos != std::string::npos) {
        startPos = bracketPos;
    }

    if (startPos != std::string::npos && startPos > 0) {
        fixed = fixed.substr(startPos);
    }

    return fixed;
}

std::optional<json> extractJsonFromResponse(const std::string& response) {
    // Strategy 1: Extract from code blocks
    std::vector<std::regex> patterns = {
        std::regex(R"(```(?:json)?\s*([\s\S]*?)\s*```)"),  // Standard code block
        std::regex(R"(`json\s*([\s\S]*?)\s*`)"),            // Single backtick with json tag
        std::regex(R"(<json>\s*([\s\S]*?)\s*</json>)"),     // XML-style tags
    };

    for (const auto& pattern : patterns) {
        std::sregex_iterator begin(response.begin(), response.end(), pattern);
        std::sregex_iterator end;

        for (auto it = begin; it != end; ++it) {
            std::string match = (*it)[1].str();
            try {
                json result = json::parse(match);
                if (result.is_object()) {
                    // Ensure tool_args exists if tool is present
                    if (result.contains("tool") && !result.contains("tool_args")) {
                        result["tool_args"] = json::object();
                    }
                    return result;
                }
            } catch (const json::parse_error&) {
                continue;
            }
        }
    }

    // Strategy 2: Bracket-matching
    std::string extracted = extractFirstJsonObject(response);
    if (!extracted.empty()) {
        // Fix common issues before parsing
        std::string fixed = std::regex_replace(extracted, std::regex(R"(,\s*\})"), "}");
        fixed = std::regex_replace(fixed, std::regex(R"(,\s*\])"), "]");
        try {
            json result = json::parse(fixed);
            if (result.is_object()) {
                if (result.contains("tool") && !result.contains("tool_args")) {
                    result["tool_args"] = json::object();
                }
                return result;
            }
        } catch (const json::parse_error&) {
            // Fall through
        }
    }

    return std::nullopt;
}

json validateJsonResponse(const std::string& responseText) {
    json result;
    bool modified = false;

    // Step 1: Parse as-is
    try {
        result = json::parse(responseText);
        // Success - skip modification steps
    } catch (const json::parse_error& initialError) {
        // Step 2: Try extracting from code blocks
        std::regex codeBlock(R"(```(?:json)?\s*(\{[\s\S]*?\})\s*```)");
        std::smatch match;
        if (std::regex_search(responseText, match, codeBlock)) {
            try {
                result = json::parse(match[1].str());
                modified = true;
            } catch (const json::parse_error&) {
                // Continue to next strategy
            }
        }

        // Step 3: Bracket-matching
        if (!modified) {
            std::string extracted = extractFirstJsonObject(responseText);
            if (!extracted.empty()) {
                try {
                    result = json::parse(extracted);
                    modified = true;
                } catch (const json::parse_error&) {
                    // Continue
                }
            }
        }

        // Step 4: Fix common errors
        if (!modified) {
            std::string fixed = fixCommonJsonErrors(responseText);
            if (fixed != responseText) {
                try {
                    result = json::parse(fixed);
                    modified = true;
                } catch (const json::parse_error&) {
                    // Give up
                }
            }
        }

        if (!modified) {
            throw std::runtime_error(
                std::string("Failed to parse response as JSON: ") + initialError.what());
        }
    }

    // Validate required fields
    if (result.contains("answer")) {
        if (!result.contains("thought")) {
            throw std::runtime_error("Response is missing required field: thought");
        }
    } else if (result.contains("tool")) {
        if (!result.contains("thought") || !result.contains("tool_args")) {
            // Auto-fill tool_args if missing
            if (!result.contains("tool_args")) {
                result["tool_args"] = json::object();
            }
            if (!result.contains("thought")) {
                throw std::runtime_error("Response is missing required field: thought");
            }
        }
    }

    return result;
}

ParsedResponse parseLlmResponse(const std::string& response) {
    ParsedResponse parsed;

    // Handle empty response
    if (response.empty() || response.find_first_not_of(" \t\n\r") == std::string::npos) {
        parsed.thought = "LLM returned empty response";
        parsed.goal = "Handle empty response error";
        parsed.answer = "I apologize, but I received an empty response from the language model. Please try again.";
        return parsed;
    }

    std::string trimmed = response;
    // Trim whitespace
    auto start = trimmed.find_first_not_of(" \t\n\r");
    auto end = trimmed.find_last_not_of(" \t\n\r");
    if (start != std::string::npos) {
        trimmed = trimmed.substr(start, end - start + 1);
    }

    // Fast path: plain text (doesn't start with '{')
    if (trimmed.empty() || trimmed[0] != '{') {
        parsed.thought = "";
        parsed.goal = "";
        parsed.answer = trimmed;
        return parsed;
    }

    // Try direct JSON parsing
    try {
        json j = json::parse(trimmed);
        if (j.is_object()) {
            parsed.thought = j.value("thought", "");
            parsed.goal = j.value("goal", "");

            if (j.contains("answer")) {
                parsed.answer = j["answer"].is_string()
                    ? j["answer"].get<std::string>()
                    : j["answer"].dump();
            }
            if (j.contains("tool")) {
                parsed.toolName = j["tool"].get<std::string>();
                parsed.toolArgs = j.value("tool_args", json::object());
            }
            if (j.contains("plan")) {
                parsed.plan = j["plan"];
            }
            // Ensure tool_args exists if tool is present
            if (parsed.toolName.has_value() && !parsed.toolArgs.has_value()) {
                parsed.toolArgs = json::object();
            }
            return parsed;
        }
    } catch (const json::parse_error&) {
        // Malformed JSON - try extraction
    }

    // Try JSON extraction methods
    auto extracted = extractJsonFromResponse(trimmed);
    if (extracted.has_value()) {
        const json& j = extracted.value();
        parsed.thought = j.value("thought", "");
        parsed.goal = j.value("goal", "");

        if (j.contains("answer")) {
            parsed.answer = j["answer"].is_string()
                    ? j["answer"].get<std::string>()
                    : j["answer"].dump();
        }
        if (j.contains("tool")) {
            parsed.toolName = j["tool"].get<std::string>();
            parsed.toolArgs = j.value("tool_args", json::object());
        }
        if (j.contains("plan")) {
            parsed.plan = j["plan"];
        }
        return parsed;
    }

    // Regex-based extraction as last resort
    std::regex thoughtRe(R"re("thought"\s*:\s*"([^"]*)")re");
    std::regex toolRe(R"re("tool"\s*:\s*"([^"]*)")re");
    std::regex answerRe(R"re("answer"\s*:\s*"([^"]*)")re");
    std::smatch match;

    if (std::regex_search(trimmed, match, answerRe)) {
        parsed.answer = match[1].str();
        if (std::regex_search(trimmed, match, thoughtRe)) {
            parsed.thought = match[1].str();
        }
        return parsed;
    }

    if (std::regex_search(trimmed, match, toolRe)) {
        parsed.toolName = match[1].str();
        parsed.toolArgs = json::object();
        if (std::regex_search(trimmed, match, thoughtRe)) {
            parsed.thought = match[1].str();
        }

        // Try to extract tool_args
        std::regex argsRe(R"re("tool_args"\s*:\s*)re");
        if (std::regex_search(trimmed, match, argsRe)) {
            size_t argsStart = static_cast<size_t>(match.position() + match.length());
            std::string remaining = trimmed.substr(argsStart);
            std::string argsJson = extractFirstJsonObject(remaining);
            if (!argsJson.empty()) {
                try {
                    parsed.toolArgs = json::parse(argsJson);
                } catch (const json::parse_error&) {
                    // Keep empty object
                }
            }
        }
        return parsed;
    }

    // No JSON found — treat as conversational response
    parsed.thought = "";
    parsed.goal = "";
    parsed.answer = trimmed;
    return parsed;
}

} // namespace gaia
