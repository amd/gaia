// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/git_tools.h>
#include <gaia/tool_registry.h>

#include <string>

using namespace gaia;

// ---------------------------------------------------------------------------
// git_status
// ---------------------------------------------------------------------------

TEST(GitToolsTest, StatusReturnsExpectedKeys) {
    ToolInfo info = GitTools::gitStatus();
    ASSERT_TRUE(info.callback);

    json result = info.callback(json::object());

    // Must have either "status"+"clean" keys or "error" key
    if (result.contains("error")) {
        EXPECT_TRUE(result["error"].is_string());
    } else {
        EXPECT_TRUE(result.contains("status"));
        EXPECT_TRUE(result.contains("clean"));
        EXPECT_TRUE(result["clean"].is_boolean());
    }
}

TEST(GitToolsTest, StatusToolInfo) {
    ToolInfo info = GitTools::gitStatus();
    EXPECT_EQ(info.name, "git_status");
    EXPECT_FALSE(info.description.empty());
    EXPECT_EQ(info.policy, ToolPolicy::ALLOW);
    EXPECT_TRUE(info.parameters.empty());
}

// ---------------------------------------------------------------------------
// git_diff
// ---------------------------------------------------------------------------

TEST(GitToolsTest, DiffReturnsExpectedKeys) {
    ToolInfo info = GitTools::gitDiff();
    ASSERT_TRUE(info.callback);

    json result = info.callback(json::object());

    if (result.contains("error")) {
        EXPECT_TRUE(result["error"].is_string());
    } else {
        EXPECT_TRUE(result.contains("diff"));
        EXPECT_TRUE(result.contains("files_changed"));
        EXPECT_TRUE(result["files_changed"].is_number_integer());
    }
}

TEST(GitToolsTest, DiffToolInfo) {
    ToolInfo info = GitTools::gitDiff();
    EXPECT_EQ(info.name, "git_diff");
    EXPECT_FALSE(info.description.empty());
    EXPECT_EQ(info.policy, ToolPolicy::ALLOW);
    EXPECT_EQ(info.parameters.size(), 3u);
}

// ---------------------------------------------------------------------------
// git_log
// ---------------------------------------------------------------------------

TEST(GitToolsTest, LogReturnsExpectedKeys) {
    ToolInfo info = GitTools::gitLog();
    ASSERT_TRUE(info.callback);

    json result = info.callback(json::object());

    if (result.contains("error")) {
        EXPECT_TRUE(result["error"].is_string());
    } else {
        EXPECT_TRUE(result.contains("log"));
        EXPECT_TRUE(result.contains("commits"));
        EXPECT_TRUE(result["commits"].is_number_integer());
    }
}

TEST(GitToolsTest, LogDefaultCount) {
    ToolInfo info = GitTools::gitLog();

    // Default count is 10 — verify we get at most 10 commits
    json result = info.callback(json::object());

    if (!result.contains("error")) {
        EXPECT_LE(result["commits"].get<int>(), 10);
        EXPECT_GT(result["commits"].get<int>(), 0);
    }
}

TEST(GitToolsTest, LogRespectsCount) {
    ToolInfo info = GitTools::gitLog();

    json args = {{"count", 3}};
    json result = info.callback(args);

    if (!result.contains("error")) {
        EXPECT_LE(result["commits"].get<int>(), 3);
        EXPECT_GT(result["commits"].get<int>(), 0);
    }
}

TEST(GitToolsTest, LogToolInfo) {
    ToolInfo info = GitTools::gitLog();
    EXPECT_EQ(info.name, "git_log");
    EXPECT_FALSE(info.description.empty());
    EXPECT_EQ(info.policy, ToolPolicy::ALLOW);
    EXPECT_EQ(info.parameters.size(), 3u);

    // Verify parameter names
    EXPECT_EQ(info.parameters[0].name, "count");
    EXPECT_EQ(info.parameters[0].type, ToolParamType::INTEGER);
    EXPECT_FALSE(info.parameters[0].required);

    EXPECT_EQ(info.parameters[1].name, "oneline");
    EXPECT_EQ(info.parameters[1].type, ToolParamType::BOOLEAN);
    EXPECT_FALSE(info.parameters[1].required);

    EXPECT_EQ(info.parameters[2].name, "path");
    EXPECT_EQ(info.parameters[2].type, ToolParamType::STRING);
    EXPECT_FALSE(info.parameters[2].required);
}

// ---------------------------------------------------------------------------
// git_show
// ---------------------------------------------------------------------------

TEST(GitToolsTest, ShowReturnsContentForHEAD) {
    ToolInfo info = GitTools::gitShow();
    ASSERT_TRUE(info.callback);

    json result = info.callback(json::object());

    if (result.contains("error")) {
        EXPECT_TRUE(result["error"].is_string());
    } else {
        EXPECT_TRUE(result.contains("content"));
        EXPECT_TRUE(result.contains("ref"));
        EXPECT_EQ(result["ref"].get<std::string>(), "HEAD");
        EXPECT_FALSE(result["content"].get<std::string>().empty());
    }
}

TEST(GitToolsTest, ShowWithBadRefReturnsError) {
    ToolInfo info = GitTools::gitShow();
    ASSERT_TRUE(info.callback);

    json args = {{"ref", "nonexistent_ref_abc123xyz"}};
    json result = info.callback(args);

    // Should return an error for a ref that doesn't exist
    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].is_string());
}

TEST(GitToolsTest, ShowToolInfo) {
    ToolInfo info = GitTools::gitShow();
    EXPECT_EQ(info.name, "git_show");
    EXPECT_FALSE(info.description.empty());
    EXPECT_EQ(info.policy, ToolPolicy::ALLOW);
    EXPECT_EQ(info.parameters.size(), 1u);
    EXPECT_EQ(info.parameters[0].name, "ref");
    EXPECT_EQ(info.parameters[0].type, ToolParamType::STRING);
    EXPECT_FALSE(info.parameters[0].required);
}

// ---------------------------------------------------------------------------
// registerAll
// ---------------------------------------------------------------------------

TEST(GitToolsTest, RegisterAllAddsAllTools) {
    ToolRegistry registry;

    GitTools::registerAll(registry);

    EXPECT_EQ(registry.size(), 4u);
    EXPECT_TRUE(registry.hasTool("git_status"));
    EXPECT_TRUE(registry.hasTool("git_diff"));
    EXPECT_TRUE(registry.hasTool("git_log"));
    EXPECT_TRUE(registry.hasTool("git_show"));
}

// ---------------------------------------------------------------------------
// Security: shell metacharacter rejection
// ---------------------------------------------------------------------------

TEST(GitToolsTest, ShowRejectsUnsafeRef) {
    ToolInfo info = GitTools::gitShow();

    // Semicolon injection
    json args1 = {{"ref", "HEAD; rm -rf /"}};
    json result1 = info.callback(args1);
    EXPECT_TRUE(result1.contains("error"));
    EXPECT_NE(result1["error"].get<std::string>().find("unsafe"), std::string::npos);

    // Pipe injection
    json args2 = {{"ref", "HEAD | cat /etc/passwd"}};
    json result2 = info.callback(args2);
    EXPECT_TRUE(result2.contains("error"));

    // Backtick injection
    json args3 = {{"ref", "`whoami`"}};
    json result3 = info.callback(args3);
    EXPECT_TRUE(result3.contains("error"));
}

TEST(GitToolsTest, DiffRejectsUnsafePath) {
    ToolInfo info = GitTools::gitDiff();

    json args = {{"path", "file.txt; cat /etc/shadow"}};
    json result = info.callback(args);
    EXPECT_TRUE(result.contains("error"));
    EXPECT_NE(result["error"].get<std::string>().find("unsafe"), std::string::npos);
}

TEST(GitToolsTest, DiffRejectsUnsafeRef) {
    ToolInfo info = GitTools::gitDiff();

    json args = {{"ref", "main && whoami"}};
    json result = info.callback(args);
    EXPECT_TRUE(result.contains("error"));
}

TEST(GitToolsTest, LogRejectsUnsafePath) {
    ToolInfo info = GitTools::gitLog();

    json args = {{"path", "$(evil)"}};
    json result = info.callback(args);
    EXPECT_TRUE(result.contains("error"));
}
