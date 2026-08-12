// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/sse_parser.h"

namespace gaia {

SseParser::SseParser(TokenCallback cb) : callback_(std::move(cb)) {}

bool SseParser::feed(const char* data, size_t len) {
    if (done_) return false;

    buffer_.append(data, len);

    // Process all complete lines in the buffer
    size_t pos = 0;
    while (pos < buffer_.size()) {
        const size_t nl = buffer_.find('\n', pos);
        if (nl == std::string::npos) break; // Incomplete line — wait for more data

        // Extract the line, stripping trailing \r if present (\r\n support)
        std::string line = buffer_.substr(pos, nl - pos);
        if (!line.empty() && line.back() == '\r') {
            line.pop_back();
        }

        processLine(line);
        pos = nl + 1;

        if (done_) break;
    }

    // Remove consumed bytes from the buffer
    if (pos > 0) {
        buffer_.erase(0, pos);
    }

    return !done_;
}

void SseParser::processLine(const std::string& line) {
    // Empty lines are SSE event separators — ignore
    if (line.empty()) return;

    // SSE comments start with ':'
    if (line[0] == ':') return;

    // Handle data: lines — strip the prefix and process the payload
    if (line.size() >= 6 && line.substr(0, 6) == "data: ") {
        processData(line.substr(6));
    } else if (line.size() >= 5 && line.substr(0, 5) == "data:") {
        processData(line.substr(5));
    }
    // Ignore event:, id:, retry: lines — not needed for chat completions
}

void SseParser::processData(const std::string& payload) {
    // Trim a single leading space that SSE may add after 'data:'
    const std::string& data = (!payload.empty() && payload[0] == ' ')
                              ? payload.substr(1) : payload;

    // [DONE] sentinel signals end of stream
    if (data == "[DONE]") {
        done_ = true;
        return;
    }

    // Parse the JSON payload and extract the delta content token
    try {
        const json j = json::parse(data);

        if (!j.contains("choices") || !j["choices"].is_array() || j["choices"].empty()) {
            return;
        }

        const auto& choice = j["choices"][0];
        if (!choice.contains("delta")) return;

        const auto& delta = choice["delta"];

        if (delta.contains("tool_calls") && delta["tool_calls"].is_array()) {
            accumulateToolCalls(delta["tool_calls"]);
        }

        if (!delta.contains("content") || delta["content"].is_null()) return;

        const std::string token = delta["content"].get<std::string>();
        if (!token.empty()) {
            hasTokens_ = true;
            callback_(token);
        }
    } catch (...) {
        // Silently skip malformed JSON — servers occasionally send partial events
    }
}

void SseParser::accumulateToolCalls(const json& deltaToolCalls) {
    int positional = 0;
    for (const auto& entry : deltaToolCalls) {
        if (!entry.is_object()) continue;

        // `index` is how parallel calls stay apart across deltas. When a server
        // omits it, fall back to the entry's position within THIS delta —
        // servers that drop `index` send the whole array in one delta, so
        // slotting everything at 0 would merge N calls into one mangled call.
        int index = positional;
        if (entry.contains("index") && entry["index"].is_number_integer()) {
            index = entry["index"].get<int>();
        }
        ++positional;
        ToolCall& tc = toolCallsByIndex_[index];

        // A delta carrying an `id` is a first-or-restated call; one without is a
        // continuation fragment. That distinction is what keeps a repeated
        // `{"id":..,"name":"echo"}` from accumulating into "echoecho" while
        // still reassembling a genuinely split name.
        const bool hasId = entry.contains("id") && entry["id"].is_string() &&
                           !entry["id"].get<std::string>().empty();
        if (tc.id.empty() && hasId) {
            tc.id = entry["id"].get<std::string>();
        }

        if (!entry.contains("function") || !entry["function"].is_object()) continue;
        const auto& fn = entry["function"];

        if (fn.contains("name") && fn["name"].is_string()) {
            if (hasId) {
                tc.name = fn["name"].get<std::string>();   // (re)statement
            } else {
                tc.name += fn["name"].get<std::string>();  // continuation
            }
        }
        if (fn.contains("arguments") && fn["arguments"].is_string()) {
            tc.arguments += fn["arguments"].get<std::string>();
        }
    }
}

std::vector<ToolCall> SseParser::toolCalls() const {
    std::vector<ToolCall> out;
    out.reserve(toolCallsByIndex_.size());
    for (const auto& [index, tc] : toolCallsByIndex_) {
        (void)index;
        out.push_back(tc);
    }
    return out;
}

} // namespace gaia
