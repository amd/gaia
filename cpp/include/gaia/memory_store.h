// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MemoryStore: facts, preferences and notes the agent keeps across sessions,
// in SQLite with FTS5 keyword search and optional vector embeddings (hybrid RRF)
// — the native counterpart of the Python agent's remember / recall /
// update_memory / forget tools.
//
// It uses its own database file (default $GAIA_HOME/memory-cpp.db, else
// ~/.gaia/memory-cpp.db), so the Python store's schema migrations never meet it.

#pragma once

#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "gaia/database.h"
#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

struct GAIA_API MemoryEntry {
    std::string id;
    std::string category;  // fact | preference | error | skill | note | reminder
    std::string content;
    std::string domain;
    std::string entity;
    bool sensitive = false;
    std::string createdAt;
    std::string updatedAt;
};

class GAIA_API MemoryStore {
public:
    /// Minimum cosine similarity for vector-only recall matches (matches Python's semantic floor).
    static constexpr double kMinVectorSimilarity = 0.35;

    /// The default database path for this user.
    static std::string defaultPath();

    /// Opens (creating) the store. ":memory:" gives a throwaway one.
    /// @param embed           Vector embedder, or empty for keyword-only recall.
    /// @param embeddingModel  Model id recorded with stored vectors; changing it clears stale vectors.
    /// @param embeddingNote   Why embeddings are inactive when embed is empty (surfaced in recall).
    explicit MemoryStore(const std::string& path = defaultPath(),
                         EmbedFunction embed = {},
                         std::string embeddingModel = "",
                         std::string embeddingNote = "");

    static const std::vector<std::string>& categories();

    /// Why `content` must not be stored (credential-shaped), or "".
    static std::string refusalFor(const std::string& content);

    /// Stores a fact and returns its id. Throws std::invalid_argument for an
    /// unknown category, empty or credential-shaped content.
    std::string remember(const std::string& content, const std::string& category = "fact",
                         const std::string& domain = "", const std::string& entity = "",
                         bool sensitive = false);

    /// Hybrid (vector + FTS5 RRF) or keyword search when `query` is set, else the newest entries.
    std::vector<MemoryEntry> recall(const std::string& query = "", const std::string& category = "",
                                    int limit = 10, bool includeSensitive = false);

    bool update(const std::string& id, const std::string& content);
    bool forget(const std::string& id);
    size_t count();

    /// Embed any rows currently missing vectors (e.g. migrated or after an embedder switch).
    /// Returns the number of rows updated. No-op (returns 0) when no embedder is configured.
    size_t backfillEmbeddings();

    bool usesEmbeddings() const { return static_cast<bool>(embed_); }
    const std::string& embeddingModel() const { return embeddingModel_; }
    const std::string& embeddingNote() const { return embeddingNote_; }

    /// A short block of stored preferences for the system prompt ("" when none).
    std::string promptBlock(int limit = 10);

    /// remember, recall, update_memory, forget.
    void registerTools(ToolRegistry& registry);

private:
    void reconcileEmbedderLocked();

    std::unique_ptr<Database> db_;
    EmbedFunction embed_;
    std::string embeddingModel_;
    std::string embeddingNote_;
    std::mutex mutex_;
};

} // namespace gaia
