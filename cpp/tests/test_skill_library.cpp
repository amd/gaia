// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/skill_library.h>
#include <gaia/tool_registry.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <random>
#include <string>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

class TempTree {
public:
    TempTree() {
        std::mt19937_64 rng(std::random_device{}());
        path_ = fs::temp_directory_path() / ("gaia-skill-lib-" + std::to_string(rng()));
        fs::create_directories(path_);
    }
    ~TempTree() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }
    const fs::path& path() const { return path_; }

    void skill(const std::string& root, const std::string& name, const std::string& description,
               const std::string& body) {
        const fs::path dir = path_ / root / name;
        fs::create_directories(dir);
        std::ofstream(dir / "SKILL.md") << "---\nname: " << name << "\ndescription: " << description
                                        << "\n---\n\n" << body << "\n";
    }
    void raw(const std::string& root, const std::string& name, const std::string& text) {
        const fs::path dir = path_ / root / name;
        fs::create_directories(dir);
        std::ofstream(dir / "SKILL.md") << text;
    }
    SkillRoot root(const std::string& label, const std::string& dir, bool listed = true) const {
        return SkillRoot{label, (path_ / dir).string(), listed, !listed};
    }

private:
    fs::path path_;
};

}  // namespace

TEST(SkillLibraryTest, EarlierRootShadowsAndIsReported) {
    TempTree t;
    t.skill("bundled", "summarize", "Summarize documents (bundled).", "Bundled body.");
    t.skill("user", "summarize", "Summarize documents (user).", "User body.");
    t.skill("user", "data-explore", "Explore a CSV or spreadsheet.", "Explore body.");
    SkillLibrary lib({t.root("agent-bundled", "bundled"), t.root("user", "user")});

    ASSERT_EQ(lib.skills().size(), 2u);
    EXPECT_EQ(lib.skills().at("summarize").root, "agent-bundled");
    ASSERT_EQ(lib.shadowed().size(), 1u);
    EXPECT_NE(lib.shadowed()[0].find("summarize (user) shadowed by agent-bundled"), std::string::npos);
}

TEST(SkillLibraryTest, BrokenSkillIsReportedNotThrown) {
    TempTree t;
    t.raw("user", "broken", "no frontmatter here\n");
    t.skill("user", "fine", "A fine skill.", "Body.");
    SkillLibrary lib({t.root("user", "user")});
    EXPECT_EQ(lib.skills().count("fine"), 1u);
    ASSERT_EQ(lib.failures().size(), 1u);
    EXPECT_NE(lib.failures()[0].find("broken"), std::string::npos);
}

TEST(SkillLibraryTest, CatalogListsOnlyListedRootsAndNoBodies) {
    TempTree t;
    t.skill("user", "data-explore", "Explore a CSV or spreadsheet.", "SECRET-BODY-TEXT");
    t.skill("claude", "imported", "Imported from another host.", "Imported body.");
    SkillLibrary lib({t.root("user", "user"), t.root("claude-import", "claude", false)});
    const std::string prompt = lib.renderPrompt();
    EXPECT_NE(prompt.find("==== SKILLS ===="), std::string::npos);
    EXPECT_NE(prompt.find("- data-explore: Explore a CSV or spreadsheet."), std::string::npos);
    EXPECT_EQ(prompt.find("imported"), std::string::npos) << "imports are loadable, not listed";
    EXPECT_EQ(prompt.find("SECRET-BODY-TEXT"), std::string::npos) << "bodies load on demand";
}

TEST(SkillLibraryTest, NoSkillsMeansNoCatalog) {
    TempTree t;
    SkillLibrary lib({t.root("user", "missing-dir")});
    EXPECT_EQ(lib.renderPrompt(), "");
}

TEST(SkillLibraryTest, LoadSkillToolReturnsInstructions) {
    TempTree t;
    t.skill("user", "data-explore", "Explore a CSV.", "Step 1: profile the columns.");
    t.skill("claude", "imported", "Imported.", "Imported body.");
    SkillLibrary lib({t.root("user", "user"), t.root("claude-import", "claude", false)});
    ToolRegistry reg;
    lib.registerTools(reg);

    json r = reg.executeTool("load_skill", json{{"name", "data-explore"}});
    EXPECT_EQ(r["status"], "success");
    EXPECT_NE(r["instructions"].get<std::string>().find("profile the columns"), std::string::npos);

    json imported = reg.executeTool("load_skill", json{{"name", "imported"}});
    EXPECT_EQ(imported["status"], "success") << "unlisted imports still load by name";

    json missing = reg.executeTool("load_skill", json{{"name", "nope"}});
    EXPECT_EQ(missing["status"], "error");
    EXPECT_NE(missing["error"].get<std::string>().find("Installed: data-explore"), std::string::npos);

    json listed = reg.executeTool("list_skills", json::object());
    EXPECT_EQ(listed["skills"].size(), 2u);
}

TEST(SkillLibraryTest, LoaderSeamPutsBodyInPrompt) {
    TempTree t;
    t.skill("user", "coding", "Write and fix code.", "Always run the tests.");
    SkillLibrary lib({t.root("user", "user")});
    EXPECT_FALSE(lib.loadSkill("absent"));
    ASSERT_TRUE(lib.loadSkill("coding"));
    EXPECT_TRUE(lib.isLoaded("coding"));
    EXPECT_NE(lib.renderPrompt().find("Always run the tests."), std::string::npos);
    EXPECT_TRUE(lib.unloadSkill("coding"));
    EXPECT_EQ(lib.renderPrompt().find("Always run the tests."), std::string::npos);
}

TEST(SkillLibraryTest, RealHubSkillsParse) {
    // The skills GAIA ships must load in the native runtime unchanged.
    const fs::path hub = fs::path(GAIA_CPP_SOURCE_DIR) / ".." / "hub" / "skills";
    if (!fs::is_directory(hub)) GTEST_SKIP() << "hub/skills not present in this checkout";
    SkillLibrary lib({SkillRoot{"agent-bundled", hub.string(), true, false}});
    EXPECT_TRUE(lib.failures().empty()) << lib.failures()[0];
    EXPECT_GE(lib.skills().size(), 10u);
    for (const auto& [name, _] : lib.skills()) {
        EXPECT_NO_THROW(lib.loadFull(name)) << name;
    }
}
