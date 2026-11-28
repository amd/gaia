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

TEST(SkillLibraryTest, ASkillWithNativeToolsTurnsThemOnWhenItLoads) {
    TempTree t;
    const auto withTool = [&](const std::string& name) {
        t.raw("user", name,
              "---\nname: " + name + "\ndescription: A skill.\nmetadata:\n  gaia:\n    tools:\n      - name: look\n"
              "        description: Look.\n---\n\nBody.\n");
    };
    withTool("calendar");
    withTool("other");
    SkillLibrary lib({t.root("user", "user")});
    ToolRegistry reg;
    lib.registerTools(reg);
    int activated = 0;
    std::string refusal;
    lib.setNativeTools("calendar", {{"calendar/list_calendar_events"}, [&] { return refusal; }, [&] { ++activated; }});

    refusal = "Skill 'calendar' needs a larger model.";
    json refused = reg.executeTool("load_skill", json{{"name", "calendar"}});
    EXPECT_EQ(refused["status"], "error");
    EXPECT_EQ(refused["error_type"], "SkillUnavailableError");
    EXPECT_EQ(refused["error"], refusal);
    EXPECT_EQ(activated, 0) << "a refused skill turns nothing on";

    refusal.clear();
    json loaded = reg.executeTool("load_skill", json{{"name", "calendar"}});
    EXPECT_EQ(loaded["status"], "success");
    EXPECT_EQ(activated, 1);
    EXPECT_EQ(loaded["tools_available"], json::array({"calendar/list_calendar_events"}));
    EXPECT_FALSE(loaded.contains("note")) << "its tools run here, so no 'needs Python' note";

    json other = reg.executeTool("load_skill", json{{"name", "other"}});
    ASSERT_TRUE(other.contains("note"));
    EXPECT_NE(other["note"].get<std::string>().find("Python GAIA runtime"), std::string::npos);
}

TEST(SkillLibraryTest, ABuiltInSkillLoadsUnlessOneOnDiskHasItsName) {
    TempTree t;
    const std::string builtIn = "---\nname: planner\ndescription: Built in.\n---\n\nBuilt-in body.\n";
    {
        SkillLibrary lib({t.root("user", "user")});
        ToolRegistry reg;
        lib.registerTools(reg);
        lib.setNativeTools("planner", {{}, nullptr, [] {}, builtIn});
        EXPECT_NE(lib.renderPrompt().find("- planner: Built in."), std::string::npos);
        json r = reg.executeTool("load_skill", json{{"name", "planner"}});
        EXPECT_EQ(r["instructions"], "Built-in body.") << r.dump();
    }
    t.skill("user", "planner", "On disk.", "Disk body.");
    SkillLibrary lib({t.root("user", "user")});
    ToolRegistry reg;
    lib.registerTools(reg);
    lib.setNativeTools("planner", {{}, nullptr, [] {}, builtIn});
    json r = reg.executeTool("load_skill", json{{"name", "planner"}});
    EXPECT_NE(r["instructions"].get<std::string>().find("Disk body."), std::string::npos) << r.dump();
    ASSERT_EQ(lib.shadowed().size(), 1u);
    EXPECT_EQ(lib.shadowed()[0], "planner (built-in) shadowed by user");
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

TEST(SkillLibraryTest, RefreshDiscoversAddedSkillsAndPrunesRemovedLoadedSkills) {
    TempTree t;
    t.skill("user", "alpha", "Alpha skill.", "Alpha body.");
    SkillLibrary lib({t.root("user", "user")});
    ASSERT_TRUE(lib.loadSkill("alpha"));
    EXPECT_TRUE(lib.isLoaded("alpha"));

    // Add a new skill and remove the previously loaded skill on disk.
    t.skill("user", "beta", "Beta skill.", "Beta body.");
    fs::remove_all(t.path() / "user" / "alpha");

    lib.refresh();
    EXPECT_EQ(lib.skills().count("beta"), 1u);
    EXPECT_EQ(lib.skills().count("alpha"), 0u);
    EXPECT_FALSE(lib.isLoaded("alpha")) << "removed skill should be pruned from loaded_";
    const std::string prompt = lib.renderPrompt();
    EXPECT_NE(prompt.find("- beta: Beta skill."), std::string::npos);
    EXPECT_EQ(prompt.find("Alpha body."), std::string::npos);
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

TEST(SkillLibraryTest, InstallValidatesCopiesAndReplaces) {
    TempTree t;
    t.skill("src", "data-explore", "Explore a CSV.", "Version one.");
    const std::string dest = (t.path() / "installed").string();
    const std::string where = SkillLibrary::installFromFolder((t.path() / "src" / "data-explore").string(), dest, true);
    EXPECT_TRUE(fs::is_regular_file(fs::path(where) / "SKILL.md"));

    t.skill("src2", "data-explore", "Explore a CSV.", "Version two.");
    SkillLibrary::installFromFolder((t.path() / "src2" / "data-explore").string(), dest, true);
    SkillLibrary lib({SkillRoot{"user", dest, true, false}});
    EXPECT_NE(lib.loadFull("data-explore").body.find("Version two."), std::string::npos);
    for (const auto& e : fs::directory_iterator(dest)) {
        EXPECT_NE(e.path().filename().string()[0], '.') << "no staging leftovers";
    }

    EXPECT_TRUE(SkillLibrary::removeInstalled("data-explore", dest));
    EXPECT_FALSE(SkillLibrary::removeInstalled("data-explore", dest));
    EXPECT_THROW(SkillLibrary::removeInstalled("../etc", dest), SkillError);
}

TEST(SkillLibraryTest, AHubInstallRefusesANameThatIsNotPlain) {
    // Refused before any request: the name reaches the unpack command line.
    for (const char* name : {"", "x'; touch pwned; '", "a$b", "a b", "-rf", "../x", "a.b"}) {
        try {
            SkillLibrary::installFromHub(name, "1.0.0", "unused", false, nullptr);
            ADD_FAILURE() << name;
        } catch (const SkillError& e) {
            EXPECT_NE(std::string(e.what()).find("Not a skill name"), std::string::npos) << name << ": " << e.what();
        }
    }
}

TEST(SkillLibraryTest, InstallLeftoversAreIgnoredAndDoNotBlockInstalls) {
    TempTree t;
    t.skill("src", "data-explore", "Explore a CSV.", "Version one.");
    t.skill("installed", ".data-explore.installing", "Explore a CSV.", "Half-copied.");
    t.skill("installed", ".data-explore.installing-1a2b-0", "Explore a CSV.", "Half-copied.");
    t.skill("installed", ".data-explore.old-1a2b-0", "Explore a CSV.", "Superseded.");
    const std::string dest = (t.path() / "installed").string();

    SkillLibrary before({SkillRoot{"user", dest, true, false}});
    EXPECT_TRUE(before.failures().empty()) << before.failures()[0];

    EXPECT_NO_THROW(
        SkillLibrary::installFromFolder((t.path() / "src" / "data-explore").string(), dest, true));
    EXPECT_TRUE(fs::exists(fs::path(dest) / ".data-explore.installing-1a2b-0"))
        << "a staging folder that may belong to a running install is left alone";
    SkillLibrary after({SkillRoot{"user", dest, true, false}});
    EXPECT_TRUE(after.failures().empty()) << after.failures()[0];
    EXPECT_EQ(after.skills().count("data-explore"), 1u);
}

TEST(SkillLibraryTest, InstallRefusesBundlesWithSymlinks) {
    TempTree t;
    t.skill("src", "data-explore", "Explore a CSV.", "Body.");
    std::ofstream(t.path() / "outside.txt") << "not part of the bundle\n";
    std::error_code ec;
    fs::create_symlink(t.path() / "outside.txt", t.path() / "src" / "data-explore" / "notes.txt", ec);
    if (ec) GTEST_SKIP() << "this account can't create symlinks: " << ec.message();
    const std::string dest = (t.path() / "installed").string();
    EXPECT_THROW(
        SkillLibrary::installFromFolder((t.path() / "src" / "data-explore").string(), dest, true),
        SkillError);
    EXPECT_FALSE(fs::exists(fs::path(dest) / "data-explore"));
}

TEST(SkillLibraryTest, InstallRefusesInvalidSkills) {
    TempTree t;
    t.raw("src", "broken", "no frontmatter\n");
    EXPECT_THROW(SkillLibrary::installFromFolder((t.path() / "src" / "broken").string(),
                                                 (t.path() / "dest").string()),
                 SkillError);
    // Folder name must match the skill's name.
    t.skill("src", "wrong-folder", "x", "y");
    fs::rename(t.path() / "src" / "wrong-folder", t.path() / "src" / "other-name");
    EXPECT_THROW(SkillLibrary::installFromFolder((t.path() / "src" / "other-name").string(),
                                                 (t.path() / "dest").string()),
                 SkillError);
    EXPECT_THROW(SkillLibrary::installFromFolder((t.path() / "nowhere").string(),
                                                 (t.path() / "dest").string()),
                 SkillError);
}
