// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/security.h>

#include <filesystem>
#include <fstream>
#include <string>

using namespace gaia;
namespace fs = std::filesystem;

// ---------------------------------------------------------------------------
// validatePath
// ---------------------------------------------------------------------------

class ValidatePathTest : public ::testing::Test {
protected:
    fs::path tmpBase;

    void SetUp() override {
        tmpBase = fs::temp_directory_path() / "gaia_pathtest";
        fs::create_directories(tmpBase);
    }

    void TearDown() override {
        fs::remove_all(tmpBase);
    }
};

TEST_F(ValidatePathTest, ValidPathInsideBase) {
    fs::path child = tmpBase / "subdir" / "file.txt";
    fs::create_directories(child.parent_path());
    // Create the file so realpath can resolve it
    { std::ofstream f(child); f << "x"; }
    EXPECT_TRUE(validatePath(tmpBase.string(), child.string()));
}

TEST_F(ValidatePathTest, IdenticalPaths) {
    EXPECT_TRUE(validatePath(tmpBase.string(), tmpBase.string()));
}

TEST_F(ValidatePathTest, PathTraversalBlocked) {
    // Construct a path that uses .. to escape tmpBase
    fs::path escape = tmpBase / ".." / "escape.txt";
    EXPECT_FALSE(validatePath(tmpBase.string(), escape.string()));
}

TEST_F(ValidatePathTest, NonExistentRequestedPath) {
    // realpath requires the path to exist on POSIX; a non-existent path returns false.
    fs::path nonExistent = tmpBase / "does_not_exist.txt";
    EXPECT_FALSE(validatePath(tmpBase.string(), nonExistent.string()));
}

TEST_F(ValidatePathTest, EmptyPaths) {
    EXPECT_FALSE(validatePath("", tmpBase.string()));
    EXPECT_FALSE(validatePath(tmpBase.string(), ""));
    EXPECT_FALSE(validatePath("", ""));
}

// ---------------------------------------------------------------------------
// isSafeShellArg
// ---------------------------------------------------------------------------

TEST(IsSafeShellArgTest, SafeInputsAccepted) {
    EXPECT_TRUE(isSafeShellArg("hello"));
    EXPECT_TRUE(isSafeShellArg("filename.txt"));
    EXPECT_TRUE(isSafeShellArg("CamelCase123"));
    EXPECT_TRUE(isSafeShellArg("dash-ok"));
    EXPECT_TRUE(isSafeShellArg("under_score"));
    EXPECT_TRUE(isSafeShellArg("path/to/file"));
    EXPECT_TRUE(isSafeShellArg("C:\\Windows\\file"));
}

TEST(IsSafeShellArgTest, InjectionCharsRejected) {
    EXPECT_FALSE(isSafeShellArg("hello world"));   // space
    EXPECT_FALSE(isSafeShellArg("cmd;evil"));       // semicolon
    EXPECT_FALSE(isSafeShellArg("a|b"));            // pipe
    EXPECT_FALSE(isSafeShellArg("a&&b"));           // &&
    EXPECT_FALSE(isSafeShellArg("$(evil)"));        // command substitution
    EXPECT_FALSE(isSafeShellArg("`cmd`"));          // backtick
    EXPECT_FALSE(isSafeShellArg("a>out"));          // redirect
    EXPECT_FALSE(isSafeShellArg("<in"));            // redirect
    EXPECT_FALSE(isSafeShellArg("'quoted'"));       // single quote
    EXPECT_FALSE(isSafeShellArg("\"quoted\""));     // double quote
    EXPECT_FALSE(isSafeShellArg("$VAR"));           // variable
}

TEST(IsSafeShellArgTest, EmptyStringRejected) {
    EXPECT_FALSE(isSafeShellArg(""));
}

// ---------------------------------------------------------------------------
// AllowedToolsStore
// ---------------------------------------------------------------------------

class AllowedToolsStoreTest : public ::testing::Test {
protected:
    fs::path storeDir;
    std::unique_ptr<AllowedToolsStore> store;

    void SetUp() override {
        storeDir = fs::temp_directory_path() / "gaia_store_test";
        fs::remove_all(storeDir);
        store = std::make_unique<AllowedToolsStore>(storeDir.string());
    }

    void TearDown() override {
        fs::remove_all(storeDir);
    }
};

TEST_F(AllowedToolsStoreTest, AddAndCheck) {
    EXPECT_FALSE(store->isAlwaysAllowed("read_file"));
    store->addAlwaysAllowed("read_file");
    EXPECT_TRUE(store->isAlwaysAllowed("read_file"));
    EXPECT_FALSE(store->isAlwaysAllowed("other_tool"));
}

TEST_F(AllowedToolsStoreTest, Persistence) {
    store->addAlwaysAllowed("check_adapter");
    store->addAlwaysAllowed("mcp_windows_Shell");

    // Reload from the same directory
    AllowedToolsStore reloaded(storeDir.string());
    EXPECT_TRUE(reloaded.isAlwaysAllowed("check_adapter"));
    EXPECT_TRUE(reloaded.isAlwaysAllowed("mcp_windows_Shell"));
    EXPECT_FALSE(reloaded.isAlwaysAllowed("nonexistent"));
}

TEST_F(AllowedToolsStoreTest, RemoveTool) {
    store->addAlwaysAllowed("tool_a");
    store->addAlwaysAllowed("tool_b");
    store->removeAlwaysAllowed("tool_a");

    EXPECT_FALSE(store->isAlwaysAllowed("tool_a"));
    EXPECT_TRUE(store->isAlwaysAllowed("tool_b"));
}

TEST_F(AllowedToolsStoreTest, ClearAll) {
    store->addAlwaysAllowed("tool_a");
    store->addAlwaysAllowed("tool_b");
    store->clearAll();

    EXPECT_FALSE(store->isAlwaysAllowed("tool_a"));
    EXPECT_FALSE(store->isAlwaysAllowed("tool_b"));
    EXPECT_TRUE(store->allAllowed().empty());

    // Persist of empty list: reload should also be empty
    AllowedToolsStore reloaded(storeDir.string());
    EXPECT_TRUE(reloaded.allAllowed().empty());
}

TEST_F(AllowedToolsStoreTest, CorruptFile) {
    fs::path storeFile = storeDir / "allowed_tools.json";
    { std::ofstream f(storeFile); f << "not valid json {{{{"; }

    // Constructing a store over a corrupt file must not throw; store starts empty.
    AllowedToolsStore reloaded(storeDir.string());
    EXPECT_TRUE(reloaded.allAllowed().empty());
}

TEST_F(AllowedToolsStoreTest, AllAllowed) {
    store->addAlwaysAllowed("tool_b");
    store->addAlwaysAllowed("tool_a");
    auto all = store->allAllowed();
    ASSERT_EQ(all.size(), 2u);
    // std::set returns sorted order
    EXPECT_EQ(all[0], "tool_a");
    EXPECT_EQ(all[1], "tool_b");
}

// ---------------------------------------------------------------------------
// makeStdinConfirmCallback
// ---------------------------------------------------------------------------

TEST(MakeStdinConfirmCallbackTest, ReturnsCallable) {
    gaia::ToolConfirmCallback cb = gaia::makeStdinConfirmCallback();
    EXPECT_TRUE(static_cast<bool>(cb));
}
