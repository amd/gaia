// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Pre-built file I/O tool callbacks for GAIA agents.
// Provides read, write, edit, and search tools that any agent can register
// to give the LLM file manipulation capabilities.

#pragma once

#include <cstdint>
#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

namespace gaia {

/// Tracks the content hash of every file an agent has read, so a later write
/// can tell "the model is editing what it saw" from "the file moved under it".
///
/// No system prompt can prevent a stale write: by the time the model emits an
/// edit, the read that justified it may be many turns old and the file may
/// have been changed by a build step, a formatter, another agent, or the user.
/// The tool has to catch it, so `file_write` and `file_edit` consult this
/// tracker and refuse rather than clobber.
///
/// Semantics:
///   - `file_read` records the SHA-256 of the file's full contents (not the
///     returned slice — a line-range read still anchors the whole file).
///   - `file_write` / `file_edit` reject when a record exists and the current
///     contents hash differently, naming both hashes and the size change.
///   - A file with no record is not blocked. Requiring a prior read would make
///     the tools unusable for creating files, and an agent that never read the
///     file has nothing stale to be wrong about.
///   - A successful write or edit re-records the new contents, so consecutive
///     edits to the same file work without an intervening read.
///
/// Thread-safe: an internal mutex guards the table.
///
/// Extension point: a future `lsp` tool (clangd) applies server-computed
/// `WorkspaceEdit`s to files the model never read. It must call
/// `recordWrite()` for each file it touches; otherwise the next `file_edit`
/// would see the LSP's own change as third-party divergence and reject it.
/// `forget()` is the hook for a file the tool no longer vouches for.
class GAIA_API FileStateTracker {
public:
    /// The result of comparing a file's current contents to what was read.
    struct Divergence {
        bool diverged = false;      ///< True when a read record exists and no longer matches
        std::string hashAtRead;     ///< Short hash captured by the last read/write
        std::string hashNow;        ///< Short hash of the contents on disk right now
        std::uint64_t sizeAtRead = 0;
        std::uint64_t sizeNow = 0;
        std::string reason;         ///< Human-readable description of the divergence
    };

    /// Process-wide tracker used by the FileIOTools callbacks.
    static FileStateTracker& instance();

    /// Record the contents an agent has just seen for `path`.
    void recordRead(const std::string& path, const std::string& contents);

    /// Record contents an agent has just written for `path`. Identical to
    /// recordRead() but named for the call site so intent stays readable.
    void recordWrite(const std::string& path, const std::string& contents);

    /// Hash the file on disk (streamed, so a huge file costs bounded memory)
    /// and record it. Returns the hash, or "" when the file is unreadable.
    std::string recordFromDisk(const std::string& path);

    /// Record an already-computed hash. Use this when the caller hashed the
    /// same bytes it acted on — re-reading the file to hash it would open a
    /// window in which the anchor describes a version nobody saw.
    void recordHash(const std::string& path,
                    const std::string& hash,
                    std::uint64_t size);

    /// Compare `currentContents` against the recorded read for `path`.
    /// Returns `diverged == false` when there is no record for the path.
    Divergence check(const std::string& path,
                     const std::string& currentContents) const;

    /// Same as check(), reading the current contents from disk. A file that
    /// has a record but no longer exists counts as diverged.
    Divergence checkFile(const std::string& path) const;

    /// True when `path` has a recorded read or write.
    bool hasRecord(const std::string& path) const;

    /// Drop the record for `path` (e.g. the file was deleted or renamed).
    void forget(const std::string& path);

    /// Drop every record. Intended for tests and session resets.
    void clear();

    /// Number of tracked files.
    size_t size() const;

    /// Lowercase hex SHA-256 of `contents`.
    static std::string hashContent(const std::string& contents);

    /// Lowercase hex SHA-256 of the bytes of the file at `path`, streamed.
    /// Returns "" when the file cannot be opened.
    static std::string hashFile(const std::string& path);

private:
    FileStateTracker() = default;

    struct Impl;
    // Storage lives in the translation unit to keep <map>/<mutex> out of the
    // public header.
    static Impl& impl();
};

/// Pre-built file I/O tool callbacks for agents.
/// Each static method returns a ToolInfo ready for ToolRegistry::registerTool().
///
/// Usage:
///   auto& reg = agent.toolRegistry();
///   reg.registerTool(FileIOTools::fileRead());
///   reg.registerTool(FileIOTools::fileWrite());
///   reg.registerTool(FileIOTools::fileEdit());
///   reg.registerTool(FileIOTools::fileSearch());
///
/// Or register all at once:
///   FileIOTools::registerAll(agent.toolRegistry());
///
/// Design note — room for `lsp`: these tools stay deliberately text-level.
/// Structural edits ("rename this symbol everywhere") are a graph operation
/// that regex cannot do correctly, and the answer is a clangd-backed `lsp`
/// tool in a follow-on milestone, not a smarter `file_edit`. That tool slots
/// in beside these without a rewrite because the two contracts it needs
/// already exist here: FileStateTracker (so LSP-applied edits and model edits
/// share one staleness ledger) and GitignoreMatcher in `gaia/ignore.h` (so
/// both agree on which files are part of the project).
class GAIA_API FileIOTools {
public:
    /// Register all file I/O tools with the given registry.
    static void registerAll(ToolRegistry& registry);

    /// file_read: Read file contents with optional line range.
    /// Args: {"path": string, "start_line"?: int, "end_line"?: int}
    /// Returns: {"content": string, "lines": int, "path": string,
    ///           "content_hash": string}
    /// The returned content_hash anchors later edits; see FileStateTracker.
    /// On error: {"error": string}
    static ToolInfo fileRead();

    /// file_write: Write content to a file (creates parent dirs).
    /// Args: {"path": string, "content": string}
    /// Returns: {"success": true, "path": string, "bytes_written": int,
    ///           "content_hash": string}
    /// Rejected with {"error": ..., "stale": true} when the file changed
    /// since it was read.
    /// On error: {"error": string}
    static ToolInfo fileWrite();

    /// file_edit: Surgical string replacement in a file.
    /// Args: {"path": string, "old_string": string, "new_string": string}
    /// Returns: {"success": true, "path": string, "replacements": int,
    ///           "content_hash": string}
    /// Rejected with {"error": ..., "stale": true} when the file changed
    /// since it was read; a non-matching old_string returns an actionable
    /// error rather than reporting success on a no-op.
    /// On error: {"error": string}
    static ToolInfo fileEdit();

    /// file_search: Search for files by glob pattern and/or content pattern.
    /// Args: {"pattern": string, "path"?: string, "content_pattern"?: string, "max_results"?: int}
    /// Returns: {"matches": [{"path": string, "line"?: int, "context"?: string}],
    ///           "total": int, "ignored_skipped": int}
    /// Paths excluded by `.gitignore` (and `.git/` itself) are skipped and
    /// counted in ignored_skipped, so a thin result set is explained rather
    /// than mysterious.
    /// On error: {"error": string}
    static ToolInfo fileSearch();

private:
    // Implementation callbacks
    static json doFileRead(const json& args);
    static json doFileWrite(const json& args);
    static json doFileEdit(const json& args);
    static json doFileSearch(const json& args);
};

} // namespace gaia
