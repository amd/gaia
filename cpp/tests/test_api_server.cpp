// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// ApiServer tests: a real Agent served over HTTP, its LLM an in-process mock.

#include <gtest/gtest.h>

#include <gaia/agent.h>
#include <gaia/api_server.h>

#include <atomic>
#include <chrono>
#include <future>
#include <memory>
#include <string>
#include <thread>
#include <vector>

#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

class ServedAgent : public Agent {
public:
    explicit ServedAgent(const AgentConfig& cfg) : Agent(cfg) { init(); }
    std::atomic<int> echoCalls{0};

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "echo", "Echo a message back",
            [this](const json& args) -> json {
                ++echoCalls;
                return json{{"echoed", args.value("message", "")}};
            },
            {{"message", ToolParamType::STRING, true, "Message to echo"}});
        toolRegistry().registerTool(
            "binary", "Return bytes that are not valid UTF-8",
            [](const json&) -> json { return json{{"data", std::string("ok \xff\xfe end")}}; },
            {});
    }
    std::string getSystemPrompt() const override { return "You are a served test agent."; }
};

const std::string kAnswerWithUsage =
    R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"done\"}"}}],)"
    R"("usage":{"prompt_tokens":11,"completion_tokens":7,"total_tokens":18}})";

/// Mock Lemonade + agent + ApiServer on a free port.
struct Harness {
    bench::MockLlmServer mock;
    std::unique_ptr<ServedAgent> agent;
    std::unique_ptr<ApiServer> server;
    std::thread thread;
    std::string runError;

    explicit Harness(ServerOptions opts = {}, bool streaming = false) {
        AgentConfig cfg;
        cfg.baseUrl = mock.baseUrl();
        cfg.modelId = "mock-model";
        cfg.maxSteps = 3;
        cfg.silentMode = true;
        cfg.streaming = streaming;
        agent = std::make_unique<ServedAgent>(cfg);
        opts.port = 0;
        server = std::make_unique<ApiServer>(*agent, opts);
        thread = std::thread([this] {
            try {
                server->run();
            } catch (const std::exception& e) {
                runError = e.what();
            }
        });
        for (int i = 0; i < 200 && !server->isRunning(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
        }
        if (!server->isRunning()) throw std::runtime_error("ApiServer did not start: " + runError);
    }

    ~Harness() {
        server->stop();
        thread.join();
    }

    httplib::Client client() const {
        httplib::Client c("127.0.0.1", server->port());
        c.set_read_timeout(20);
        return c;
    }
};

std::string chatBody(const json& messages, bool stream = false, json extra = json::object()) {
    json b = {{"model", "gaia"}, {"messages", messages}, {"stream", stream}};
    b.update(extra);
    return b.dump();
}

json userOnly(const std::string& text) {
    return json::array({{{"role", "user"}, {"content", text}}});
}

/// Parse an SSE body into its JSON payloads; asserts the [DONE] terminator.
std::vector<json> parseSse(const std::string& body, bool& sawDone) {
    std::vector<json> out;
    sawDone = false;
    size_t pos = 0;
    while (pos < body.size()) {
        size_t end = body.find("\n\n", pos);
        if (end == std::string::npos) end = body.size();
        std::string frame = body.substr(pos, end - pos);
        pos = end + 2;
        if (frame.empty()) continue;
        EXPECT_EQ(frame.rfind("data: ", 0), 0u) << frame;
        std::string data = frame.substr(6);
        if (data == "[DONE]") {
            sawDone = true;
            continue;
        }
        EXPECT_FALSE(sawDone) << "frame after [DONE]";
        out.push_back(json::parse(data));
    }
    return out;
}

/// Validate standard chunk fields and return the concatenated content.
std::string checkChunks(const std::vector<json>& chunks) {
    std::string content;
    for (const auto& c : chunks) {
        EXPECT_EQ(c.value("object", ""), "chat.completion.chunk");
        EXPECT_TRUE(c["id"].is_string());
        EXPECT_TRUE(c["created"].is_number_integer());
        EXPECT_TRUE(c["model"].is_string());
        EXPECT_TRUE(c["choices"].is_array());
        if (c["choices"].empty()) continue;  // usage-only chunk
        const auto& ch = c["choices"][0];
        EXPECT_EQ(ch["index"], 0);
        EXPECT_TRUE(ch["delta"].is_object());
        EXPECT_TRUE(ch["finish_reason"].is_null() || ch["finish_reason"].is_string());
        if (ch["delta"].contains("content")) content += ch["delta"]["content"].get<std::string>();
    }
    return content;
}

template <typename Pred>
bool waitFor(Pred pred, int ms = 5000) {
    for (int i = 0; i < ms / 10; ++i) {
        if (pred()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return pred();
}

} // namespace

// ---- Security ----

TEST(ApiServerTest, HealthNeedsNoAuthAndReportsVersion) {
    ServerOptions o;
    o.apiKey = "s3cret";
    o.agentVersion = "9.9.9";
    Harness h(o);
    auto res = h.client().Get("/health");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 200);
    json j = json::parse(res->body);
    EXPECT_EQ(j["version"], ApiServer::version());
    EXPECT_FALSE(std::string(ApiServer::version()).empty());
    EXPECT_EQ(j["agent_version"], "9.9.9");
}

TEST(ApiServerTest, ApiKeyRequiredAcceptedAndRejected) {
    ServerOptions o;
    o.apiKey = "s3cret";
    Harness h(o);
    auto cli = h.client();

    auto none = cli.Get("/v1/models");
    ASSERT_TRUE(none);
    EXPECT_EQ(none->status, 401);
    EXPECT_EQ(json::parse(none->body)["error"]["code"], "invalid_api_key");

    auto wrong = cli.Get("/v1/models", {{"Authorization", "Bearer s3cres"}});
    ASSERT_TRUE(wrong);
    EXPECT_EQ(wrong->status, 401);

    auto prefix = cli.Get("/v1/models", {{"Authorization", "Bearer s3cret-and-more"}});
    ASSERT_TRUE(prefix);
    EXPECT_EQ(prefix->status, 401);

    auto ok = cli.Get("/v1/models", {{"Authorization", "Bearer s3cret"}});
    ASSERT_TRUE(ok);
    EXPECT_EQ(ok->status, 200);

    auto chat = cli.Post("/v1/chat/completions", chatBody(userOnly("hi")), "application/json");
    ASSERT_TRUE(chat);
    EXPECT_EQ(chat->status, 401);
    EXPECT_EQ(h.mock.requestCount(), 0);
}

TEST(ApiServerTest, NoCorsByDefaultAndForeignOriginRejected) {
    Harness h;
    auto cli = h.client();

    auto plain = cli.Get("/health");
    ASSERT_TRUE(plain);
    EXPECT_FALSE(plain->has_header("Access-Control-Allow-Origin"));

    auto evil = cli.Post("/v1/chat/completions", {{"Origin", "http://evil.example"}},
                         chatBody(userOnly("rm -rf")), "application/json");
    ASSERT_TRUE(evil);
    EXPECT_EQ(evil->status, 403);
    EXPECT_FALSE(evil->has_header("Access-Control-Allow-Origin"));
    EXPECT_EQ(h.mock.requestCount(), 0);

    auto preflight = cli.Options("/v1/chat/completions", {{"Origin", "http://evil.example"}});
    ASSERT_TRUE(preflight);
    EXPECT_EQ(preflight->status, 403);
}

TEST(ApiServerTest, AllowedOriginGetsCors) {
    ServerOptions o;
    o.allowedOrigins = {"http://localhost:5173"};
    Harness h(o);
    auto cli = h.client();
    auto pre = cli.Options("/v1/models", {{"Origin", "http://localhost:5173"}});
    ASSERT_TRUE(pre);
    EXPECT_EQ(pre->status, 204);
    EXPECT_EQ(pre->get_header_value("Access-Control-Allow-Origin"), "http://localhost:5173");
    auto get = cli.Get("/v1/models", {{"Origin", "http://localhost:5173"}});
    ASSERT_TRUE(get);
    EXPECT_EQ(get->status, 200);
    EXPECT_EQ(get->get_header_value("Access-Control-Allow-Origin"), "http://localhost:5173");
}

TEST(ApiServerTest, ForeignHostHeaderRejected) {
    Harness h;
    auto res = h.client().Get("/v1/models", {{"Host", "attacker.example:8200"}});
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 403);
    EXPECT_EQ(json::parse(res->body)["error"]["code"], "host_not_allowed");
}

TEST(ApiServerTest, PostRequiresJsonContentType) {
    Harness h;
    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi")), "text/plain");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 415);
    EXPECT_EQ(h.mock.requestCount(), 0);
}

TEST(ApiServerTest, ToolRouteDisabledByDefault) {
    Harness h;
    auto res = h.client().Post("/v1/tools/echo", R"({"message":"x"})", "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 404);
    EXPECT_EQ(h.agent->echoCalls.load(), 0);

    auto list = h.client().Get("/v1/tools");
    ASSERT_TRUE(list);
    EXPECT_EQ(list->status, 200);
    bool found = false;
    const json listed = json::parse(list->body);
    for (const auto& t : listed["tools"]) found |= t["name"] == "echo";
    EXPECT_TRUE(found) << list->body;
}

TEST(ApiServerTest, ToolRouteWhenOptedIn) {
    ServerOptions o;
    o.exposeToolRoutes = true;
    Harness h(o);
    auto res = h.client().Post("/v1/tools/echo", R"({"message":"x"})", "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 200);
    EXPECT_EQ(json::parse(res->body)["result"]["echoed"], "x");
    EXPECT_EQ(h.agent->echoCalls.load(), 1);
}

TEST(ApiServerTest, NonLoopbackWithoutKeyRefused) {
    bench::MockLlmServer mock;
    AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.silentMode = true;
    ServedAgent agent(cfg);
    ServerOptions o;
    o.host = "0.0.0.0";
    o.port = 0;
    ApiServer server(agent, o);
    EXPECT_THROW(server.run(), std::runtime_error);
}

// ---- Concurrency ----

TEST(ApiServerTest, ConcurrentCompletionGets409) {
    Harness h;
    std::promise<void> release;
    h.mock.holdNextResponse(release.get_future().share());

    auto first = std::async(std::launch::async, [&] {
        return h.client().Post("/v1/chat/completions", chatBody(userOnly("one")),
                               "application/json");
    });
    ASSERT_TRUE(waitFor([&] { return h.mock.requestCount() >= 1; }));

    auto second = h.client().Post("/v1/chat/completions", chatBody(userOnly("two")),
                                  "application/json");
    ASSERT_TRUE(second);
    EXPECT_EQ(second->status, 409);
    EXPECT_EQ(json::parse(second->body)["error"]["code"], "agent_busy");

    auto streamed = h.client().Post("/v1/chat/completions", chatBody(userOnly("three"), true),
                                    "application/json");
    ASSERT_TRUE(streamed);
    EXPECT_EQ(streamed->status, 409);

    release.set_value();
    auto r1 = first.get();
    ASSERT_TRUE(r1);
    EXPECT_EQ(r1->status, 200);
}

TEST(ApiServerTest, AgentBusyOutsideServerMapsTo409) {
    // The agent is already running a query the server did not start: the
    // server must recognise processQuery's re-entrancy error, not 500.
    Harness h;
    std::promise<void> release;
    h.mock.holdNextResponse(release.get_future().share());
    auto direct = std::async(std::launch::async, [&] { return h.agent->processQuery("direct"); });
    ASSERT_TRUE(waitFor([&] { return h.mock.requestCount() >= 1; }));

    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi")),
                               "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 409);
    EXPECT_EQ(json::parse(res->body)["error"]["code"], "agent_busy");

    release.set_value();
    direct.get();
}

// ---- Completions ----

TEST(ApiServerTest, NonStreamingIsValidOpenAiWithUsage) {
    Harness h;
    h.mock.pushResponse(kAnswerWithUsage);
    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi")),
                               "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200) << res->body;
    json j = json::parse(res->body);
    EXPECT_EQ(j["object"], "chat.completion");
    EXPECT_TRUE(j["id"].is_string());
    EXPECT_TRUE(j["created"].is_number_integer());
    EXPECT_EQ(j["model"], "mock-model");
    ASSERT_EQ(j["choices"].size(), 1u);
    EXPECT_EQ(j["choices"][0]["message"]["role"], "assistant");
    EXPECT_EQ(j["choices"][0]["message"]["content"], "done");
    EXPECT_EQ(j["choices"][0]["finish_reason"], "stop");
    EXPECT_EQ(j["usage"]["prompt_tokens"], 11);
    EXPECT_EQ(j["usage"]["completion_tokens"], 7);
    EXPECT_EQ(j["usage"]["total_tokens"], 18);
}

TEST(ApiServerTest, MultiTurnHistoryReachesTheLlm) {
    Harness h;
    json messages = json::array({
        {{"role", "system"}, {"content", "CLIENT-SYSTEM-PROMPT"}},
        {{"role", "user"}, {"content", "my name is Ada"}},
        {{"role", "assistant"}, {"content", "Nice to meet you, Ada."}},
        {{"role", "user"}, {"content", json::array({{{"type", "text"}, {"text", "what is my name?"}}})}},
    });
    auto res = h.client().Post("/v1/chat/completions", chatBody(messages), "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200) << res->body;

    auto bodies = h.mock.receivedBodies();
    ASSERT_FALSE(bodies.empty());
    json sent = json::parse(bodies.front())["messages"];
    std::vector<std::pair<std::string, std::string>> turns;
    for (const auto& m : sent) {
        std::string content = m["content"].is_string() ? m["content"].get<std::string>() : m["content"].dump();
        EXPECT_EQ(content.find("CLIENT-SYSTEM-PROMPT"), std::string::npos);
        if (m["role"] != "system") turns.emplace_back(m["role"].get<std::string>(), content);
    }
    ASSERT_EQ(turns.size(), 3u);
    EXPECT_EQ(turns[0], std::make_pair(std::string("user"), std::string("my name is Ada")));
    EXPECT_EQ(turns[1], std::make_pair(std::string("assistant"), std::string("Nice to meet you, Ada.")));
    EXPECT_EQ(turns[2].first, "user");
    EXPECT_NE(turns[2].second.find("what is my name?"), std::string::npos);

    // A second, independent request does not inherit the first one's turns.
    auto res2 = h.client().Post("/v1/chat/completions", chatBody(userOnly("fresh")),
                                "application/json");
    ASSERT_TRUE(res2);
    json sent2 = json::parse(h.mock.receivedBodies().back())["messages"];
    for (const auto& m : sent2) EXPECT_EQ(m.dump().find("Ada"), std::string::npos);
}

TEST(ApiServerTest, ImagePartBecomesImageContent) {
    Harness h;
    // 1x1 PNG header bytes, base64.
    const std::string png = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAAA=";
    json messages = json::array({{{"role", "user"},
                                  {"content", json::array({
                                      {{"type", "text"}, {"text", "describe"}},
                                      {{"type", "image_url"},
                                       {"image_url", {{"url", "data:image/png;base64," + png}}}},
                                  })}}});
    auto res = h.client().Post("/v1/chat/completions", chatBody(messages), "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200) << res->body;
    EXPECT_NE(h.mock.receivedBodies().front().find("data:image/png;base64,"), std::string::npos);
}

TEST(ApiServerTest, BadRequestsFailLoudly) {
    Harness h;
    auto cli = h.client();
    auto post = [&](const json& messages, json extra = json::object()) {
        auto r = cli.Post("/v1/chat/completions", chatBody(messages, false, extra),
                          "application/json");
        return r ? r->status : -1;
    };
    EXPECT_EQ(post(json::array()), 400);
    EXPECT_EQ(post(json::array({{{"role", "assistant"}, {"content", "x"}}})), 400);
    EXPECT_EQ(post(json::array({{{"role", "tool"}, {"content", "x"}}})), 400);
    EXPECT_EQ(post(json::array({{{"role", "user"},
                                 {"content", json::array({{{"type", "image_url"},
                                                           {"image_url", {{"url", "http://x/a.png"}}}}})}}})),
              400);
    EXPECT_EQ(post(userOnly("hi"), {{"tools", json::array({{{"type", "function"}}})}}), 400);
    EXPECT_EQ(h.mock.requestCount(), 0);
}

TEST(ApiServerTest, RejectSystemMessagesOption) {
    ServerOptions o;
    o.rejectSystemMessages = true;
    Harness h(o);
    json messages = json::array({{{"role", "system"}, {"content", "x"}},
                                 {{"role", "user"}, {"content", "hi"}}});
    auto res = h.client().Post("/v1/chat/completions", chatBody(messages), "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 400);
}

TEST(ApiServerTest, ModelsListsAdvertisedName) {
    ServerOptions o;
    o.modelName = "gaia";
    Harness h(o);
    auto res = h.client().Get("/v1/models");
    ASSERT_TRUE(res);
    json j = json::parse(res->body);
    EXPECT_EQ(j["object"], "list");
    ASSERT_EQ(j["data"].size(), 1u);
    EXPECT_EQ(j["data"][0]["id"], "gaia");
    EXPECT_EQ(j["data"][0]["object"], "model");
}

// ---- Streaming ----

TEST(ApiServerTest, StreamingForwardsLiveTokens) {
    Harness h({}, /*streaming=*/true);
    h.mock.enableSseStreaming(true);  // mock streams "Hello" + " world"
    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi"), true),
                               "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200) << res->body;
    EXPECT_NE(res->get_header_value("Content-Type").find("text/event-stream"), std::string::npos);

    bool done = false;
    auto chunks = parseSse(res->body, done);
    EXPECT_TRUE(done);
    ASSERT_GE(chunks.size(), 3u);
    EXPECT_EQ(chunks.front()["choices"][0]["delta"]["role"], "assistant");
    EXPECT_EQ(checkChunks(chunks), "Hello world");

    // Tokens arrive as separate deltas, not one blob after the fact.
    int contentChunks = 0;
    for (const auto& c : chunks) {
        if (!c["choices"].empty() && c["choices"][0]["delta"].contains("content") &&
            !c["choices"][0]["delta"]["content"].get<std::string>().empty())
            ++contentChunks;
    }
    EXPECT_GE(contentChunks, 2);
    EXPECT_EQ(chunks.back()["choices"][0]["finish_reason"], "stop");
}

TEST(ApiServerTest, StreamingWithoutAgentStreamingSendsAnswerAndUsage) {
    Harness h;
    h.mock.pushResponse(kAnswerWithUsage);
    auto res = h.client().Post(
        "/v1/chat/completions",
        chatBody(userOnly("hi"), true, {{"stream_options", {{"include_usage", true}}}}),
        "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200) << res->body;
    bool done = false;
    auto chunks = parseSse(res->body, done);
    EXPECT_TRUE(done);
    // A planning-JSON reply is never leaked as content; only the answer is.
    EXPECT_EQ(checkChunks(chunks), "done");
    const json& last = chunks.back();
    EXPECT_TRUE(last["choices"].empty());
    EXPECT_EQ(last["usage"]["total_tokens"], 18);

    bool sawStep = false;
    for (const auto& c : chunks) sawStep |= c.contains("gaia") && c["gaia"]["type"] == "step";
    EXPECT_TRUE(sawStep);
}

TEST(ApiServerTest, StreamingReportsToolActivity) {
    Harness h;
    h.mock.pushResponse(bench::kToolCall);  // calls echo
    h.mock.pushResponse(kAnswerWithUsage);
    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("echo it"), true),
                               "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200);
    bool done = false;
    auto chunks = parseSse(res->body, done);
    EXPECT_TRUE(done);
    EXPECT_EQ(checkChunks(chunks), "done");
    bool start = false, end = false;
    for (const auto& c : chunks) {
        if (!c.contains("gaia")) continue;
        start |= c["gaia"]["type"] == "tool_start" && c["gaia"]["tool"] == "echo";
        end |= c["gaia"]["type"] == "tool_end" && c["gaia"]["tool"] == "echo";
    }
    EXPECT_TRUE(start);
    EXPECT_TRUE(end);
    EXPECT_EQ(h.agent->echoCalls.load(), 1);
}

TEST(ApiServerTest, ClientDisconnectCancelsTheRun) {
    Harness h;
    std::promise<void> release;
    h.mock.holdNextResponse(release.get_future().share());

    auto cli = h.client();
    httplib::Request req;
    req.method = "POST";
    req.path = "/v1/chat/completions";
    req.body = chatBody(userOnly("long task"), true);
    req.set_header("Content-Type", "application/json");
    req.content_receiver = [](const char*, size_t, uint64_t, uint64_t) {
        return false;  // hang up after the first bytes
    };
    auto aborted = std::async(std::launch::async, [&] { return cli.send(req); });

    EXPECT_TRUE(waitFor([&] { return h.agent->isCancelled(); }));
    release.set_value();
    aborted.get();
    // The server frees its slot once the cancelled run unwinds.
    EXPECT_TRUE(waitFor([&] {
        auto r = h.client().Get("/health");
        return r && !json::parse(r->body)["busy"].get<bool>();
    }));
}

TEST(ApiServerTest, StopEndsRun) {
    auto h = std::make_unique<Harness>();
    EXPECT_TRUE(h->server->isRunning());
    std::thread([&] { h->server->stop(); }).join();
    EXPECT_TRUE(waitFor([&] { return !h->server->isRunning(); }));
    EXPECT_TRUE(h->runError.empty()) << h->runError;
}

TEST(ApiServerTest, HostThatOnlyStartsWith127IsRejected) {
    Harness h;
    auto res = h.client().Get("/v1/models", {{"Host", "127.attacker.example:8200"}});
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 403) << "DNS-rebinding names must not pass as loopback";
    auto ok = h.client().Get("/v1/models", {{"Host", "127.0.0.1:8200"}});
    ASSERT_TRUE(ok);
    EXPECT_EQ(ok->status, 200);
}

TEST(ApiServerTest, FailedRunIsAnErrorNotACompletion) {
    Harness h;
    for (int i = 0; i < 4; ++i) h.mock.pushResponse(R"({"error":{"message":"bad model"}})", 400);
    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi")),
                               "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 500) << res->body;
    json j = json::parse(res->body);
    EXPECT_EQ(j["error"]["code"], "error");
    EXPECT_EQ(j["gaia"]["status"], "error");

    for (int i = 0; i < 4; ++i) h.mock.pushResponse(R"({"error":{"message":"bad model"}})", 400);
    auto st = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi"), true),
                              "application/json");
    ASSERT_TRUE(st);
    EXPECT_NE(st->body.find("\"agent_error\""), std::string::npos) << st->body;
    EXPECT_EQ(st->body.find("\"finish_reason\":\"stop\""), std::string::npos)
        << "a failed stream must not end like a completed one";
}

TEST(ApiServerTest, InvalidUtf8ToolOutputDoesNotAbortTheRun) {
    Harness h;
    h.mock.pushResponse(R"({"choices":[{"message":{"content":"{\"tool\":\"binary\",\"tool_args\":{}}"}}]})");
    h.mock.pushResponse(kAnswerWithUsage);
    auto res = h.client().Post("/v1/chat/completions", chatBody(userOnly("hi"), true),
                               "application/json");
    ASSERT_TRUE(res);
    ASSERT_EQ(res->status, 200) << res->body;
    bool done = false;
    auto chunks = parseSse(res->body, done);
    EXPECT_TRUE(done);
    EXPECT_EQ(checkChunks(chunks), "done");
}
