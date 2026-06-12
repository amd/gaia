// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/session.h"

#include <algorithm>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace fs = std::filesystem;

namespace gaia {

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

namespace {

/// Convert a MessageRole enum to/from string for JSON serialization.
MessageRole roleFromString(const std::string& s) {
    if (s == "system")    return MessageRole::SYSTEM;
    if (s == "user")      return MessageRole::USER;
    if (s == "assistant") return MessageRole::ASSISTANT;
    if (s == "tool")      return MessageRole::TOOL;
    throw std::runtime_error("Unknown message role: " + s);
}

/// Get the current UTC time as an ISO 8601 string.
std::string nowIso8601() {
    auto now = std::chrono::system_clock::now();
    auto time = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &time);
#else
    gmtime_r(&time, &tm);
#endif
    std::ostringstream oss;
    oss << std::put_time(&tm, "%Y-%m-%dT%H:%M:%SZ");
    return oss.str();
}

/// Get a timestamp string suitable for an ID (YYYYMMDD-HHMMSS).
std::string nowIdTimestamp() {
    auto now = std::chrono::system_clock::now();
    auto time = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    gmtime_s(&tm, &time);
#else
    gmtime_r(&time, &tm);
#endif
    std::ostringstream oss;
    oss << std::put_time(&tm, "%Y%m%d-%H%M%S");
    return oss.str();
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// SessionStore — construction
// ---------------------------------------------------------------------------

std::string SessionStore::defaultDir() {
#ifdef _WIN32
    const char* profile = std::getenv("USERPROFILE");
    std::string home = profile ? profile : "C:\\Users\\Default";
    return home + "\\.gaia\\sessions";
#else
    const char* home = std::getenv("HOME");  // NOLINT(concurrency-mt-unsafe)
    std::string h = home ? home : "/tmp";
    return h + "/.gaia/sessions";
#endif
}

SessionStore::SessionStore()
    : SessionStore(defaultDir()) {}

SessionStore::SessionStore(const std::string& dir)
    : dir_(dir) {}

// ---------------------------------------------------------------------------
// ID validation
// ---------------------------------------------------------------------------

void SessionStore::validateId(const std::string& id) {
    if (id.empty()) {
        throw std::invalid_argument("Session ID must not be empty");
    }
    for (char c : id) {
        if (!std::isalnum(static_cast<unsigned char>(c)) && c != '-' && c != '_') {
            throw std::invalid_argument(
                "Session ID contains invalid character '" + std::string(1, c) +
                "'. Only alphanumeric, hyphens, and underscores are allowed.");
        }
    }
}

// ---------------------------------------------------------------------------
// Path helper
// ---------------------------------------------------------------------------

std::string SessionStore::pathForId(const std::string& id) const {
    fs::path p = fs::path(dir_) / (id + ".json");
    return p.string();
}

// ---------------------------------------------------------------------------
// Message serialization
// ---------------------------------------------------------------------------

Message SessionStore::messageFromJson(const json& j) {
    Message m;

    // Role (required)
    if (!j.contains("role") || !j["role"].is_string()) {
        throw std::runtime_error("Message JSON missing 'role' string field");
    }
    m.role = roleFromString(j["role"].get<std::string>());

    // Content — accept string only (parts/array content not round-tripped)
    if (j.contains("content")) {
        if (j["content"].is_string()) {
            m.content = j["content"].get<std::string>();
        } else if (j["content"].is_array()) {
            // Flatten array content to text-only for simplicity
            std::string combined;
            for (const auto& part : j["content"]) {
                if (part.is_object() && part.value("type", "") == "text" &&
                    part.contains("text") && part["text"].is_string()) {
                    if (!combined.empty()) combined += "\n";
                    combined += part["text"].get<std::string>();
                }
            }
            m.content = combined;
        }
    }

    // Optional fields
    if (j.contains("name") && j["name"].is_string()) {
        m.name = j["name"].get<std::string>();
    }
    if (j.contains("tool_call_id") && j["tool_call_id"].is_string()) {
        m.toolCallId = j["tool_call_id"].get<std::string>();
    }

    return m;
}

// ---------------------------------------------------------------------------
// save
// ---------------------------------------------------------------------------

void SessionStore::save(const std::string& id, const std::vector<Message>& history) {
    validateId(id);

    // Ensure directory exists
    std::error_code ec;
    fs::create_directories(dir_, ec);
    if (ec) {
        throw std::runtime_error(
            "Failed to create session directory '" + dir_ + "': " + ec.message());
    }

    // Build JSON envelope
    json j;
    j["version"] = 1;
    j["id"] = id;
    j["timestamp"] = nowIso8601();

    json messages = json::array();
    for (const auto& msg : history) {
        messages.push_back(msg.toJson());
    }
    j["messages"] = messages;

    // Write atomically-ish: write to file directly (no temp-rename on Windows
    // for simplicity, matching the AllowedToolsStore pattern)
    std::string path = pathForId(id);
    std::ofstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Failed to open session file for writing: " + path);
    }
    f << j.dump(2) << "\n";
    if (!f.good()) {
        throw std::runtime_error("Failed to write session file: " + path);
    }
}

// ---------------------------------------------------------------------------
// load
// ---------------------------------------------------------------------------

std::vector<Message> SessionStore::load(const std::string& id) const {
    validateId(id);

    std::string path = pathForId(id);
    std::ifstream f(path);
    if (!f.is_open()) {
        throw std::runtime_error("Session not found: " + id);
    }

    json j;
    try {
        f >> j;
    } catch (const json::parse_error& e) {
        throw std::runtime_error(
            "Failed to parse session file '" + path + "': " + e.what());
    }

    if (!j.contains("messages") || !j["messages"].is_array()) {
        throw std::runtime_error(
            "Session file '" + path + "' is malformed: missing 'messages' array");
    }

    std::vector<Message> history;
    history.reserve(j["messages"].size());
    for (const auto& msgJson : j["messages"]) {
        history.push_back(messageFromJson(msgJson));
    }
    return history;
}

// ---------------------------------------------------------------------------
// exists
// ---------------------------------------------------------------------------

bool SessionStore::exists(const std::string& id) const {
    validateId(id);
    return fs::exists(pathForId(id));
}

// ---------------------------------------------------------------------------
// remove
// ---------------------------------------------------------------------------

bool SessionStore::remove(const std::string& id) {
    validateId(id);
    std::error_code ec;
    return fs::remove(pathForId(id), ec);
}

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

std::vector<SessionInfo> SessionStore::list() const {
    std::vector<SessionInfo> sessions;

    if (!fs::exists(dir_) || !fs::is_directory(dir_)) {
        return sessions;
    }

    for (const auto& entry : fs::directory_iterator(dir_)) {
        if (!entry.is_regular_file()) continue;
        if (entry.path().extension() != ".json") continue;

        try {
            std::ifstream f(entry.path());
            if (!f.is_open()) continue;

            json j = json::parse(f);

            SessionInfo info;
            info.id = j.value("id", entry.path().stem().string());
            info.timestamp = j.value("timestamp", "");

            // Message count
            if (j.contains("messages") && j["messages"].is_array()) {
                info.messageCount = j["messages"].size();

                // Preview: first user message, truncated
                for (const auto& msg : j["messages"]) {
                    if (msg.value("role", "") == "user") {
                        std::string content;
                        if (msg.contains("content") && msg["content"].is_string()) {
                            content = msg["content"].get<std::string>();
                        }
                        if (content.size() > 100) {
                            content = content.substr(0, 97) + "...";
                        }
                        info.preview = content;
                        break;
                    }
                }
            }

            sessions.push_back(std::move(info));
        } catch (...) {
            // Skip malformed session files
            continue;
        }
    }

    // Sort by timestamp, newest first
    std::sort(sessions.begin(), sessions.end(),
              [](const SessionInfo& a, const SessionInfo& b) {
                  return a.timestamp > b.timestamp;
              });

    return sessions;
}

// ---------------------------------------------------------------------------
// generateId
// ---------------------------------------------------------------------------

std::string SessionStore::generateId() {
    std::string base = "session-" + nowIdTimestamp();

    // Check for collision — append a suffix if needed
    // This handles the case where generateId() is called twice within the same second
    static int counter = 0;
    static std::string lastTimestamp;

    std::string ts = nowIdTimestamp();
    if (ts == lastTimestamp) {
        ++counter;
        lastTimestamp = ts;
        return base + "-" + std::to_string(counter);
    }

    lastTimestamp = ts;
    counter = 0;
    return base;
}

} // namespace gaia
