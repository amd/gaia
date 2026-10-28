// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Document Q&A over the user's files: index_document, query_documents,
// query_specific_file, list_indexed_documents and rag_status, the tools the
// Python flagship's RAG mixin registers, with the same result keys (chunks with
// source_file, page and relevance_score). Retrieval is by embedding similarity;
// when the embedder is unavailable the tools say so instead of guessing.

#pragma once

#include <functional>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

class ToolRegistry;
class VectorIndex;

/// Texts → vectors, in order. Throws std::runtime_error when it cannot embed.
using Embedder = std::function<std::vector<std::vector<float>>(const std::vector<std::string>&)>;

/// One vision-model call: (prompt, images as raw bytes) → reply. Throws on failure.
using VisionReader = std::function<std::string(const std::string& prompt, const std::vector<std::string>& images)>;

/// Prompt used to read a scanned page.
inline constexpr const char* kTranscribePagePrompt =
    "Transcribe all text on this page exactly as written, in reading order. Keep numbers, names, "
    "dates and table rows exact. Output only the text.";

/// One model call with no tools: (instruction, text) → reply. Throws on failure.
using Summarizer = std::function<std::string(const std::string& instruction, const std::string& text)>;

class GAIA_API DocumentIndex {
public:
    /// Python's RAG defaults: 500-token chunks with 100 tokens of overlap
    /// (~4 characters a token), capped to what the embedder takes whole.
    static constexpr std::size_t kChunkChars = 2000;
    static constexpr std::size_t kOverlapChars = 400;
    static constexpr std::size_t kTopK = 5;

    explicit DocumentIndex(Embedder embedder);
    ~DocumentIndex();

    /// @p resolve maps a model-supplied path to an allowed absolute path, or ""
    /// when it is outside the session's folders.
    void setPathResolver(std::function<std::string(const std::string&)> resolve) { resolve_ = std::move(resolve); }
    /// Why a resolved file must not be read ("" when it may be).
    void setRefusal(std::function<std::string(const std::string&)> refuse) { refuse_ = std::move(refuse); }

    /// Reads scanned PDF pages while indexing. Unset, such pages are reported as unread.
    void setVision(VisionReader vision) { vision_ = std::move(vision); }

    /// summarize_document's model call. Unset, summarize_document says it is unavailable.
    void setSummarizer(Summarizer summarize) { summarize_ = std::move(summarize); }

    json indexDocument(const std::string& filePath);
    json indexDirectory(const std::string& directory, bool recursive);
    json searchChunks(const std::string& pattern) const;
    json summarizeDocument(const std::string& filePath, const std::string& summaryType, long long maxWordsPerSection);
    json queryDocuments(const std::string& query, const std::string& onlyFile = "");
    json listIndexed() const;
    json status() const;
    bool empty() const;

    void registerTools(ToolRegistry& registry);

private:
    struct Chunk {
        std::string file;
        int page = 0;  // 0 when the source has no pages
        std::string text;
    };
    struct FileInfo {
        std::size_t chunks = 0;
        int pages = 0;
        std::string kind;
    };

    Embedder embed_;
    Summarizer summarize_;
    VisionReader vision_;
    std::function<std::string(const std::string&)> resolve_;
    std::function<std::string(const std::string&)> refuse_;
    mutable std::mutex mutex_;
    std::vector<Chunk> chunks_;
    std::map<std::string, FileInfo> files_;
    std::unique_ptr<VectorIndex> index_;
};

/// Semantic search over a code repository: index_codebase, search_code_index,
/// get_index_status and clear_code_index, the Python CodeIndexToolsMixin's
/// tools. Chunks are line ranges, so every hit names file:start-end.
class GAIA_API CodeIndex {
public:
    explicit CodeIndex(Embedder embedder, std::string defaultRoot);
    ~CodeIndex();
    void setPathResolver(std::function<std::string(const std::string&)> resolve) { resolve_ = std::move(resolve); }
    /// Why a resolved file must not be read ("" when it may be).
    void setRefusal(std::function<std::string(const std::string&)> refuse) { refuse_ = std::move(refuse); }

    json indexCodebase(const std::string& repoPath);
    json search(const std::string& query, const std::string& scope, long long topK);
    json status() const;
    json clear();
    void registerTools(ToolRegistry& registry);

private:
    struct Chunk {
        std::string file;
        int startLine = 0;
        int endLine = 0;
        std::string text;
    };
    Embedder embed_;
    std::string defaultRoot_;
    std::string root_;
    std::function<std::string(const std::string&)> resolve_;
    std::function<std::string(const std::string&)> refuse_;
    mutable std::mutex mutex_;
    std::vector<Chunk> chunks_;
    std::size_t files_ = 0;
    std::unique_ptr<VectorIndex> index_;
};

struct DocumentText;

/// Read up to @p maxPages of @p doc's text-less PDF pages with @p vision, in place:
/// their text goes under their page markers and emptySections/warnings are updated.
GAIA_API void fillScannedPages(const std::string& path, DocumentText& doc, const VisionReader& vision,
                               std::size_t maxPages = 20);

/// Split text into overlapping chunks on paragraph and sentence boundaries.
GAIA_API std::vector<std::string> chunkText(const std::string& text, std::size_t chunkChars, std::size_t overlapChars);

}  // namespace gaia
