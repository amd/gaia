// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/session.h>
#include <gaia/types.h>

#include <filesystem>
#include <fstream>
#include <string>

#include <nlohmann/json.hpp>

using json = nlohmann::json;

using namespace gaia;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// Test fixture — uses a temp directory, cleaned up after each test
// ---------------------------------------------------------------------------

class SessionStoreTest : public ::testing::Test {
protected:
    fs::path storeDir;
    std::unique_ptr<SessionStore> store;

    void SetUp() override {
        storeDir = fs::temp_directory_path() / "gaia_session_test";
        fs::remove_all(storeDir);
        store = std::make_unique<SessionStore>(storeDir.string());
    }

    void TearDown() override {
        fs::remove_all(storeDir);
    }

    /// Helper: create a simple conversation history with mixed roles.
    static std::vector<Message> makeSampleHistory() {
        std::vector<Message> history;

        Message sys;
        sys.role = MessageRole::SYSTEM;
        sys.content = "You are a helpful assistant.";
        history.push_back(sys);

        Message user;
        user.role = MessageRole::USER;
        user.content = "What is the capital of France?";
        history.push_back(user);

        Message asst;
        asst.role = MessageRole::ASSISTANT;
        asst.content = "The capital of France is Paris.";
        history.push_back(asst);

        return history;
    }

    /// Helper: create a history with TOOL messages.
    static std::vector<Message> makeToolHistory() {
        std::vector<Message> history;

        Message user;
        user.role = MessageRole::USER;
        user.content = "Search for information about AMD.";
        history.push_back(user);

        Message asst;
        asst.role = MessageRole::ASSISTANT;
        asst.content = "I'll search for that.";
        history.push_back(asst);

        Message tool;
        tool.role = MessageRole::TOOL;
        tool.content = "{\"result\": \"AMD makes processors.\"}";
        tool.name = "web_search";
        tool.toolCallId = "call_12345";
        history.push_back(tool);

        Message asst2;
        asst2.role = MessageRole::ASSISTANT;
        asst2.content = "AMD is a semiconductor company that makes processors.";
        history.push_back(asst2);

        return history;
    }
};

// ---------------------------------------------------------------------------
// 1. Save and load round-trip
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, SaveAndLoadRoundTrip) {
    auto history = makeSampleHistory();
    store->save("test-session", history);

    auto loaded = store->load("test-session");
    ASSERT_EQ(loaded.size(), history.size());

    for (size_t i = 0; i < history.size(); ++i) {
        EXPECT_EQ(roleToString(loaded[i].role), roleToString(history[i].role));
        EXPECT_EQ(loaded[i].content, history[i].content);
    }
}

// ---------------------------------------------------------------------------
// 2. Save with multiple message roles (USER, ASSISTANT, TOOL)
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, MultipleRolesRoundTrip) {
    auto history = makeToolHistory();
    store->save("tool-session", history);

    auto loaded = store->load("tool-session");
    ASSERT_EQ(loaded.size(), 4u);

    // User message
    EXPECT_EQ(loaded[0].role, MessageRole::USER);
    EXPECT_EQ(loaded[0].content, "Search for information about AMD.");

    // Assistant message
    EXPECT_EQ(loaded[1].role, MessageRole::ASSISTANT);
    EXPECT_EQ(loaded[1].content, "I'll search for that.");

    // Tool message — verify name and toolCallId
    EXPECT_EQ(loaded[2].role, MessageRole::TOOL);
    EXPECT_EQ(loaded[2].content, "{\"result\": \"AMD makes processors.\"}");
    ASSERT_TRUE(loaded[2].name.has_value());
    EXPECT_EQ(loaded[2].name.value(), "web_search");
    ASSERT_TRUE(loaded[2].toolCallId.has_value());
    EXPECT_EQ(loaded[2].toolCallId.value(), "call_12345");

    // Final assistant message
    EXPECT_EQ(loaded[3].role, MessageRole::ASSISTANT);
}

// ---------------------------------------------------------------------------
// 3. Load non-existent session throws
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, LoadNonExistentThrows) {
    EXPECT_THROW(store->load("nonexistent-session"), std::runtime_error);
}

// ---------------------------------------------------------------------------
// 4. exists() returns true after save, false before
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, ExistsBeforeAndAfterSave) {
    EXPECT_FALSE(store->exists("check-session"));

    auto history = makeSampleHistory();
    store->save("check-session", history);

    EXPECT_TRUE(store->exists("check-session"));
}

// ---------------------------------------------------------------------------
// 5. remove() — verify file deleted, subsequent load throws
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, RemoveDeletesSession) {
    auto history = makeSampleHistory();
    store->save("remove-me", history);
    ASSERT_TRUE(store->exists("remove-me"));

    bool removed = store->remove("remove-me");
    EXPECT_TRUE(removed);
    EXPECT_FALSE(store->exists("remove-me"));

    // Subsequent load should throw
    EXPECT_THROW(store->load("remove-me"), std::runtime_error);
}

TEST_F(SessionStoreTest, RemoveNonExistentReturnsFalse) {
    bool removed = store->remove("never-existed");
    EXPECT_FALSE(removed);
}

// ---------------------------------------------------------------------------
// 6. list() — save multiple sessions, verify all returned, sorted by timestamp
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, ListMultipleSessions) {
    // Write session files directly with known timestamps to ensure deterministic
    // ordering (avoids relying on sub-second timing in CI).
    auto writeSession = [&](const std::string& id, const std::string& timestamp) {
        auto history = makeSampleHistory();
        // Save normally first to create the file
        store->save(id, history);
        // Then overwrite with a controlled timestamp
        fs::path filePath = fs::path(store->directory()) / (id + ".json");
        std::ifstream fin(filePath);
        json j = json::parse(fin);
        fin.close();
        j["timestamp"] = timestamp;
        std::ofstream fout(filePath);
        fout << j.dump(2) << "\n";
    };

    writeSession("session-a", "2026-01-01T10:00:00Z");
    writeSession("session-b", "2026-01-01T11:00:00Z");
    writeSession("session-c", "2026-01-01T12:00:00Z");

    auto sessions = store->list();
    ASSERT_EQ(sessions.size(), 3u);

    // Newest first — session-c should be first
    EXPECT_EQ(sessions[0].id, "session-c");
    EXPECT_EQ(sessions[1].id, "session-b");
    EXPECT_EQ(sessions[2].id, "session-a");

    // Verify metadata
    for (const auto& info : sessions) {
        EXPECT_FALSE(info.timestamp.empty());
        EXPECT_EQ(info.messageCount, 3u);
        EXPECT_EQ(info.preview, "What is the capital of France?");
    }
}

// ---------------------------------------------------------------------------
// 7. list() on empty directory — verify empty vector
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, ListEmptyDirectory) {
    auto sessions = store->list();
    EXPECT_TRUE(sessions.empty());
}

TEST_F(SessionStoreTest, ListNonExistentDirectory) {
    SessionStore nonExistent((storeDir / "does_not_exist").string());
    auto sessions = nonExistent.list();
    EXPECT_TRUE(sessions.empty());
}

// ---------------------------------------------------------------------------
// 8. generateId() — verify format and uniqueness
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, GenerateIdFormat) {
    std::string id = SessionStore::generateId();

    // Must start with "session-"
    EXPECT_EQ(id.substr(0, 8), "session-");

    // Must contain only valid characters (alphanumeric, hyphens)
    for (char c : id) {
        EXPECT_TRUE(std::isalnum(static_cast<unsigned char>(c)) || c == '-' || c == '_');
    }
}

TEST_F(SessionStoreTest, GenerateIdUniqueness) {
    std::string id1 = SessionStore::generateId();
    std::string id2 = SessionStore::generateId();

    // Two rapid calls should produce different IDs
    EXPECT_NE(id1, id2);
}

// ---------------------------------------------------------------------------
// 9. Invalid session ID (contains path separator) — verify rejected
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, InvalidIdPathSeparator) {
    auto history = makeSampleHistory();

    EXPECT_THROW(store->save("../escape", history), std::invalid_argument);
    EXPECT_THROW(store->save("sub/dir", history), std::invalid_argument);
    EXPECT_THROW(store->save("back\\slash", history), std::invalid_argument);
    EXPECT_THROW(store->load("../escape"), std::invalid_argument);
    EXPECT_THROW(store->exists("sub/dir"), std::invalid_argument);
    EXPECT_THROW(store->remove("has.dot"), std::invalid_argument);
}

TEST_F(SessionStoreTest, InvalidIdDot) {
    auto history = makeSampleHistory();

    EXPECT_THROW(store->save("has.dot", history), std::invalid_argument);
    EXPECT_THROW(store->save(".hidden", history), std::invalid_argument);
    EXPECT_THROW(store->save("..", history), std::invalid_argument);
}

TEST_F(SessionStoreTest, InvalidIdEmpty) {
    auto history = makeSampleHistory();
    EXPECT_THROW(store->save("", history), std::invalid_argument);
    EXPECT_THROW(store->load(""), std::invalid_argument);
}

// ---------------------------------------------------------------------------
// Additional: save overwrites existing session
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, SaveOverwritesExisting) {
    auto history1 = makeSampleHistory();
    store->save("overwrite-test", history1);

    auto history2 = makeToolHistory();
    store->save("overwrite-test", history2);

    auto loaded = store->load("overwrite-test");
    ASSERT_EQ(loaded.size(), history2.size());
    EXPECT_EQ(loaded[0].content, "Search for information about AMD.");
}

// ---------------------------------------------------------------------------
// Additional: directory is returned correctly
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, DirectoryAccessor) {
    EXPECT_EQ(store->directory(), storeDir.string());
}

// ---------------------------------------------------------------------------
// Additional: empty history saves and loads correctly
// ---------------------------------------------------------------------------

TEST_F(SessionStoreTest, EmptyHistory) {
    std::vector<Message> empty;
    store->save("empty-session", empty);

    auto loaded = store->load("empty-session");
    EXPECT_TRUE(loaded.empty());
}
