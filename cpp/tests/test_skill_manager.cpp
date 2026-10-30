// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/skill_manager.h>
#include <gaia/tool_registry.h>

#include <deque>
#include <filesystem>
#include <fstream>
#include <thread>

using namespace gaia;
namespace fs = std::filesystem;

namespace {

std::string skillMd(const std::string& name, const std::string& description,
                    const std::string& gaiaBlock = "", const std::string& body = "Do the thing.") {
    std::string s = "---\nname: " + name + "\ndescription: " + description + "\n";
    if (!gaiaBlock.empty()) s += "metadata:\n  gaia:\n" + gaiaBlock;
    return s + "---\n\n" + body + "\n";
}

class SkillManagerTest : public ::testing::Test {
protected:
    fs::path root_;
    std::deque<std::string> texts_;  // stable addresses keep EmbeddedSkill pointers valid

    void SetUp() override {
        root_ = fs::temp_directory_path() / "gaia_skill_manager_test";
        std::error_code ec;
        fs::remove_all(root_, ec);
        fs::create_directories(root_);
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(root_, ec);
    }
    void writeSkill(const fs::path& dir, const std::string& name, const std::string& text) {
        fs::create_directories(dir / name);
        std::ofstream(dir / name / "SKILL.md", std::ios::binary) << text;
    }
    EmbeddedSkill embed(const std::string& dir, const std::string& text) {
        texts_.push_back(dir);
        texts_.push_back(text);
        return {texts_[texts_.size() - 2].c_str(), texts_.back().c_str()};
    }
};

}  // namespace

TEST_F(SkillManagerTest, RefusesPythonToolsAndUnbridgedPermissions) {
    auto refusal = [](const std::string& gaiaBlock) {
        return skillRefusalReason(parseSkill(skillMd("probe", "A probe skill.", gaiaBlock)));
    };
    EXPECT_EQ(refusal(""), "");
    EXPECT_EQ(refusal("    permissions:\n      - network:read\n"), "");
    EXPECT_EQ(refusal("    permissions:\n      - mcp:connect:github\n"), "");
    EXPECT_EQ(refusal("    permissions:\n      - shell:execute:gh\n"), "");
    EXPECT_EQ(refusal("    permissions:\n      - filesystem:none\n"), "");
    EXPECT_NE(refusal("    permissions:\n      - filesystem:read\n").find("local-capability"), std::string::npos);
    EXPECT_NE(refusal("    permissions:\n      - shell:execute\n").find("whole shell"), std::string::npos);
    EXPECT_NE(refusal("    permissions:\n      - shell:execute:curl\n").find("no command policy"), std::string::npos);
    EXPECT_NE(refusal("    tools:\n      - name: fetch_rss\n        description: Fetch a feed.\n").find("tools.py"),
              std::string::npos);
}

TEST_F(SkillManagerTest, BundledWinsOverUserAndTheCopyIsRecordedAsShadowed) {
    const fs::path user = root_ / "user";
    writeSkill(user, "notes", skillMd("notes", "User copy."));
    writeSkill(user, "journal", skillMd("journal", "Keep a journal."));
    SkillManager mgr({embed("notes", skillMd("notes", "Bundled copy."))}, {{"user", user.u8string(), false, true}});
    ASSERT_TRUE(mgr.find("notes").has_value());
    EXPECT_EQ(mgr.find("notes")->skill.description, "Bundled copy.");
    EXPECT_EQ(mgr.find("notes")->rootLabel, "bundled");
    ASSERT_EQ(mgr.shadowed().size(), 1u);
    EXPECT_EQ(mgr.shadowed()[0].first, "notes");
    EXPECT_TRUE(mgr.find("journal").has_value());
}

TEST_F(SkillManagerTest, BrokenSkillIsReportedNotThrown) {
    const fs::path user = root_ / "user";
    writeSkill(user, "broken", "no frontmatter here");
    writeSkill(user, "mismatch", skillMd("other-name", "Wrong folder."));
    SkillManager mgr({}, {{"user", user.u8string(), false, true}});
    EXPECT_EQ(mgr.skills().size(), 0u);
    EXPECT_EQ(mgr.invalid().size(), 2u);
}

TEST_F(SkillManagerTest, CatalogMatchesThePythonFormat) {
    std::string longDesc(600, 'w');
    SkillManager mgr({embed("alpha", skillMd("alpha", "Read   CSV    files.", "    pairs_with:\n      - beta\n")),
                      embed("beta", skillMd("beta", longDesc)),
                      embed("gamma", skillMd("gamma", "Gamma.", "    pairs_with:\n      - alpha\n"))},
                     {});
    const std::string cat = mgr.catalogPrompt();
    EXPECT_EQ(cat.rfind("==== SOURCING ====\n", 0), 0u);
    EXPECT_NE(cat.find("\n\n==== SKILLS ====\nInstalled skills."), std::string::npos);
    // Whitespace collapsed; own pairs first, then skills naming it.
    EXPECT_NE(cat.find("\n- alpha: Read CSV files. (pairs with: beta, gamma)\n"), std::string::npos) << cat;
    EXPECT_NE(cat.find("\n- beta: " + std::string(480, 'w') + "\xE2\x80\xA6 (pairs with: alpha)\n"), std::string::npos);
    EXPECT_LT(cat.find("- alpha:"), cat.find("- beta:"));
}

TEST_F(SkillManagerTest, CatalogLeavesOutRefusedImportedAndUnrunnableSkills) {
    const fs::path imports = root_ / "claude";
    writeSkill(imports, "imported", skillMd("imported", "From another host."));
    SkillManager mgr({embed("ok", skillMd("ok", "Fine.")),
                      embed("coded", skillMd("coded", "Has code.", "    tools:\n      - name: run_it\n        description: Run.\n")),
                      embed("needs-rag", skillMd("needs-rag", "Needs RAG.", "    tools_required:\n      - query_documents\n"))},
                     {{"claude-import", imports.u8string(), true, false}});
    mgr.setToolAvailable([](const std::string& t) { return t != "query_documents"; });
    const std::string cat = mgr.catalogPrompt();
    EXPECT_NE(cat.find("- ok: Fine."), std::string::npos);
    EXPECT_EQ(cat.find("coded"), std::string::npos);
    EXPECT_EQ(cat.find("imported"), std::string::npos);
    EXPECT_EQ(cat.find("needs-rag"), std::string::npos);
    EXPECT_TRUE(mgr.find("imported").has_value());  // still loadable by name
    EXPECT_EQ(mgr.missingTools(mgr.find("needs-rag")->skill), std::vector<std::string>{"query_documents"});
}

TEST_F(SkillManagerTest, NoSkillsMeansOnlyTheSourcingRule) {
    SkillManager mgr({}, {});
    EXPECT_EQ(mgr.catalogPrompt().find("==== SKILLS ===="), std::string::npos);
    EXPECT_EQ(mgr.catalogPrompt().rfind("==== SOURCING ====", 0), 0u);
}

TEST_F(SkillManagerTest, RuntimeLoadsInjectsAndUnloads) {
    SkillManager mgr({embed("voice", skillMd("voice", "Voice.", "", "Be warm.")),
                      embed("data", skillMd("data", "Data.", "    tools_required:\n      - query_data\n      - nope\n", "Use SQL.")),
                      embed("coded", skillMd("coded", "Code.", "    tools:\n      - name: x\n        description: X.\n"))},
                     {});
    ToolRegistry reg;
    SkillRuntime rt(mgr, reg);
    rt.setAlwaysOn({"voice"});
    int changes = 0;
    rt.registerTools(reg, [&changes] { ++changes; });
    EXPECT_EQ(rt.loadedPrompt(), "");

    ASSERT_TRUE(rt.loadSkill("voice"));
    json r = reg.executeTool("load_skill", {{"name", "data"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(changes, 1);
    EXPECT_EQ(r["unmet_tools_required"], json::array({"query_data", "nope"}));
    EXPECT_EQ(rt.loadedPrompt(),
              "==== LOADED SKILLS ====\n--- SKILL: voice ---\nBe warm.\n\n--- SKILL: data ---\nUse SQL.");

    EXPECT_EQ(reg.executeTool("load_skill", {{"name", "coded"}})["status"], "error");
    EXPECT_THROW(rt.loadSkill("coded"), SkillError);
    EXPECT_EQ(reg.executeTool("load_skill", {{"name", "ghost"}})["status"], "error");
    EXPECT_EQ(reg.executeTool("unload_skill", {{"name", "voice"}})["status"], "error");
    EXPECT_EQ(reg.executeTool("unload_skill", {{"name", "data"}})["status"], "success");
    EXPECT_EQ(changes, 2);
    EXPECT_FALSE(rt.isLoaded("data"));

    json status = reg.executeTool("skill_status", json::object());
    EXPECT_EQ(status["loaded_count"], 1);
    json list = reg.executeTool("list_skills", json::object());
    ASSERT_TRUE(list.contains("refused"));
    EXPECT_EQ(list["refused"][0]["name"], "coded");
}

TEST_F(SkillManagerTest, MissingBinaryAddsAnOnThisMachineNote) {
    SkillManager mgr({embed("gh-thing", skillMd("gh-thing", "GH.",
                                                "    permissions:\n      - shell:execute:gh\n", "Use gh."))},
                     {});
    ToolRegistry reg;
    SkillRuntime rt(mgr, reg);
    rt.loadSkill("gh-thing");
    // Present or absent, the body itself is always first and unchanged.
    EXPECT_EQ(rt.loadedPrompt().rfind("==== LOADED SKILLS ====\n--- SKILL: gh-thing ---\nUse gh.", 0), 0u);
}

TEST_F(SkillManagerTest, RescanWhileAnotherThreadReadsIsSafe) {
    const fs::path user = root_ / "user";
    for (int i = 0; i < 8; ++i) writeSkill(user, "s" + std::to_string(i), skillMd("s" + std::to_string(i), "Skill."));
    SkillManager mgr({embed("voice", skillMd("voice", "Voice."))}, {{"user", user.u8string(), false, true}});
    mgr.rescan();
    std::thread writer([&mgr] {
        for (int i = 0; i < 50; ++i) mgr.rescan();
    });
    for (int i = 0; i < 200; ++i) {
        EXPECT_TRUE(mgr.find("voice").has_value());
        EXPECT_NE(mgr.catalogPrompt().find("- s7: Skill."), std::string::npos);
    }
    writer.join();
    EXPECT_EQ(mgr.skills().size(), 9u);
}
