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
        // The read/write anchor table is process-wide; clear it so these
        // cases are independent of each other and of run order.
        FileStateTracker::instance().clear();
    }

    void TearDown() override {
        FileStateTracker::instance().clear();
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

// ---------------------------------------------------------------------------
// Hardening: stale-write rejection and ignore-aware search
//
// Separate fixture (and separate temp directory) so the process-wide
// FileStateTracker can be cleared without touching the cases above.
// ---------------------------------------------------------------------------

class FileToolsHardeningTest : public ::testing::Test {
protected:
    fs::path tempDir_;

    void SetUp() override {
        tempDir_ = fs::temp_directory_path() / "gaia_file_tools_hardening";
        std::error_code ec;
        fs::remove_all(tempDir_, ec);
        fs::create_directories(tempDir_);
        FileStateTracker::instance().clear();
    }

    void TearDown() override {
        FileStateTracker::instance().clear();
        std::error_code ec;
        fs::remove_all(tempDir_, ec);
    }

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

    std::string readFile(const std::string& path) {
        std::ifstream f(path, std::ios::binary);
        std::ostringstream buf;
        buf << f.rdbuf();
        return buf.str();
    }
};

// --- SHA-256 -----------------------------------------------------------------

TEST_F(FileToolsHardeningTest, HashContent_MatchesKnownVectors) {
    EXPECT_EQ(FileStateTracker::hashContent(""),
              "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    EXPECT_EQ(FileStateTracker::hashContent("abc"),
              "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
    // Spans multiple 64-byte blocks and exercises the length-padding path.
    EXPECT_EQ(FileStateTracker::hashContent(std::string(1000, 'a')),
              "41edece42d63e8d9bf515a9ba6932e1c20cbc9f5a5d134645adb5db1b9737ea3");
}

TEST_F(FileToolsHardeningTest, HashFile_MatchesHashContent) {
    const std::string body(200000, 'x');  // larger than the streaming buffer
    std::string path = writeFile("big.bin", body);
    EXPECT_EQ(FileStateTracker::hashFile(path),
              FileStateTracker::hashContent(body));
    EXPECT_EQ(FileStateTracker::hashFile((tempDir_ / "absent.bin").string()), "");
}

// --- Stale-write rejection ---------------------------------------------------

TEST_F(FileToolsHardeningTest, FileRead_ReturnsContentHashAndTracksFile) {
    std::string path = writeFile("tracked.txt", "original\n");

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error"));
    ASSERT_TRUE(read.contains("content_hash"));
    EXPECT_FALSE(read["content_hash"].get<std::string>().empty());
    EXPECT_TRUE(FileStateTracker::instance().hasRecord(path));

    // The hash anchors the whole file, not just the returned slice.
    json ranged = FileIOTools::fileRead().callback(
        {{"path", path}, {"start_line", 1}, {"end_line", 1}});
    EXPECT_EQ(ranged["content_hash"], read["content_hash"]);
}

TEST_F(FileToolsHardeningTest, FileEdit_RejectsEditAgainstDivergedFile) {
    std::string path = writeFile("race.txt", "alpha beta\n");

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error"));

    // Something else changes the file between the read and the edit.
    writeFile("race.txt", "alpha beta gamma delta\n");

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha"}, {"new_string", "ALPHA"}});

    ASSERT_TRUE(result.contains("error"));
    EXPECT_TRUE(result.value("stale", false));
    const std::string message = result["error"].get<std::string>();
    EXPECT_NE(message.find("changed on disk"), std::string::npos);
    EXPECT_NE(message.find("Nothing was written"), std::string::npos);
    EXPECT_NE(message.find("file_read"), std::string::npos);

    // The divergence is named: both hashes are reported and differ.
    ASSERT_TRUE(result.contains("hash_at_read"));
    ASSERT_TRUE(result.contains("hash_now"));
    EXPECT_NE(result["hash_at_read"], result["hash_now"]);
    EXPECT_NE(message.find(result["hash_at_read"].get<std::string>()),
              std::string::npos);
    EXPECT_NE(message.find(result["hash_now"].get<std::string>()),
              std::string::npos);

    // Rejected means rejected — the file is untouched.
    EXPECT_EQ(readFile(path), "alpha beta gamma delta\n");
}

TEST_F(FileToolsHardeningTest, FileWrite_RejectsWriteAgainstDivergedFile) {
    std::string path = writeFile("doc.txt", "v1\n");

    ASSERT_FALSE(FileIOTools::fileRead().callback({{"path", path}}).contains("error"));
    writeFile("doc.txt", "v2 from someone else\n");

    json result = FileIOTools::fileWrite().callback(
        {{"path", path}, {"content", "v3 from the model\n"}});

    ASSERT_TRUE(result.contains("error"));
    EXPECT_TRUE(result.value("stale", false));
    EXPECT_NE(result["error"].get<std::string>().find("changed on disk"),
              std::string::npos);
    EXPECT_EQ(readFile(path), "v2 from someone else\n");
}

TEST_F(FileToolsHardeningTest, FileEdit_SucceedsAfterReReading) {
    std::string path = writeFile("recover.txt", "alpha\n");
    FileIOTools::fileRead().callback({{"path", path}});
    writeFile("recover.txt", "alpha beta\n");

    ASSERT_TRUE(FileIOTools::fileEdit()
                    .callback({{"path", path},
                               {"old_string", "alpha"},
                               {"new_string", "ALPHA"}})
                    .contains("error"));

    // Re-read, then the same edit lands.
    FileIOTools::fileRead().callback({{"path", path}});
    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha"}, {"new_string", "ALPHA"}});

    ASSERT_FALSE(result.contains("error"));
    EXPECT_EQ(result["replacements"], 1);
    EXPECT_EQ(readFile(path), "ALPHA beta\n");
}

TEST_F(FileToolsHardeningTest, FileEdit_ConsecutiveEditsNeedNoReRead) {
    std::string path = writeFile("chain.txt", "one two three\n");
    FileIOTools::fileRead().callback({{"path", path}});

    ASSERT_FALSE(FileIOTools::fileEdit()
                     .callback({{"path", path},
                                {"old_string", "one"},
                                {"new_string", "1"}})
                     .contains("error"));
    json second = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "two"}, {"new_string", "2"}});

    ASSERT_FALSE(second.contains("error"));
    EXPECT_EQ(readFile(path), "1 2 three\n");
}

TEST_F(FileToolsHardeningTest, FileWrite_AfterWriteFollowUpEditIsAllowed) {
    std::string path = (tempDir_ / "fresh.txt").string();

    ASSERT_FALSE(FileIOTools::fileWrite()
                     .callback({{"path", path}, {"content", "hello world\n"}})
                     .contains("error"));
    json edit = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "world"}, {"new_string", "there"}});

    ASSERT_FALSE(edit.contains("error"));
    EXPECT_EQ(readFile(path), "hello there\n");
}

TEST_F(FileToolsHardeningTest, FileEdit_NonMatchingOldStringIsActionable) {
    std::string path = writeFile("nomatch.txt", "the quick brown fox\n");
    const std::string before = readFile(path);

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "lazy dog"}, {"new_string", "cat"}});

    ASSERT_TRUE(result.contains("error"));
    const std::string message = result["error"].get<std::string>();
    EXPECT_NE(message.find("not found"), std::string::npos);
    EXPECT_NE(message.find("no replacement was made"), std::string::npos);
    EXPECT_NE(message.find("file_read"), std::string::npos);
    EXPECT_EQ(result["replacements"], 0);
    EXPECT_EQ(readFile(path), before);
}

TEST_F(FileToolsHardeningTest, FileEdit_WhitespaceMismatchIsNamed) {
    std::string path = writeFile("indent.py", "def main():\n    return 1\n");

    json result = FileIOTools::fileEdit().callback(
        {{"path", path},
         {"old_string", "def main():\n        return 1"},  // wrong indentation
         {"new_string", "def main():\n    return 2"}});

    ASSERT_TRUE(result.contains("error"));
    EXPECT_NE(result["error"].get<std::string>().find("whitespace-insensitive"),
              std::string::npos);
}

TEST_F(FileToolsHardeningTest, FileWrite_RecreatesADeletedFile) {
    // Read, then the file goes away (git checkout, rm, a build clean). Writing
    // it again is a create, not a conflict — a leftover anchor must not block
    // it forever.
    std::string path = writeFile("gone.txt", "v1\n");
    FileIOTools::fileRead().callback({{"path", path}});
    std::error_code ec;
    fs::remove(path, ec);

    json result = FileIOTools::fileWrite().callback(
        {{"path", path}, {"content", "recreated\n"}});

    ASSERT_FALSE(result.contains("error")) << result.dump();
    EXPECT_EQ(readFile(path), "recreated\n");
}

TEST_F(FileToolsHardeningTest, FileEdit_ReportsAFileThatVanishedAfterRead) {
    std::string path = writeFile("vanished.txt", "v1\n");
    FileIOTools::fileRead().callback({{"path", path}});
    std::error_code ec;
    fs::remove(path, ec);

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "v1"}, {"new_string", "v2"}});

    ASSERT_TRUE(result.contains("error"));
    EXPECT_NE(result["error"].get<std::string>().find("Cannot open"),
              std::string::npos);
}

TEST_F(FileToolsHardeningTest, FileStateTracker_ForgetDropsTheAnchor) {
    std::string path = writeFile("forget.txt", "v1\n");
    FileIOTools::fileRead().callback({{"path", path}});
    writeFile("forget.txt", "v2\n");

    FileStateTracker::instance().forget(path);
    EXPECT_FALSE(FileStateTracker::instance().hasRecord(path));

    // With no anchor there is nothing to be stale about.
    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "v2"}, {"new_string", "v3"}});
    EXPECT_FALSE(result.contains("error"));
}

TEST_F(FileToolsHardeningTest, FileStateTracker_RecordsAreKeyedCanonically) {
    std::string path = writeFile("canon.txt", "data\n");
    FileIOTools::fileRead().callback({{"path", path}});

    // The same file reached through a redundant "." segment is one record.
    const std::string viaDot = (tempDir_ / "." / "canon.txt").string();
    EXPECT_TRUE(FileStateTracker::instance().hasRecord(viaDot));
    EXPECT_EQ(FileStateTracker::instance().size(), 1u);
}

// --- Ignore-aware search -----------------------------------------------------

TEST_F(FileToolsHardeningTest, FileSearch_SkipsGitignoredPaths) {
    // A realistic repo: .git, a .gitignore, vendored deps and build output.
    fs::create_directories(tempDir_ / ".git");
    writeFile(".gitignore", "node_modules/\nbuild/\n*.log\n");
    writeFile("src/index.js", "export const answer = 42;\n");
    writeFile("src/util.js", "export const noop = () => {};\n");
    writeFile("node_modules/left-pad/index.js", "module.exports = 1;\n");
    writeFile("node_modules/react/react.js", "module.exports = 2;\n");
    writeFile("build/bundle.js", "/* generated */\n");
    writeFile("debug.log", "noise\n");
    writeFile(".git/hooks/pre-commit.js", "#!/bin/sh\n");

    json result = FileIOTools::fileSearch().callback(
        {{"pattern", "*.js"}, {"path", tempDir_.string()}});

    ASSERT_FALSE(result.contains("error"));
    EXPECT_EQ(result["total"], 2);
    for (const auto& match : result["matches"]) {
        const std::string path = match["path"].get<std::string>();
        EXPECT_EQ(path.find("node_modules"), std::string::npos) << path;
        EXPECT_EQ(path.find("/build/"), std::string::npos) << path;
        EXPECT_EQ(path.find("/.git/"), std::string::npos) << path;
    }
    // The skips are reported, so a thin result set is explained.
    EXPECT_GT(result["ignored_skipped"].get<int>(), 0);
}

TEST_F(FileToolsHardeningTest, FileSearch_ContentSearchSkipsIgnoredFiles) {
    fs::create_directories(tempDir_ / ".git");
    writeFile(".gitignore", "vendor/\n");
    writeFile("src/app.ts", "const needle = 1;\n");
    writeFile("vendor/lib.ts", "const needle = 2;\n");

    json result = FileIOTools::fileSearch().callback({
        {"pattern", "*.ts"},
        {"path", tempDir_.string()},
        {"content_pattern", "needle"},
    });

    ASSERT_FALSE(result.contains("error"));
    EXPECT_EQ(result["total"], 1);
    EXPECT_NE(result["matches"][0]["path"].get<std::string>().find("src/app.ts"),
              std::string::npos);
}

TEST_F(FileToolsHardeningTest, FileSearch_NegatedGitignoreRuleIsHonored) {
    fs::create_directories(tempDir_ / ".git");
    writeFile(".gitignore", "*.log\n!keep.log\n");
    writeFile("drop.log", "x\n");
    writeFile("keep.log", "y\n");

    json result = FileIOTools::fileSearch().callback(
        {{"pattern", "*.log"}, {"path", tempDir_.string()}});

    ASSERT_FALSE(result.contains("error"));
    EXPECT_EQ(result["total"], 1);
    EXPECT_NE(result["matches"][0]["path"].get<std::string>().find("keep.log"),
              std::string::npos);
}

TEST_F(FileToolsHardeningTest, FileSearch_PathPatternsAndCharacterClasses) {
    writeFile("src/core/agent.h", "#pragma once\n");
    writeFile("src/core/agent.cpp", "// impl\n");
    writeFile("include/agent.h", "#pragma once\n");
    writeFile("test_1.cpp", "// one\n");
    writeFile("test_x.cpp", "// x\n");

    json byPath = FileIOTools::fileSearch().callback(
        {{"pattern", "src/**/*.h"}, {"path", tempDir_.string()}});
    ASSERT_FALSE(byPath.contains("error"));
    EXPECT_EQ(byPath["total"], 1);
    EXPECT_NE(byPath["matches"][0]["path"].get<std::string>().find("src/core/agent.h"),
              std::string::npos);

    json byClass = FileIOTools::fileSearch().callback(
        {{"pattern", "test_[0-9].cpp"}, {"path", tempDir_.string()}});
    ASSERT_FALSE(byClass.contains("error"));
    EXPECT_EQ(byClass["total"], 1);
}

// --- Regressions found in review --------------------------------------------

TEST_F(FileToolsHardeningTest, FileRead_ReturnsRawBytesSoEditsMatch) {
    // A CRLF file must read back with its CRLFs intact: the model copies
    // old_string out of what file_read showed it, and file_edit searches the
    // bytes on disk. If the two disagree the edit can never match.
    std::string path = writeFile("crlf.txt", "alpha\r\nbeta\r\n");

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error"));
    const std::string shown = read["content"].get<std::string>();
    EXPECT_NE(shown.find("alpha\r"), std::string::npos);
    EXPECT_EQ(read["lines"], 2);

    json edit = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha\r\nbeta"}, {"new_string", "one\r\ntwo"}});
    ASSERT_FALSE(edit.contains("error")) << edit.dump();
    EXPECT_EQ(readFile(path), "one\r\ntwo\r\n");
}

TEST_F(FileToolsHardeningTest, FileRead_HashAnchorsTheBytesItShowed) {
    // One pass over the file: the hash must be of exactly the content
    // returned, never of a second, later read.
    const std::string body = "alpha\nbeta\ngamma\n";
    std::string path = writeFile("anchor.txt", body);

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error"));
    EXPECT_EQ(read["content_hash"].get<std::string>(),
              FileStateTracker::hashContent(body).substr(0, 12));
}

TEST_F(FileToolsHardeningTest, FileRead_LineCountsAndRanges) {
    EXPECT_EQ(FileIOTools::fileRead()
                  .callback({{"path", writeFile("nl.txt", "a\nb\n")}})["lines"],
              2);
    EXPECT_EQ(FileIOTools::fileRead()
                  .callback({{"path", writeFile("nonl.txt", "a\nb")}})["lines"],
              2);
    EXPECT_EQ(FileIOTools::fileRead()
                  .callback({{"path", writeFile("empty.txt", "")}})["lines"],
              0);

    json blanks = FileIOTools::fileRead().callback(
        {{"path", writeFile("blanks.txt", "AAA\n\nBBB\n")}});
    EXPECT_EQ(blanks["content"].get<std::string>(), "AAA\n\nBBB");
}

TEST_F(FileToolsHardeningTest, FileWrite_RecreationIsReported) {
    std::string path = writeFile("resurrect.txt", "v1\n");
    FileIOTools::fileRead().callback({{"path", path}});
    std::error_code ec;
    fs::remove(path, ec);

    json result = FileIOTools::fileWrite().callback(
        {{"path", path}, {"content", "back\n"}});

    ASSERT_FALSE(result.contains("error"));
    EXPECT_TRUE(result.value("recreated", false));

    // An ordinary create says nothing about recreation.
    json plain = FileIOTools::fileWrite().callback(
        {{"path", (tempDir_ / "brand_new.txt").string()}, {"content", "x\n"}});
    EXPECT_FALSE(plain.contains("recreated"));
}

TEST_F(FileToolsHardeningTest, FileSearch_HonorsNestedGitignoreFiles) {
    // .gitignore files below the search root govern their own subtree only.
    fs::create_directories(tempDir_ / ".git");
    writeFile(".gitignore", "*.log\n");
    writeFile("pkg/.gitignore", "generated/\n");
    writeFile("pkg/src/main.ts", "export {};\n");
    writeFile("pkg/generated/api.ts", "// generated\n");
    writeFile("other/generated/keep.ts", "// a sibling, not governed\n");

    json result = FileIOTools::fileSearch().callback(
        {{"pattern", "*.ts"}, {"path", tempDir_.string()}});

    ASSERT_FALSE(result.contains("error"));
    EXPECT_EQ(result["total"], 2);
    for (const auto& match : result["matches"]) {
        EXPECT_EQ(match["path"].get<std::string>().find("pkg/generated"),
                  std::string::npos);
    }
}

TEST_F(FileToolsHardeningTest, FileSearch_GitDirectoryIsNotCountedAsAnIgnoreSkip) {
    // The counter exists to explain a thin result set; counting the always
    // pruned .git would make it non-zero on every repository.
    fs::create_directories(tempDir_ / ".git");
    writeFile("src/a.ts", "x\n");

    json result = FileIOTools::fileSearch().callback(
        {{"pattern", "*.ts"}, {"path", tempDir_.string()}});

    EXPECT_EQ(result["total"], 1);
    EXPECT_EQ(result["ignored_skipped"], 0);
}

TEST_F(FileToolsHardeningTest, FileSearch_OversizedPatternIsRejected) {
    json result = FileIOTools::fileSearch().callback(
        {{"pattern", std::string(600, '*')}, {"path", tempDir_.string()}});

    ASSERT_TRUE(result.contains("error"));
    EXPECT_NE(result["error"].get<std::string>().find("limit is 512"),
              std::string::npos);
}
