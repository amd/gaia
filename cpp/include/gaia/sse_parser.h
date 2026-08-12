// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// SSE (Server-Sent Events) parser for OpenAI-compatible streaming endpoints.
//
// Parses text/event-stream responses from POST /chat/completions with
// "stream": true. Extracts choices[0].delta.content tokens and invokes
// a callback for each non-empty token.
//
// Usage:
//   SseParser parser([](const std::string& token) {
//       std::cout << token << std::flush;
//   });
//   // Feed raw HTTP response chunks as they arrive:
//   bool more = parser.feed(data, len);  // false when [DONE] received

#pragma once

#include <functional>
#include <map>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

using json = nlohmann::json;

/// Stateful SSE parser for OpenAI-compatible streaming chat completions.
///
/// Thread-safety: NOT thread-safe. Use from a single thread only.
class GAIA_API SseParser {
public:
    using TokenCallback = std::function<void(const std::string& token)>;

    /// Construct with a per-token callback.
    explicit SseParser(TokenCallback cb);

    /// Feed raw HTTP response bytes into the parser.
    ///
    /// May invoke the token callback zero or more times per call. Buffers
    /// partial lines across calls so chunks may be split anywhere.
    ///
    /// @param data  Pointer to raw bytes (not NUL-terminated)
    /// @param len   Number of bytes
    /// @return true to continue receiving; false when [DONE] is received
    bool feed(const char* data, size_t len);

    /// Return true if the [DONE] sentinel has been received.
    bool done() const { return done_; }

    /// Return true if at least one token has been emitted via the callback.
    bool hasTokens() const { return hasTokens_; }

    /// Native tool calls accumulated from ``choices[0].delta.tool_calls``.
    ///
    /// Servers fragment these across chunks: the first delta for an index
    /// carries the id and (usually) the function name, and the JSON argument
    /// string then arrives in arbitrary pieces. Entries are returned ordered by
    /// their ``index`` field, which is how parallel calls are distinguished.
    /// Valid only once done() is true — the arguments string is incomplete
    /// before then.
    std::vector<ToolCall> toolCalls() const;

    /// Return true if any tool_calls delta has been seen.
    bool hasToolCalls() const { return !toolCallsByIndex_.empty(); }

private:
    /// Process one complete SSE line (after newline stripping).
    void processLine(const std::string& line);

    /// Handle the payload of a data: SSE line.
    void processData(const std::string& payload);

    /// Merge one ``delta.tool_calls`` array into the accumulator.
    void accumulateToolCalls(const json& deltaToolCalls);

    TokenCallback callback_;
    std::string   buffer_;     // Accumulates partial lines across feed() calls
    bool          done_      = false;
    bool          hasTokens_ = false;

    // Keyed by the delta's ``index`` so parallel calls stay separate and
    // out-of-order chunks land in the right slot. std::map keeps index order.
    std::map<int, ToolCall> toolCallsByIndex_;
};

} // namespace gaia
