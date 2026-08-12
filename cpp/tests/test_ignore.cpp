// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/ignore.h>

#include <filesystem>
#include <fstream>
#include <string>

namespace fs = std::filesystem;
using namespace gaia;

namespace {

GlobOptions nameMode() { return GlobOptions{/*pathMode=*/false, false}; }
GlobOptions pathMode() { return GlobOptions{/*pathMode=*/true, false}; }

} // namespace

// ---------------------------------------------------------------------------
// globMatch
// ---------------------------------------------------------------------------

TEST(GlobMatchTest, StarAndQuestion) {
    EXPECT_TRUE(globMatch("*.cpp", "main.cpp", nameMode()));
    EXPECT_FALSE(globMatch("*.cpp", "main.h", nameMode()));
    EXPECT_TRUE(globMatch("test_?.cpp", "test_a.cpp", nameMode()));
    EXPECT_FALSE(globMatch("test_?.cpp", "test_ab.cpp", nameMode()));
    EXPECT_TRUE(globMatch("*", "anything", nameMode()));
}

TEST(GlobMatchTest, CharacterClasses) {
    EXPECT_TRUE(globMatch("file[0-9].txt", "file7.txt", nameMode()));
    EXPECT_FALSE(globMatch("file[0-9].txt", "filex.txt", nameMode()));
    EXPECT_TRUE(globMatch("file[!0-9].txt", "filex.txt", nameMode()));
    EXPECT_FALSE(globMatch("file[!0-9].txt", "file7.txt", nameMode()));
    EXPECT_TRUE(globMatch("[abc]bc", "abc", nameMode()));
    // An unterminated class is a literal '[' — patterns from an LLM are not
    // guaranteed well-formed and must not throw or match everything.
    EXPECT_TRUE(globMatch("a[bc", "a[bc", nameMode()));
}

TEST(GlobMatchTest, EscapeSequences) {
    EXPECT_TRUE(globMatch("a\\*b", "a*b", nameMode()));
    EXPECT_FALSE(globMatch("a\\*b", "axxb", nameMode()));
}

TEST(GlobMatchTest, SingleStarDoesNotCrossSeparatorInPathMode) {
    EXPECT_TRUE(globMatch("src/*.cpp", "src/main.cpp", pathMode()));
    EXPECT_FALSE(globMatch("src/*.cpp", "src/deep/main.cpp", pathMode()));
    // Without path mode the whole string is one segment.
    EXPECT_TRUE(globMatch("src/*.cpp", "src/deep/main.cpp", nameMode()));
}

TEST(GlobMatchTest, DoubleStarSpansDirectories) {
    EXPECT_TRUE(globMatch("src/**/*.h", "src/a/b/c.h", pathMode()));
    EXPECT_TRUE(globMatch("src/**/*.h", "src/c.h", pathMode()));  // zero dirs
    EXPECT_FALSE(globMatch("src/**/*.h", "other/c.h", pathMode()));
    EXPECT_TRUE(globMatch("**/node_modules", "a/b/node_modules", pathMode()));
    EXPECT_TRUE(globMatch("**/node_modules", "node_modules", pathMode()));
}

TEST(GlobMatchTest, PathologicalPatternTerminates) {
    // Memoization keeps this from exploding; without it the test would hang.
    const std::string text(64, 'a');
    EXPECT_FALSE(globMatch("*a*a*a*a*a*a*a*a*b", text, nameMode()));
}

TEST(GlobMatchTest, CaseInsensitiveOption) {
    GlobOptions ci{/*pathMode=*/false, /*caseInsensitive=*/true};
    EXPECT_TRUE(globMatch("*.CPP", "main.cpp", ci));
    EXPECT_FALSE(globMatch("*.CPP", "main.cpp", nameMode()));
}

// ---------------------------------------------------------------------------
// GitignoreMatcher
// ---------------------------------------------------------------------------

class GitignoreTest : public ::testing::Test {
protected:
    fs::path root_;

    void SetUp() override {
        root_ = fs::temp_directory_path() / "gaia_ignore_test";
        std::error_code ec;
        fs::remove_all(root_, ec);
        fs::create_directories(root_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(root_, ec);
    }

    GitignoreMatcher withRules(const std::string& rules) {
        GitignoreMatcher matcher;
        matcher.addRules(rules, root_.string());
        return matcher;
    }

    std::string p(const std::string& rel) { return (root_ / rel).string(); }
};

TEST_F(GitignoreTest, EmptyMatcherIgnoresNothing) {
    GitignoreMatcher matcher;
    EXPECT_TRUE(matcher.empty());
    EXPECT_FALSE(matcher.isIgnored(p("anything.txt"), false));
}

TEST_F(GitignoreTest, DirectoryRuleIgnoresContents) {
    auto matcher = withRules("node_modules/\n");
    EXPECT_TRUE(matcher.isIgnored(p("node_modules"), true));
    EXPECT_TRUE(matcher.isIgnored(p("node_modules/left-pad/index.js"), false));
    EXPECT_TRUE(matcher.isIgnored(p("packages/app/node_modules/x.js"), false));
    EXPECT_FALSE(matcher.isIgnored(p("src/index.js"), false));
    // A *file* named node_modules is not matched by a directory-only rule.
    EXPECT_FALSE(matcher.isIgnored(p("node_modules"), false));
}

TEST_F(GitignoreTest, ExtensionRuleMatchesAtAnyDepth) {
    auto matcher = withRules("*.log\n");
    EXPECT_TRUE(matcher.isIgnored(p("debug.log"), false));
    EXPECT_TRUE(matcher.isIgnored(p("a/b/debug.log"), false));
    EXPECT_FALSE(matcher.isIgnored(p("a/b/debug.txt"), false));
}

TEST_F(GitignoreTest, AnchoredRuleOnlyMatchesAtRoot) {
    auto matcher = withRules("/build\n");
    EXPECT_TRUE(matcher.isIgnored(p("build/out.o"), false));
    EXPECT_FALSE(matcher.isIgnored(p("src/build/out.o"), false));
}

TEST_F(GitignoreTest, NegationReincludes) {
    auto matcher = withRules("*.log\n!keep.log\n");
    EXPECT_TRUE(matcher.isIgnored(p("debug.log"), false));
    EXPECT_FALSE(matcher.isIgnored(p("keep.log"), false));
}

TEST_F(GitignoreTest, CommentsBlankLinesAndTrailingSpace) {
    auto matcher = withRules("# a comment\n\n   \nbuild   \n");
    EXPECT_EQ(matcher.ruleCount(), 1u);
    EXPECT_TRUE(matcher.isIgnored(p("build"), true));
    EXPECT_FALSE(matcher.isIgnored(p("# a comment"), false));
}

TEST_F(GitignoreTest, CrlfLineEndings) {
    auto matcher = withRules("dist/\r\n*.tmp\r\n");
    EXPECT_TRUE(matcher.isIgnored(p("dist/app.js"), false));
    EXPECT_TRUE(matcher.isIgnored(p("scratch.tmp"), false));
}

TEST_F(GitignoreTest, ForDirectoryWalksToRepositoryRoot) {
    // A repository root is where .git lives; a .gitignore above it must not
    // leak in, and one at the root must reach a nested search directory.
    fs::create_directories(root_ / "repo" / ".git");
    fs::create_directories(root_ / "repo" / "src" / "nested");
    {
        std::ofstream f(root_ / "repo" / ".gitignore");
        f << "build/\n";
    }
    {
        std::ofstream f(root_ / "repo" / "src" / ".gitignore");
        f << "*.gen.cpp\n";
    }

    auto matcher = GitignoreMatcher::forDirectory((root_ / "repo" / "src").string());
    EXPECT_TRUE(matcher.isIgnored((root_ / "repo" / "build" / "a.o").string(), false));
    EXPECT_TRUE(matcher.isIgnored((root_ / "repo" / "src" / "x.gen.cpp").string(), false));
    EXPECT_FALSE(matcher.isIgnored((root_ / "repo" / "src" / "x.cpp").string(), false));
}

TEST_F(GitignoreTest, ForDirectoryOutsideRepoUsesOnlyItsOwnFile) {
    fs::create_directories(root_ / "loose");
    {
        std::ofstream f(root_ / "loose" / ".gitignore");
        f << "secret.txt\n";
    }

    auto matcher = GitignoreMatcher::forDirectory((root_ / "loose").string());
    EXPECT_TRUE(matcher.isIgnored((root_ / "loose" / "secret.txt").string(), false));
    EXPECT_FALSE(matcher.isIgnored((root_ / "loose" / "public.txt").string(), false));
}

TEST_F(GitignoreTest, PathsOutsideBaseDirAreNeverIgnored) {
    auto matcher = withRules("*.log\n");
    EXPECT_FALSE(matcher.isIgnored((fs::temp_directory_path() / "elsewhere.log").string(),
                                   false));
}
