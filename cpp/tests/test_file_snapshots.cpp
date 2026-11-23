// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// TurnRecorder writes the store the Python agent writes, which the Agent UI reads.

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <random>
#include <sstream>

#include <gaia/file_snapshots.h>

namespace fs = std::filesystem;
using gaia::json;
using gaia::SnapshotError;
using gaia::TurnRecorder;

namespace {

struct Tree {
    fs::path root;
    bool keep = false;
    Tree() {
        // GAIA_SNAPSHOT_TEST_KEEP leaves the store where it names, for a cross-check from Python.
        if (const char* k = std::getenv("GAIA_SNAPSHOT_TEST_KEEP")) {
            root = fs::u8path(k);
            keep = true;
        } else {
            root = fs::temp_directory_path() / ("gaia-snapshots-" + std::to_string(std::random_device{}()));
        }
        fs::create_directories(root);
    }
    ~Tree() {
        if (keep) return;
        std::error_code ec;
        fs::remove_all(root, ec);
    }
};

void write(const fs::path& p, const std::string& text) {
    fs::create_directories(p.parent_path());
    std::ofstream(p, std::ios::binary) << text;
}

std::string read(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    std::ostringstream out;
    out << in.rdbuf();
    return out.str();
}

}  // namespace

TEST(FileSnapshots, ATurnSavesEachFileBeforeAndAfterInThePythonLayout) {
    Tree t;
    const fs::path work = t.root / "work";
    write(work / "calc.py", "def add(a, b):\n    return a - b\n");
    TurnRecorder rec("chat-1", "20261122120000-0123abcd", t.root / "store");
    rec.capture((work / "calc.py").u8string(), "file_edit");
    write(work / "calc.py", "def add(a, b):\n    return a + b\n");
    rec.capture((work / "calc.py").u8string(), "file_edit");  // a second change keeps the first copy
    rec.capture((work / "new" / "deep" / "test_calc.py").u8string(), "file_write");
    write(work / "new" / "deep" / "test_calc.py", "assert True\n");
    rec.noteUntracked("shell", "pytest -q", false);
    rec.finish();

    const fs::path dir = t.root / "store" / "chat-1" / "20261122120000-0123abcd";
    const json m = json::parse(read(dir / "manifest.json"));
    EXPECT_EQ(m["version"], 1);
    EXPECT_EQ(m["session_id"], "chat-1");
    EXPECT_FALSE(m["finished_at"].is_null());
    ASSERT_EQ(m["files"].size(), 2u);
    const json& edited = m["files"][0];
    EXPECT_EQ(edited["existed"], true);
    EXPECT_EQ(edited["before_blob"], "0000.before");
    EXPECT_EQ(read(dir / "blobs" / "0000.before"), "def add(a, b):\n    return a - b\n");
    EXPECT_EQ(edited["after_blob"], "0000.after");
    EXPECT_EQ(read(dir / "blobs" / "0000.after"), "def add(a, b):\n    return a + b\n");
    EXPECT_EQ(edited["before_sha256"].get<std::string>().size(), 64u);
    const json& created = m["files"][1];
    EXPECT_EQ(created["existed"], false);
    ASSERT_EQ(created["created_dirs"].size(), 2u) << "outermost first, as undo removes them";
    EXPECT_NE(created["created_dirs"][0].get<std::string>().find("new"), std::string::npos);
    EXPECT_EQ(created["after_exists"], true);
    ASSERT_EQ(m["untracked"].size(), 1u);
    EXPECT_EQ(m["untracked"][0]["may_write"], false);
}

TEST(FileSnapshots, AnEmptyTurnLeavesNothingBehind) {
    Tree t;
    TurnRecorder rec("chat-2", "20261122120000-0123abce", t.root / "store");
    rec.noteUntracked("shell", "git status", false);
    rec.finish();
    EXPECT_FALSE(fs::exists(t.root / "store" / "chat-2" / "20261122120000-0123abce" / "manifest.json"));
}

TEST(FileSnapshots, AnIdThatWouldLeaveTheStoreIsRefused) {
    Tree t;
    EXPECT_THROW(TurnRecorder("..", "20261122120000-0123abcd", t.root), SnapshotError);
    EXPECT_THROW(TurnRecorder("chat", "../escape", t.root), SnapshotError);
    EXPECT_THROW(TurnRecorder("chat/../../x", "20261122120000-0123abcd", t.root), SnapshotError);
}

TEST(FileSnapshots, AFolderIsNotSavedAndTheChangeMustNotGoAhead) {
    Tree t;
    fs::create_directories(t.root / "work" / "pkg");
    TurnRecorder rec("chat-3", "20261122120000-0123abcf", t.root / "store");
    EXPECT_THROW(rec.capture((t.root / "work" / "pkg").u8string(), "file_write"), SnapshotError);
}
