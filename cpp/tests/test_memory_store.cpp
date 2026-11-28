// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <httplib.h>
#include <gaia/lemonade_client.h>
#include <gaia/memory_store.h>
#include <gaia/tool_registry.h>

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <filesystem>
#include <random>
#include <thread>

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

TEST(MemoryStoreTest, ASearchFindsASensitiveFactButAListingLeavesItOut) {
    MemoryStore m(":memory:");
    ToolRegistry reg;
    m.registerTools(reg);
    reg.executeTool("remember", json{{"fact", "My bike lock code is 4471"}, {"sensitive", true}});
    const json found = reg.executeTool("recall", json{{"query", "bike lock code"}});
    ASSERT_EQ(found["count"], 1);
    EXPECT_NE(found["memories"][0]["content"].get<std::string>().find("4471"), std::string::npos);
    EXPECT_EQ(reg.executeTool("recall", json::object())["count"], 0);
    EXPECT_EQ(reg.executeTool("recall", json{{"query", " ?! "}})["count"], 0) << "no words is a listing";
    EXPECT_EQ(reg.executeTool("recall", json{{"include_sensitive", true}})["count"], 1);
}

TEST(MemoryStoreTest, FactsWithoutPreferencesStillPointAtRecall) {
    MemoryStore m(":memory:");
    m.remember("My bike lock code is 4471", "fact", "", "", true);
    EXPECT_NE(m.promptBlock().find("recall"), std::string::npos);
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

namespace {

std::string lowerAscii(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

// Semantic topic vectors: vehicle terms map near [1,0,0], food near [0,1,0], work near [0,0,1].
std::vector<float> topicVector(const std::string& text) {
    const std::string s = lowerAscii(text);
    float v = 0.0f, f = 0.0f, w = 0.0f;
    for (const char* k : {"car", "automobile", "vehicle", "sedan", "garage", "tire", "motor"}) {
        if (s.find(k) != std::string::npos) v += 1.0f;
    }
    for (const char* k : {"pizza", "pasta", "bread", "dinner", "cuisine", "italian"}) {
        if (s.find(k) != std::string::npos) f += 1.0f;
    }
    for (const char* k : {"sprint", "standup", "release", "deploy", "server", "gateway"}) {
        if (s.find(k) != std::string::npos) w += 1.0f;
    }
    if (v == 0.0f && f == 0.0f && w == 0.0f) return {0.0f, 0.0f, 0.0f, 1.0f};
    return {v, f, w, 0.0f};
}

}  // namespace

TEST(MemoryStoreTest, SemanticRecallOverLemonadeEmbeddingsFindsMeaningWithoutWordOverlap) {
    httplib::Server srv;
    std::atomic<int> calls{0};
    std::string seenAuth;
    std::string seenModel;
    srv.Post("/api/v1/embeddings", [&](const httplib::Request& req, httplib::Response& res) {
        ++calls;
        seenAuth = req.get_header_value("Authorization");
        const json body = json::parse(req.body);
        seenModel = body.value("model", "");
        json data = json::array();
        const auto& inputs = body.at("input");
        for (size_t i = 0; i < inputs.size(); ++i) {
            data.push_back({{"index", i}, {"embedding", topicVector(inputs[i].get<std::string>())}});
        }
        res.set_content(json{{"data", data}}.dump(), "application/json");
    });
    const int port = srv.bind_to_any_port("127.0.0.1");
    std::thread serverThread([&srv] { srv.listen_after_bind(); });
    while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));

    LemonadeClient client("http://127.0.0.1:" + std::to_string(port) + "/api/v1");
    client.setApiKey("test-embed-key");
    EmbedFunction embed = [&client](const std::vector<std::string>& texts) {
        return client.embeddings("user.embeddinggemma-300m-GGUF", texts);
    };

    MemoryStore m(":memory:", embed, "user.embeddinggemma-300m-GGUF");
    EXPECT_TRUE(m.usesEmbeddings());
    const std::string carId = m.remember("My car is parked in the garage");
    m.remember("I love pepperoni pizza for dinner");

    // Zero lexical overlap between "automobile" and "car"/"garage": cosine similarity finds it.
    const auto hits = m.recall("automobile");
    ASSERT_EQ(hits.size(), 1u);
    EXPECT_EQ(hits[0].id, carId);
    EXPECT_EQ(seenAuth, "Bearer test-embed-key");
    EXPECT_EQ(seenModel, "user.embeddinggemma-300m-GGUF");
    EXPECT_EQ(calls.load(), 3);

    ToolRegistry reg;
    m.registerTools(reg);
    const json toolOut = reg.executeTool("recall", json{{"query", "automobile"}});
    ASSERT_EQ(toolOut["status"], "success");
    ASSERT_EQ(toolOut["count"], 1);
    EXPECT_EQ(toolOut["memories"][0]["id"], carId);
    EXPECT_NE(toolOut["retrieval"].get<std::string>().find("hybrid"), std::string::npos);

    srv.stop();
    serverThread.join();
}

TEST(MemoryStoreTest, RrfHybridRankingCombinesLexicalAndVectorSignals) {
    EmbedFunction embed = [](const std::vector<std::string>& texts) {
        std::vector<std::vector<float>> out;
        for (const auto& t : texts) {
            if (t.find("both-signal") != std::string::npos) out.push_back({0.95f, 0.31f, 0.0f});
            else if (t.find("vector-only") != std::string::npos) out.push_back({1.0f, 0.0f, 0.0f});
            else if (t.find("keyword-only") != std::string::npos) out.push_back({0.0f, 1.0f, 0.0f});
            else out.push_back({1.0f, 0.0f, 0.0f});  // query vector
        }
        return out;
    };
    MemoryStore m(":memory:", embed, "test-embed");
    const std::string vecOnly = m.remember("vector-only semantic match for vehicle");
    const std::string kwOnly = m.remember("keyword-only cobalt blueprint cobalt");
    const std::string both = m.remember("both-signal cobalt motor");

    // "cobalt" hits BM25 for kwOnly and both; vector hits vecOnly (#1) and both (#2).
    // RRF boosts "both" above single-signal matches, while still retrieving vector-only and keyword-only.
    const auto hits = m.recall("cobalt", "", 5);
    ASSERT_EQ(hits.size(), 3u);
    EXPECT_EQ(hits[0].id, both) << "entry matching both BM25 and vector must rank first under RRF";
    EXPECT_EQ(hits[1].id, vecOnly) << "vector-only match beats keyword-only when vector RRF weight is 0.6";
    EXPECT_EQ(hits[2].id, kwOnly) << "keyword-only match is still retained";
}

TEST(MemoryStoreTest, SchemaMigrationV1ToV2PreservesExistingRowsAndBackfillsEmbeddings) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path dbPath = fs::temp_directory_path() / ("gaia-mem-mig-" + std::to_string(rng()) + ".db");
    {
        // Build a raw v1 schema (no embedding column, no meta table).
        Database raw(dbPath.string());
        raw.migrate({Migration::fromSql(1, "knowledge + fts", R"(
CREATE TABLE knowledge (
    id TEXT PRIMARY KEY, category TEXT NOT NULL, content TEXT NOT NULL,
    domain TEXT, entity TEXT, sensitive INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(id UNINDEXED, content, domain, category,
    tokenize='porter unicode61');
)")});
        raw.run("INSERT INTO knowledge VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                "m-legacy", "fact", "My vehicle needs new tires", "", "", 0,
                "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z");
        raw.run("INSERT INTO knowledge_fts (id, content, domain, category) VALUES (?, ?, ?, ?)",
                "m-legacy", "My vehicle needs new tires", "", "fact");
    }

    std::atomic<int> embedCalls{0};
    EmbedFunction embed = [&embedCalls](const std::vector<std::string>& texts) {
        ++embedCalls;
        std::vector<std::vector<float>> out;
        for (const auto& t : texts) out.push_back(topicVector(t));
        return out;
    };
    {
        MemoryStore m(dbPath.string(), embed, "embed-v1");
        EXPECT_EQ(m.count(), 1u) << "v1 row must survive migration";
        const auto hits = m.recall("car");
        ASSERT_EQ(hits.size(), 1u);
        EXPECT_EQ(hits[0].id, "m-legacy");
        EXPECT_EQ(embedCalls.load(), 2) << "1 backfill batch + 1 query embed";
    }
    {
        embedCalls = 0;
        MemoryStore m(dbPath.string(), embed, "embed-v1");
        const auto hits = m.recall("car");
        ASSERT_EQ(hits.size(), 1u);
        EXPECT_EQ(embedCalls.load(), 1) << "persisted BLOB avoids re-embedding on reopen";
    }
    {
        embedCalls = 0;
        MemoryStore m(dbPath.string(), embed, "embed-v2");
        const auto hits = m.recall("car");
        ASSERT_EQ(hits.size(), 1u);
        EXPECT_EQ(embedCalls.load(), 2) << "changing embedder model clears stale vectors and re-embeds";
    }

    std::error_code ec;
    fs::remove(dbPath, ec);
}

TEST(MemoryStoreTest, EmbeddingHttp500AndBadJsonFailLoudlyWithoutSilentFallback) {
    httplib::Server srv;
    std::atomic<int> mode{0};  // 0 = ok, 1 = HTTP 500, 2 = malformed JSON
    srv.Post("/api/v1/embeddings", [&](const httplib::Request& req, httplib::Response& res) {
        const int m = mode.load();
        if (m == 1) {
            res.status = 500;
            res.set_content(R"({"error":"embedder crashed"})", "application/json");
            return;
        }
        if (m == 2) {
            res.status = 200;
            res.set_content("not valid json {{{", "application/json");
            return;
        }
        const json body = json::parse(req.body);
        json data = json::array();
        for (size_t i = 0; i < body.at("input").size(); ++i) {
            data.push_back({{"index", i}, {"embedding", topicVector(body.at("input")[i].get<std::string>())}});
        }
        res.set_content(json{{"data", data}}.dump(), "application/json");
    });
    const int port = srv.bind_to_any_port("127.0.0.1");
    std::thread serverThread([&srv] { srv.listen_after_bind(); });
    while (!srv.is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(5));

    LemonadeClient client("http://127.0.0.1:" + std::to_string(port) + "/api/v1");
    EmbedFunction embed = [&client](const std::vector<std::string>& texts) {
        return client.embeddings("embed-model", texts);
    };

    MemoryStore m(":memory:", embed, "embed-model");
    mode = 1;
    EXPECT_THROW(m.remember("My car is green"), HttpError);
    EXPECT_EQ(m.count(), 0u) << "failed embedding must not silently persist unembedded row";

    mode = 2;
    EXPECT_THROW(m.remember("My car is green"), std::exception);
    EXPECT_EQ(m.count(), 0u);

    mode = 0;
    const std::string id = m.remember("My car is green");
    ASSERT_EQ(m.count(), 1u);

    // HTTP 500 during recall must fail loudly (no silent keyword fallback).
    mode = 1;
    EXPECT_THROW(m.recall("car"), HttpError);
    ToolRegistry reg;
    m.registerTools(reg);
    const json toolErr = reg.executeTool("recall", json{{"query", "car"}});
    EXPECT_EQ(toolErr["status"], "error");
    EXPECT_NE(toolErr.value("error", "").find("500"), std::string::npos) << toolErr.dump();

    // HTTP 500 during update leaves old row intact.
    EXPECT_THROW(m.update(id, "My car is yellow"), HttpError);
    mode = 0;
    const auto stillGreen = m.recall("green");
    ASSERT_EQ(stillGreen.size(), 1u);
    EXPECT_NE(stillGreen[0].content.find("green"), std::string::npos);

    srv.stop();
    serverThread.join();
}

TEST(MemoryStoreTest, SensitiveAndCategoryFiltersApplyToSemanticRecall) {
    EmbedFunction embed = [](const std::vector<std::string>& texts) {
        std::vector<std::vector<float>> out;
        for (const auto& t : texts) out.push_back(topicVector(t));
        return out;
    };
    MemoryStore m(":memory:", embed, "test-embed");
    const std::string secretCar = m.remember("My car is a red sedan", "fact", "", "", true);
    const std::string prefCar = m.remember("Prefers an electric vehicle", "preference", "", "", false);

    const auto nonSensitive = m.recall("automobile", "", 10, false);
    ASSERT_EQ(nonSensitive.size(), 1u);
    EXPECT_EQ(nonSensitive[0].id, prefCar);

    const auto withSensitive = m.recall("automobile", "", 10, true);
    ASSERT_EQ(withSensitive.size(), 2u);

    const auto factOnly = m.recall("automobile", "fact", 10, true);
    ASSERT_EQ(factOnly.size(), 1u);
    EXPECT_EQ(factOnly[0].id, secretCar);
}

TEST(MemoryStoreTest, WeaklyOverlappingUnrelatedVectorsFilteredBySimilarityFloor) {
    // {0.9, 0.1} vs {0.1, 0.9} has cosine similarity ~0.219 (> 0, < kMinVectorSimilarity=0.35).
    EmbedFunction embed = [](const std::vector<std::string>& texts) {
        std::vector<std::vector<float>> out;
        for (const auto& t : texts) {
            if (t.find("ocean") != std::string::npos || t.find("marine") != std::string::npos) {
                out.push_back({0.9f, 0.1f});
            } else {
                out.push_back({0.1f, 0.9f});
            }
        }
        return out;
    };
    MemoryStore m(":memory:", embed, "test-embed");
    const std::string ocean = m.remember("Deep ocean trenches host bioluminescent squid");
    m.remember("Sourdough starter needs daily flour and water");

    const auto hits = m.recall("marine biology", "", 10);
    ASSERT_EQ(hits.size(), 1u) << "weakly overlapping unrelated vector must be filtered out";
    EXPECT_EQ(hits[0].id, ocean);
}

TEST(MemoryStoreTest, ConcurrentStoresWithDifferentEmbeddersNeverMixDimensions) {
    std::mt19937_64 rng(std::random_device{}());
    const fs::path dbPath = fs::temp_directory_path() / ("gaia-mem-switch-" + std::to_string(rng()) + ".db");

    EmbedFunction embed2d = [](const std::vector<std::string>& texts) {
        std::vector<std::vector<float>> out;
        for (size_t i = 0; i < texts.size(); ++i) out.push_back({1.0f, 0.0f});
        return out;
    };
    EmbedFunction embed3d = [](const std::vector<std::string>& texts) {
        std::vector<std::vector<float>> out;
        for (size_t i = 0; i < texts.size(); ++i) out.push_back({1.0f, 0.0f, 0.0f});
        return out;
    };

    {
        MemoryStore storeA(dbPath.string(), embed2d, "model-2d");
        MemoryStore storeB(dbPath.string(), embed3d, "model-3d");

        storeA.remember("First fact from store A");
        storeB.remember("Second fact from store B");
        EXPECT_NO_THROW({
            const auto hitsB = storeB.recall("fact", "", 10);
            EXPECT_EQ(hitsB.size(), 2u);
        });

        // Long-lived storeA writes and searches after storeB switched meta.embedder_id.
        storeA.remember("Third fact from store A");
        EXPECT_NO_THROW({
            const auto hitsA = storeA.recall("fact", "", 10);
            EXPECT_EQ(hitsA.size(), 3u);
        });
        EXPECT_NO_THROW({
            const auto hitsB2 = storeB.recall("fact", "", 10);
            EXPECT_EQ(hitsB2.size(), 3u);
        });
    }

    std::error_code ec;
    fs::remove(dbPath, ec);
}

TEST(MemoryStoreTest, KeywordOnlyRetrievalSurfacesEmbeddingNote) {
    MemoryStore m(":memory:", {}, "",
                  "user.embeddinggemma-300m-GGUF is not downloaded; install it with `gaia download user.embeddinggemma-300m-GGUF`");
    m.remember("Prefers dark theme", "preference");
    ToolRegistry reg;
    m.registerTools(reg);
    const json out = reg.executeTool("recall", json{{"query", "dark"}});
    ASSERT_EQ(out["status"], "success");
    EXPECT_NE(out["retrieval"].get<std::string>().find("gaia download user.embeddinggemma-300m-GGUF"),
              std::string::npos)
        << out.dump();
}
