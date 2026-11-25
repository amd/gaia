// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/session.h"

#include "gaia/security.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <random>
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
    const std::string home = gaiaHome();
    if (home.empty()) {
        // A shared folder such as /tmp would let another account read saved chats.
        throw std::runtime_error("Neither GAIA_HOME nor the home folder variable is set, so there is no private "
                                 "folder for saved sessions. Set GAIA_HOME, or pass a folder to SessionStore.");
    }
    return (fs::path(home) / "sessions").string();
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

    // Preserve the wire representation, including ordered vision content parts.
    if (j.contains("content")) {
        if (j["content"].is_string()) {
            m.content = j["content"].get<std::string>();
        } else if (j["content"].is_array()) {
            m.parts.emplace();
            m.parts->reserve(j["content"].size());
            for (const auto& part : j["content"]) {
                if (!part.is_object() || !part.contains("type") ||
                    !part["type"].is_string()) {
                    throw std::runtime_error("Message content part missing 'type' string field");
                }
                const auto type = part["type"].get<std::string>();
                if (type == "text") {
                    if (!part.contains("text") || !part["text"].is_string()) {
                        throw std::runtime_error("Text content part missing 'text' string field");
                    }
                    const auto text = part["text"].get<std::string>();
                    m.parts->push_back(ContentPart::makeText(text));
                    // Keep the existing text view for callers that read content.
                    if (!m.content.empty()) m.content += "\n";
                    m.content += text;
                } else if (type == "image_url") {
                    if (!part.contains("image_url") || !part["image_url"].is_object() ||
                        !part["image_url"].contains("url") ||
                        !part["image_url"]["url"].is_string()) {
                        throw std::runtime_error("Image content part missing 'image_url.url' string field");
                    }
                    m.parts->push_back(ContentPart::makeImageUrl(
                        part["image_url"]["url"].get<std::string>()));
                } else {
                    throw std::runtime_error("Unsupported message content part type: " + type);
                }
            }
        }
    }

    // Optional fields
    if (j.contains("name") && j["name"].is_string()) {
        m.name = j["name"].get<std::string>();
    }
    if (j.contains("tool_call_id") && j["tool_call_id"].is_string()) {
        m.toolCallId = j["tool_call_id"].get<std::string>();
    }
    // Native tool_calls must round-trip, or a resumed session replays role=tool
    // messages whose originating assistant turn has lost its tool_calls array.
    if (j.contains("tool_calls") && j["tool_calls"].is_array()) {
        for (const auto& entry : j["tool_calls"]) {
            m.toolCalls.push_back(ToolCall::fromJson(entry));
        }
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

    // Written beside the session and renamed over it, so a failed write never
    // leaves a half-written session where the last good one was.
    const std::string path = pathForId(id);
    static const unsigned int processTag = std::random_device{}();
    static std::atomic<unsigned long long> saves{0};
    const std::string tmp =
        path + ".tmp-" + std::to_string(processTag) + "-" + std::to_string(saves.fetch_add(1));
    {
        std::ofstream f(tmp);
        if (!f.is_open()) {
            throw std::runtime_error("Failed to open session file for writing: " + tmp);
        }
        f << j.dump(2, ' ', false, json::error_handler_t::replace) << "\n";
        f.close();
        if (f.fail()) {
            fs::remove(tmp, ec);
            throw std::runtime_error("Failed to write session file: " + tmp);
        }
    }
    fs::rename(tmp, path, ec);
    if (ec) {
        const std::string why = ec.message();
        std::error_code cleanup;
        fs::remove(tmp, cleanup);
        throw std::runtime_error("Failed to move the saved session into place at '" + path + "': " + why);
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
            if (!f.is_open()) throw std::runtime_error("the file could not be opened");

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
        } catch (const std::exception& e) {
            // Listed, not skipped: a session that vanished from the list looks deleted.
            SessionInfo broken;
            broken.id = entry.path().stem().string();
            broken.problem = e.what();
            broken.preview = "[unreadable session file " + entry.path().filename().string() + "]";
            sessions.push_back(std::move(broken));
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
    // Every id after the first carries its number, so two calls in one second differ.
    static std::atomic<unsigned long long> issued{0};
    const unsigned long long n = issued.fetch_add(1);
    const std::string base = "session-" + nowIdTimestamp();
    return n == 0 ? base : base + "-" + std::to_string(n);
}

} // namespace gaia
