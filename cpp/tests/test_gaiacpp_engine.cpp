// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// `gaiacpp --engine`: the loop with a host's prompt, tools, history and limits.
// Every tool call id is `<turn>:<n>`, so a test can queue the host's answers
// before the engine asks.

#include <gtest/gtest.h>

#include <chrono>
#include <fstream>
#include <future>
#include <map>
#include <random>
#include <set>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "../agents/gaiacpp/host_server.h"
#include "gaia/types.h"
#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

std::string reply(const json& message, int outputTokens = 7) {
    return json{{"choices", json::array({{{"message", message}}})},
                {"usage",
                 {{"prompt_tokens", 100},
                  {"completion_tokens", outputTokens},
                  {"total_tokens", 100 + outputTokens}}}}
        .dump();
}

std::string answer(const std::string& text, int outputTokens = 7) {
    return reply({{"role", "assistant"}, {"content", text}}, outputTokens);
}

std::string toolCall(const std::string& name, const json& args, int outputTokens = 7) {
    return reply({{"role", "assistant"},
                  {"content", nullptr},
                  {"tool_calls",
                   json::array({{{"id", "c1"},
                                 {"type", "function"},
                                 {"function", {{"name", name}, {"arguments", args.dump()}}}}})}},
                 outputTokens);
}

const json kGetOrder = {
    {"name", "get_order"},
    {"description", "Look up an order by its number."},
    {"parameters",
     {{"type", "object"},
      {"properties", {{"order_number", {{"type", "string"}, {"description", "The order number."}}}}},
      {"required", json::array({"order_number"})}}}};

class GaiacppEngine : public ::testing::Test {
protected:
    json init() const {
        return {{"type", "init"},
                {"protocol", 1},
                {"model", "host-model"},
                {"base_url", mock_.baseUrl() + "/api/v1"},
                {"system_prompt", "You answer questions about orders."},
                {"tools", json::array({kGetOrder})},
                {"max_steps", 8}};
    }

    static json turn(const std::string& id, const std::string& text) {
        return {{"type", "turn"},
                {"id", id},
                {"messages", json::array({{{"role", "user"}, {"content", text}}})}};
    }

    json gatedInit() const {
        json message = init();
        message["gate_model_calls"] = true;
        return message;
    }

    static json go(const std::string& id) { return {{"type", "model_go"}, {"id", id}}; }

    /// Run the engine over `lines`, and return what it wrote.
    std::vector<json> run(const std::vector<json>& lines) {
        std::string input;
        for (const auto& line : lines) input += line.dump() + "\n";
        std::istringstream in(input);
        std::ostringstream out;
        exitCode_ = runHostServer(in, out, HostOptions{"test"});
        std::vector<json> events;
        std::istringstream written(out.str());
        std::string line;
        while (std::getline(written, line)) {
            if (!line.empty()) events.push_back(json::parse(line));
        }
        return events;
    }

    static std::vector<json> ofType(const std::vector<json>& events, const std::string& type) {
        std::vector<json> found;
        for (const auto& e : events) {
            if (e.value("type", "") == type) found.push_back(e);
        }
        return found;
    }

    json requestBody(size_t n) const { return json::parse(mock_.receivedBodies().at(n)); }

    bench::MockLlmServer mock_;
    int exitCode_ = -1;
};

TEST_F(GaiacppEngine, InitIsAnsweredWithReady) {
    const auto events = run({init()});

    ASSERT_EQ(events.size(), 1u);
    EXPECT_EQ(events[0]["type"], "ready");
    EXPECT_EQ(events[0]["protocol"], 1);
    EXPECT_EQ(events[0]["version"], "test");
    EXPECT_EQ(exitCode_, 0);
}

TEST_F(GaiacppEngine, AnAnswerIsOneModelCallAndOneFinal) {
    mock_.pushResponse(answer("Hello there."));

    const auto events = run({init(), turn("t1", "hi")});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["turn"], "t1");
    EXPECT_EQ(finals[0]["status"], "success");
    EXPECT_EQ(finals[0]["stop_reason"], "answered");
    EXPECT_EQ(finals[0]["text"], "Hello there.");
    EXPECT_TRUE(finals[0]["failure"].is_null());
    EXPECT_EQ(finals[0]["usage"]["model_calls"], 1);
    EXPECT_EQ(finals[0]["usage"]["input_tokens"], 100);
    EXPECT_EQ(finals[0]["usage"]["output_tokens"], 7);
    EXPECT_EQ(ofType(events, "model_call").size(), 1u);
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, TheModelGetsTheHostsPromptAndOnlyTheHostsTools) {
    mock_.pushResponse(answer("Hello there."));

    run({init(), turn("t1", "hi")});

    const json body = requestBody(0);
    EXPECT_EQ(body["model"], "host-model");
    ASSERT_EQ(body["messages"][0]["role"], "system");
    // As written: no tool list, no response-format template, only trailing blank lines.
    EXPECT_EQ(body["messages"][0]["content"], "You answer questions about orders.\n\n");
    EXPECT_EQ(body["messages"].back()["content"], "hi");
    std::vector<std::string> names;
    for (const auto& tool : body["tools"]) names.push_back(tool["function"]["name"]);
    ASSERT_FALSE(names.empty());
    EXPECT_EQ(names[0], "get_order");
    for (const auto& name : names) {
        EXPECT_TRUE(name == "get_order" || name == "read_tool_output") << "unexpected tool " << name;
    }
}

TEST_F(GaiacppEngine, AToolCallGoesToTheHostAndItsResultToTheModel) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("It has shipped."));
    const json result = {{"type", "tool_result"},
                         {"id", "t1:1"},
                         {"result", {{"status", "shipped"}, {"carrier", "UPS"}}}};

    const auto events = run({init(), turn("t1", "Where is order 1001?"), result});

    const auto requests = ofType(events, "tool_request");
    ASSERT_EQ(requests.size(), 1u);
    EXPECT_EQ(requests[0]["id"], "t1:1");
    EXPECT_EQ(requests[0]["name"], "get_order");
    EXPECT_EQ(requests[0]["arguments"], (json{{"order_number", "1001"}}));
    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["status"], "success");
    EXPECT_EQ(finals[0]["text"], "It has shipped.");
    ASSERT_EQ(finals[0]["tool_calls"].size(), 1u);
    EXPECT_EQ(finals[0]["tool_calls"][0]["name"], "get_order");
    EXPECT_EQ(finals[0]["tool_calls"][0]["result"]["carrier"], "UPS");
    EXPECT_EQ(finals[0]["tool_calls"][0]["failed"], false);
    EXPECT_EQ(finals[0]["usage"]["model_calls"], 2);
    // The second request carries what the host's tool returned.
    EXPECT_NE(mock_.receivedBodies().at(1).find("UPS"), std::string::npos);
}

TEST_F(GaiacppEngine, AToolErrorFromTheHostIsRecordedAsFailed) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("I could not look that up."));
    const json result = {{"type", "tool_result"}, {"id", "t1:1"}, {"error", "archive offline"}};

    const auto events = run({init(), turn("t1", "Where is order 1001?"), result});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["tool_calls"][0]["failed"], true);
    EXPECT_NE(mock_.receivedBodies().at(1).find("archive offline"), std::string::npos);
}

TEST_F(GaiacppEngine, EarlierMessagesOfTheConversationAreSent) {
    mock_.pushResponse(answer("It shipped on Monday."));
    json t = turn("t2", "and when?");
    t["messages"] = json::array({{{"role", "user"}, {"content", "Where is order 1001?"}},
                                 {{"role", "assistant"}, {"content", "It has shipped."}},
                                 {{"role", "user"}, {"content", "and when?"}}});

    run({init(), t});

    const json messages = requestBody(0)["messages"];
    ASSERT_EQ(messages.size(), 4u);
    EXPECT_EQ(messages[1]["content"], "Where is order 1001?");
    EXPECT_EQ(messages[2]["role"], "assistant");
    EXPECT_EQ(messages[3]["content"], "and when?");
}

TEST_F(GaiacppEngine, AModelCallLimitStopsBeforeTheNextRequestAndTheToolIsNotRun) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("never asked for"));
    json t = turn("t1", "Where is order 1001?");
    t["budget"] = {{"model_calls", 1}};

    const auto events = run({init(), t});

    EXPECT_TRUE(ofType(events, "tool_request").empty());
    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["status"], "incomplete");
    EXPECT_EQ(finals[0]["stop_reason"], "model_calls");
    EXPECT_EQ(finals[0]["text"], "");
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, AnOutputTokenLimitStopsTheTurn) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}, 60));
    mock_.pushResponse(answer("never asked for"));
    json t = turn("t1", "Where is order 1001?");
    t["budget"] = {{"output_tokens", 50}};

    const auto events = run({init(), t});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["stop_reason"], "output_tokens");
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, AnAnswerOnTheLastAllowedCallIsStillAnAnswer) {
    mock_.pushResponse(answer("Hello there."));
    json t = turn("t1", "hi");
    t["budget"] = {{"model_calls", 1}};

    const auto events = run({init(), t});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["status"], "success");
    EXPECT_EQ(finals[0]["text"], "Hello there.");
}

TEST_F(GaiacppEngine, AProviderFailureIsTypedAndNotRetried) {
    mock_.pushResponse(R"({"error":{"message":"upstream overloaded"}})", 503);

    const auto events = run({init(), turn("t1", "hi")});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["status"], "failed");
    EXPECT_EQ(finals[0]["stop_reason"], "model_call");
    EXPECT_EQ(finals[0]["failure"]["kind"], "model_call");
    EXPECT_FALSE(finals[0]["failure"]["message"].get<std::string>().empty());
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, ASecondTurnStartsItsCountsAgain) {
    mock_.pushResponse(answer("Hello."));
    mock_.pushResponse(answer("Hello again."));

    const auto events = run({init(), turn("t1", "hi"), turn("t2", "hi again")});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 2u);
    EXPECT_EQ(finals[1]["turn"], "t2");
    EXPECT_EQ(finals[1]["usage"]["model_calls"], 1);
    EXPECT_TRUE(finals[1]["tool_calls"].empty());
    // The host owns history: the second turn sent only its own message.
    EXPECT_EQ(requestBody(1)["messages"].size(), 2u);
}

TEST_F(GaiacppEngine, InitWithoutTheProtocolVersionIsRefused) {
    json bad = init();
    bad.erase("protocol");

    const auto events = run({bad, turn("t1", "hi")});

    ASSERT_EQ(events.size(), 1u);
    EXPECT_EQ(events[0]["type"], "error");
    EXPECT_EQ(events[0]["code"], "bad_init");
    EXPECT_EQ(exitCode_, 2);
    EXPECT_EQ(mock_.requestCount(), 0);
}

TEST_F(GaiacppEngine, AKeyOnTheWireIsRefused) {
    json bad = init();
    bad["api_key"] = "sk-not-a-real-key";

    const auto events = run({bad});

    ASSERT_EQ(events.size(), 1u);
    EXPECT_EQ(events[0]["code"], "bad_init");
    EXPECT_NE(events[0]["message"].get<std::string>().find("api_key_env"), std::string::npos);
    EXPECT_EQ(events[0].dump().find("sk-not-a-real-key"), std::string::npos);
}

TEST_F(GaiacppEngine, ABadTurnIsNamedAndTheNextOneStillRuns) {
    mock_.pushResponse(answer("Hello."));
    json bad = turn("t1", "hi");
    bad["messages"] = json::array({{{"role", "tool"}, {"content", "x"}}});

    const auto events = run({init(), bad, turn("t2", "hi")});

    const auto errors = ofType(events, "error");
    ASSERT_EQ(errors.size(), 1u);
    EXPECT_EQ(errors[0]["code"], "bad_turn");
    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["turn"], "t2");
    EXPECT_EQ(exitCode_, 0);
}

TEST_F(GaiacppEngine, ADuplicateToolNameIsRefused) {
    json bad = init();
    bad["tools"] = json::array({kGetOrder, kGetOrder});

    const auto events = run({bad});

    ASSERT_EQ(events.size(), 1u);
    EXPECT_EQ(events[0]["code"], "bad_init");
    EXPECT_NE(events[0]["message"].get<std::string>().find("declared twice"), std::string::npos);
}

TEST_F(GaiacppEngine, AHostThatLeavesMidToolCallEndsTheTurnWithNoFurtherModelCall) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("never asked for"));

    // Input ends right after the turn: the tool request is never answered.
    const auto events = run({init(), turn("t1", "Where is order 1001?")});

    EXPECT_EQ(ofType(events, "tool_request").size(), 1u);
    EXPECT_TRUE(ofType(events, "final").empty());
    EXPECT_TRUE(ofType(events, "error").empty());
    EXPECT_EQ(mock_.requestCount(), 1);
    EXPECT_EQ(exitCode_, 0);
}

TEST_F(GaiacppEngine, AShutdownInPlaceOfAToolResultIsHonoured) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("never asked for"));

    const auto events = run({init(), turn("t1", "Where is order 1001?"), {{"type", "shutdown"}}});

    EXPECT_EQ(ofType(events, "tool_request").size(), 1u);
    EXPECT_TRUE(ofType(events, "final").empty());
    EXPECT_EQ(mock_.requestCount(), 1);
    EXPECT_EQ(exitCode_, 0);
}

TEST_F(GaiacppEngine, AWrongMessageInPlaceOfAToolResultIsNamedAndTheTurnStops) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("Hello."));
    const json wrongId = {{"type", "tool_result"}, {"id", "t1:9"}, {"result", {{"status", "shipped"}}}};

    const auto events = run({init(), turn("t1", "Where is order 1001?"), wrongId, turn("t2", "hi")});

    EXPECT_EQ(ofType(events, "tool_request").size(), 1u);
    const auto errors = ofType(events, "error");
    ASSERT_EQ(errors.size(), 1u);
    EXPECT_EQ(errors[0]["code"], "bad_message");
    // The broken turn has no final; the engine is still serving the next one.
    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["turn"], "t2");
    EXPECT_EQ(finals[0]["text"], "Hello.");
    EXPECT_EQ(mock_.requestCount(), 2);
}

TEST_F(GaiacppEngine, WithNoHostToolsThePromptStillGoesOutAsWritten) {
    mock_.pushResponse(answer("Hello there."));
    json bare = init();
    bare.erase("tools");

    run({bare, turn("t1", "hi")});

    EXPECT_EQ(requestBody(0)["messages"][0]["content"], "You answer questions about orders.\n\n");
}

TEST_F(GaiacppEngine, RetriesAndStepCountsThatAreNotSmallWholeNumbersAreRefused) {
    for (const auto& [key, value] : std::vector<std::pair<std::string, json>>{
             {"retries", "3"}, {"retries", -1}, {"max_steps", 3000000000LL}, {"max_steps", 0}}) {
        json bad = init();
        bad[key] = value;

        const auto events = run({bad});

        ASSERT_EQ(events.size(), 1u) << key;
        EXPECT_EQ(events[0]["code"], "bad_init") << key << " = " << value.dump();
        EXPECT_EQ(exitCode_, 2);
    }
}

TEST_F(GaiacppEngine, AToolPastItsPerTurnLimitIsNotRunAndTheModelIsToldSo) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(toolCall("get_order", {{"order_number", "2002"}}));
    mock_.pushResponse(answer("Order 1001 has shipped."));
    json limited = init();
    limited["tools"][0]["max_calls_per_turn"] = 1;
    const json result = {{"type", "tool_result"}, {"id", "t1:1"}, {"result", {{"status", "shipped"}}}};

    const auto events = run({limited, turn("t1", "Where are my orders?"), result});

    // The host was asked once; the second call never reached it.
    EXPECT_EQ(ofType(events, "tool_request").size(), 1u);
    const json final = ofType(events, "final").at(0);
    EXPECT_EQ(final["status"], "success");
    ASSERT_EQ(final["tool_calls"].size(), 2u);
    EXPECT_EQ(final["tool_calls"][1]["arguments"]["order_number"], "2002");
    EXPECT_EQ(final["tool_calls"][1]["result"]["status"], "refused");
    EXPECT_EQ(final["tool_calls"][1]["failed"], false);
    EXPECT_NE(mock_.receivedBodies().at(2).find("already ran once this turn"), std::string::npos);
}

TEST_F(GaiacppEngine, AfterATerminalToolNoOtherToolRuns) {
    mock_.pushResponse(toolCall("handoff", {{"summary", "empty box"}}));
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("A colleague will email you today."));
    json withHandoff = init();
    withHandoff["tools"].push_back(
        {{"name", "handoff"},
         {"description", "Pass the conversation to a person."},
         {"parameters",
          {{"type", "object"},
           {"properties", {{"summary", {{"type", "string"}}}}},
           {"required", json::array({"summary"})}}},
         {"terminal", true}});
    const json result = {{"type", "tool_result"}, {"id", "t1:1"}, {"result", {{"ticket", "T-17"}}}};

    const auto events = run({withHandoff, turn("t1", "my box was empty"), result});

    const auto requests = ofType(events, "tool_request");
    ASSERT_EQ(requests.size(), 1u);
    EXPECT_EQ(requests[0]["name"], "handoff");
    const json final = ofType(events, "final").at(0);
    EXPECT_EQ(final["status"], "success");
    EXPECT_EQ(final["text"], "A colleague will email you today.");
    EXPECT_EQ(final["tool_calls"][1]["result"]["status"], "refused");
}

TEST_F(GaiacppEngine, ATerminalToolThatFailedDoesNotEndToolUse) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("It has shipped."));
    json terminal = init();
    terminal["tools"][0]["terminal"] = true;
    const json failed = {{"type", "tool_result"}, {"id", "t1:1"}, {"error", "archive offline"}};
    const json worked = {{"type", "tool_result"}, {"id", "t1:2"}, {"result", {{"status", "shipped"}}}};

    const auto events = run({terminal, turn("t1", "Where is order 1001?"), failed, worked});

    EXPECT_EQ(ofType(events, "tool_request").size(), 2u);
    EXPECT_EQ(ofType(events, "final").at(0)["text"], "It has shipped.");
}

TEST_F(GaiacppEngine, ADollarLimitStopsTheTurnAtTheHostsPrices) {
    // 100 in + 7 out at $1,000 and $10,000 per million: $0.17 a request.
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("never asked for"));
    json priced = init();
    priced["prices"] = {{"input", 1000.0}, {"cached_input", 0.0}, {"output", 10000.0}};
    json t = turn("t1", "Where is order 1001?");
    t["budget"] = {{"usd", 0.10}};

    const auto events = run({priced, t});

    const json final = ofType(events, "final").at(0);
    EXPECT_EQ(final["status"], "incomplete");
    EXPECT_EQ(final["stop_reason"], "cost");
    EXPECT_NEAR(final["cost_usd"].get<double>(), 0.17, 1e-9);
    EXPECT_TRUE(ofType(events, "tool_request").empty());
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, CostIsNullWithoutPricesAndADollarLimitIsRefused) {
    mock_.pushResponse(answer("Hello."));
    json t = turn("t2", "hi");
    t["budget"] = {{"usd", 0.10}};

    const auto events = run({init(), turn("t1", "hi"), t});

    EXPECT_TRUE(ofType(events, "final").at(0)["cost_usd"].is_null());
    const auto errors = ofType(events, "error");
    ASSERT_EQ(errors.size(), 1u);
    EXPECT_EQ(errors[0]["code"], "bad_turn");
    EXPECT_NE(errors[0]["message"].get<std::string>().find("prices"), std::string::npos);
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, BadToolLimitsAndPricesAreRefused) {
    for (const auto& [where, value] : std::vector<std::pair<std::string, json>>{
             {"max_calls_per_turn", 0}, {"max_calls_per_turn", "1"}, {"terminal", "yes"}}) {
        json bad = init();
        bad["tools"][0][where] = value;
        const auto events = run({bad});
        ASSERT_EQ(events.size(), 1u) << where;
        EXPECT_EQ(events[0]["code"], "bad_init") << where;
    }
    json bad = init();
    bad["prices"] = {{"input", 1.0}};
    const auto events = run({bad});
    ASSERT_EQ(events.size(), 1u);
    EXPECT_EQ(events[0]["code"], "bad_init");
}

// The contract both SDKs are written against.
json wireFixture() {
    std::ifstream in(std::string(GAIA_REPO_FIXTURES_DIR) + "/stdio/gaia_host_wire.json");
    EXPECT_TRUE(in.good()) << "tests/fixtures/stdio/gaia_host_wire.json is missing";
    json fixture;
    in >> fixture;
    return fixture;
}

std::set<std::string> keysOf(const json& object) {
    std::set<std::string> keys;
    for (auto it = object.begin(); it != object.end(); ++it) keys.insert(it.key());
    return keys;
}

std::set<std::string> listed(const json& names) {
    std::set<std::string> out;
    for (const auto& name : names) out.insert(name.get<std::string>());
    return out;
}

TEST_F(GaiacppEngine, ARequestStillInFlightAtTheDeadlineIsCutOff) {
    std::promise<void> release;
    mock_.holdNextResponse(release.get_future().share());
    // Without the cut-off the held reply arrives here and the turn "succeeds" late.
    std::thread provider([&release] {
        std::this_thread::sleep_for(std::chrono::seconds(8));
        release.set_value();
    });
    json limited = turn("t1", "hi");
    limited["budget"] = {{"seconds", 1}};

    const auto started = std::chrono::steady_clock::now();
    const auto events = run({init(), limited});
    const double waited =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    provider.join();

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["status"], "incomplete");
    EXPECT_EQ(finals[0]["stop_reason"], "deadline");
    EXPECT_EQ(finals[0]["text"], "");
    EXPECT_TRUE(finals[0]["failure"].is_null());
    EXPECT_LT(waited, 6.0);
}

TEST_F(GaiacppEngine, AProviderFailureBeforeTheDeadlineIsStillAFailure) {
    mock_.pushResponse(R"({"error":{"message":"upstream overloaded"}})", 503);
    json limited = turn("t1", "hi");
    limited["budget"] = {{"seconds", 60}};

    const auto finals = ofType(run({init(), limited}), "final");

    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["stop_reason"], "model_call");
}

TEST_F(GaiacppEngine, NoRunOfBadLinesCrashesTheEngineOrGarblesItsOutput) {
    const std::vector<std::string> junk = {
        "not json", "{", "[]", "null", "42", "\"text\"", "", "   ", "{\"type\":5}",
        "{\"type\":\"dance\"}", "{\"type\":\"tool_result\",\"id\":\"zzz\",\"result\":1}",
        "{\"type\":\"tool_result\"}", "{\"type\":\"turn\"}",
        "{\"type\":\"turn\",\"id\":5,\"messages\":\"hi\"}",
        "{\"type\":\"turn\",\"id\":\"x\",\"messages\":[5,null,{\"role\":1}]}",
        "{\"type\":\"turn\",\"id\":\"y\",\"messages\":[],\"budget\":\"lots\"}",
        "{\"type\":\"turn\",\"id\":\"z\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],"
        "\"budget\":{\"model_calls\":-5,\"seconds\":\"x\"}}",
        "{\"type\":\"turn\",\"id\":\"w\",\"messages\":[{\"role\":\"wizard\",\"content\":\"hi\"}]}",
        std::string(20000, '['), std::string("{\"type\":\"turn\",\"id\":\"n\0x\"}", 27),
        "{\"type\":\"init\",\"protocol\":\"1\",\"model\":5,\"tools\":\"x\"}",
    };
    const std::vector<std::string> modelReplies = {
        answer("Here is the answer."), toolCall("get_order", {{"order_number", "1"}}),
        toolCall("delete_everything", {{"confirm", true}}), "garbage", "{}", R"({"choices":[]})",
        R"({"choices":[{"message":null}]})", R"({"choices":[{"message":{"content":5}}]})",
        R"({"choices":[{"message":{"tool_calls":"x"}}]})",
        R"({"choices":[{"message":{"tool_calls":[{"id":"c"}]}}]})", answer(""),
    };

    for (unsigned seed = 0; seed < 150; ++seed) {
        std::mt19937 rng(seed);
        auto pick = [&rng](size_t n) { return std::uniform_int_distribution<size_t>(0, n - 1)(rng); };
        mock_.clearQueue();
        for (int i = 0; i < 6; ++i) mock_.pushResponse(modelReplies[pick(modelReplies.size())]);
        mock_.pushResponses(answer("Here is the answer."), 20);

        std::string input;
        if (pick(4) != 0) input += (pick(2) ? gatedInit() : init()).dump() + "\n";
        int turns = 0;
        const size_t lines = 1 + pick(8);
        for (size_t i = 0; i < lines; ++i) {
            switch (pick(6)) {
            case 4:
                input += go("t" + std::to_string(turns) + ":m" + std::to_string(1 + pick(3))).dump() + "\n";
                break;
            case 5:
                input += pick(2) ? std::string("{\"type\":\"cancel\"}\n")
                                 : std::string("{\"type\":\"model_go\",\"id\":\"t1:m1\",\"stop\":\"quota\"}\n");
                break;
            case 0:
                input += turn("t" + std::to_string(++turns), "Where is my order?").dump() + "\n";
                break;
            case 1:
                // The id of the tool call a turn in flight would be waiting on.
                input += json{{"type", "tool_result"},
                              {"id", "t" + std::to_string(turns) + ":1"},
                              {"result", {{"status", "shipped"}}}}
                             .dump() +
                         "\n";
                break;
            default:
                input += junk[pick(junk.size())] + "\n";
            }
        }

        std::istringstream in(input);
        std::ostringstream out;
        const int code = runHostServer(in, out, HostOptions{"test"});

        EXPECT_TRUE(code == 0 || code == 2) << "seed " << seed << " exit " << code;
        std::istringstream written(out.str());
        std::string line;
        std::map<std::string, int> finalsPerTurn;
        while (std::getline(written, line)) {
            if (line.empty()) continue;
            const json event = json::parse(line, nullptr, false);
            ASSERT_FALSE(event.is_discarded()) << "seed " << seed << " wrote: " << line.substr(0, 120);
            ASSERT_TRUE(event.is_object() && event.contains("type") && event["type"].is_string())
                << "seed " << seed;
            if (event["type"] == "final") ++finalsPerTurn[event.value("turn", std::string())];
        }
        for (const auto& [id, count] : finalsPerTurn) {
            EXPECT_EQ(count, 1) << "seed " << seed << " turn " << id;
        }
    }
}

TEST_F(GaiacppEngine, WithTheGateOnEveryModelRequestWaitsForTheHost) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("It has shipped."));
    const json result = {{"type", "tool_result"}, {"id", "t1:1"}, {"result", {{"status", "shipped"}}}};
    json limited = turn("t1", "Where is order 1001?");
    limited["budget"] = {{"seconds", 60}};

    const auto events = run({gatedInit(), limited, go("t1:m1"), result, go("t1:m2")});

    const auto asked = ofType(events, "model_request");
    ASSERT_EQ(asked.size(), 2u);
    EXPECT_EQ(asked[0]["id"], "t1:m1");
    EXPECT_EQ(asked[0]["model_calls"], 0);
    EXPECT_EQ(asked[1]["model_calls"], 1);
    EXPECT_EQ(asked[1]["output_tokens"], 7);
    EXPECT_GT(asked[1]["seconds_left"].get<double>(), 0.0);
    EXPECT_LE(asked[1]["seconds_left"].get<double>(), 60.0);
    EXPECT_EQ(ofType(events, "final").at(0)["text"], "It has shipped.");
    EXPECT_TRUE(ofType(events, "final").at(0)["host_stop"].is_null());
}

TEST_F(GaiacppEngine, WithoutTheGateNoModelRequestIsSent) {
    mock_.pushResponse(answer("Hello."));

    const auto events = run({init(), turn("t1", "hi")});

    EXPECT_TRUE(ofType(events, "model_request").empty());
    EXPECT_EQ(ofType(events, "final").at(0)["text"], "Hello.");
}

TEST_F(GaiacppEngine, AHostStopEndsTheTurnWithoutSendingTheRequest) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("Next turn."));
    const json result = {{"type", "tool_result"}, {"id", "t1:1"}, {"result", {{"status", "shipped"}}}};
    json stop = go("t1:m2");
    stop["stop"] = "account is over its rate limit";

    const auto events = run({gatedInit(), turn("t1", "Where is order 1001?"), go("t1:m1"), result, stop,
                             turn("t2", "hi"), go("t2:m1")});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 2u);
    EXPECT_EQ(finals[0]["status"], "incomplete");
    EXPECT_EQ(finals[0]["stop_reason"], "host_stop");
    EXPECT_EQ(finals[0]["host_stop"], "account is over its rate limit");
    EXPECT_EQ(finals[0]["text"], "");
    EXPECT_TRUE(finals[0]["failure"].is_null());
    EXPECT_EQ(finals[0]["usage"]["model_calls"], 1);
    EXPECT_EQ(finals[1]["status"], "success");
    EXPECT_EQ(finals[1]["text"], "Next turn.");
    EXPECT_EQ(mock_.requestCount(), 2);
}

TEST_F(GaiacppEngine, ACancelInPlaceOfModelGoEndsTheTurnCancelled) {
    mock_.pushResponse(answer("Next turn."));

    const auto events =
        run({gatedInit(), turn("t1", "hi"), json{{"type", "cancel"}}, turn("t2", "hi"), go("t2:m1")});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 2u);
    EXPECT_EQ(finals[0]["status"], "cancelled");
    EXPECT_EQ(finals[0]["stop_reason"], "cancelled");
    EXPECT_EQ(finals[0]["text"], "");
    EXPECT_EQ(finals[1]["text"], "Next turn.");
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, ACancelInPlaceOfAToolResultEndsTheTurnCancelled) {
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("never asked for"));

    const auto events = run({init(), turn("t1", "Where is order 1001?"), json{{"type", "cancel"}}});

    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["status"], "cancelled");
    EXPECT_EQ(finals[0]["tool_calls"][0]["failed"], true);
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, AWrongAnswerToAModelRequestIsNamedAndTheNextTurnStillRuns) {
    mock_.pushResponse(answer("Next turn."));
    json notAString = go("t1:m1");
    notAString["stop"] = 5;

    const auto events = run({gatedInit(), turn("t1", "hi"), notAString, turn("t2", "hi"), go("t2:m1")});

    ASSERT_EQ(ofType(events, "error").size(), 1u);
    EXPECT_EQ(ofType(events, "error")[0]["code"], "bad_message");
    const auto finals = ofType(events, "final");
    ASSERT_EQ(finals.size(), 1u);
    EXPECT_EQ(finals[0]["turn"], "t2");
    EXPECT_EQ(mock_.requestCount(), 1);
}

TEST_F(GaiacppEngine, AGateFlagThatIsNotABooleanIsRefused) {
    json bad = init();
    bad["gate_model_calls"] = "yes";

    const auto events = run({bad});

    EXPECT_EQ(ofType(events, "error").at(0)["code"], "bad_init");
}

TEST_F(GaiacppEngine, EveryEventHasExactlyTheFieldsTheWireFixtureLists) {
    const json fixture = wireFixture();
    mock_.pushResponse(toolCall("get_order", {{"order_number", "1001"}}));
    mock_.pushResponse(answer("It has shipped."));
    const json result = {{"type", "tool_result"}, {"id", "t1:1"}, {"result", {{"status", "shipped"}}}};
    json bad = turn("t2", "hi");
    bad.erase("messages");

    const auto events =
        run({gatedInit(), turn("t1", "Where is order 1001?"), go("t1:m1"), result, go("t1:m2"), bad});

    std::set<std::string> seen;
    for (const auto& event : events) {
        const std::string type = event["type"];
        seen.insert(type);
        ASSERT_TRUE(fixture["engine_to_host"].contains(type)) << "event not in the fixture: " << type;
        EXPECT_EQ(keysOf(event), listed(fixture["engine_to_host"][type])) << type;
    }
    EXPECT_EQ(seen, keysOf(fixture["engine_to_host"]));
    const json final = ofType(events, "final").at(0);
    EXPECT_EQ(keysOf(final["usage"]), listed(fixture["usage"]));
    EXPECT_EQ(keysOf(final["tool_calls"][0]), listed(fixture["tool_call"]));
    EXPECT_EQ(keysOf(ofType(events, "model_call").at(0)["usage"]), listed(fixture["model_call_usage"]));
    EXPECT_EQ(fixture["protocol"], kHostProtocol);
    EXPECT_TRUE(listed(fixture["status"]).count(final["status"]));
    EXPECT_TRUE(listed(fixture["stop_reason"]).count(final["stop_reason"]));
    EXPECT_TRUE(listed(fixture["error_code"]).count(ofType(events, "error").at(0)["code"]));
}

}  // namespace
