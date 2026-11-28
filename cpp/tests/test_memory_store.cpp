// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <gaia/memory_store.h>
#include <gaia/tool_registry.h>

#include <filesystem>
#include <random>

using namespace gaia;
namespace fs = std::filesystem;

TEST(MemoryStoreTest, RememberRecallUpdateForget) {
    MemoryStore m(":memory:");
    const std::string id = m.remember("The staging gateway runs on port 8080 with basic HTTP auth");
    m.remember("Prefers answers as short bullet lists", "preference");
    EXPECT_EQ(m.count(), 2u);

    auto hits = m.recall("which port is the gateway on");
    ASSERT_FALSE(hits.empty());
    EXPECT_EQ(hits[0].id, id);

    EXPECT_TRUE(m.update(id, "The staging gateway moved to port 9090"));
    hits = m.recall("gateway");
    ASSERT_FALSE(hits.empty());
    EXPECT_NE(hits[0].content.find("9090"), std::string::npos);
    EXPECT_TRUE(m.recall("8080").empty()) << "the search index follows updates";

    EXPECT_TRUE(m.forget(id));
    EXPECT_FALSE(m.forget(id));
    EXPECT_TRUE(m.recall("gateway").empty());
}

TEST(MemoryStoreTest, DuplicateIsOneMemory) {
    MemoryStore m(":memory:");
    const auto a = m.remember("Uses metric units");
    const auto b = m.remember("Uses metric units");
    EXPECT_EQ(a, b);
    EXPECT_EQ(m.count(), 1u);
}

TEST(MemoryStoreTest, RefusesCredentialsAndBadInput) {
    MemoryStore m(":memory:");
    EXPECT_THROW(m.remember("my api key: sk-abcdefghijklmnopqrstu"), std::invalid_argument);
    EXPECT_THROW(m.remember("password = hunter2hunter2"), std::invalid_argument);
    EXPECT_THROW(m.remember("my bank password is swordfish"), std::invalid_argument);
    EXPECT_THROW(m.remember("PIN for the garage: 4471"), std::invalid_argument);
    EXPECT_THROW(m.remember("Visa 4111 1111 1111 1111 expires in May"), std::invalid_argument);
    EXPECT_THROW(m.remember("SSN 078-05-1120"), std::invalid_argument);
    const std::string id = m.remember("Prefers tea");
    EXPECT_THROW(m.update(id, "the cvv is 123"), std::invalid_argument);
    EXPECT_NO_THROW(m.remember("Read up to page 4471 of the manual"));
    EXPECT_THROW(m.remember("  "), std::invalid_argument);
    EXPECT_THROW(m.remember("x", "gossip"), std::invalid_argument);
    EXPECT_NO_THROW(m.remember("The API key is in the team password manager"));
}

TEST(MemoryStoreTest, AHugeTokenShapedFactIsRefusedWithoutCrashing) {
    MemoryStore m(":memory:");
    EXPECT_THROW(m.remember("sk-" + std::string(200000, 'a')), std::invalid_argument);
    EXPECT_THROW(m.remember(std::string(10000, 'x') + " sk-abcdefghijklmnopqrstu"), std::invalid_argument)
        << "a key past the first window is still found";
}

TEST(MemoryStoreTest, ARefusedSecretIsStillInThisChat) {
    MemoryStore m(":memory:");
    ToolRegistry reg;
    m.registerTools(reg);
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    const json out = reg.executeTool("remember", json{{"fact", "The vault password is cobalt-heron-417"}});
    EXPECT_EQ(out["status"], "error");
    const std::string why = out.value("error", "");
    EXPECT_NE(why.find("a new chat won't know it"), std::string::npos) << why;
    EXPECT_NE(why.find("still in this chat"), std::string::npos) << why;
    EXPECT_EQ(m.count(), 0u);

    const std::string id = m.remember("The vault is in the study");
    try {
        m.update(id, "The vault password is cobalt-heron-417");
        FAIL() << "a credential was stored by update";
    } catch (const std::invalid_argument& e) {
        EXPECT_NE(std::string(e.what()).find("still in this chat"), std::string::npos) << e.what();
    }
}

TEST(MemoryStoreTest, SensitiveEntriesStayOutOfDefaultListings) {
    MemoryStore m(":memory:");
    m.remember("Has a dentist appointment on Friday", "reminder", "", "", true);
    EXPECT_TRUE(m.recall("dentist").empty());
    EXPECT_EQ(m.recall("dentist", "", 10, true).size(), 1u);
}

TEST(MemoryStoreTest, PunctuationIsNotFtsSyntax) {
    MemoryStore m(":memory:");
    m.remember("Deploys go out on Tuesdays");
    EXPECT_NO_THROW(m.recall("deploys AND (\"tuesday* OR NEAR"));
}

TEST(MemoryStoreTest, PreferencesReachThePrompt) {
    MemoryStore m(":memory:");
    EXPECT_EQ(m.promptBlock(), "");
    m.remember("Prefers metric units", "preference");
    EXPECT_NE(m.promptBlock().find("Prefers metric units"), std::string::npos);
}

TEST(MemoryStoreTest, PersistsAcrossOpens) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path db = fs::temp_directory_path() / ("gaia-mem-" + std::to_string(rng()) + ".db");
    {
        MemoryStore m(db.string());
        m.remember("Team standup is at 9:30");
    }
    {
        MemoryStore m(db.string());
        EXPECT_EQ(m.recall("standup").size(), 1u);
    }
    std::error_code ec;
    fs::remove(db, ec);
}

TEST(MemoryStoreTest, AFailedIndexWriteStoresNothing) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path db = fs::temp_directory_path() / ("gaia-mem-" + std::to_string(rng()) + ".db");
    {
        MemoryStore m(db.string());
        {
            Database raw(db.string());
            raw.execute("DROP TABLE knowledge_fts");
        }
        EXPECT_ANY_THROW(m.remember("Uses metric units"));
        EXPECT_EQ(m.count(), 0u) << "the fact row must roll back with the failed index row";
    }
    std::error_code ec;
    fs::remove(db, ec);
}

TEST(MemoryStoreTest, Tools) {
    MemoryStore m(":memory:");
    ToolRegistry reg;
    m.registerTools(reg);
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    json r = reg.executeTool("remember", json{{"fact", "Likes dark mode"}, {"category", "preference"}});
    ASSERT_EQ(r["status"], "success");
    json q = reg.executeTool("recall", json{{"query", "dark mode"}});
    EXPECT_EQ(q["count"], 1);
    json bad = reg.executeTool("remember", json{{"fact", "token: ghp_aaaaaaaaaaaaaaaaaaaaaaaa"}});
    EXPECT_EQ(bad["status"], "error");
    EXPECT_EQ(reg.executeTool("forget", json{{"id", r["id"]}})["status"], "success");
}

// The Memory page lists up to 1000; the model's own recall tool stays at 100.
TEST(MemoryStoreTest, AListingReachesPastAHundredButTheModelsRecallDoesNot) {
    MemoryStore m(":memory:");
    for (int i = 0; i < 150; ++i) m.remember("Project milestone number " + std::to_string(i) + " is done");
    EXPECT_EQ(m.recall("", "", 1000, true).size(), 150u);
    EXPECT_EQ(m.recall("milestone", "", 1000, true).size(), 150u);

    ToolRegistry reg;
    m.registerTools(reg);
    const json q = reg.executeTool("recall", json{{"query", "milestone"}, {"limit", 500}});
    EXPECT_EQ(q["count"], 100) << q.dump().substr(0, 300);
}
