// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Persistent memory for the flagship: the five tools Python's MemoryMixin
// registers (remember, recall, update_memory, forget,
// search_past_conversations), the conversation log they search, and the
// dated-notes block the system prompt carries.
//
// Differences from Python, by design:
//   - Recall ranks with FTS5 BM25 only. Python fuses BM25 with embeddings;
//     the C++ runtime has no embeddings call yet (#2791).
//   - The store is its own file (defaultPath()), never Python's memory.db, so
//     neither runtime migrates the other's schema. Column names match Python's.
// Credentials are never written: remember/update_memory refuse them, the
// conversation log masks them, and recall redacts any that slipped in.

#pragma once

#include <memory>
#include <mutex>
#include <string>

#include "gaia/database.h"
#include "gaia/rag.h"
#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

class ToolRegistry;

class GAIA_API MemoryStore {
public:
    /// Open (creating if needed) the store at @p dbPath; ":memory:" for tests.
    explicit MemoryStore(const std::string& dbPath);

    /// GAIA_NATIVE_MEMORY_DB, else <gaiaHomeDir()>/native/memory.db.
    static std::string defaultPath();

    /// Rank recall by meaning as well as keywords (Python's hybrid search).
    /// Unset, or when it fails, recall ranks by keywords and says so.
    void setEmbedder(Embedder embedder) { embed_ = std::move(embedder); }

    /// Up to @p k memories whose meaning is close to @p query (cosine >=
    /// @p minScore), for the turn's context. Empty when there is no embedder.
    std::vector<std::string> relevantTo(const std::string& query, std::size_t k = 3, double minScore = 0.5);

    /// GAIA_MEMORY_DISABLED, the same switch Python honors.
    static bool disabledByEnv();

    // Tool bodies. Arguments and results follow the Python tools' shapes.
    json remember(const json& args);
    json recall(const json& args);
    json updateMemory(const json& args);
    json forget(const json& args);
    json searchPastConversations(const json& args);

    /// Record one chat turn (credentials masked) for search_past_conversations.
    void logTurn(const std::string& sessionId, const std::string& role, const std::string& content);

    /// The memory section of the system prompt: how to use the tools, then
    /// stored items as dated notes, newest first, capped at 4,000 characters.
    std::string promptBlock();

    /// The TUI's /memory snapshot, in the Python flagship's build_memory_dump()
    /// shape: {available, stats, contexts, shown, total, items}.
    json dump(int limit = 300);

    /// Register the five tools, bound to this store.
    void registerTools(ToolRegistry& registry);

private:
    // Vectors for the rows that pass @p where (bound to @p binds), embedding any that lack one.
    std::vector<std::pair<std::string, std::vector<float>>> vectorsLocked(const std::string& where,
                                                                          const std::vector<std::string>& binds);

    std::mutex mutex_;
    Database db_;
    Embedder embed_;
};

}  // namespace gaia
