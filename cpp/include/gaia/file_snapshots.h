// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Copies of the files a turn changes, so the Agent UI can show and undo them. Writes
// the same store the Python agent does (src/gaia/agents/base/file_snapshots.py):
// <gaia_home>/snapshots/<session>/<turn>/manifest.json plus blobs/NNNN.before and
// NNNN.after. The Agent UI reads, diffs and restores it; this side only records.

#pragma once

#include <filesystem>
#include <mutex>
#include <stdexcept>
#include <string>

#include "gaia/export.h"
#include "gaia/json_utils.h"

namespace gaia {

/// A copy could not be saved; the change it guards must not be made.
class GAIA_API SnapshotError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

class GAIA_API TurnRecorder {
public:
    /// Throws SnapshotError for a session or turn id the store can't hold.
    TurnRecorder(const std::string& sessionId, const std::string& turnId,
                 const std::filesystem::path& root = defaultRoot());

    /// <gaia_home>/snapshots.
    static std::filesystem::path defaultRoot();

    /// Save `path` as it is now, the first time this turn changes it; true when this call
    /// added it. Throws SnapshotError when the copy can't be saved: the caller must then leave
    /// the file alone. After finish(), the write is noted as one undo can't cover.
    bool capture(const std::string& path, const std::string& tool);

    /// Forget a copy capture() just added, for a change the tool then didn't make.
    void discard(const std::string& path);

    /// Work done without a copy (a shell command). may_write false records work known
    /// not to have written files; true marks the turn's undo as possibly incomplete.
    void noteUntracked(const std::string& kind, const std::string& detail, bool mayWrite);

    /// Save the state the turn left each file in; an empty turn leaves nothing behind.
    /// Throws SnapshotError when the record can't be saved.
    void finish();

    const std::filesystem::path& dir() const { return dir_; }

private:
    void save();
    /// A tool that outlived the turn wrote after finish(): mark the saved record incomplete.
    void noteLate(const std::string& kind, const std::string& detail);
    bool hasChanges() const;

    std::filesystem::path dir_;
    std::mutex mu_;
    json manifest_;
    bool closed_ = false;
    bool finished_ = false;
};

}  // namespace gaia
