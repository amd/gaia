// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/file_tools.h>
#include <gaia/tool_registry.h>

#include <filesystem>
#include <fstream>
#include <string>

namespace fs = std::filesystem;
using namespace gaia;

class FileToolsTest : public ::testing::Test {
protected:
    fs::path tempDir_;

    void SetUp() override {
        tempDir_ = fs::temp_directory_path() / "gaia_file_tools_test";
        fs::create_directories(tempDir_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(tempDir_, ec);
    }

    /// Write a helper file and return its path.
    std::string writeFile(const std::string& name, const std::string& content) {
        fs::path p = tempDir_ / name;
        if (p.has_parent_path()) {
            fs::create_directories(p.parent_path());
        }
        std::ofstream f(p, std::ios::binary);
        f << content;
        f.close();
        return p.string();
    }

    /// Read a file back for verification.
    std::string readFile(const std::string& path) {
        std::ifstream f(path);
        std::ostringstream buf;
        buf << f.rdbuf();
        return buf.str();
    }
};

// ---------------------------------------------------------------------------
// file_read tests
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, FileRead_BasicContent) {
    std::string path = writeFile("hello.txt", "line1\nline2\nline3\n");

    ToolInfo tool = FileIOTools::fileRead();
    ASSERT_TRUE(tool.callback);

    json result = tool.callback({{"path", path}});
    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["path"], path);
    EXPECT_EQ(result["lines"], 3);
    // Content should contain all three lines
    std::string content = result["content"].get<std::string>();
    EXPECT_TRUE(content.find("line1") != std::string::npos);
    EXPECT_TRUE(content.find("line2") != std::string::npos);
    EXPECT_TRUE(content.find("line3") != std::string::npos);
}

TEST_F(FileToolsTest, FileRead_WithLineRange) {
    std::string path = writeFile("lines.txt", "AAA\nBBB\nCCC\nDDD\nEEE\n");

    ToolInfo tool = FileIOTools::fileRead();
    json result = tool.callback({{"path", path}, {"start_line", 2}, {"end_line", 4}});

    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["lines"], 5);

    std::string content = result["content"].get<std::string>();
    EXPECT_TRUE(content.find("BBB") != std::string::npos);
    EXPECT_TRUE(content.find("CCC") != std::string::npos);
    EXPECT_TRUE(content.find("DDD") != std::string::npos);
    EXPECT_TRUE(content.find("AAA") == std::string::npos);
    EXPECT_TRUE(content.find("EEE") == std::string::npos);
}

TEST_F(FileToolsTest, FileRead_MissingFile) {
    ToolInfo tool = FileIOTools::fileRead();
    json result = tool.callback({{"path", (tempDir_ / "nonexistent.txt").string()}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("Cannot open") != std::string::npos);
}

TEST_F(FileToolsTest, FileRead_EmptyPath) {
    ToolInfo tool = FileIOTools::fileRead();
    json result = tool.callback({{"path", ""}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("required") != std::string::npos);
}

// ---------------------------------------------------------------------------
// file_write tests
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, FileWrite_BasicWrite) {
    std::string path = (tempDir_ / "output.txt").string();

    ToolInfo tool = FileIOTools::fileWrite();
    ASSERT_TRUE(tool.callback);

    json result = tool.callback({{"path", path}, {"content", "Hello, world!"}});
    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["success"], true);
    EXPECT_EQ(result["path"], path);
    EXPECT_EQ(result["bytes_written"], 13);

    // Verify on disk
    EXPECT_EQ(readFile(path), "Hello, world!");
}

TEST_F(FileToolsTest, FileWrite_CreatesParentDirs) {
    std::string path = (tempDir_ / "sub" / "dir" / "nested.txt").string();

    ToolInfo tool = FileIOTools::fileWrite();
    json result = tool.callback({{"path", path}, {"content", "nested content"}});

    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["success"], true);
    EXPECT_TRUE(fs::exists(path));
    EXPECT_EQ(readFile(path), "nested content");
}

TEST_F(FileToolsTest, FileWrite_EmptyPath) {
    ToolInfo tool = FileIOTools::fileWrite();
    json result = tool.callback({{"path", ""}, {"content", "data"}});

    EXPECT_TRUE(result.contains("error"));
}

TEST_F(FileToolsTest, FileWrite_MissingContent) {
    std::string path = (tempDir_ / "no_content.txt").string();

    ToolInfo tool = FileIOTools::fileWrite();
    json result = tool.callback({{"path", path}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("content") != std::string::npos);
}

// ---------------------------------------------------------------------------
// file_edit tests
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, FileEdit_BasicReplacement) {
    std::string path = writeFile("edit_me.txt", "foo bar baz foo");

    ToolInfo tool = FileIOTools::fileEdit();
    ASSERT_TRUE(tool.callback);

    json result = tool.callback({{"path", path}, {"old_string", "foo"}, {"new_string", "qux"}});
    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["success"], true);
    EXPECT_EQ(result["replacements"], 2);
    EXPECT_EQ(result["path"], path);

    EXPECT_EQ(readFile(path), "qux bar baz qux");
}

TEST_F(FileToolsTest, FileEdit_StringNotFound) {
    std::string path = writeFile("no_match.txt", "hello world");

    ToolInfo tool = FileIOTools::fileEdit();
    json result = tool.callback({{"path", path}, {"old_string", "xyz"}, {"new_string", "abc"}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("not found") != std::string::npos);
}

TEST_F(FileToolsTest, FileEdit_MissingFile) {
    ToolInfo tool = FileIOTools::fileEdit();
    json result = tool.callback({
        {"path", (tempDir_ / "gone.txt").string()},
        {"old_string", "a"},
        {"new_string", "b"},
    });

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("Cannot open") != std::string::npos);
}

TEST_F(FileToolsTest, FileEdit_EmptyOldString) {
    std::string path = writeFile("empty_old.txt", "data");

    ToolInfo tool = FileIOTools::fileEdit();
    json result = tool.callback({{"path", path}, {"old_string", ""}, {"new_string", "x"}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("old_string") != std::string::npos);
}

// ---------------------------------------------------------------------------
// file_search tests
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, FileSearch_ByNamePattern) {
    writeFile("alpha.cpp", "int main() {}");
    writeFile("beta.cpp", "void foo() {}");
    writeFile("gamma.h", "#pragma once");

    ToolInfo tool = FileIOTools::fileSearch();
    ASSERT_TRUE(tool.callback);

    json result = tool.callback({{"pattern", "*.cpp"}, {"path", tempDir_.string()}});
    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["total"], 2);
    EXPECT_EQ(result["matches"].size(), 2u);
}

TEST_F(FileToolsTest, FileSearch_WithContentPattern) {
    writeFile("a.txt", "hello world\ngoodbye world\n");
    writeFile("b.txt", "nothing here\n");
    writeFile("c.txt", "hello again\n");

    ToolInfo tool = FileIOTools::fileSearch();
    json result = tool.callback({
        {"pattern", "*.txt"},
        {"path", tempDir_.string()},
        {"content_pattern", "hello"},
    });

    EXPECT_FALSE(result.contains("error"));
    // a.txt has "hello" on line 1, c.txt has "hello" on line 1 => 2 matches
    EXPECT_EQ(result["total"], 2);

    // Each match should have line and context
    for (const auto& m : result["matches"]) {
        EXPECT_TRUE(m.contains("line"));
        EXPECT_TRUE(m.contains("context"));
        std::string ctx = m["context"].get<std::string>();
        EXPECT_TRUE(ctx.find("hello") != std::string::npos);
    }
}

TEST_F(FileToolsTest, FileSearch_NonexistentPath) {
    ToolInfo tool = FileIOTools::fileSearch();
    json result = tool.callback({{"pattern", "*"}, {"path", (tempDir_ / "nope").string()}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("does not exist") != std::string::npos);
}

TEST_F(FileToolsTest, FileSearch_EmptyPattern) {
    ToolInfo tool = FileIOTools::fileSearch();
    json result = tool.callback({{"pattern", ""}, {"path", tempDir_.string()}});

    EXPECT_TRUE(result.contains("error"));
    EXPECT_TRUE(result["error"].get<std::string>().find("required") != std::string::npos);
}

TEST_F(FileToolsTest, FileSearch_MaxResults) {
    // Create more files than max_results
    for (int i = 0; i < 10; ++i) {
        writeFile("file" + std::to_string(i) + ".txt", "content");
    }

    ToolInfo tool = FileIOTools::fileSearch();
    json result = tool.callback({
        {"pattern", "*.txt"},
        {"path", tempDir_.string()},
        {"max_results", 3},
    });

    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["total"], 10);
    EXPECT_EQ(result["matches"].size(), 3u);
}

// ---------------------------------------------------------------------------
// registerAll
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, RegisterAll_RegistersAllTools) {
    ToolRegistry registry;
    FileIOTools::registerAll(registry);

    EXPECT_EQ(registry.size(), 4u);
    EXPECT_TRUE(registry.hasTool("file_read"));
    EXPECT_TRUE(registry.hasTool("file_write"));
    EXPECT_TRUE(registry.hasTool("file_edit"));
    EXPECT_TRUE(registry.hasTool("file_search"));
}

// ---------------------------------------------------------------------------
// ToolInfo structure validation
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, ToolInfo_FileReadParams) {
    ToolInfo info = FileIOTools::fileRead();
    EXPECT_EQ(info.name, "file_read");
    EXPECT_EQ(info.policy, ToolPolicy::ALLOW);
    EXPECT_EQ(info.parameters.size(), 3u);
    // First param: path (required)
    EXPECT_EQ(info.parameters[0].name, "path");
    EXPECT_TRUE(info.parameters[0].required);
    // Second/third params: optional
    EXPECT_EQ(info.parameters[1].name, "start_line");
    EXPECT_FALSE(info.parameters[1].required);
    EXPECT_EQ(info.parameters[2].name, "end_line");
    EXPECT_FALSE(info.parameters[2].required);
}

TEST_F(FileToolsTest, ToolInfo_FileWriteParams) {
    ToolInfo info = FileIOTools::fileWrite();
    EXPECT_EQ(info.name, "file_write");
    EXPECT_EQ(info.policy, ToolPolicy::CONFIRM);
    EXPECT_EQ(info.parameters.size(), 2u);
    EXPECT_EQ(info.parameters[0].name, "path");
    EXPECT_TRUE(info.parameters[0].required);
    EXPECT_EQ(info.parameters[1].name, "content");
    EXPECT_TRUE(info.parameters[1].required);
}

TEST_F(FileToolsTest, ToolInfo_FileEditParams) {
    ToolInfo info = FileIOTools::fileEdit();
    EXPECT_EQ(info.name, "file_edit");
    EXPECT_EQ(info.policy, ToolPolicy::CONFIRM);
    EXPECT_EQ(info.parameters.size(), 3u);
    EXPECT_EQ(info.parameters[0].name, "path");
    EXPECT_EQ(info.parameters[1].name, "old_string");
    EXPECT_EQ(info.parameters[2].name, "new_string");
    for (const auto& p : info.parameters) {
        EXPECT_TRUE(p.required);
    }
}

TEST_F(FileToolsTest, ToolInfo_FileSearchParams) {
    ToolInfo info = FileIOTools::fileSearch();
    EXPECT_EQ(info.name, "file_search");
    EXPECT_EQ(info.policy, ToolPolicy::ALLOW);
    EXPECT_EQ(info.parameters.size(), 4u);
    EXPECT_EQ(info.parameters[0].name, "pattern");
    EXPECT_TRUE(info.parameters[0].required);
    EXPECT_EQ(info.parameters[1].name, "path");
    EXPECT_FALSE(info.parameters[1].required);
    EXPECT_EQ(info.parameters[2].name, "content_pattern");
    EXPECT_FALSE(info.parameters[2].required);
    EXPECT_EQ(info.parameters[3].name, "max_results");
    EXPECT_FALSE(info.parameters[3].required);
}
