// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Agent-loop behaviour that keeps a turn alive: endpoint auth, transient
// retries, context-overflow recovery, clipped-but-readable tool output,
// reasoning stripping, step-budget notices and the closing answer.

#include <gtest/gtest.h>

#include <map>

#include <gaia/agent.h>
#include <gaia/tool_registry.h>
#include <gaia/web_tools.h>

#include <string>
#include <vector>

#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

constexpr const char* kModel = "Gemma-4-E4B-it-GGUF";  // native tool calling

class ResilienceAgent : public Agent {
public:
    using Agent::Agent;
    std::vector<std::string> executed;
    std::string bigOutput = std::string(50000, 'x');

    void initForTest() { init(); }

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "add", "Add two numbers",
            [this](const json& args) -> json {
                executed.push_back("add");
                return json{{"sum", args.value("a", 0) + args.value("b", 0)}};
            },
            {{"a", ToolParamType::INTEGER, true, "First"},
             {"b", ToolParamType::INTEGER, false, "Second"}});
        toolRegistry().registerTool(
            "big", "Return a large output",
            [this](const json&) -> json {
                executed.push_back("big");
                return json{{"text", "HEAD" + bigOutput + "TAIL"}};
            },
            {});
        toolRegistry().registerTool(
            "soft_fail", "Fail with ok:false",
            [this](const json&) -> json {
                executed.push_back("soft_fail");
                return json{{"ok", false}, {"error", "disk full"}};
            },
            {});
    }
    std::string getSystemPrompt() const override { return "test"; }
};

AgentConfig cfgFor(const bench::MockLlmServer& mock) {
    AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.modelId = kModel;
    cfg.maxSteps = 10;
    cfg.silentMode = true;
    cfg.streaming = false;
    cfg.llmRetries = 0;
    return cfg;
}

std::string call(const std::string& id, const std::string& name, const std::string& argsJson) {
    json args = argsJson;  // arguments travel as a JSON-encoded string
    return json{{"choices",
                 json::array({{{"message",
                                {{"role", "assistant"},
                                 {"content", nullptr},
                                 {"tool_calls",
                                  json::array({{{"id", id},
                                                {"type", "function"},
                                                {"function", {{"name", name}, {"arguments", args}}}}})}}}}})}}
        .dump();
}

std::string answer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})}}
        .dump();
}

}  // namespace

TEST(AgentResilience, ApiKeyIsSentAsBearer) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("hi"));
    AgentConfig cfg = cfgFor(mock);
    cfg.apiKey = "sk-test-123";
    ResilienceAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("hello");
    ASSERT_FALSE(mock.receivedAuthorization().empty());
    EXPECT_EQ(mock.receivedAuthorization()[0], "Bearer sk-test-123");
}

TEST(AgentResilience, NoKeySendsNoAuthorization) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("hi"));
    AgentConfig cfg = cfgFor(mock);
    cfg.apiKeyEnv = "";
    ResilienceAgent agent(cfg);
    agent.initForTest();
    if (!getEnvVar("GAIA_API_KEY").empty() || !getEnvVar("LEMONADE_API_KEY").empty()) {
        GTEST_SKIP() << "a key in the environment would be sent";
    }
    agent.processQuery("hello");
    EXPECT_EQ(mock.receivedAuthorization()[0], "");
}

TEST(AgentResilience, TransientFailureIsRetried) {
    bench::MockLlmServer mock;
    mock.pushResponse(R"({"error":"overloaded"})", 503);
    mock.pushResponse(answer("recovered"));
    AgentConfig cfg = cfgFor(mock);
    cfg.llmRetries = 1;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["result"], "recovered");
    EXPECT_EQ(r["status"], "success");
    EXPECT_EQ(mock.requestCount(), 2);
}

TEST(AgentResilience, AnUnreadable200IsRetriedLikeAServerError) {
    // A gateway's HTML page, an empty body and a reply with no choices, each with HTTP 200.
    for (const char* garbage : {"<html><body>502 from the proxy</body></html>", "", R"({"id":"x","choices":[]})"}) {
        bench::MockLlmServer mock;
        mock.pushResponse(garbage);
        mock.pushResponse(answer("recovered"));
        AgentConfig cfg = cfgFor(mock);
        cfg.llmRetries = 1;
        ResilienceAgent agent(cfg);
        agent.initForTest();
        json r = agent.processQuery("go");
        EXPECT_EQ(r["result"], "recovered") << garbage;
        EXPECT_EQ(r["status"], "success") << garbage;
        EXPECT_EQ(mock.requestCount(), 2) << garbage;
    }
}

TEST(AgentResilience, AnUnreadable200StillFailsTheTurnOnceRetriesRunOut) {
    bench::MockLlmServer mock;
    mock.pushResponse("<html>still down</html>");
    ResilienceAgent agent(cfgFor(mock));  // llmRetries = 0
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["status"], "error");
    EXPECT_NE(r["result"].get<std::string>().find("Failed to parse LLM response"), std::string::npos) << r.dump();
    EXPECT_EQ(mock.requestCount(), 1);
}

TEST(AgentResilience, AToolCallSentWithoutAnIdStillRuns) {
    bench::MockLlmServer mock;
    const json noId = {
        {"choices",
         json::array({{{"message",
                        {{"role", "assistant"},
                         {"content", nullptr},
                         {"tool_calls",
                          json::array({{{"type", "function"},
                                        {"function", {{"name", "add"}, {"arguments", "{\"a\": 2, \"b\": 3}"}}}}})}}}}})}};
    mock.pushResponse(noId.dump());
    mock.pushResponse(answer("done"));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["result"], "done");

    // The id the agent assigned ties the echoed call to its result.
    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 2u);
    const json messages = json::parse(bodies[1])["messages"];
    std::string callId, replyId;
    for (const auto& m : messages) {
        if (m.value("role", "") == "assistant" && m.contains("tool_calls")) callId = m["tool_calls"][0].value("id", "");
        if (m.value("role", "") == "tool") replyId = m.value("tool_call_id", "");
    }
    EXPECT_FALSE(callId.empty());
    EXPECT_EQ(callId, replyId);
}

TEST(AgentResilience, ClientErrorIsNotRetried) {
    bench::MockLlmServer mock;
    mock.pushResponse(R"({"error":{"message":"bad model"}})", 400);
    AgentConfig cfg = cfgFor(mock);
    cfg.llmRetries = 3;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["status"], "error");
    EXPECT_EQ(mock.requestCount(), 1);
}

TEST(AgentResilience, LargeOutputIsClippedAndReadable) {
    bench::MockLlmServer mock;
    mock.pushResponse(call("c1", "big", "{}"));
    mock.pushResponse(call("c2", "read_tool_output", R"({"output_id":1,"offset":0,"length":20})"));
    mock.pushResponse(answer("done"));
    AgentConfig cfg = cfgFor(mock);
    cfg.maxToolResultChars = 2000;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["result"], "done");

    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 3u);
    const json second = json::parse(bodies[1]);
    const std::string clipped = second["messages"].back()["content"];
    EXPECT_LT(clipped.size(), 2600u);
    EXPECT_NE(clipped.find("read_tool_output"), std::string::npos);
    EXPECT_NE(clipped.find("TAIL"), std::string::npos) << "the tail must survive the clip";
    bool offered = false;
    for (const auto& t : second["tools"]) offered |= t["function"]["name"] == "read_tool_output";
    EXPECT_TRUE(offered);

    const json third = json::parse(bodies[2]);
    const json readBack = json::parse(third["messages"].back()["content"].get<std::string>());
    EXPECT_EQ(readBack["status"], "success");
    EXPECT_EQ(readBack["text"].get<std::string>().size(), 20u);
    EXPECT_GT(readBack["total"].get<int>(), 50000);
}

TEST(AgentResilience, ContextOverflowElidesOldOutputAndRetries) {
    bench::MockLlmServer mock;
    mock.pushResponse(call("c1", "big", "{}"));
    mock.pushResponse(call("c2", "big", "{}"));
    mock.pushResponse(call("c3", "big", "{}"));
    mock.pushResponse(R"({"error":{"message":"request exceeds the available context size"}})", 400);
    mock.pushResponse(answer("fits now"));
    AgentConfig cfg = cfgFor(mock);
    cfg.maxToolResultChars = 3000;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["result"], "fits now");
    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 5u);
    const json retry = json::parse(bodies[4]);
    int stubs = 0;
    for (const auto& m : retry["messages"]) {
        if (m["role"] == "tool" &&
            m["content"].get<std::string>().find("removed to fit the context window") !=
                std::string::npos) {
            ++stubs;
        }
    }
    EXPECT_EQ(stubs, 1) << "all but the two newest outputs are elided";
}

TEST(AgentResilience, ReasoningIsStrippedFromTheAnswer) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("<think>let me see</think>\n\nThe answer is 4."));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    EXPECT_EQ(agent.processQuery("2+2")["result"], "The answer is 4.");
}

TEST(AgentResilience, StripReasoningHandlesStrayAndUnclosedTags) {
    EXPECT_EQ(stripReasoning("plan</think>answer"), "answer");
    EXPECT_EQ(stripReasoning("a<think>b</think>c<think>d</think>e"), "ace");
    EXPECT_EQ(stripReasoning("answer<think>never closed"), "answer");
    EXPECT_EQ(stripReasoning("no tags"), "no tags");
}

namespace {

// One distinct tool call per step until the limit, then a closing answer.
std::vector<std::string> runToTheLimit(int limit, json* result = nullptr) {
    bench::MockLlmServer mock;
    for (int i = 0; i < limit; ++i) {
        mock.pushResponse(call("c" + std::to_string(i), "add", R"({"a":)" + std::to_string(i) + R"(,"b":1})"));
    }
    mock.pushResponse(answer("Did what fit; the rest is unfinished."));
    AgentConfig cfg = cfgFor(mock);
    cfg.maxSteps = limit;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");
    if (result) *result = r;
    return mock.receivedBodies();
}

// The notice each request ends with, by steps left when it was sent ("" = none).
std::map<int, std::string> noticesBySteps(const std::vector<std::string>& bodies, int limit) {
    std::map<int, std::string> out;
    for (int i = 0; i < limit; ++i) {
        const json last = json::parse(bodies[static_cast<size_t>(i)])["messages"].back();
        const std::string text = last["content"].is_string() ? last["content"].get<std::string>() : "";
        if (last.value("role", "") == "user" && text.rfind("Step budget:", 0) == 0) out[limit - i] = text;
    }
    return out;
}

}  // namespace

TEST(AgentResilience, StepBudgetNoticesAndClosingAnswer) {
    json r;
    const auto bodies = runToTheLimit(10, &r);

    EXPECT_EQ(r["result"], "Did what fit; the rest is unfinished.");
    EXPECT_EQ(r["status"], "error") << "running out of steps is not success";
    ASSERT_EQ(bodies.size(), 11u);
    const auto notices = noticesBySteps(bodies, 10);
    ASSERT_EQ(notices.size(), 3u);
    EXPECT_EQ(notices.at(5).rfind("Step budget: 5 of 10 used, 5 left. Stop gathering context.", 0), 0u)
        << notices.at(5);
    EXPECT_EQ(notices.at(2).rfind("Step budget: 2 of 10 steps left. Write the requested deliverables now", 0), 0u)
        << notices.at(2);
    EXPECT_EQ(notices.at(1).rfind("Step budget: 1 of 10 steps left. Do not research further.", 0), 0u)
        << notices.at(1);
    const json closing = json::parse(bodies.back());
    EXPECT_EQ(closing["tool_choice"], "none") << "tools stay declared, but none may be called";
    EXPECT_NE(closing["messages"].back()["content"].get<std::string>().find("[Step limit reached]"),
              std::string::npos);
}

TEST(AgentResilience, ALargeBudgetIsToldAtTwentyTenAndFiveStepsLeft) {
    const auto notices = noticesBySteps(runToTheLimit(200), 200);
    ASSERT_EQ(notices.size(), 3u);
    EXPECT_EQ(notices.count(100), 0u) << "half of a large budget is not 'few steps left'";
    EXPECT_EQ(notices.at(20).rfind("Step budget: 180 of 200 used, 20 left.", 0), 0u) << notices.at(20);
    EXPECT_EQ(notices.at(10).rfind("Step budget: 10 of 200 steps left.", 0), 0u) << notices.at(10);
    EXPECT_EQ(notices.at(5).rfind("Step budget: 5 of 200 steps left.", 0), 0u) << notices.at(5);
}

TEST(AgentResilience, EachBudgetLevelFiresOnceAtTheAbsoluteFigure) {
    const auto notices = noticesBySteps(runToTheLimit(60), 60);
    std::vector<int> at;
    for (const auto& [left, text] : notices) at.push_back(left);
    EXPECT_EQ(at, (std::vector<int>{5, 10, 20}));
}

TEST(AgentResilience, ABudgetTooSmallToActOnGetsNoNotice) {
    EXPECT_TRUE(noticesBySteps(runToTheLimit(7), 7).empty());
    EXPECT_EQ(noticesBySteps(runToTheLimit(8), 8).size(), 3u);
}

TEST(AgentResilience, BudgetNoticesNeverReachALaterTurn) {
    bench::MockLlmServer mock;
    for (int i = 0; i < 8; ++i) {
        mock.pushResponse(call("c" + std::to_string(i), "add", R"({"a":)" + std::to_string(i) + R"(,"b":1})"));
    }
    mock.pushResponse(answer("ok"));
    mock.pushResponse(answer("second answer"));
    AgentConfig cfg = cfgFor(mock);
    cfg.maxSteps = 8;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("first");
    EXPECT_NE(mock.receivedBodies()[7].find("Step budget:"), std::string::npos);
    for (const auto& m : agent.history()) EXPECT_NE(m.content.rfind("Step budget:", 0), 0u) << m.content;

    agent.processQuery("second");
    EXPECT_EQ(mock.receivedBodies().back().find("Step budget:"), std::string::npos)
        << "a stale '1 step left' must not open the next turn";
}

TEST(AgentResilience, BudgetNoticesCanBeTurnedOff) {
    bench::MockLlmServer mock;
    for (int i = 0; i < 10; ++i) {
        mock.pushResponse(call("c" + std::to_string(i), "add", R"({"a":)" + std::to_string(i) + R"(,"b":1})"));
    }
    AgentConfig cfg = cfgFor(mock);
    cfg.stepBudgetNotices = false;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("go");
    for (const auto& body : mock.receivedBodies()) EXPECT_EQ(body.find("Step budget:"), std::string::npos);
}

TEST(AgentResilience, StringNumbersAreCoerced) {
    bench::MockLlmServer mock;
    mock.pushResponse(call("c1", "add", R"({"a":"5","b":"7"})"));
    mock.pushResponse(answer("12"));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    agent.processQuery("go");
    ASSERT_EQ(agent.executed.size(), 1u);
    const json second = json::parse(mock.receivedBodies()[1]);
    EXPECT_NE(second["messages"].back()["content"].get<std::string>().find("\"sum\":12"),
              std::string::npos);
}

TEST(AgentResilience, OkFalseCountsAsAToolError) {
    bench::MockLlmServer mock;
    mock.pushResponse(call("c1", "soft_fail", "{}"));
    mock.pushResponse(answer("could not"));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["tool_errors"], 1);
}

TEST(AgentResilience, ClippingNeverSplitsUtf8) {
    bench::MockLlmServer mock;
    mock.pushResponse(call("c1", "big", "{}"));
    mock.pushResponse(answer("ok"));
    AgentConfig cfg = cfgFor(mock);
    cfg.maxToolResultChars = 1001;
    ResilienceAgent agent(cfg);
    std::string euros;
    for (int i = 0; i < 20000; ++i) euros += "\xE2\x82\xAC";  // three-byte characters
    agent.bigOutput = euros;
    agent.initForTest();
    json r;
    ASSERT_NO_THROW(r = agent.processQuery("go"));
    EXPECT_EQ(r["result"], "ok") << "the clipped request must still serialize";
}

TEST(WebToolsTest, HtmlToTextKeepsTextDropsScripts) {
    const std::string html =
        "<html><head><title>T</title><style>p{}</style></head><body>"
        "<script>alert(1)</script><h1>Title</h1><p>One &amp; two</p>"
        "<table><tr><td>a</td><td>b</td></tr></table></body></html>";
    const std::string text = WebTools::htmlToText(html);
    EXPECT_NE(text.find("Title"), std::string::npos);
    EXPECT_NE(text.find("One & two"), std::string::npos);
    EXPECT_EQ(text.find("alert"), std::string::npos);
    EXPECT_EQ(text.find("p{}"), std::string::npos);
}

TEST(WebToolsTest, RejectsNonHttpUrls) {
    ToolRegistry reg;
    WebTools::registerAll(reg);
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    json r = reg.executeTool("web_fetch", json{{"url", "file:///etc/passwd"}});
    EXPECT_EQ(r["status"], "error");
}

TEST(ToolRegistryCoercion, OnlyWholeValuesAreCoerced) {
    std::vector<ToolParameter> params = {{"n", ToolParamType::INTEGER, true, ""},
                                         {"f", ToolParamType::NUMBER, true, ""},
                                         {"b", ToolParamType::BOOLEAN, true, ""}};
    json out = ToolRegistry::coerceArgsToSchema(params, json{{"n", "12"}, {"f", "2.5"}, {"b", "TRUE"}});
    EXPECT_EQ(out["n"], 12);
    EXPECT_DOUBLE_EQ(out["f"].get<double>(), 2.5);
    EXPECT_EQ(out["b"], true);
    json bad = ToolRegistry::coerceArgsToSchema(params, json{{"n", "12abc"}, {"f", "x"}, {"b", "yes"}});
    EXPECT_EQ(bad["n"], "12abc");
    EXPECT_EQ(bad["f"], "x");
    EXPECT_EQ(bad["b"], "yes");
}

TEST(WebToolsTest, RefusesLocalAndPrivateAddresses) {
    ToolRegistry reg;
    WebTools::registerAll(reg);
    reg.setConfirmCallback([](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    for (const char* url : {"http://127.0.0.1:13305/api/v1/models", "http://localhost/",
                            "http://169.254.169.254/latest/meta-data/", "http://10.1.2.3/",
                            "http://[::1]:8080/"}) {
        json r = reg.executeTool("web_fetch", json{{"url", url}});
        EXPECT_EQ(r["status"], "error") << url;
        EXPECT_NE(r["error"].get<std::string>().find("Refused"), std::string::npos) << url << r.dump();
    }
}

TEST(AgentResilience, EmptyReplyIsRetriedOnce) {
    bench::MockLlmServer mock;
    mock.pushResponse(answer("<think>long reasoning cut off by max_tokens"));
    mock.pushResponse(answer("Here it is."));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["result"], "Here it is.");
    EXPECT_EQ(r["status"], "success");
}

TEST(AgentResilience, MalformedArgumentsAreNotReplayed) {
    bench::MockLlmServer mock;
    mock.pushResponse(call("c1", "add", "{\"a\":"));
    mock.pushResponse(answer("ok"));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    agent.processQuery("go");
    const json second = json::parse(mock.receivedBodies()[1]);
    for (const auto& m : second["messages"]) {
        if (m.contains("tool_calls")) {
            EXPECT_EQ(m["tool_calls"][0]["function"]["arguments"], "{}");
        }
    }
}

namespace {
std::string withUsage(const std::string& body, int prompt, int cached, int completion) {
    json j = json::parse(body);
    j["usage"] = {{"prompt_tokens", prompt},
                  {"completion_tokens", completion},
                  {"total_tokens", prompt + completion},
                  {"prompt_tokens_details", {{"cached_tokens", cached}}}};
    return j.dump();
}
}  // namespace

namespace {
// One SSE event per JSON object, then the terminator.
std::string sse(std::initializer_list<json> events) {
    std::string out;
    for (const auto& event : events) out += "data: " + event.dump() + "\n\n";
    return out + "data: [DONE]\n\n";
}

json delta(const json& fields, const json& finish = nullptr) {
    return {{"choices", json::array({{{"index", 0}, {"delta", fields}, {"finish_reason", finish}}})}};
}
}  // namespace

TEST(AgentStreaming, AStreamedAnswerReportsItsTokenUsage) {
    bench::MockLlmServer mock;
    mock.enableSseStreaming(true);
    mock.pushSseStream(sse({delta({{"content", "Hel"}}), delta({{"content", "lo"}}, "stop"),
                            {{"choices", json::array()},
                             {"usage", {{"prompt_tokens", 900}, {"completion_tokens", 7}, {"total_tokens", 907}}}}}));
    AgentConfig cfg = cfgFor(mock);
    cfg.streaming = true;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");

    EXPECT_EQ(r["result"], "Hello");
    EXPECT_EQ(r["usage"]["prompt_tokens"], 900);
    EXPECT_EQ(r["usage"]["completion_tokens"], 7);
    // The server only sends usage on a stream when the request asks for it.
    const json sent = json::parse(mock.receivedBodies().at(0));
    EXPECT_EQ(sent["stream"], true);
    EXPECT_EQ(sent["stream_options"]["include_usage"], true);
}

TEST(AgentStreaming, AReplyCutOffInItsReasoningSaysSoAndIsNotRetried) {
    bench::MockLlmServer mock;
    mock.enableSseStreaming(true);
    mock.pushSseStream(sse({delta({{"reasoning_content", "Let me think about this at length"}}),
                            delta({{"reasoning_content", " and then some"}}, "length")}));
    AgentConfig cfg = cfgFor(mock);
    cfg.streaming = true;
    cfg.llmRetries = 3;
    cfg.maxTokens = 4096;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");

    EXPECT_EQ(r["status"], "error");
    const std::string why = r["result"].get<std::string>();
    EXPECT_NE(why.find("reply limit of 4096 tokens"), std::string::npos) << why;
    EXPECT_NE(why.find("reasoning used the whole limit"), std::string::npos) << why;
    EXPECT_NE(why.find("--max-tokens"), std::string::npos) << why;
    EXPECT_EQ(mock.requestCount(), 1) << "sending the same request again would hit the same limit";
}

TEST(AgentStreaming, AStreamWithReasoningButNoAnswerIsRetried) {
    bench::MockLlmServer mock;
    mock.enableSseStreaming(true);
    mock.pushSseStream(sse({delta({{"reasoning_content", "thinking"}}, "stop")}));
    mock.pushSseStream(sse({delta({{"content", "Here it is."}}, "stop")}));
    AgentConfig cfg = cfgFor(mock);
    cfg.streaming = true;
    cfg.llmRetries = 1;
    ResilienceAgent agent(cfg);
    agent.initForTest();
    json r = agent.processQuery("go");

    EXPECT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["result"], "Here it is.");
    EXPECT_EQ(mock.requestCount(), 2);
}

TEST(AgentTelemetry, ResultCountsCallsCachedTokensAndPeakContext) {
    bench::MockLlmServer mock;
    mock.pushResponse(withUsage(call("c1", "add", R"({"a":5,"b":7})"), 1000, 0, 40));
    mock.pushResponse(withUsage(call("c2", "soft_fail", "{}"), 1100, 900, 20));
    mock.pushResponse(withUsage(answer("done"), 1200, 1000, 60));
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    json r = agent.processQuery("go");

    EXPECT_EQ(r["llm_calls"], 3);
    EXPECT_EQ(r["tool_calls"], 2);
    EXPECT_EQ(r["tool_errors"], 1);
    EXPECT_EQ(r["usage"]["prompt_tokens"], 3300);
    EXPECT_EQ(r["usage"]["cached_tokens"], 1900);
    EXPECT_EQ(r["usage"]["completion_tokens"], 120);
    EXPECT_EQ(r["usage"]["peak_context_tokens"], 1260) << "largest single call, not the sum";

    const json& llm = r["telemetry"]["llm_calls"];
    ASSERT_EQ(llm.size(), 3u);
    EXPECT_EQ(llm[1]["step"], 2);
    EXPECT_EQ(llm[1]["input_tokens"], 1100);
    EXPECT_EQ(llm[1]["cached_tokens"], 900);
    EXPECT_EQ(llm[1]["output_tokens"], 20);
    EXPECT_GE(llm[1]["wall_s"].get<double>(), 0.0);

    const json& tools = r["telemetry"]["tool_calls"];
    ASSERT_EQ(tools.size(), 2u);
    EXPECT_EQ(tools[0]["name"], "add");
    EXPECT_EQ(tools[0]["step"], 1);
    EXPECT_EQ(tools[0]["ok"], true);
    EXPECT_TRUE(json::parse(tools[0]["args"].get<std::string>()).is_object());
    EXPECT_EQ(tools[1]["name"], "soft_fail");
    EXPECT_EQ(tools[1]["step"], 2);
    EXPECT_EQ(tools[1]["ok"], false);
}

TEST(AgentTelemetry, UsageWithoutCacheDetailsReportsZeroCached) {
    bench::MockLlmServer mock;
    json body = json::parse(answer("hi"));
    body["usage"] = {{"prompt_tokens", 50}, {"completion_tokens", 5}, {"total_tokens", 55}};
    mock.pushResponse(body.dump());
    ResilienceAgent agent(cfgFor(mock));
    agent.initForTest();
    json r = agent.processQuery("go");
    EXPECT_EQ(r["usage"]["cached_tokens"], 0);
    EXPECT_EQ(r["usage"]["peak_context_tokens"], 55);
    EXPECT_EQ(r["tool_calls"], 0);
    EXPECT_TRUE(r["telemetry"]["tool_calls"].empty());
}
