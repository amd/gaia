// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Agent-level VLM tests (Ttest4). All tests run against an in-process mock
// HTTP server — no Lemonade required.

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <nlohmann/json.hpp>

#include <atomic>
#include <chrono>
#include <future>
#include <string>
#include <thread>
#include <vector>

#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

gaia::AgentConfig makeCfg(const std::string& url,
                          const std::string& modelId = "") {
    gaia::AgentConfig cfg;
    cfg.baseUrl = url;
    cfg.modelId = modelId;     // empty skips ensureModelLoaded()
    cfg.maxSteps = 3;
    cfg.silentMode = true;
    cfg.debug = false;
    return cfg;
}

// Canned "answer" response so the loop terminates after one LLM call.
const std::string kAnswerResponse = R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"done\"}"}}]})";

std::vector<std::uint8_t> makePngBytes() {
    return {0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,
            0,0,0,13,'I','H','D','R',0,0,0,1,0,0,0,1,8,2};
}

// Find the last request body posted to /chat/completions with a user-role
// message that has parts or content matching a predicate.
json parseBody(const std::string& raw) {
    return json::parse(raw);
}

} // namespace

// ---- AC-9: outbound body shape ----

TEST(AgentVlmTest, ProcessQueryWithImagesSendsArrayContent) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl()));

    Image img = Image::fromBytes(makePngBytes());
    json result = agent.processQuery("describe this", {img}, 1);

    ASSERT_GE(mock.receivedBodies().size(), 1u);
    json body = parseBody(mock.receivedBodies().back());
    ASSERT_TRUE(body.contains("messages"));
    auto& msgs = body["messages"];
    // Find the USER message with an array content
    bool foundArray = false;
    for (auto& m : msgs) {
        if (m["role"] == "user" && m["content"].is_array()) {
            foundArray = true;
            auto& c = m["content"];
            bool hasText = false, hasImage = false;
            for (auto& part : c) {
                if (part["type"] == "text") { hasText = true; EXPECT_EQ(part["text"], "describe this"); }
                if (part["type"] == "image_url") {
                    hasImage = true;
                    std::string url = part["image_url"]["url"].get<std::string>();
                    EXPECT_EQ(url.rfind("data:image/png;base64,", 0), 0u);
                }
            }
            EXPECT_TRUE(hasText);
            EXPECT_TRUE(hasImage);
        }
    }
    EXPECT_TRUE(foundArray);
    EXPECT_EQ(result["result"], "done");
}

// ---- AC-14: backward-compat text-only request shape ----

TEST(AgentVlmTest, ProcessQueryTextOnlyParsedJsonEqualsBaseline) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    AgentConfig cfg = makeCfg(mock.baseUrl());
    cfg.modelId = "mock-model";
    cfg.temperature = 0.7;
    cfg.maxTokens = 4096;
    Bare agent(cfg);

    agent.processQuery("hello world", 1);

    ASSERT_GE(mock.receivedBodies().size(), 1u);
    json body = parseBody(mock.receivedBodies().back());
    // Semantic invariants
    EXPECT_EQ(body["model"], "mock-model");
    EXPECT_EQ(body["max_tokens"], 4096);
    ASSERT_TRUE(body["messages"].is_array());
    // The user turn must be a STRING content (not array).
    bool sawUser = false;
    for (auto& m : body["messages"]) {
        if (m["role"] == "user") {
            sawUser = true;
            EXPECT_TRUE(m["content"].is_string());
            EXPECT_EQ(m["content"], "hello world");
        }
    }
    EXPECT_TRUE(sawUser);
}

// ---- AC-10: vector<Message> overload forwards as composed ----

TEST(AgentVlmTest, ProcessQueryMessagesListOverloadSendsRequestAsComposed) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl()));

    Image img = Image::fromBytes(makePngBytes());
    std::vector<Message> msgs;
    msgs.push_back(Message::fromUser("look here", {img}));

    json result = agent.processQuery(msgs, 1);

    ASSERT_GE(mock.receivedBodies().size(), 1u);
    json body = parseBody(mock.receivedBodies().back());
    bool foundArray = false;
    for (auto& m : body["messages"]) {
        if (m["role"] == "user" && m["content"].is_array()) {
            foundArray = true;
            EXPECT_EQ(m["content"][0]["text"], "look here");
        }
    }
    EXPECT_TRUE(foundArray);
    EXPECT_EQ(result["result"], "done");
}

// ---- AC-11: empty vector<Message> throws, no HTTP, no /load ----

TEST(AgentVlmTest, ProcessQueryMessagesListEmptyThrows) {
    bench::MockLlmServer mock(/*reportModelLoaded=*/false);

    class Bare : public Agent { public: using Agent::Agent; };
    AgentConfig cfg = makeCfg(mock.baseUrl());
    cfg.modelId = "some-model";
    Bare agent(cfg);

    std::vector<Message> empty;
    EXPECT_THROW(agent.processQuery(empty, 1), std::invalid_argument);

    EXPECT_TRUE(mock.receivedBodies().empty());
    EXPECT_EQ(mock.loadRequestCount(), 0);
}

TEST(AgentVlmTest, ProcessQueryAllEmptyUserMessagesThrows) {
    bench::MockLlmServer mock(/*reportModelLoaded=*/false);
    class Bare : public Agent { public: using Agent::Agent; };
    AgentConfig cfg = makeCfg(mock.baseUrl());
    cfg.modelId = "some-model";
    Bare agent(cfg);

    std::vector<Message> msgs;
    Message m; m.role = MessageRole::USER; m.content = "";
    msgs.push_back(m);
    EXPECT_THROW(agent.processQuery(msgs, 1), std::invalid_argument);
    EXPECT_TRUE(mock.receivedBodies().empty());
    EXPECT_EQ(mock.loadRequestCount(), 0);
}

// ---- AC-12: history strips image parts (two-turn) ----

TEST(AgentVlmTest, ProcessQueryWithImagesStripsFromHistory) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswerResponse);
    mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl()));

    Image img = Image::fromBytes(makePngBytes());
    agent.processQuery("turn1", {img}, 1);
    agent.processQuery("turn2 text only", 1);

    ASSERT_GE(mock.receivedBodies().size(), 2u);
    json body2 = parseBody(mock.receivedBodies().back());
    // History at turn 2: prior USER turn should have STRING content (text-only).
    int userCount = 0;
    for (auto& m : body2["messages"]) {
        if (m["role"] == "user") {
            ++userCount;
            // Each user message in turn-2 must now be a plain string
            EXPECT_TRUE(m["content"].is_string())
                << "User content at turn 2 should be string (image stripped); "
                << "got: " << m["content"].dump();
        }
    }
    EXPECT_GE(userCount, 2);
}

// ---- AC-12 / AC-15i: three-turn isolation ----

TEST(AgentVlmTest, ProcessQueryWithImagesStripsAcrossThreeTurns) {
    bench::MockLlmServer mock;
    for (int i = 0; i < 3; ++i) mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl()));

    Image img = Image::fromBytes(makePngBytes());
    agent.processQuery("turn1", {img}, 1);
    agent.processQuery("turn2", 1);
    agent.processQuery("turn3", 1);

    ASSERT_GE(mock.receivedBodies().size(), 3u);
    std::string turn3 = mock.receivedBodies().back();
    // No base64 data URI should appear anywhere in turn 3's outbound body.
    EXPECT_EQ(turn3.find("data:image/"), std::string::npos);
}

// ---- AC-15j: stored-history semantic invariant ----

TEST(AgentVlmTest, ProcessQueryInternalOwnsAllHistoryWrites) {
    bench::MockLlmServer mock;
    for (int i = 0; i < 2; ++i) mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl()));

    Image img = Image::fromBytes(makePngBytes());
    agent.processQuery("look", {img}, 1);
    agent.processQuery("and again", 1);

    ASSERT_GE(mock.receivedBodies().size(), 2u);
    // Turn 2's request body should NOT contain any base64 data-URI substring
    // (which would indicate image data leaked through from turn 1's history).
    EXPECT_EQ(mock.receivedBodies().back().find("data:image/"), std::string::npos);
}

// ---- AC-13: both new overloads trigger /load on first use ----

TEST(AgentVlmTest, ProcessQueryEnsuresModelLoadedForBothOverloads) {
    bench::MockLlmServer mock(/*reportModelLoaded=*/false);
    mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    AgentConfig cfg = makeCfg(mock.baseUrl());
    cfg.modelId = "some-model";
    Bare agent(cfg);

    Image img = Image::fromBytes(makePngBytes());
    agent.processQuery("x", {img}, 1);
    EXPECT_GT(mock.loadRequestCount(), 0);

    // Messages-list overload on a fresh agent:
    bench::MockLlmServer mock2(/*reportModelLoaded=*/false);
    mock2.pushResponse(kAnswerResponse);
    AgentConfig cfg2 = makeCfg(mock2.baseUrl());
    cfg2.modelId = "some-model";
    Bare agent2(cfg2);
    std::vector<Message> msgs = {Message::fromUser("hi", {})};
    agent2.processQuery(msgs, 1);
    EXPECT_GT(mock2.loadRequestCount(), 0);
}

// ---- AC-15b: concurrent entry guard ----

TEST(AgentVlmTest, ProcessQueryConcurrentEntryThrows) {
    bench::MockLlmServer mock;
    std::promise<void> releasePromise;
    std::shared_future<void> release = releasePromise.get_future().share();
    mock.holdNextResponse(release);
    mock.pushResponse(kAnswerResponse); // for thread 1 continuation after release

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl()));

    std::exception_ptr secondError = nullptr;

    std::thread t1([&]() {
        try {
            agent.processQuery("first", 1);
        } catch (...) {
            // Should not throw
            secondError = std::current_exception();
        }
    });

    // Wait until thread 1 is IN the critical section — detected by its LLM
    // request hitting the mock.
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (mock.requestCount() < 1 &&
           std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
    ASSERT_GE(mock.requestCount(), 1) << "thread 1 did not enter in time";

    // Thread 2: must throw runtime_error.
    bool threw = false;
    try {
        agent.processQuery("second", 1);
    } catch (const std::runtime_error&) {
        threw = true;
    }
    EXPECT_TRUE(threw);

    releasePromise.set_value();
    t1.join();
    EXPECT_FALSE(secondError) << "thread 1 unexpectedly threw";
}

// ---- AC-15c: MCP+VLM interop (system prompt contains tool schema) ----

TEST(AgentVlmTest, VlmWithMcpToolPreservesToolSchema) {
    bench::MockLlmServer mock;
    mock.pushResponse(kAnswerResponse);

    class ToolAgent : public Agent {
    public:
        using Agent::Agent;
        void doInit() {
            auto& reg = toolRegistry();
            ToolInfo t;
            t.name = "my_vision_tool";
            t.description = "A tool that is visible to the agent";
            t.callback = [](const json&) -> json { return json::object(); };
            reg.registerTool(std::move(t));
            rebuildSystemPrompt();
        }
    };

    ToolAgent agent(makeCfg(mock.baseUrl()));
    agent.doInit();

    Image img = Image::fromBytes(makePngBytes());
    agent.processQuery("see this", {img}, 1);

    ASSERT_GE(mock.receivedBodies().size(), 1u);
    json body = parseBody(mock.receivedBodies().back());
    ASSERT_TRUE(body["messages"].is_array());
    ASSERT_GE(body["messages"].size(), 1u);
    EXPECT_EQ(body["messages"][0]["role"], "system");
    std::string sys = body["messages"][0]["content"].get<std::string>();
    EXPECT_NE(sys.find("my_vision_tool"), std::string::npos);
}

// ---- AC-15d: streaming VLM smoke ----

TEST(AgentVlmTest, StreamingVlmDoesNotCrash) {
    // Minimal SSE: two delta frames + [DONE]. Mock server returns this as a
    // JSON string body; for a full SSE contract the integration test covers
    // real Lemonade traffic. This unit-level smoke just asserts streaming
    // + VLM doesn't crash the pipeline.
    bench::MockLlmServer mock;
    // Non-streaming fallback answer — LemonadeClient streaming parser
    // falls back to parsing the body as a non-streaming response when no
    // SSE data:... frames are present.
    mock.pushResponse(kAnswerResponse);

    class Bare : public Agent { public: using Agent::Agent; };
    AgentConfig cfg = makeCfg(mock.baseUrl());
    cfg.streaming = true;
    Bare agent(cfg);

    Image img = Image::fromBytes(makePngBytes());
    json r = agent.processQuery("describe", {img}, 1);
    EXPECT_FALSE(r["result"].get<std::string>().empty());
}
