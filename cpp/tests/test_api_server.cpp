// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The --serve HTTP API against a real loopback socket: who may call it, how
// big a request may be, and what a failed model turn looks like on the wire.

#include <gtest/gtest.h>

#include "api_server.h"
#include "support/mock_llm_server.h"

#include <gaia/agent.h>

#include <chrono>
#include <functional>
#include <future>
#include <string>
#include <thread>

#include <httplib.h>

using namespace gaia;

namespace {

class EchoAgent : public Agent {
public:
    explicit EchoAgent(const AgentConfig& cfg) : Agent(cfg) { init(); }

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "echo", "Echo a message back",
            [](const json& args) -> json { return json{{"echoed", args.value("message", "")}}; },
            {{"message", ToolParamType::STRING, false, "Message to echo"}});
    }
};

AgentConfig agentCfg(const std::string& url) {
    AgentConfig cfg;
    cfg.baseUrl = url;
    cfg.modelId = "mock-model";
    cfg.contextSize = 16384;  // what the mock reports loaded, so no /load
    cfg.maxSteps = 3;
    cfg.silentMode = true;
    cfg.streaming = false;
    return cfg;
}

const char* const kJson = "application/json";

std::string chatBody(bool stream = false) {
    return json{{"messages", json::array({{{"role", "user"}, {"content", "hi"}}})}, {"stream", stream}}.dump();
}

class ApiServerTest : public ::testing::Test {
protected:
    bench::MockLlmServer mock_;
    std::unique_ptr<EchoAgent> agent_;
    std::unique_ptr<ApiServer> server_;
    std::thread thread_;
    int port_ = 0;

    void SetUp() override {
        agent_ = std::make_unique<EchoAgent>(agentCfg(mock_.baseUrl()));
        server_ = std::make_unique<ApiServer>(*agent_, 0);
        port_ = server_->bind();
        thread_ = std::thread([this] { server_->run(); });
        httplib::Client probe("127.0.0.1", port_);
        for (int i = 0; i < 100; ++i) {
            if (auto r = probe.Get("/health"); r && r->status == 200) return;
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        FAIL() << "ApiServer did not come up";
    }
    void TearDown() override {
        server_->stop();
        if (thread_.joinable()) thread_.join();
    }
    httplib::Client client() {
        httplib::Client c("127.0.0.1", port_);
        c.set_read_timeout(30);
        c.set_write_timeout(30);
        return c;
    }
};

}  // namespace

TEST_F(ApiServerTest, OnlyThisMachineMayCall) {
    auto c = client();
    auto foreignHost = c.Get("/health", {{"Host", "evil.example"}});
    ASSERT_TRUE(foreignHost);
    EXPECT_EQ(foreignHost->status, 403);
    auto foreignOrigin = c.Get("/health", {{"Origin", "http://evil.example"}});
    ASSERT_TRUE(foreignOrigin);
    EXPECT_EQ(foreignOrigin->status, 403);
    EXPECT_EQ(json::parse(foreignOrigin->body)["error"]["type"], "forbidden");
    auto local = c.Get("/health", {{"Origin", "http://localhost:5174"}});
    ASSERT_TRUE(local);
    EXPECT_EQ(local->status, 200);
    EXPECT_EQ(local->get_header_value("Access-Control-Allow-Origin"), "http://localhost:5174");
}

TEST_F(ApiServerTest, PostsMustBeJsonWithALength) {
    // A refusal closes the connection over the unread body, which can reset it
    // before the client reads the answer, so a few attempts are allowed.
    auto answered = [](const std::function<httplib::Result()>& send) {
        httplib::Result r;
        for (int attempt = 0; attempt < 5 && !r; ++attempt) r = send();
        return r;
    };
    auto form = answered([this] {
        return client().Post("/v1/chat/completions", "messages=hi", "application/x-www-form-urlencoded");
    });
    ASSERT_TRUE(form);
    EXPECT_EQ(form->status, 415);
    // A chunked upload has no length to check against the limit.
    auto chunked = answered([this] {
        return client().Post(
            "/v1/chat/completions",
            [](size_t, httplib::DataSink& sink) {
                const std::string body = chatBody();
                sink.write(body.data(), body.size());
                sink.done();
                return true;
            },
            kJson);
    });
    ASSERT_TRUE(chunked);
    EXPECT_EQ(chunked->status, 411);
    EXPECT_EQ(mock_.requestCount(), 0);
}

TEST_F(ApiServerTest, OversizedBodyIs413WithAnApiError) {
    auto c = client();
    const std::string huge(17 * 1024 * 1024, 'a');
    auto r = c.Post("/v1/chat/completions", huge, kJson);
    ASSERT_TRUE(r) << httplib::to_string(r.error());
    EXPECT_EQ(r->status, 413);
    const json body = json::parse(r->body);
    EXPECT_EQ(body["error"]["code"], "payload_too_large");
    EXPECT_EQ(mock_.requestCount(), 0);
}

TEST_F(ApiServerTest, AnswerCarriesTheRealUsage) {
    mock_.pushResponse(
        R"({"choices":[{"message":{"content":"{\"answer\":\"hello\"}"}}],)"
        R"("usage":{"prompt_tokens":11,"completion_tokens":3,"total_tokens":14}})");
    auto r = client().Post("/v1/chat/completions", chatBody(), kJson);
    ASSERT_TRUE(r);
    ASSERT_EQ(r->status, 200) << r->body;
    const json body = json::parse(r->body);
    EXPECT_EQ(body["choices"][0]["message"]["content"], "hello");
    EXPECT_EQ(body["usage"]["prompt_tokens"], 11);
    EXPECT_EQ(body["usage"]["total_tokens"], 14);
}

TEST_F(ApiServerTest, AModelFailureIs502NotAnAnswer) {
    for (const bool stream : {false, true}) {
        mock_.pushResponse(R"({"error":"backend down"})", 500);
        mock_.pushResponse(R"({"error":"backend down"})", 500);
        auto r = client().Post("/v1/chat/completions", chatBody(stream), kJson);
        ASSERT_TRUE(r);
        EXPECT_EQ(r->status, 502) << "stream=" << stream << " " << r->body;
        const json body = json::parse(r->body);
        EXPECT_EQ(body["error"]["type"], "upstream_error");
        EXPECT_NE(body["error"]["message"].get<std::string>().find("500"), std::string::npos) << r->body;
    }
}

TEST_F(ApiServerTest, ADirectToolCallWaitsForNoTurn) {
    std::promise<void> release;
    mock_.holdNextResponse(release.get_future().share());
    auto turn = std::async(std::launch::async, [this] { return client().Post("/v1/chat/completions", chatBody(), kJson); });
    while (mock_.requestCount() < 1) std::this_thread::sleep_for(std::chrono::milliseconds(10));

    auto tool = client().Post("/v1/tools/echo", R"({"message":"x"})", kJson);
    ASSERT_TRUE(tool);
    EXPECT_EQ(tool->status, 409);
    EXPECT_EQ(json::parse(tool->body)["error"]["code"], "agent_busy");

    release.set_value();
    auto done = turn.get();
    ASSERT_TRUE(done);
    EXPECT_EQ(done->status, 200);

    auto after = client().Post("/v1/tools/echo", R"({"message":"x"})", kJson);
    ASSERT_TRUE(after);
    EXPECT_EQ(after->status, 200);
    EXPECT_EQ(json::parse(after->body)["result"]["echoed"], "x");
}
