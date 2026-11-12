// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/json_utils.h"

#include <algorithm>
#include <cstring>
#include <regex>

namespace gaia {

// Shared patterns used by multiple functions — defined once at namespace scope.
static const std::regex kTrailingCommaObj(R"(,\s*\})");
static const std::regex kTrailingCommaArr(R"(,\s*\])");

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
    fixed = std::regex_replace(fixed, kTrailingCommaObj, "}");
    fixed = std::regex_replace(fixed, kTrailingCommaArr, "]");

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
    static const std::vector<std::regex> patterns = {
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
                    if (result.contains("tool") && (!result.contains("tool_args") || result["tool_args"].is_null())) {
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
        std::string fixed = std::regex_replace(extracted, kTrailingCommaObj, "}");
        fixed = std::regex_replace(fixed, kTrailingCommaArr, "]");
        try {
            json result = json::parse(fixed);
            if (result.is_object()) {
                if (result.contains("tool") && (!result.contains("tool_args") || result["tool_args"].is_null())) {
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
        static const std::regex codeBlock(R"(```(?:json)?\s*(\{[\s\S]*?\})\s*```)");
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
        if (!result.contains("thought") || !result.contains("tool_args") || result["tool_args"].is_null()) {
            // Auto-fill tool_args if missing or null
            if (!result.contains("tool_args") || result["tool_args"].is_null()) {
                result["tool_args"] = json::object();
            }
            if (!result.contains("thought")) {
                throw std::runtime_error("Response is missing required field: thought");
            }
        }
    }

    return result;
}

namespace {
// A tool call that ends the reply, and the prose before it. Only an object that
// is the last thing in the reply and holds nothing but tool-call keys counts,
// so an answer that merely quotes one as an example stays an answer.
std::optional<std::pair<json, std::string>> trailingToolCall(const std::string& reply) {
    static const char* const kSpace = " \t\r\n";
    std::string body = reply;
    auto rtrim = [&body] {
        const auto last = body.find_last_not_of(kSpace);
        body.erase(last == std::string::npos ? 0 : last + 1);
    };
    if (body.size() >= 3 && body.compare(body.size() - 3, 3, "```") == 0) {
        body.erase(body.size() - 3);
        rtrim();
    }
    if (body.empty() || body.back() != '}') return std::nullopt;

    for (size_t at = body.find('{'); at != std::string::npos; at = body.find('{', at + 1)) {
        const std::string candidate = extractFirstJsonObject(body.substr(at));
        if (candidate.empty() || at + candidate.size() != body.size()) continue;
        json call = json::parse(candidate, nullptr, false);
        if (!call.is_object() || !call.contains("tool") || !call["tool"].is_string()) return std::nullopt;
        if (call.contains("tool_args") && !call["tool_args"].is_object()) return std::nullopt;
        for (const auto& [key, value] : call.items()) {
            if (key != "tool" && key != "tool_args" && key != "thought" && key != "goal") return std::nullopt;
        }
        std::string prose = body.substr(0, at);
        const auto fence = prose.rfind("```");
        if (fence != std::string::npos && prose.find_first_not_of(kSpace, fence + 3) == std::string::npos) {
            prose.erase(fence);
        } else if (fence != std::string::npos && prose.compare(fence, 7, "```json") == 0 &&
                   prose.find_first_not_of(kSpace, fence + 7) == std::string::npos) {
            prose.erase(fence);
        }
        const auto last = prose.find_last_not_of(kSpace);
        prose.erase(last == std::string::npos ? 0 : last + 1);
        return std::make_pair(std::move(call), std::move(prose));
    }
    return std::nullopt;
}

struct TagToolCall {
    std::string name;
    json args = json::object();
    std::string prose;
    bool textArgs = false;
    std::string problem;  // set when the call is there but cannot be run
};

bool startsWithAt(const std::string& text, size_t at, const char* what) {
    return text.compare(at, std::strlen(what), what) == 0;
}

size_t skipSpace(const std::string& text, size_t at) {
    const size_t next = text.find_first_not_of(" \t\r\n", at);
    return next == std::string::npos ? text.size() : next;
}

bool insideCodeFence(const std::string& text, size_t at) {
    size_t fences = 0;
    for (size_t p = text.find("```"); p != std::string::npos && p < at; p = text.find("```", p + 3)) ++fences;
    return fences % 2 == 1;
}

std::string trimmedCopy(const std::string& text) {
    const size_t first = text.find_first_not_of(" \t\r\n");
    if (first == std::string::npos) return "";
    return text.substr(first, text.find_last_not_of(" \t\r\n") - first + 1);
}

// <function=name><parameter=key>value</parameter>...</function>, from its "<function=".
void parseFunctionTags(const std::string& text, size_t at, TagToolCall& call) {
    call.textArgs = true;
    const size_t nameEnd = text.find('>', at);
    if (nameEnd == std::string::npos) {
        call.problem = "The tool call was cut off inside its <function=...> tag.";
        return;
    }
    call.name = trimmedCopy(text.substr(at + 10, nameEnd - at - 10));
    if (call.name.empty()) {
        call.problem = "The <function=...> tag has no tool name.";
        return;
    }
    for (size_t pos = skipSpace(text, nameEnd + 1);; pos = skipSpace(text, pos)) {
        if (startsWithAt(text, pos, "</function>") || startsWithAt(text, pos, "</tool_call>")) return;
        if (!startsWithAt(text, pos, "<parameter=")) {
            call.problem = pos >= text.size()
                               ? "The tool call '" + call.name + "' was cut off before </function>."
                               : "The tool call '" + call.name + "' has text between its tags that is not a parameter.";
            return;
        }
        const size_t keyEnd = text.find('>', pos);
        const size_t close = keyEnd == std::string::npos ? keyEnd : text.find("</parameter>", keyEnd + 1);
        if (close == std::string::npos) {
            call.problem = "A parameter of the tool call '" + call.name + "' was cut off before </parameter>.";
            return;
        }
        std::string value = text.substr(keyEnd + 1, close - keyEnd - 1);
        if (!value.empty() && value.front() == '\n') value.erase(0, 1);
        if (!value.empty() && value.back() == '\n') value.pop_back();
        call.args[trimmedCopy(text.substr(pos + 11, keyEnd - pos - 11))] = std::move(value);
        pos = close + 12;
    }
}

// <tool_call>{"name": ..., "arguments": {...}}</tool_call>, from its "{".
void parseJsonToolCall(const std::string& text, size_t at, TagToolCall& call) {
    const std::string body = extractFirstJsonObject(text.substr(at));
    const json payload = body.empty() ? json() : json::parse(body, nullptr, false);
    if (!payload.is_object()) {
        call.problem = "The <tool_call> block was cut off or is not valid JSON.";
        return;
    }
    if (!payload.contains("name") || !payload["name"].is_string() || payload["name"].get<std::string>().empty()) {
        call.problem = "The <tool_call> block has no tool name.";
        return;
    }
    call.name = payload["name"].get<std::string>();
    json args = payload.contains("arguments") ? payload["arguments"] : payload.value("parameters", json::object());
    if (args.is_string()) args = json::parse(args.get<std::string>(), nullptr, false);
    if (!args.is_object()) {
        call.problem = "The arguments of the tool call '" + call.name + "' are not a JSON object.";
        return;
    }
    call.args = std::move(args);
}

// A tool call a model wrote as tags instead of sending it as one. "<tool_call>"
// followed by words is prose about the format, and so is anything in a code fence.
std::optional<TagToolCall> tagToolCall(const std::string& reply) {
    for (size_t at = reply.find('<'); at != std::string::npos; at = reply.find('<', at + 1)) {
        size_t body = std::string::npos;
        if (startsWithAt(reply, at, "<tool_call>")) {
            const size_t next = skipSpace(reply, at + 11);
            if (next < reply.size() && (reply[next] == '{' || startsWithAt(reply, next, "<function="))) body = next;
        } else if (startsWithAt(reply, at, "<function=")) {
            const size_t nameEnd = reply.find('>', at);
            const size_t next = nameEnd == std::string::npos ? nameEnd : skipSpace(reply, nameEnd + 1);
            if (next != std::string::npos &&
                (startsWithAt(reply, next, "<parameter=") || startsWithAt(reply, next, "</function>"))) {
                body = at;
            }
        }
        if (body == std::string::npos || insideCodeFence(reply, at)) continue;
        TagToolCall call;
        call.prose = trimmedCopy(reply.substr(0, at));
        if (reply[body] == '{') parseJsonToolCall(reply, body, call);
        else parseFunctionTags(reply, body, call);
        return call;
    }
    return std::nullopt;
}
}  // namespace

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
        if (auto call = tagToolCall(trimmed)) {
            parsed.thought = call->prose;
            if (!call->problem.empty()) {
                parsed.malformedToolCall = call->problem;
                return parsed;
            }
            parsed.toolName = call->name;
            parsed.toolArgs = call->args;
            parsed.toolArgsAreText = call->textArgs;
            return parsed;
        }
        // Models often say what they are about to do, then emit the call.
        if (auto call = trailingToolCall(trimmed)) {
            parsed.thought = call->second;
            parsed.toolName = call->first.at("tool").get<std::string>();
            parsed.toolArgs = call->first.value("tool_args", json::object());
            return parsed;
        }
        parsed.thought = "";
        parsed.goal = "";
        parsed.answer = trimmed;
        return parsed;
    }

    // Coerce a JSON value to string (handles number/bool answers from LLMs)
    auto jsonToString = [](const json& v) -> std::string {
        if (v.is_string()) return v.get<std::string>();
        return v.dump();
    };

    // Try direct JSON parsing
    try {
        json j = json::parse(trimmed);
        if (j.is_object()) {
            parsed.thought = j.value("thought", "");
            parsed.goal = j.value("goal", "");

            if (j.contains("answer")) {
                parsed.answer = jsonToString(j["answer"]);
            }
            if (j.contains("tool") && j["tool"].is_string()) {
                parsed.toolName = j["tool"].get<std::string>();
                parsed.toolArgs = (j.contains("tool_args") && j["tool_args"].is_object())
                    ? j["tool_args"] : json::object();
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

        if (j.contains("answer") && !j["answer"].is_null()) {
            parsed.answer = jsonToString(j["answer"]);
        }
        if (j.contains("tool") && j["tool"].is_string()) {
            parsed.toolName = j["tool"].get<std::string>();
            parsed.toolArgs = (j.contains("tool_args") && j["tool_args"].is_object())
                ? j["tool_args"] : json::object();
        }
        if (j.contains("plan")) {
            parsed.plan = j["plan"];
        }
        return parsed;
    }

    // Regex-based extraction as last resort
    static const std::regex thoughtRe(R"re("thought"\s*:\s*"([^"]*)")re");
    static const std::regex toolRe(R"re("tool"\s*:\s*"([^"]*)")re");
    static const std::regex answerRe(R"re("answer"\s*:\s*"([^"]*)")re");
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
        static const std::regex argsRe(R"re("tool_args"\s*:\s*)re");
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
