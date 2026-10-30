// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Paged tool output: a result too large for the context window is kept whole
// here, and the model sees a head, a tail, and a handle it can page through
// with `read_tool_output`. Mirrors Python's agents/base/artifacts.py and the
// elision in Agent._condense_tool_result.

#pragma once

#include <chrono>
#include <cstddef>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

/// Session-scoped store of full tool results, addressed by an opaque handle.
class GAIA_API ToolOutputStore {
public:
    static constexpr std::size_t kPageChars = 8000;
    static constexpr std::size_t kDefaultReadChars = 2000;
    static constexpr std::size_t kMaxTotalBytes = 64u * 1024u * 1024u;
    static constexpr int kTtlSeconds = 3600;

    /// One indexed part of a stored result: a byte range of the full text.
    struct Entry {
        std::string label;
        std::size_t offset = 0;
        std::size_t length = 0;
    };

    /// Store @p text and return its handle ("output_<hex>").
    std::string put(const std::string& text, std::vector<Entry> entries = {});

    /// Page through a stored result. Mirrors Python's ArtifactStore.read:
    /// `entry` selects one indexed part (1-based) and may not be combined with
    /// `offset`; `limit` caps the returned characters at kPageChars.
    /// @return {artifact, content, offset, next_offset|null, total_chars,
    ///          [remaining], [entry]} or {status:"error", error}.
    json read(const std::string& artifact, int entry, long long offset, long long limit);

    std::size_t size() const;
    void clear();

    /// The `read_tool_output` tool bound to @p store.
    static ToolInfo readTool(ToolOutputStore& store);

private:
    struct Item {
        std::string text;
        std::vector<Entry> entries;
        std::chrono::steady_clock::time_point stored;
    };
    void evictLocked();

    mutable std::mutex mutex_;
    std::map<std::string, Item> items_;
    std::vector<std::string> order_;
    std::size_t totalBytes_ = 0;
    unsigned long long counter_ = 0;
};

/// Character budget for one tool result, proportional to the context window:
/// over `threshold` a result is elided down to about `target` characters.
/// Same ratio as Python's `budget_for_ctx` (30000 chars per 32K tokens).
struct GAIA_API ToolOutputBudget {
    std::size_t threshold;
    std::size_t target;
    static ToolOutputBudget forContext(int contextTokens);
};

/// Elide @p serialized when it exceeds the budget, storing the full text in
/// @p store. Returns the text the model sees: unchanged when it fits, else a
/// JSON object {truncated, original_chars, omitted_chars, head, tail,
/// artifact, continuation, fetch, total_chars}.
GAIA_API std::string condenseToolOutput(const std::string& toolName,
                                        const std::string& serialized,
                                        const ToolOutputBudget& budget,
                                        ToolOutputStore& store);

}  // namespace gaia
