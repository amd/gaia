// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "support/unique_temp.h"
#include <gtest/gtest.h>
#include <gaia/file_tools.h>
#include <gaia/tool_registry.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>
#include <string>

namespace fs = std::filesystem;
using namespace gaia;

class FileToolsTest : public ::testing::Test {
protected:
    fs::path tempDir_;

    void SetUp() override {
        tempDir_ = fs::temp_directory_path() / testsupport::perProcess("gaia_file_tools_test");
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

TEST_F(FileToolsTest, FileRead_OneRepeatedCharacterIsDescribedNotReturned) {
    ToolInfo tool = FileIOTools::fileRead();
    json result = tool.callback({{"path", writeFile("report.txt", std::string(4000, 'D'))}});
    EXPECT_FALSE(result.contains("error"));
    EXPECT_LT(result["content"].get<std::string>().size(), 40u);
    EXPECT_NE(result["note"].get<std::string>().find("no real content"), std::string::npos);

    json large = tool.callback({{"path", writeFile("big.txt", std::string(40000, 'D'))}});
    EXPECT_NE(large.value("note", "").find("first 32 KB"), std::string::npos) << large.dump().substr(0, 300);
    std::string column;
    for (int i = 0; i < 1500; ++i) column += "0\n";
    EXPECT_FALSE(tool.callback({{"path", writeFile("column.txt", column)}}).contains("note"));

    // Real text, a short run, and a ranged read come back as they are.
    json prose = tool.callback({{"path", writeFile("prose.txt", std::string(3000, 'a') + " then a sentence.")}});
    EXPECT_FALSE(prose.contains("note"));
    json brief = tool.callback({{"path", writeFile("rule.txt", std::string(200, '-'))}});
    EXPECT_EQ(brief["content"].get<std::string>().size(), 200u);
    std::string rows;
    for (int i = 0; i < 600; ++i) rows += "0,0\n";
    json ranged = tool.callback({{"path", writeFile("zeros.csv", rows)}, {"start_line", 1}, {"end_line", 5}});
    EXPECT_EQ(ranged.value("note", "").find("no real content"), std::string::npos);
}

TEST_F(FileToolsTest, FileRead_AMissingFileNamesTheNearMatchBesideIt) {
    ToolInfo tool = FileIOTools::fileRead();
    const std::string real = writeFile("q3_summary.md", "Revenue: 412,000\n");
    const std::string typo = real.substr(0, real.size() - std::string("q3_summary.md").size()) + "q3_sumary.md";
    const std::string error = tool.callback({{"path", typo}}).value("error", "");
    EXPECT_NE(error.find("q3_summary.md"), std::string::npos) << error;
    EXPECT_NE(error.find("tell the user"), std::string::npos) << error;
    const std::string other = real.substr(0, real.size() - std::string("q3_summary.md").size()) + "budget.md";
    EXPECT_EQ(tool.callback({{"path", other}}).value("error", "").find("q3_summary.md"), std::string::npos);
}

TEST_F(FileToolsTest, FileRead_ALaterVersionBesideTheFileIsPointedOut) {
    ToolInfo tool = FileIOTools::fileRead();
    const std::string v1 = writeFile("pricing_policy_v1.md", "Discount limit: 15%\n");
    writeFile("pricing_policy_v2.md", "Discount limit: 12%\n");
    const std::string old = writeFile("terms_2024.md", "Net 30\n");
    const std::string current = writeFile("terms_2026.txt", "Net 45\n");
    EXPECT_NE(tool.callback({{"path", v1}}).value("note", "").find("pricing_policy_v2.md"), std::string::npos);
    EXPECT_NE(tool.callback({{"path", old}}).value("note", "").find("terms_2026.txt"), std::string::npos);
    const std::string newest = tool.callback({{"path", current}}).value("note", "");
    EXPECT_NE(newest.find("terms_2024.md"), std::string::npos) << newest;
    EXPECT_NE(newest.find("earlier version"), std::string::npos) << newest;

    // Dated notes, data files and unrelated names are not versions of one document.
    const std::string roundup = writeFile("news_2026-09.md", "a\n");
    writeFile("news_2026-10.md", "b\n");
    const std::string data = writeFile("sales_2024.csv", "a,b\n1,2\n");
    writeFile("sales_2025.csv", "a,b\n3,4\n");
    const std::string lone = writeFile("v1.md", "x\n");
    writeFile("v2.md", "y\n");
    for (const std::string& p : {roundup, data, lone}) {
        EXPECT_FALSE(tool.callback({{"path", p}}).contains("note")) << p;
    }
}

TEST_F(FileToolsTest, FileRead_Utf16TextIsDecoded) {
    ToolInfo tool = FileIOTools::fileRead();
    std::string raw = "\xFF\xFE";
    for (char c : std::string("team,score\r\nSync,4\r\nCore,9\r\nSync,6\r\n")) {
        raw.push_back(c);
        raw.push_back('\0');
    }
    raw += std::string("\xE9\x00\n\x00", 4);  // e-acute, then a line end
    const std::string path = writeFile("survey_utf16.csv", raw);
    const json read = tool.callback({{"path", path}});
    EXPECT_EQ(read.value("content", ""), "team,score\nSync,4\nCore,9\nSync,6\n\xC3\xA9") << read.dump();
    EXPECT_NE(read.value("note", "").find("UTF-16"), std::string::npos);
    EXPECT_NE(read.value("summary", "").find("score by team: Sync 10"), std::string::npos) << read.dump();
    EXPECT_EQ(tool.callback({{"path", path}, {"start_line", 2}, {"end_line", 2}}).value("content", ""), "Sync,4");
}

TEST_F(FileToolsTest, FileRead_AStrayWindows1252ByteIsShownAsItsCharacter) {
    ToolInfo tool = FileIOTools::fileRead();
    const std::string menu = writeFile("menu.txt", "Tasting menu for ten: \x80" "1,250\nCaf\xE9 terrace, na\xC3\xAFve art\n");
    const json read = tool.callback({{"path", menu}});
    const std::string content = read.value("content", "");
    EXPECT_NE(content.find("\xE2\x82\xAC" "1,250"), std::string::npos) << content;
    EXPECT_NE(content.find("Caf\xC3\xA9 terrace, na\xC3\xAFve"), std::string::npos) << content;
    EXPECT_NE(read.value("note", "").find("2 bytes are not UTF-8"), std::string::npos) << read.dump();
    const std::string plain = writeFile("plain.txt", "na\xC3\xAFve\n");
    EXPECT_FALSE(tool.callback({{"path", plain}}).contains("note"));
}

TEST_F(FileToolsTest, FileRead_ACsvComesWithItsTotals) {
    ToolInfo tool = FileIOTools::fileRead();
    const std::string messy = writeFile(
        "sales_messy.csv",
        "Date,Region,Rep,Amount\n2026-01-04,North,Priya,\"$1,204.50\"\n2026-01-06,south,Marco,880.00\n"
        "2026-01-09,North ,Priya,\"$2,310.00\"\n\nDate,Region,Rep,Amount\n2026-02-22,NORTH,Lena,\"$1,480.00\"\n"
        "TOTAL,,,\"$5,874.50\"\n");
    const json read = tool.callback({{"path", messy}});
    const std::string summary = read.value("summary", "");
    EXPECT_NE(summary.find("all 4 data rows"), std::string::npos) << summary;
    EXPECT_NE(summary.find("Amount: total 5874.50 over 4 rows (average 1468.6"), std::string::npos) << summary;
    EXPECT_NE(summary.find("Amount by Region: North 4994.50 (85.0%), south 880.00 (15.0%)"), std::string::npos)
        << summary;
    EXPECT_NE(summary.find("Amount by Date: 2026-01 4394.50"), std::string::npos) << summary;
    EXPECT_NE(summary.find("rows by Rep: Lena 1, Marco 1, Priya 2"), std::string::npos) << summary;
    EXPECT_NE(read.value("note", "").find("summary field"), std::string::npos);

    // Semicolons, whole numbers, and a cell that is not a number.
    const std::string ledger = writeFile("ledger.csv", "team;units\nA;3\nB;n/a\nA;4\nB;5\nA;1\n");
    const std::string units = tool.callback({{"path", ledger}}).value("summary", "");
    EXPECT_NE(units.find("units: total 13 over 4 rows"), std::string::npos) << units;
    EXPECT_NE(units.find("1 cells are not numbers"), std::string::npos) << units;
    EXPECT_NE(units.find("units by team: A 8 (61.5%), B 5 (38.5%)"), std::string::npos) << units;
    EXPECT_NE(units.find("average units by team: B 5.00, A 2.67"), std::string::npos) << units;

    // A header with no rows points at the log beside it.
    const std::string empty = writeFile("orders_2026-09.csv", "order_id,amount\n");
    writeFile("export_run.txt", "ERROR could not connect\n");
    EXPECT_NE(tool.callback({{"path", empty}}).value("note", "").find("export_run.txt"), std::string::npos);

    const std::string euro = writeFile("konto.csv", "posten;betrag\nMiete;1.200,50\nStrom;80,25\nMiete;1200,50\n");
    const std::string betrag = tool.callback({{"path", euro}}).value("summary", "");
    EXPECT_NE(betrag.find("betrag: total 2481.25 over 3 rows"), std::string::npos) << betrag;

    // Nothing to total: no summary.
    const std::string words = writeFile("words.csv", "term,meaning\nalpha,the first one of a long series of them\n"
                                                     "beta,the second one of a long series of things\n");
    EXPECT_FALSE(tool.callback({{"path", words}}).contains("summary"));
}

TEST_F(FileToolsTest, FileRead_ACsvsRowsThatAreNotDataAreNamed) {
    ToolInfo tool = FileIOTools::fileRead();
    const std::string messy = writeFile(
        "sales_messy.csv",
        "Date,Region,Rep,Amount\n2026-01-04,North,Priya,\"$1,204.50\"\n2026-01-06,south,Marco,880.00\n"
        "2026-01-09,North ,Priya,\"$2,310.00\"\n\nDate,Region,Rep,Amount\n2026-01-22,NORTH,Lena,\"$1,480.00\"\n"
        "TOTAL,,,\"$5,874.50\"\n");
    const std::string note = tool.callback({{"path", messy}}).value("note", "");
    EXPECT_NE(note.find("line 6 repeats the header"), std::string::npos) << note;
    EXPECT_NE(note.find("line 8 is a totals row"), std::string::npos) << note;
    EXPECT_NE(note.find("1 blank line"), std::string::npos) << note;
    EXPECT_NE(note.find("column region spells one value several ways"), std::string::npos) << note;

    const std::string headerOnly = writeFile("orders_2026-09.csv", "order_id,customer,amount,placed_at\n");
    EXPECT_NE(tool.callback({{"path", headerOnly}}).value("note", "").find("no data rows"), std::string::npos);
    const std::string ledger = writeFile("ledger.csv", "datum;posten;betrag\n2026-01-01;Miete;1200,00\n");
    EXPECT_NE(tool.callback({{"path", ledger}}).value("note", "").find("semicolons"), std::string::npos);

    // A tidy file gets no such note.
    const std::string tidy = writeFile("tidy.csv", "region,amount\nNorth,1200\nSouth,340\nNorth,2150\n");
    EXPECT_EQ(tool.callback({{"path", tidy}}).value("note", "").find("Before totalling"), std::string::npos);
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
    std::string path = writeFile("edit_me.txt", "foo bar baz");

    ToolInfo tool = FileIOTools::fileEdit();
    ASSERT_TRUE(tool.callback);

    json result = tool.callback({{"path", path}, {"old_string", "foo"}, {"new_string", "qux"}});
    EXPECT_FALSE(result.contains("error"));
    EXPECT_EQ(result["success"], true);
    EXPECT_EQ(result["replacements"], 1);
    EXPECT_EQ(result["path"], path);

    EXPECT_EQ(readFile(path), "qux bar baz");
}

TEST_F(FileToolsTest, FileEdit_AmbiguousOldStringIsRejected) {
    // Two matches: neither "edit the first" nor "edit both" is what the caller
    // asked for, and both report success. Refuse instead (#3377).
    std::string path = writeFile("ambiguous.txt", "foo bar baz foo");

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "foo"}, {"new_string", "qux"}});

    ASSERT_TRUE(result.contains("error")) << result.dump();
    EXPECT_EQ(result["ambiguous"], true);
    EXPECT_EQ(result["replacements"], 0);
    EXPECT_NE(result["error"].get<std::string>().find("2 locations"), std::string::npos);

    // Untouched — an ambiguous edit must not be a partial edit.
    EXPECT_EQ(readFile(path), "foo bar baz foo");
}

TEST_F(FileToolsTest, FileEdit_AmbiguityNamesEveryMatchingLine) {
    std::string path = writeFile("ambiguous_lines.txt", "alpha\nvalue\nbeta\nvalue\ngamma\n");

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "value"}, {"new_string", "other"}});

    ASSERT_TRUE(result.contains("match_lines")) << result.dump();
    EXPECT_EQ(result["match_lines"], (json::array({2, 4})));
}

TEST_F(FileToolsTest, FileEdit_UniqueMatchStillWorksWithContext) {
    // The documented recovery from an ambiguity error: add surrounding lines.
    std::string path = writeFile("recover.txt", "alpha\nvalue\nbeta\nvalue\ngamma\n");

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha\nvalue"}, {"new_string", "alpha\nother"}});

    ASSERT_FALSE(result.contains("error")) << result.dump();
    EXPECT_EQ(result["replacements"], 1);
    EXPECT_EQ(readFile(path), "alpha\nother\nbeta\nvalue\ngamma\n");
}

TEST_F(FileToolsTest, FileEdit_StaleRejectionIsNotADeadEnd) {
    // Rejecting forever is as broken as clobbering: the rejection hands the
    // current content back, so it counts as a read and the retry goes through.
    std::string path = writeFile("relock.txt", "alpha\nbeta\n");
    FileStateTracker::instance().recordRead(path, "something older");

    json rejected = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha"}, {"new_string", "gamma"}});
    ASSERT_TRUE(rejected.contains("error")) << rejected.dump();
    EXPECT_EQ(rejected["stale"], true);
    EXPECT_TRUE(rejected.contains("current_content"));

    json retry = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha"}, {"new_string", "gamma"}});
    ASSERT_FALSE(retry.contains("error")) << retry.dump();
    EXPECT_EQ(readFile(path), "gamma\nbeta\n");
}

TEST_F(FileToolsTest, FileEdit_RejectionCarriesCurrentContent) {
    // A rejected edit must hand back the text, or the retry costs a re-read.
    std::string path = writeFile("carry.txt", "alpha\nbeta\ngamma\n");

    json result = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "nowhere"}, {"new_string", "x"}});

    ASSERT_TRUE(result.contains("current_content")) << result.dump();
    EXPECT_NE(result["current_content"].get<std::string>().find("alpha"),
              std::string::npos);
    EXPECT_EQ(result["current_content_total_lines"], 3);
}

TEST_F(FileToolsTest, FileEdit_ExcerptFollowsTheIntendedRegion) {
    // Getting a later line's indentation wrong is the commonest miss. The
    // excerpt must land where the caller was aiming, not on line 1 of a long
    // file — the anchor is the first line of old_string, matched without its
    // own indentation.
    std::string body;
    for (int i = 1; i <= 200; ++i) body += "filler line " + std::to_string(i) + "\n";
    body += "def target_function():\n    inner = 1\n";
    std::string path = writeFile("long.py", body);

    json result = FileIOTools::fileEdit().callback(
        {{"path", path},
         {"old_string", "def target_function():\n        inner = 1"},  // wrong indent
         {"new_string", "def target_function():\n    inner = 2"}});

    ASSERT_TRUE(result.contains("error")) << result.dump();
    EXPECT_NE(result["current_content"].get<std::string>().find("target_function"),
              std::string::npos);
    EXPECT_GT(result["current_content_start_line"].get<int>(), 150);
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
        tempDir_ = fs::temp_directory_path() / testsupport::perProcess("gaia_file_tools_hardening");
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
    // The error hands the text back rather than sending the model to re-read.
    EXPECT_NE(message.find("current_content"), std::string::npos);
    EXPECT_NE(result["current_content"].get<std::string>().find("quick brown"),
              std::string::npos);
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

TEST_F(FileToolsHardeningTest, CrlfFile_ReadShowsLfAndAnLfEditKeepsCrlf) {
    const std::string original = "[server]\r\nhost = local\r\nport = 8080\r\n";
    std::string path = writeFile("crlf.ini", original);

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error")) << read.dump();
    EXPECT_EQ(read.value("content", ""), "[server]\nhost = local\nport = 8080");
    EXPECT_EQ(read.value("line_endings", ""), "crlf");
    EXPECT_EQ(read["lines"], 3);
    EXPECT_EQ(read["content_hash"], FileStateTracker::hashContent(original).substr(0, 12));

    json ranged = FileIOTools::fileRead().callback({{"path", path}, {"start_line", 2}, {"end_line", 2}});
    EXPECT_EQ(ranged.value("content", ""), "host = local") << ranged.dump();

    json edit = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "host = local\nport = 8080"}, {"new_string", "host = remote\nport = 9090\ntls = on"}});
    ASSERT_FALSE(edit.contains("error")) << edit.dump();
    EXPECT_EQ(edit.value("line_endings", ""), "crlf");
    const std::string after = readFile(path);
    EXPECT_EQ(after, "[server]\r\nhost = remote\r\nport = 9090\r\ntls = on\r\n");
    EXPECT_EQ(after.find("\r\r"), std::string::npos);
    EXPECT_EQ(edit["content_hash"], FileStateTracker::hashContent(after).substr(0, 12));

    json again = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", "tls = on"}, {"new_string", "tls = off"}});
    ASSERT_FALSE(again.contains("error")) << "an edit after an edit is not stale: " << again.dump();
    EXPECT_EQ(readFile(path), "[server]\r\nhost = remote\r\nport = 9090\r\ntls = off\r\n");
}

TEST_F(FileToolsHardeningTest, CrlfFile_AnEditGivenInCrlfWritesTheSameBytes) {
    const std::string original = "alpha\r\nbeta\r\ngamma";
    std::string lfPath = writeFile("lf_args.txt", original);
    std::string crlfPath = writeFile("crlf_args.txt", original);

    json lf = FileIOTools::fileEdit().callback(
        {{"path", lfPath}, {"old_string", "alpha\nbeta"}, {"new_string", "one\ntwo"}});
    json crlf = FileIOTools::fileEdit().callback(
        {{"path", crlfPath}, {"old_string", "alpha\r\nbeta"}, {"new_string", "one\r\ntwo"}});
    ASSERT_FALSE(lf.contains("error")) << lf.dump();
    ASSERT_FALSE(crlf.contains("error")) << crlf.dump();
    EXPECT_EQ(readFile(lfPath), "one\r\ntwo\r\ngamma") << "no final newline before, none after";
    EXPECT_EQ(readFile(crlfPath), readFile(lfPath));
}

TEST_F(FileToolsHardeningTest, CrlfFile_EditErrorsShowLfText) {
    std::string path = writeFile("hints.txt", "one\r\ntwo\r\none\r\n");

    json missing = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", "three"}, {"new_string", "x"}});
    ASSERT_TRUE(missing.contains("error"));
    EXPECT_EQ(missing.value("current_content", ""), "one\ntwo\none") << missing.dump();

    json ambiguous = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", "one\n"}, {"new_string", "x\n"}});
    ASSERT_TRUE(ambiguous.value("ambiguous", false)) << ambiguous.dump();
    EXPECT_EQ(ambiguous["match_lines"], (json::array({1, 3})));
    EXPECT_EQ(ambiguous.value("current_content", ""), "one\ntwo\none");
    EXPECT_EQ(readFile(path), "one\r\ntwo\r\none\r\n");
}

TEST_F(FileToolsHardeningTest, CrlfFile_WriteKeepsCrlfAndNeverDoublesIt) {
    std::string path = writeFile("notes.txt", "old\r\ntext\r\n");

    json lf = FileIOTools::fileWrite().callback({{"path", path}, {"content", "new\ntext\nhere\n"}});
    ASSERT_FALSE(lf.contains("error")) << lf.dump();
    EXPECT_EQ(lf.value("line_endings", ""), "crlf");
    std::string after = readFile(path);
    EXPECT_EQ(after, "new\r\ntext\r\nhere\r\n");
    EXPECT_EQ(lf["bytes_written"], static_cast<int>(after.size()));
    EXPECT_EQ(lf["content_hash"], FileStateTracker::hashContent(after).substr(0, 12));

    const std::string given = "a\r\nb\r\nno newline at the end";
    json crlf = FileIOTools::fileWrite().callback({{"path", path}, {"content", given}});
    ASSERT_FALSE(crlf.contains("error")) << "a write after a write is not stale: " << crlf.dump();
    EXPECT_FALSE(crlf.contains("line_endings"));
    after = readFile(path);
    EXPECT_EQ(after, given);
    EXPECT_EQ(after.find("\r\r\n"), std::string::npos);

    std::string fresh = (tempDir_ / "fresh.txt").string();
    ASSERT_FALSE(FileIOTools::fileWrite().callback({{"path", fresh}, {"content", "a\nb\n"}}).contains("error"));
    EXPECT_EQ(readFile(fresh), "a\nb\n");
}

TEST_F(FileToolsHardeningTest, LfFile_IsLeftByteForByteAlone) {
    std::string path = writeFile("unix.txt", "alpha\nbeta\ngamma\n");

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error"));
    EXPECT_EQ(read.value("content", ""), "alpha\nbeta\ngamma");
    EXPECT_FALSE(read.contains("line_endings"));

    json edit = FileIOTools::fileEdit().callback(
        {{"path", path}, {"old_string", "alpha\r\nbeta"}, {"new_string", "one\r\ntwo"}});
    ASSERT_FALSE(edit.contains("error")) << edit.dump();
    EXPECT_FALSE(edit.contains("line_endings"));
    EXPECT_EQ(readFile(path), "one\ntwo\ngamma\n");

    json write = FileIOTools::fileWrite().callback({{"path", path}, {"content", "x\ny\n"}});
    ASSERT_FALSE(write.contains("error")) << write.dump();
    EXPECT_FALSE(write.contains("line_endings"));
    EXPECT_EQ(readFile(path), "x\ny\n");
}

TEST_F(FileToolsHardeningTest, MixedEndings_AnExactEditTouchesNothingElse) {
    std::string path = writeFile("mixed.txt", "one\r\ntwo\nthree\r\nfour\rfive\n");

    json edit = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", "two\nthree\r\n"}, {"new_string", "2\n3\r\n"}});
    ASSERT_FALSE(edit.contains("error")) << edit.dump();
    EXPECT_EQ(readFile(path), "one\r\n2\n3\r\nfour\rfive\n");

    json word = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", "one"}, {"new_string", "uno"}});
    ASSERT_FALSE(word.contains("error")) << word.dump();
    EXPECT_EQ(readFile(path), "uno\r\n2\n3\r\nfour\rfive\n");
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

// ---------------------------------------------------------------------------
// Non-ASCII names: a model writes paths in UTF-8, whatever the code page
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, NonAsciiNamesAreReadAndWrittenAsUtf8) {
    const std::string name = "r\xc3\xa9sum\xc3\xa9-\xe6\x97\xa5\xe6\x9c\xac.txt";
    const fs::path onDisk = tempDir_ / fs::u8path("docs \xc3\xb1") / fs::u8path(name);
    const std::string path = onDisk.u8string();
    const std::string cafe = "caf\xc3\xa9";

    json written = FileIOTools::fileWrite().callback({{"path", path}, {"content", cafe + " au lait\n"}});
    ASSERT_FALSE(written.contains("error")) << written.dump();
    ASSERT_TRUE(fs::exists(onDisk)) << "the file carries the name the model gave";
    size_t entries = 0;
    for (const auto& e : fs::directory_iterator(onDisk.parent_path())) {
        ++entries;
        EXPECT_EQ(e.path().filename().u8string(), name);
    }
    EXPECT_EQ(entries, 1u) << "and no second, mis-spelled copy";

    json read = FileIOTools::fileRead().callback({{"path", path}});
    ASSERT_FALSE(read.contains("error")) << read.dump();
    EXPECT_NE(read["content"].get<std::string>().find(cafe), std::string::npos);
    EXPECT_EQ(read["path"], path);

    json edited = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", cafe}, {"new_string", "tea"}});
    ASSERT_FALSE(edited.contains("error")) << edited.dump();
    std::ifstream in(onDisk, std::ios::binary);
    std::ostringstream now;
    now << in.rdbuf();
    EXPECT_EQ(now.str(), "tea au lait\n");

    for (const json& args : {json{{"pattern", "*.txt"}, {"path", tempDir_.u8string()}},
                             json{{"pattern", "*.txt"}, {"path", tempDir_.u8string()}, {"content_pattern", "tea"}}}) {
        json found = FileIOTools::fileSearch().callback(args);
        ASSERT_FALSE(found.contains("error")) << found.dump();
        ASSERT_EQ(found["matches"].size(), 1u) << found.dump();
        const std::string match = found["matches"][0]["path"];
        EXPECT_EQ(fs::u8path(match).filename().u8string(), name);
        EXPECT_NO_THROW(found.dump()) << "the result is valid UTF-8 JSON";
    }
}

TEST_F(FileToolsTest, FileSearch_DoesNotLeaveTheFolderThroughALink) {
    const fs::path outside = fs::temp_directory_path() / testsupport::perProcess("gaia_file_tools_outside");
    std::error_code ec;
    fs::remove_all(outside, ec);
    fs::create_directories(outside);
    {
        std::ofstream secret(outside / "private.txt", std::ios::binary);
        secret << "ORCHID\n";
    }
    writeFile("inside.txt", "ORCHID\n");
    const fs::path link = tempDir_ / "linked";
#ifdef _WIN32
    // A junction, which needs no privilege and is not reported as a symlink.
    const std::string make = "cmd /c mklink /J \"" + link.string() + "\" \"" + outside.string() + "\" >nul";
    ASSERT_EQ(std::system(make.c_str()), 0) << make;
#else
    fs::create_directory_symlink(outside, link);
    fs::create_symlink(outside / "private.txt", tempDir_ / "alias.txt");
#endif

    for (const json& args : {json{{"pattern", "*"}, {"path", tempDir_.u8string()}},
                             json{{"pattern", "*"}, {"path", tempDir_.u8string()}, {"content_pattern", "ORCHID"}}}) {
        json found = FileIOTools::fileSearch().callback(args);
        ASSERT_FALSE(found.contains("error")) << found.dump();
        ASSERT_EQ(found["matches"].size(), 1u) << found.dump();
        const std::string match = found["matches"][0]["path"];
        EXPECT_EQ(fs::u8path(match).filename().u8string(), "inside.txt");
    }

    // The link first, so cleaning up cannot reach through it.
    fs::remove(link, ec);
    fs::remove_all(outside, ec);
}

// Junctions need no privilege, so on Windows this runs everywhere rather than skipping.
TEST_F(FileToolsTest, FileSearch_DoesNotWalkIntoAFolderLinkOutOrBackIn) {
    const fs::path outside = fs::temp_directory_path() / testsupport::perProcess("gaia_file_tools_far");
    std::error_code ec;
    fs::remove_all(outside, ec);
    fs::create_directories(outside / "deeper");
    std::ofstream(outside / "deeper" / "beyond.txt", std::ios::binary) << "ORCHID\n";
    writeFile("inside.txt", "ORCHID\n");
    fs::create_directories(tempDir_ / "sub");
    const fs::path out = tempDir_ / "sub" / "far";
    const fs::path back = tempDir_ / "sub" / "circular";
    for (const auto& [link, target] : {std::pair{out, outside}, std::pair{back, tempDir_}}) {
#ifdef _WIN32
        const std::string make = "cmd /c mklink /J \"" + link.string() + "\" \"" + target.string() + "\" >nul";
        ASSERT_EQ(std::system(make.c_str()), 0) << make;
#else
        fs::create_directory_symlink(target, link, ec);
        ASSERT_FALSE(ec) << ec.message();
#endif
    }
    for (const json& args : {json{{"pattern", "*.txt"}, {"path", tempDir_.u8string()}},
                             json{{"pattern", "**/*.txt"}, {"path", tempDir_.u8string()}},
                             json{{"pattern", "*"}, {"path", tempDir_.u8string()}, {"content_pattern", "ORCHID"}}}) {
        json found = FileIOTools::fileSearch().callback(args);
        ASSERT_FALSE(found.contains("error")) << found.dump();
        ASSERT_EQ(found["matches"].size(), 1u) << found.dump();
        EXPECT_EQ(fs::u8path(found["matches"][0]["path"].get<std::string>()).filename().u8string(), "inside.txt");
        EXPECT_EQ(found.dump().find("beyond"), std::string::npos) << found.dump();
        EXPECT_EQ(found.dump().find("circular"), std::string::npos) << found.dump();
    }
    // The links first, so cleaning up cannot reach through them.
    fs::remove(out, ec);
    fs::remove(back, ec);
    fs::remove_all(outside, ec);
}

TEST_F(FileToolsTest, FileWrite_ReplacesTheFileWholeAndLeavesNoTemporaryFile) {
    const std::string path = writeFile("keep.sh", "old\n");
#ifndef _WIN32
    fs::permissions(path, fs::perms::owner_all);
#endif
    ASSERT_FALSE(FileIOTools::fileRead().callback({{"path", path}}).contains("error"));

    json written = FileIOTools::fileWrite().callback({{"path", path}, {"content", "new\n"}});
    ASSERT_FALSE(written.contains("error")) << written.dump();
    json edited = FileIOTools::fileEdit().callback({{"path", path}, {"old_string", "new"}, {"new_string", "newer"}});
    ASSERT_FALSE(edited.contains("error")) << edited.dump();

    EXPECT_EQ(readFile(path), "newer\n");
    size_t entries = 0;
    for (const auto& e : fs::directory_iterator(tempDir_)) {
        ++entries;
        EXPECT_EQ(e.path().filename().string(), "keep.sh");
    }
    EXPECT_EQ(entries, 1u);
#ifndef _WIN32
    EXPECT_NE(fs::status(path).permissions() & fs::perms::owner_exec, fs::perms::none)
        << "an edited script stays executable";
#endif
}

TEST_F(FileToolsTest, FileRead_RefusesFoldersAndDeviceNames) {
    json folder = FileIOTools::fileRead().callback({{"path", tempDir_.string()}});
    ASSERT_TRUE(folder.contains("error")) << folder.dump();
    EXPECT_NE(folder["error"].get<std::string>().find("is a folder"), std::string::npos) << folder.dump();
#ifdef _WIN32
    for (const char* name : {"NUL", "con", "COM1.txt", "aux.log", "lpt9"}) {
        json device = FileIOTools::fileRead().callback({{"path", (tempDir_ / name).string()}});
        ASSERT_TRUE(device.contains("error")) << name;
        EXPECT_NE(device["error"].get<std::string>().find("Windows device"), std::string::npos) << device.dump();
    }
#endif
}

// ---------------------------------------------------------------------------
// credential files and masked secrets
// ---------------------------------------------------------------------------

TEST_F(FileToolsTest, FileRead_ACredentialFileIsRefusedByName) {
    const auto read = FileIOTools::fileRead().callback;
    const auto edit = FileIOTools::fileEdit().callback;
    for (const std::string name : {".env", "server.KEY", "sub/id_rsa"}) {
        const std::string path = writeFile(name, "DB_PASSWORD=hunter2\n");
        const json shown = read({{"path", path}});
        ASSERT_TRUE(shown.contains("error")) << name;
        EXPECT_NE(shown["error"].get<std::string>().find("holds credentials"), std::string::npos) << name;
        EXPECT_EQ(shown.dump().find("hunter2"), std::string::npos) << name;
        const json edited = edit({{"path", path}, {"old_string", "absent"}, {"new_string", "x"}});
        ASSERT_TRUE(edited.contains("error")) << name;
        EXPECT_EQ(edited.dump().find("hunter2"), std::string::npos) << name;
    }
    const std::string example = writeFile(".env.example", "DB_PASSWORD=\n");
    EXPECT_FALSE(read({{"path", example}}).contains("error"));
}

TEST_F(FileToolsTest, FileSearch_AContentSearchSkipsCredentialFiles) {
    writeFile("proj/.env", "DB_PASSWORD=hunter2\n");
    const json found = FileIOTools::fileSearch().callback(
        {{"pattern", ".env"}, {"path", (tempDir_ / "proj").string()}, {"content_pattern", "DB_PASSWORD"}});
    ASSERT_FALSE(found.contains("error")) << found.dump();
    EXPECT_EQ(found["total"], 0);
    EXPECT_EQ(found.value("credential_files_skipped", 0), 1);
    EXPECT_EQ(found.dump().find("hunter2"), std::string::npos);
}

TEST_F(FileToolsTest, FileEdit_TheRedactedMarkerIsNotWrittenOverASecret) {
    const std::string token = std::string("ghp_") + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4";
    const std::string masked = "\turl = https://[redacted]@github.com/amd/gaia.git";
    const std::string config =
        "[core]\n\turl = https://user:" + token + "@github.com/amd/gaia.git\n\tremote = origin\n";
    const std::string path = writeFile("remotes.cfg", config);
    const auto edit = FileIOTools::fileEdit().callback;
    ASSERT_FALSE(FileIOTools::fileRead().callback({{"path", path}}).contains("error"));

    json r = edit({{"path", path}, {"old_string", masked}, {"new_string", "\turl = https://github.com/amd/gaia.git"}});
    EXPECT_EQ(r.value("error_type", ""), "redacted_secret") << r.dump();
    r = edit({{"path", path}, {"old_string", "[core]"}, {"new_string", "[core]\n" + masked}});
    EXPECT_EQ(r.value("error_type", ""), "redacted_secret") << r.dump();
    r = FileIOTools::fileWrite().callback({{"path", path}, {"content", "[core]\n" + masked + "\n"}});
    EXPECT_EQ(r.value("error_type", ""), "redacted_secret") << r.dump();
    EXPECT_EQ(readFile(path), config);

    r = edit({{"path", path},
              {"old_string", "\tremote = origin\n"},
              {"new_string", "\tremote = origin\n\tmerge = refs/heads/main\n"}});
    EXPECT_FALSE(r.contains("error")) << r.dump();
    EXPECT_NE(readFile(path).find(token), std::string::npos);
    EXPECT_NE(readFile(path).find("merge = refs/heads/main"), std::string::npos);
}

TEST_F(FileToolsTest, FileEdit_AFileThatHoldsTheMarkerItselfStaysEditable) {
    const std::string path = writeFile("check_masking.py", "MARKER = \"[redacted]\"\n");
    ASSERT_FALSE(FileIOTools::fileRead().callback({{"path", path}}).contains("error"));
    json r = FileIOTools::fileEdit().callback({{"path", path},
                                               {"old_string", "MARKER = \"[redacted]\""},
                                               {"new_string", "MARKER = \"[redacted]\"\nOTHER = \"[redacted]\""}});
    EXPECT_FALSE(r.contains("error")) << r.dump();
    EXPECT_NE(readFile(path).find("OTHER = \"[redacted]\""), std::string::npos);

    const std::string notes = (tempDir_ / "notes.md").string();
    r = FileIOTools::fileWrite().callback({{"path", notes}, {"content", "Tokens show as [redacted].\n"}});
    EXPECT_FALSE(r.contains("error")) << r.dump();
    EXPECT_EQ(readFile(notes), "Tokens show as [redacted].\n");
}

TEST_F(FileToolsHardeningTest, FileWrite_ACredentialFileIsRefusedAsAnEditIs) {
    const fs::path env = tempDir_ / ".env";
    json result = FileIOTools::fileWrite().callback({{"path", env.string()}, {"content", "KEY=1\n"}});
    ASSERT_TRUE(result.contains("error")) << result.dump();
    EXPECT_NE(result["error"].get<std::string>().find("credentials"), std::string::npos) << result.dump();
    EXPECT_FALSE(fs::exists(env));
}
