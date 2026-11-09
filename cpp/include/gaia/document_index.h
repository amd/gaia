// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// DocumentIndex: index documents into page-tagged chunks and retrieve the ones
// that answer a question — the native counterpart of the Python RAG SDK's
// index_document / query_documents.
//
// Retrieval is hybrid: BM25 keyword ranking, fused (reciprocal rank fusion)
// with vector ranking when an embedding model is configured. Without one it is
// keyword-only, and every result says which mode produced it.
//
// PDFs are read with `pdftotext -layout` (poppler), whose form feeds give page
// numbers; .docx with the zip's document.xml. Other formats go through
// gaia::extractFile (plain text, Markdown, source) or registerExtractor().

#pragma once

#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "gaia/chunking.h"
#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"
#include "gaia/vector_index.h"

namespace gaia {

/// Turns text into vectors; throws on failure. Typically wraps
/// LemonadeClient::embeddings for the configured embedding model.
using EmbedFunction = std::function<std::vector<std::vector<float>>(const std::vector<std::string>&)>;

struct GAIA_API DocumentChunk {
    std::string file;
    int page = 0;  // 1-based; 0 when the format has no pages
    std::string text;
};

struct GAIA_API DocumentHit {
    DocumentChunk chunk;
    double score = 0;  // fused reciprocal-rank score, higher is better
};

class GAIA_API DocumentIndex {
public:
    /// @param embed  Vector embedder, or empty for keyword-only retrieval.
    /// @param embeddingModel  Name recorded with the vectors (reported by tools).
    explicit DocumentIndex(EmbedFunction embed = {}, std::string embeddingModel = "",
                           ChunkingConfig chunking = {});

    /// Split a file into page-tagged text. Throws with the reason a file can't be read.
    static std::vector<std::pair<int, std::string>> extractPages(const std::string& path);

    /// Index (or re-index) a file. Returns the number of chunks added.
    size_t indexFile(const std::string& path);

    /// Top `k` chunks for `query`, optionally only from files whose path contains `fileFilter`.
    std::vector<DocumentHit> query(const std::string& query, size_t k = 5,
                                   const std::string& fileFilter = "") const;

    /// Indexed files and their chunk counts.
    std::map<std::string, size_t> files() const;

    bool usesEmbeddings() const { return static_cast<bool>(embed_); }
    const std::string& embeddingModel() const { return embeddingModel_; }

    /// index_document, query_documents, list_indexed_documents. `checkPath`
    /// (may be empty) returns a refusal for paths the agent may not read.
    void registerTools(ToolRegistry& registry,
                       std::function<std::string(const std::string&)> checkPath = {});

private:
    void removeFile(const std::string& file);
    std::vector<std::string> tokens(const std::string& text) const;

    EmbedFunction embed_;
    std::string embeddingModel_;
    ChunkingConfig chunking_;

    mutable std::mutex mutex_;
    std::vector<DocumentChunk> chunks_;
    std::vector<std::map<std::string, int>> termFreq_;
    std::vector<size_t> lengths_;
    std::map<std::string, int> docFreq_;
    std::unique_ptr<VectorIndex> vectors_;
};

} // namespace gaia
