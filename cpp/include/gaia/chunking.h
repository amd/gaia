// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Text extraction and semantic chunking for GAIA retrieval.
//
// The splitter is a line-for-line port of RAGSDK._split_text_into_chunks
// (src/gaia/rag/sdk.py) so that a document chunked by the C++ runtime and the
// same document chunked by the Python runtime produce identical chunk text.
// tests/test_chunking.cpp pins that contract against a committed fixture.

#pragma once

#include <cstddef>
#include <functional>
#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia {

/// Chunking parameters. Defaults mirror RAGConfig in src/gaia/rag/sdk.py.
/// Sizes are in estimated tokens, where one token is approximated as four
/// characters — the same estimate the Python SDK uses.
struct GAIA_API ChunkingConfig {
    std::size_t chunkSize = 500;    ///< Target chunk size in estimated tokens.
    std::size_t chunkOverlap = 100; ///< Overlap carried into the next chunk.
};

/// What kind of text a file was extracted as. Informational — all three kinds
/// are read as UTF-8 text; the format only records how the file was classified.
enum class TextFormat {
    PlainText,  ///< .txt, .log, .rst, and other prose files
    Markdown,   ///< .md, .markdown
    SourceCode, ///< source, markup, and config files (.py, .cpp, .ts, .yaml, …)
};

/// Result of extracting a file.
struct GAIA_API ExtractedText {
    std::string text;      ///< Whitespace-stripped file contents.
    TextFormat format = TextFormat::PlainText;
    std::string extension; ///< Lower-cased extension including the dot ("" if none).
};

/// Split text into semantic chunks.
///
/// Port of RAGSDK._split_text_into_chunks (heuristic path). The algorithm:
///   1. Split into sections on markdown headers, horizontal rules, and
///      title-shaped lines surrounded by blank lines.
///   2. If fewer than four sections were found, fall back to paragraph splitting
///      on blank lines.
///   3. Accumulate paragraphs until the token budget is hit; a paragraph larger
///      than the budget is split on sentence boundaries first.
///   4. Carry `chunkOverlap` tokens of the previous chunk into the next one.
///
/// Two deliberate divergences from Python:
///   - LLM-assisted chunking (RAGConfig.use_llm_chunking) is not ported; the C++
///     runtime always takes the heuristic path, which is what Python falls back
///     to when no LLM is reachable.
///   - VLM image blocks are not kept atomic. Python emits those markers only
///     from its PDF/PPTX extractors, which are out of scope here (see
///     extractFile), so text that carries them verbatim is the one input where
///     the two runtimes chunk differently.
///
/// Character counts are Unicode code points, matching Python's len(str);
/// whitespace and uppercase tests match str.isspace() and str.isupper(). The
/// sentence-boundary capital test stays ASCII, because Python's regex uses the
/// literal [A-Z] class.
///
/// @param text  Document text. Empty or whitespace-only text yields no chunks.
/// @param config Chunk size and overlap.
/// @return Chunks in document order. Never contains empty strings.
/// @throws std::invalid_argument if chunkSize is 0 or chunkOverlap >= chunkSize
GAIA_API std::vector<std::string> splitTextIntoChunks(const std::string& text,
                                                     const ChunkingConfig& config = {});

/// Split text into sentences using the same heuristics as
/// RAGSDK._split_into_sentences: break after `.`/`!`/`?` followed by whitespace
/// and an ASCII capital, with common abbreviations (Dr., e.g., etc.) protected.
/// Exposed because chunk boundaries depend on it and it is worth testing directly.
GAIA_API std::vector<std::string> splitIntoSentences(const std::string& text);

/// Extract text from a file, dispatching on its extension.
///
/// Supported: plain text (.txt, .log, .rst), Markdown (.md, .markdown),
/// and source/markup/config files (see supportedExtensions()).
///
/// **Out of scope: PDF, DOCX, XLSX, PPTX.** Those need a document-parsing
/// backend of PyMuPDF's weight, which the native coding-agent target does not
/// need. They are not silently returned as empty text — extractFile throws and
/// names the type. To add one, register a handler with registerExtractor().
///
/// Files whose bytes are not valid UTF-8 are decoded as latin-1, matching the
/// Python extractor's encoding fallback chain. Files containing NUL bytes are
/// rejected as binary rather than indexed as mojibake.
///
/// @throws std::runtime_error if the file is missing, unreadable, binary, or of
///         an unsupported type. Unsupported-type errors name the extension and
///         the supported set.
GAIA_API ExtractedText extractFile(const std::string& filePath);

/// Extract a file as plain text regardless of its extension.
///
/// The escape hatch for callers who know a file is text but whose extension is
/// unknown to extractFile (README, Makefile, Dockerfile, .gaiaignore, …). Still
/// rejects binary content, so it degrades loudly rather than silently.
///
/// @throws std::runtime_error if the file is missing, unreadable, or binary.
GAIA_API ExtractedText extractPlainTextFile(const std::string& filePath);

/// Extract a file and split it into chunks. Convenience for
/// splitTextIntoChunks(extractFile(path).text, config).
GAIA_API std::vector<std::string> chunkFile(const std::string& filePath,
                                            const ChunkingConfig& config = {});

/// Extension point for formats the core does not handle.
///
/// A handler receives the absolute file path and returns the extracted text.
/// This is how a consumer that *does* want PDF or DOCX links its own parser in:
///
///   gaia::registerExtractor(".pdf", [](const std::string& p) {
///       return myPdfBackend::toText(p);   // e.g. a PyMuPDF/Poppler wrapper
///   });
///
/// Registering an extension already handled by the core overrides it. The
/// extension is matched case-insensitively and must start with a dot.
using TextExtractor = std::function<std::string(const std::string&)>;
GAIA_API void registerExtractor(const std::string& extension, TextExtractor extractor);

/// True if extractFile() can handle this extension (dot-prefixed, any case).
GAIA_API bool isSupportedExtension(const std::string& extension);

/// Every extension extractFile() handles, sorted, including registered ones.
GAIA_API std::vector<std::string> supportedExtensions();

} // namespace gaia
