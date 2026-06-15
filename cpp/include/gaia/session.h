// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Persistent session store for agent conversation history.
// Enables save/load/resume of chat sessions to/from disk.

#pragma once

#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

/// Metadata for a saved session (returned by list()).
struct GAIA_API SessionInfo {
    std::string id;           ///< Unique session identifier.
    std::string timestamp;    ///< ISO 8601 creation time.
    std::string preview;      ///< First user message (truncated to ~100 chars).
    size_t messageCount = 0;  ///< Total messages in session.
};

/// Persistent session store for agent conversation history.
///
/// Sessions are stored as JSON files in a configurable directory
/// (default: ~/.gaia/sessions/). Each file contains the full
/// conversation history serialized as an array of Message objects.
///
/// Usage:
/// @code
///   SessionStore store;
///   store.save("my-session", conversationHistory);
///   auto history = store.load("my-session");
///   auto sessions = store.list();
/// @endcode
class GAIA_API SessionStore {
public:
    /// Construct with default directory (~/.gaia/sessions/).
    SessionStore();

    /// Construct with explicit directory (for testing).
    explicit SessionStore(const std::string& dir);

    /// Save conversation history to a session file.
    /// @param id Session identifier (used as filename stem).
    /// @param history The conversation messages to persist.
    /// @throws std::runtime_error if the directory can't be created or file can't be written.
    /// @throws std::invalid_argument if the session ID contains invalid characters.
    void save(const std::string& id, const std::vector<Message>& history);

    /// Load conversation history from a session file.
    /// @param id Session identifier.
    /// @return The persisted conversation messages.
    /// @throws std::runtime_error if the session file doesn't exist or is malformed.
    /// @throws std::invalid_argument if the session ID contains invalid characters.
    std::vector<Message> load(const std::string& id) const;

    /// Check whether a session exists.
    /// @param id Session identifier.
    /// @return true if a session file exists for the given ID.
    bool exists(const std::string& id) const;

    /// Delete a session file.
    /// @param id Session identifier.
    /// @return true if the file was deleted, false if it didn't exist.
    bool remove(const std::string& id);

    /// List all saved sessions, sorted by timestamp (newest first).
    /// @return Vector of SessionInfo for every valid session file in the directory.
    std::vector<SessionInfo> list() const;

    /// Generate a unique session ID based on current timestamp.
    /// Format: "session-YYYYMMDD-HHMMSS" (with disambiguation suffix if needed).
    /// @return A unique session identifier string.
    static std::string generateId();

    /// Get the storage directory path.
    const std::string& directory() const { return dir_; }

private:
    std::string dir_;

    /// Get the file path for a session ID.
    std::string pathForId(const std::string& id) const;

    /// Parse a Message from JSON (inverse of Message::toJson()).
    static Message messageFromJson(const json& j);

    /// Validate a session ID (alphanumeric, hyphens, underscores only).
    /// @throws std::invalid_argument if the ID is invalid.
    static void validateId(const std::string& id);

    /// Determine the default sessions directory.
    static std::string defaultDir();
};

} // namespace gaia
