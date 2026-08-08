// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Native OpenAI tool calling (issue #2794).
//
// Covers the model gate, the request-body switch, parallel tool_calls, the
// spec-correct assistant/tool message pair, streaming tool_calls delta
// accumulation, and both response modes. Everything runs against the
// in-process mock HTTP server or against SseParser directly — no Lemonade.

#include <gtest/gtest.h>

#include <gaia/agent.h>
#include <gaia/model_registry.h>
#include <gaia/sse_parser.h>
#include <gaia/tool_registry.h>

#include <nlohmann/json.hpp>

#include <set>
#include <string>
#include <vector>

#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

// A model id known to support native tool calls (mirrors Python MODELS).
constexpr const char* kToolCallingModel = "Gemma-4-E4B-it-GGUF";
// A model id known NOT to support them (the FLM/NPU build).
constexpr const char* kNonToolCallingModel = "gemma4-it-e2b-FLM";

/// Agent with two tools, used to exercise the schema and execution paths.
class ToolAgent : public Agent {
public:
    using Agent::Agent;

    std::vector<std::string> executed;

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "echo", "Echo a message back",
            [this](const json& args) -> json {
                executed.push_back("echo");
                return json{{"echoed", args.value("message", "")}};
            },
            {{"message", ToolParamType::STRING, true, "Message to echo"}});

        toolRegistry().registerTool(
            "add", "Add two numbers",
            [this](const json& args) -> json {
                executed.push_back("add");
                return json{{"sum", args.value("a", 0) + args.value("b", 0)}};
            },
            {{"a", ToolParamType::INTEGER, true, "First number"},
             {"b", ToolParamType::INTEGER, false, "Second number"}});

        toolRegistry().registerTool(
            "ping", "Take no arguments",
            [this](const json&) -> json {
                executed.push_back("ping");
                return json{{"pong", true}};
            });
    }

    std::string getSystemPrompt() const override { return "You are a tool agent."; }

public:
    void initForTest() { init(); }
};

AgentConfig makeCfg(const std::string& url, const std::string& modelId) {
    AgentConfig cfg;
    cfg.baseUrl = url;
    cfg.modelId = modelId;
    cfg.maxSteps = 3;
    cfg.silentMode = true;
    // Pinned, not inherited: AgentConfig::streaming defaults from GAIA_STREAMING,
    // and a streaming body carries an extra "stream" key that would break the
    // exact-key-set assertion below.
    cfg.streaming = false;
    return cfg;
}

/// Non-streaming chat completion carrying native tool_calls.
std::string toolCallsResponse(const std::string& callsJson,
                              const std::string& content = "null") {
    return R"({"choices":[{"message":{"role":"assistant","content":)" + content +
           R"(,"tool_calls":)" + callsJson + "}}]}";
}

const std::string kPlainAnswer =
    R"({"choices":[{"message":{"role":"assistant","content":"All done."}}]})";

std::set<std::string> keysOf(const json& obj) {
    std::set<std::string> keys;
    for (auto it = obj.begin(); it != obj.end(); ++it) keys.insert(it.key());
    return keys;
}

/// Return the system message content from a captured request body.
std::string systemPromptOf(const json& body) {
    for (const auto& m : body.at("messages")) {
        if (m.value("role", "") == "system") return m.value("content", "");
    }
    return "";
}

} // namespace

// ---------------------------------------------------------------------------
// The isToolCallingModel gate — both directions
// ---------------------------------------------------------------------------

TEST(ModelGateTest, KnownToolCallingModelsReturnTrue) {
    EXPECT_TRUE(isToolCallingModel("Gemma-4-E4B-it-GGUF"));
    EXPECT_TRUE(isToolCallingModel("Qwen3-0.6B-GGUF"));
    EXPECT_TRUE(isToolCallingModel("Qwen3-8B-GGUF"));
    EXPECT_TRUE(isToolCallingModel("Qwen3.5-35B-A3B-GGUF"));
    EXPECT_TRUE(isToolCallingModel("Qwen3-VL-4B-Instruct-GGUF"));
}

TEST(ModelGateTest, KnownNonToolCallingModelsReturnFalse) {
    // The FastFlowLM/NPU build 500s on an OpenAI tools payload.
    EXPECT_FALSE(isToolCallingModel("gemma4-it-e2b-FLM"));
    // Embedders never chat.
    EXPECT_FALSE(isToolCallingModel("user.embeddinggemma-300m-GGUF"));
    EXPECT_FALSE(isToolCallingModel("embed-gemma-300m-FLM"));
}

TEST(ModelGateTest, UnknownAndEmptyModelsReturnFalse) {
    // Divergence from Python's optimistic default — see model_registry.h.
    EXPECT_FALSE(isToolCallingModel("Qwen3-4B-GGUF"));  // the C++ default model
    EXPECT_FALSE(isToolCallingModel("some-random-model"));
    EXPECT_FALSE(isToolCallingModel(""));
}

TEST(ModelGateTest, MirrorsThePythonModelTableExactly) {
    // Pins the mirrored table so a Python-side change that is not mirrored here
    // shows up as a test edit in review. Source of truth: MODELS in
    // src/gaia/llm/lemonade_client.py.
    const std::vector<std::pair<std::string, bool>> expected = {
        {"Gemma-4-E4B-it-GGUF", true},
        {"gemma4-it-e2b-FLM", false},
        {"Qwen3.5-35B-A3B-GGUF", true},
        {"Qwen3-0.6B-GGUF", true},
        {"Qwen3-VL-4B-Instruct-GGUF", true},
        {"Qwen3-8B-GGUF", true},
        {"user.embeddinggemma-300m-GGUF", false},
        {"embed-gemma-300m-FLM", false},
    };
    ASSERT_EQ(knownModels().size(), expected.size());
    for (size_t i = 0; i < expected.size(); ++i) {
        EXPECT_EQ(knownModels()[i].modelId, expected[i].first) << "at index " << i;
        EXPECT_EQ(knownModels()[i].toolCalling, expected[i].second) << "at index " << i;
    }
}

TEST(ModelGateTest, ConfigPolicyOverridesTheModel) {
    AgentConfig cfg;

    cfg.modelId = kNonToolCallingModel;
    cfg.nativeToolCalls = NativeToolCalls::Auto;
    EXPECT_FALSE(cfg.useNativeToolCalls());
    cfg.nativeToolCalls = NativeToolCalls::Always;
    EXPECT_TRUE(cfg.useNativeToolCalls());

    cfg.modelId = kToolCallingModel;
    cfg.nativeToolCalls = NativeToolCalls::Auto;
    EXPECT_TRUE(cfg.useNativeToolCalls());
    cfg.nativeToolCalls = NativeToolCalls::Never;
    EXPECT_FALSE(cfg.useNativeToolCalls());
}

// ---------------------------------------------------------------------------
// Request body: legacy path is untouched, native path adds tools/tool_choice
// ---------------------------------------------------------------------------

TEST(NativeToolCallsTest, NonToolCallingModelRequestBodyIsUnchanged) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kNonToolCallingModel));
    agent.initForTest();
    agent.processQuery("hello", 1);

    ASSERT_GE(mock.receivedBodies().size(), 1u);
    json body = json::parse(mock.receivedBodies().back());

    // Exactly the four legacy keys — no tools, no tool_choice.
    EXPECT_EQ(keysOf(body),
              (std::set<std::string>{"model", "max_tokens", "temperature", "messages"}));
    EXPECT_FALSE(body.contains("tools"));
    EXPECT_FALSE(body.contains("tool_choice"));
}

TEST(NativeToolCallsTest, NonToolCallingModelKeepsTheResponseFormatTemplate) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kNonToolCallingModel));
    agent.initForTest();
    agent.processQuery("hello", 1);

    const std::string prompt = systemPromptOf(json::parse(mock.receivedBodies().back()));
    EXPECT_NE(prompt.find("==== RESPONSE FORMAT ===="), std::string::npos);
    EXPECT_NE(prompt.find("You must respond ONLY in valid JSON"), std::string::npos);
    EXPECT_NE(prompt.find("==== AVAILABLE TOOLS ===="), std::string::npos);
    // Identical to what the agent exposes through its public accessor.
    EXPECT_EQ(prompt, agent.systemPrompt());
}

TEST(NativeToolCallsTest, ToolCallingModelSendsToolsAndToolChoice) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("hello", 1);

    ASSERT_GE(mock.receivedBodies().size(), 1u);
    json body = json::parse(mock.receivedBodies().back());

    ASSERT_TRUE(body.contains("tools"));
    ASSERT_TRUE(body["tools"].is_array());
    EXPECT_EQ(body["tools"].size(), 3u);  // add, echo, ping
    EXPECT_EQ(body["tool_choice"], "auto");

    // Registry order is alphabetical (std::map): add, echo.
    const auto& addFn = body["tools"][0]["function"];
    EXPECT_EQ(body["tools"][0]["type"], "function");
    EXPECT_EQ(addFn["name"], "add");
    EXPECT_EQ(addFn["description"], "Add two numbers");
    EXPECT_EQ(addFn["parameters"]["type"], "object");
    EXPECT_EQ(addFn["parameters"]["properties"]["a"]["type"], "integer");
    EXPECT_EQ(addFn["parameters"]["properties"]["a"]["description"], "First number");
    // Only required params appear in "required".
    EXPECT_EQ(addFn["parameters"]["required"], json::array({"a"}));

    const auto& echoFn = body["tools"][1]["function"];
    EXPECT_EQ(echoFn["name"], "echo");
    EXPECT_EQ(echoFn["parameters"]["properties"]["message"]["type"], "string");
}

TEST(NativeToolCallsTest, ToolCallingModelDropsTheResponseFormatTemplate) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("hello", 1);

    const std::string prompt = systemPromptOf(json::parse(mock.receivedBodies().back()));
    EXPECT_EQ(prompt.find("==== RESPONSE FORMAT ===="), std::string::npos);
    // The tool list stays — it is what lets the model pick a tool.
    EXPECT_NE(prompt.find("==== AVAILABLE TOOLS ===="), std::string::npos);
    EXPECT_NE(prompt.find("You are a tool agent."), std::string::npos);
}

TEST(NativeToolCallsTest, NeverPolicyKeepsTheLegacyBodyForAToolCallingModel) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    AgentConfig cfg = makeCfg(mock.baseUrl(), kToolCallingModel);
    cfg.nativeToolCalls = NativeToolCalls::Never;
    ToolAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("hello", 1);

    json body = json::parse(mock.receivedBodies().back());
    EXPECT_FALSE(body.contains("tools"));
    EXPECT_NE(systemPromptOf(body).find("==== RESPONSE FORMAT ===="), std::string::npos);
}

TEST(NativeToolCallsTest, AlwaysPolicySendsToolsForAnUnknownModel) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    AgentConfig cfg = makeCfg(mock.baseUrl(), "some-third-party-model");
    cfg.nativeToolCalls = NativeToolCalls::Always;
    cfg.toolChoice = "required";
    ToolAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("hello", 1);

    json body = json::parse(mock.receivedBodies().back());
    ASSERT_TRUE(body.contains("tools"));
    EXPECT_EQ(body["tool_choice"], "required");
}

TEST(NativeToolCallsTest, NoToolsRegisteredOmitsToolsAndToolChoice) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.processQuery("hello", 1);

    json body = json::parse(mock.receivedBodies().back());
    EXPECT_FALSE(body.contains("tools"));
    EXPECT_FALSE(body.contains("tool_choice"));
}

TEST(NativeToolCallsTest, DisabledToolsAreExcludedFromTheSchemas) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.toolRegistry().setEnabled("echo", false);
    agent.processQuery("hello", 1);

    json body = json::parse(mock.receivedBodies().back());
    ASSERT_TRUE(body.contains("tools"));
    ASSERT_EQ(body["tools"].size(), 2u);
    EXPECT_EQ(body["tools"][0]["function"]["name"], "add");
    EXPECT_EQ(body["tools"][1]["function"]["name"], "ping");
}

// ---------------------------------------------------------------------------
// Executing native tool calls — single and parallel
// ---------------------------------------------------------------------------

TEST(NativeToolCallsTest, SingleToolCallIsExecutedAndAnswered) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_1","type":"function","function":{"name":"echo","arguments":"{\"message\":\"hi\"}"}}])"));
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    json result = agent.processQuery("say hi", 3);

    EXPECT_EQ(agent.executed, (std::vector<std::string>{"echo"}));
    EXPECT_EQ(result["result"], "All done.");

    // The follow-up request must carry a spec-correct assistant + tool pair.
    ASSERT_GE(mock.receivedBodies().size(), 2u);
    json second = json::parse(mock.receivedBodies().back());
    const auto& msgs = second.at("messages");

    const json* assistant = nullptr;
    const json* toolMsg = nullptr;
    for (const auto& m : msgs) {
        if (m.value("role", "") == "assistant" && m.contains("tool_calls")) assistant = &m;
        if (m.value("role", "") == "tool") toolMsg = &m;
    }
    ASSERT_NE(assistant, nullptr) << second.dump(2);
    ASSERT_NE(toolMsg, nullptr) << second.dump(2);

    EXPECT_TRUE((*assistant)["content"].is_null());
    ASSERT_EQ((*assistant)["tool_calls"].size(), 1u);
    EXPECT_EQ((*assistant)["tool_calls"][0]["id"], "call_1");
    EXPECT_EQ((*assistant)["tool_calls"][0]["type"], "function");
    EXPECT_EQ((*assistant)["tool_calls"][0]["function"]["name"], "echo");

    // The tool reply correlates by tool_call_id — not a "[Result from X]" user turn.
    EXPECT_EQ((*toolMsg)["tool_call_id"], "call_1");
    EXPECT_EQ((*toolMsg)["name"], "echo");
    EXPECT_NE((*toolMsg)["content"].get<std::string>().find("\"echoed\":\"hi\""),
              std::string::npos);
}

TEST(NativeToolCallsTest, ParallelToolCallsAllExecuteAndEachGetsItsOwnReply) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_a","type":"function","function":{"name":"echo","arguments":"{\"message\":\"one\"}"}},)"
        R"({"id":"call_b","type":"function","function":{"name":"add","arguments":"{\"a\":2,\"b\":3}"}}])"));
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    json result = agent.processQuery("do both", 3);

    // Both ran, in the order the model asked for.
    EXPECT_EQ(agent.executed, (std::vector<std::string>{"echo", "add"}));
    EXPECT_EQ(result["result"], "All done.");

    json second = json::parse(mock.receivedBodies().back());
    std::vector<std::string> toolCallIds;
    std::vector<std::string> replyIds;
    for (const auto& m : second.at("messages")) {
        if (m.value("role", "") == "assistant" && m.contains("tool_calls")) {
            for (const auto& tc : m["tool_calls"]) {
                toolCallIds.push_back(tc["id"].get<std::string>());
            }
        }
        if (m.value("role", "") == "tool") {
            replyIds.push_back(m.at("tool_call_id").get<std::string>());
        }
    }
    // One assistant turn carrying both calls, one role=tool reply per call.
    EXPECT_EQ(toolCallIds, (std::vector<std::string>{"call_a", "call_b"}));
    EXPECT_EQ(replyIds, (std::vector<std::string>{"call_a", "call_b"}));
}

TEST(NativeToolCallsTest, AssistantTextAlongsideToolCallsIsPreserved) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_1","type":"function","function":{"name":"echo","arguments":"{\"message\":\"x\"}"}}])",
        R"("Let me check that.")"));
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("check", 3);

    json second = json::parse(mock.receivedBodies().back());
    for (const auto& m : second.at("messages")) {
        if (m.value("role", "") == "assistant" && m.contains("tool_calls")) {
            EXPECT_EQ(m["content"], "Let me check that.");
        }
    }
}

TEST(NativeToolCallsTest, ZeroArgumentToolCallDecodesToAnEmptyObject) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_1","type":"function","function":{"name":"ping","arguments":""}}])"));
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("ping", 3);

    EXPECT_EQ(agent.executed, (std::vector<std::string>{"ping"}));
}

TEST(NativeToolCallsTest, PreDecodedArgumentObjectIsAccepted) {
    // Some OpenAI-compatible servers send function.arguments as an object
    // rather than a JSON-encoded string.
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_1","type":"function","function":{"name":"add","arguments":{"a":4,"b":6}}}])"));
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("add", 3);

    EXPECT_EQ(agent.executed, (std::vector<std::string>{"add"}));
    json second = json::parse(mock.receivedBodies().back());
    bool sawSum = false;
    for (const auto& m : second.at("messages")) {
        if (m.value("role", "") == "tool") {
            sawSum = m["content"].get<std::string>().find("\"sum\":10") != std::string::npos;
        }
    }
    EXPECT_TRUE(sawSum);
}

// ---------------------------------------------------------------------------
// Malformed tool_calls must surface, never degrade to prose parsing
// ---------------------------------------------------------------------------

TEST(NativeToolCallsTest, ToolCallMissingIdIsRejectedWithAnActionableError) {
    json entry = json::parse(
        R"({"type":"function","function":{"name":"echo","arguments":"{}"}})");
    try {
        ToolCall::fromJson(entry);
        FAIL() << "expected a throw for a tool_calls entry with no id";
    } catch (const std::runtime_error& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("'id'"), std::string::npos) << msg;
        EXPECT_NE(msg.find("correlated"), std::string::npos) << msg;
    }
}

TEST(NativeToolCallsTest, ToolCallMissingFunctionNameIsRejected) {
    json entry = json::parse(R"({"id":"call_1","function":{"arguments":"{}"}})");
    try {
        ToolCall::fromJson(entry);
        FAIL() << "expected a throw for a tool_calls entry with no function.name";
    } catch (const std::runtime_error& e) {
        EXPECT_NE(std::string(e.what()).find("function.name"), std::string::npos);
    }
}

TEST(NativeToolCallsTest, MalformedArgumentsJsonThrowsNamingTheTool) {
    ToolCall tc;
    tc.id = "call_9";
    tc.name = "echo";
    tc.arguments = "{\"message\": ";  // truncated
    try {
        tc.parsedArgs();
        FAIL() << "expected a throw for unparseable arguments";
    } catch (const std::runtime_error& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("echo"), std::string::npos) << msg;
        EXPECT_NE(msg.find("call_9"), std::string::npos) << msg;
        // Names the escape hatch so the error is actionable.
        EXPECT_NE(msg.find("NativeToolCalls::Never"), std::string::npos) << msg;
    }
}

TEST(NativeToolCallsTest, NonObjectArgumentsThrow) {
    ToolCall tc;
    tc.id = "call_9";
    tc.name = "echo";
    tc.arguments = "[1,2,3]";
    EXPECT_THROW(tc.parsedArgs(), std::runtime_error);
}

TEST(NativeToolCallsTest, MalformedToolCallsSurfaceAsAnErrorNotAProseAnswer) {
    bench::MockLlmServer mock;
    // Both the first attempt and the one retry return the same malformed body.
    mock.pushResponse(toolCallsResponse(R"([{"type":"function","function":{"name":"echo"}}])"));
    mock.pushResponse(toolCallsResponse(R"([{"type":"function","function":{"name":"echo"}}])"));

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    json result = agent.processQuery("go", 2);

    // The loop reports the LLM error rather than silently answering with prose.
    const std::string answer = result["result"].get<std::string>();
    EXPECT_NE(answer.find("LLM error"), std::string::npos) << answer;
    EXPECT_TRUE(agent.executed.empty());
}

// ---------------------------------------------------------------------------
// Message serialization
// ---------------------------------------------------------------------------

TEST(NativeToolCallsTest, AssistantMessageSerializesToolCallsWithNullContent) {
    Message m;
    m.role = MessageRole::ASSISTANT;
    m.toolCalls = {ToolCall{"call_1", "echo", R"({"message":"hi"})"}};

    json j = m.toJson();
    EXPECT_EQ(j["role"], "assistant");
    EXPECT_TRUE(j["content"].is_null());
    ASSERT_EQ(j["tool_calls"].size(), 1u);
    EXPECT_EQ(j["tool_calls"][0]["id"], "call_1");
    EXPECT_EQ(j["tool_calls"][0]["type"], "function");
    EXPECT_EQ(j["tool_calls"][0]["function"]["name"], "echo");
    // arguments stays a JSON-encoded *string*, per the OpenAI wire format.
    EXPECT_TRUE(j["tool_calls"][0]["function"]["arguments"].is_string());
}

TEST(NativeToolCallsTest, MessageWithoutToolCallsHasNoToolCallsKey) {
    Message m;
    m.role = MessageRole::ASSISTANT;
    m.content = "plain";
    json j = m.toJson();
    EXPECT_FALSE(j.contains("tool_calls"));
    EXPECT_EQ(j["content"], "plain");
}

// ---------------------------------------------------------------------------
// Streaming: tool_calls deltas accumulated across chunk boundaries
// ---------------------------------------------------------------------------

namespace {

/// Feed a whole SSE payload to a parser one byte at a time, which is the
/// harshest possible chunk split.
void feedByteByByte(SseParser& parser, const std::string& sse) {
    for (char c : sse) {
        parser.feed(&c, 1);
    }
}

} // namespace

TEST(SseToolCallsTest, AccumulatesASingleToolCallAcrossChunks) {
    std::string collected;
    SseParser parser([&](const std::string& t) { collected += t; });

    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_1\","
        "\"type\":\"function\",\"function\":{\"name\":\"echo\",\"arguments\":\"\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,"
        "\"function\":{\"arguments\":\"{\\\"mess\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,"
        "\"function\":{\"arguments\":\"age\\\":\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,"
        "\"function\":{\"arguments\":\"\\\"hi\\\"}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";

    feedByteByByte(parser, sse);

    EXPECT_TRUE(parser.done());
    EXPECT_TRUE(parser.hasToolCalls());
    EXPECT_TRUE(collected.empty()) << "tool-call streams carry no content tokens";

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 1u);
    EXPECT_EQ(calls[0].id, "call_1");
    EXPECT_EQ(calls[0].name, "echo");
    EXPECT_EQ(calls[0].arguments, R"({"message":"hi"})");
    EXPECT_EQ(calls[0].parsedArgs()["message"], "hi");
}

TEST(SseToolCallsTest, AccumulatesParallelToolCallsByIndex) {
    SseParser parser([](const std::string&) {});

    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":["
        "{\"index\":0,\"id\":\"call_a\",\"function\":{\"name\":\"echo\",\"arguments\":\"\"}},"
        "{\"index\":1,\"id\":\"call_b\",\"function\":{\"name\":\"add\",\"arguments\":\"\"}}]}}]}\n\n"
        // Interleaved argument fragments — index keeps them apart.
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":1,"
        "\"function\":{\"arguments\":\"{\\\"a\\\":1,\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,"
        "\"function\":{\"arguments\":\"{\\\"message\\\":\\\"x\\\"}\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":1,"
        "\"function\":{\"arguments\":\"\\\"b\\\":2}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";

    feedByteByByte(parser, sse);

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 2u);
    EXPECT_EQ(calls[0].id, "call_a");
    EXPECT_EQ(calls[0].name, "echo");
    EXPECT_EQ(calls[0].arguments, R"({"message":"x"})");
    EXPECT_EQ(calls[1].id, "call_b");
    EXPECT_EQ(calls[1].name, "add");
    EXPECT_EQ(calls[1].arguments, R"({"a":1,"b":2})");
    EXPECT_EQ(calls[1].parsedArgs()["b"], 2);
}

TEST(SseToolCallsTest, ReassemblesAFragmentedFunctionName) {
    SseParser parser([](const std::string&) {});
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\","
        "\"function\":{\"name\":\"ec\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,"
        "\"function\":{\"name\":\"ho\",\"arguments\":\"{}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";

    feedByteByByte(parser, sse);

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 1u);
    EXPECT_EQ(calls[0].name, "echo");
}

TEST(SseToolCallsTest, RepeatedIdInLaterDeltasDoesNotDuplicate) {
    SseParser parser([](const std::string&) {});
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_x\","
        "\"function\":{\"name\":\"echo\",\"arguments\":\"{\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"call_x\","
        "\"function\":{\"arguments\":\"}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";

    feedByteByByte(parser, sse);

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 1u);
    EXPECT_EQ(calls[0].id, "call_x");
    EXPECT_EQ(calls[0].arguments, "{}");
}

TEST(SseToolCallsTest, ToolCallsWithoutAnIndexLandInSlotZero) {
    SseParser parser([](const std::string&) {});
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"id\":\"c1\","
        "\"function\":{\"name\":\"echo\",\"arguments\":\"{}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";
    feedByteByByte(parser, sse);

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 1u);
    EXPECT_EQ(calls[0].name, "echo");
}

TEST(SseToolCallsTest, ContentOnlyStreamReportsNoToolCalls) {
    std::string collected;
    SseParser parser([&](const std::string& t) { collected += t; });
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"content\":\"Hello\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"content\":\" world\"}}]}\n\n"
        "data: [DONE]\n\n";
    feedByteByByte(parser, sse);

    EXPECT_EQ(collected, "Hello world");
    EXPECT_FALSE(parser.hasToolCalls());
    EXPECT_TRUE(parser.toolCalls().empty());
}

TEST(SseToolCallsTest, MixedContentAndToolCallsBothSurvive) {
    std::string collected;
    SseParser parser([&](const std::string& t) { collected += t; });
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"content\":\"Checking\"}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\","
        "\"function\":{\"name\":\"echo\",\"arguments\":\"{}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";
    feedByteByByte(parser, sse);

    EXPECT_EQ(collected, "Checking");
    ASSERT_EQ(parser.toolCalls().size(), 1u);
    EXPECT_EQ(parser.toolCalls()[0].name, "echo");
}

// ---------------------------------------------------------------------------
// Response modes
// ---------------------------------------------------------------------------

TEST(ResponseModeTest, PlanningIsTheDefaultAndEmitsThePlanningTemplate) {
    AgentConfig cfg;
    EXPECT_EQ(cfg.responseMode, ResponseMode::Planning);

    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);
    ToolAgent agent(makeCfg(mock.baseUrl(), kNonToolCallingModel));
    agent.initForTest();
    agent.processQuery("hi", 1);

    const std::string prompt = systemPromptOf(json::parse(mock.receivedBodies().back()));
    EXPECT_NE(prompt.find("You must respond ONLY in valid JSON"), std::string::npos);
    EXPECT_EQ(prompt.find("Respond in plain text for normal conversation"), std::string::npos);
}

TEST(ResponseModeTest, ConversationalEmitsThePortedConversationalTemplate) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    AgentConfig cfg = makeCfg(mock.baseUrl(), kNonToolCallingModel);
    cfg.responseMode = ResponseMode::Conversational;
    ToolAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("hi", 1);

    const std::string prompt = systemPromptOf(json::parse(mock.receivedBodies().back()));
    // Byte-for-byte the lines Python's _CONVERSATIONAL_FORMAT carries.
    EXPECT_NE(prompt.find("Respond in plain text for normal conversation."), std::string::npos);
    EXPECT_NE(prompt.find(R"({"tool": "tool_name", "tool_args": {"arg1": "value1"}})"),
              std::string::npos);
    EXPECT_NE(prompt.find("Do NOT wrap conversational replies in JSON."), std::string::npos);
    EXPECT_EQ(prompt.find("You must respond ONLY in valid JSON"), std::string::npos);
}

TEST(ResponseModeTest, ConversationalStillExecutesABareToolJsonReply) {
    bench::MockLlmServer mock;
    mock.pushResponse(
        R"({"choices":[{"message":{"content":"{\"tool\":\"echo\",\"tool_args\":{\"message\":\"yo\"}}"}}]})");
    mock.pushResponse(kPlainAnswer);

    AgentConfig cfg = makeCfg(mock.baseUrl(), kNonToolCallingModel);
    cfg.responseMode = ResponseMode::Conversational;
    ToolAgent agent(cfg);
    agent.initForTest();
    json result = agent.processQuery("echo yo", 3);

    EXPECT_EQ(agent.executed, (std::vector<std::string>{"echo"}));
    EXPECT_EQ(result["result"], "All done.");
}

TEST(ResponseModeTest, NativeToolCallingSuppressesBothTemplates) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    AgentConfig cfg = makeCfg(mock.baseUrl(), kToolCallingModel);
    cfg.responseMode = ResponseMode::Conversational;
    ToolAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("hi", 1);

    const std::string prompt = systemPromptOf(json::parse(mock.receivedBodies().back()));
    EXPECT_EQ(prompt.find("==== RESPONSE FORMAT ===="), std::string::npos);
}

// ---------------------------------------------------------------------------
// Config round-trip
// ---------------------------------------------------------------------------

TEST(ResponseModeTest, ConfigRoundTripsTheNewFields) {
    AgentConfig cfg;
    cfg.responseMode = ResponseMode::Conversational;
    cfg.nativeToolCalls = NativeToolCalls::Always;
    cfg.toolChoice = "required";

    AgentConfig back = AgentConfig::fromJson(cfg.toJson());
    EXPECT_EQ(back.responseMode, ResponseMode::Conversational);
    EXPECT_EQ(back.nativeToolCalls, NativeToolCalls::Always);
    EXPECT_EQ(back.toolChoice, "required");
}

TEST(ResponseModeTest, ConfigDefaultsAreUnchangedWhenFieldsAreAbsent) {
    AgentConfig back = AgentConfig::fromJson(json{{"modelId", "m"}});
    EXPECT_EQ(back.responseMode, ResponseMode::Planning);
    EXPECT_EQ(back.nativeToolCalls, NativeToolCalls::Auto);
    EXPECT_EQ(back.toolChoice, "auto");
}

TEST(ResponseModeTest, InvalidEnumStringsAreRejected) {
    EXPECT_THROW(AgentConfig::fromJson(json{{"responseMode", "chatty"}}),
                 std::invalid_argument);
    EXPECT_THROW(AgentConfig::fromJson(json{{"nativeToolCalls", "sometimes"}}),
                 std::invalid_argument);
    EXPECT_THROW(AgentConfig::fromJson(json{{"toolChoice", "maybe"}}),
                 std::invalid_argument);
}

// ---------------------------------------------------------------------------
// History: native tool exchanges survive intact, orphans are dropped
// ---------------------------------------------------------------------------

TEST(NativeToolCallsTest, HistoryKeepsTheSpecCorrectToolExchange) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_1","type":"function","function":{"name":"echo","arguments":"{\"message\":\"hi\"}"}}])"));
    mock.pushResponse(kPlainAnswer);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("say hi", 3);

    bool sawAssistantToolCalls = false;
    bool sawToolReply = false;
    for (const auto& m : agent.history()) {
        if (m.role == MessageRole::ASSISTANT && !m.toolCalls.empty()) {
            sawAssistantToolCalls = true;
            EXPECT_EQ(m.toolCalls[0].id, "call_1");
        }
        if (m.role == MessageRole::TOOL) {
            sawToolReply = true;
            ASSERT_TRUE(m.toolCallId.has_value());
            EXPECT_EQ(*m.toolCallId, "call_1");
            // NOT downgraded to a "[Result from echo]:" user turn.
            EXPECT_EQ(m.content.rfind("[Result from", 0), std::string::npos);
        }
    }
    EXPECT_TRUE(sawAssistantToolCalls);
    EXPECT_TRUE(sawToolReply);
}

TEST(NativeToolCallsTest, PromptJsonToolResultsStillDowngradeToUserTurns) {
    bench::MockLlmServer mock;
    mock.pushResponse(
        R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"tool\":\"echo\",\"tool_args\":{\"message\":\"hi\"}}"}}]})");
    mock.pushResponse(
        R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"ok\"}"}}]})");

    ToolAgent agent(makeCfg(mock.baseUrl(), kNonToolCallingModel));
    agent.initForTest();
    agent.processQuery("say hi", 3);

    bool sawDowngraded = false;
    for (const auto& m : agent.history()) {
        EXPECT_NE(m.role, MessageRole::TOOL) << "prompt-JSON results must not stay role=tool";
        if (m.role == MessageRole::USER &&
            m.content.rfind("[Result from echo]:", 0) == 0) {
            sawDowngraded = true;
        }
    }
    EXPECT_TRUE(sawDowngraded);
}

TEST(NativeToolCallsTest, PruningDropsToolRepliesWhoseAssistantTurnIsGone) {
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_1","type":"function","function":{"name":"echo","arguments":"{\"message\":\"hi\"}"}}])"));
    mock.pushResponse(kPlainAnswer);

    // maxHistoryMessages=2 keeps only the tail, cutting the assistant turn that
    // owns call_1 — its role=tool reply must not survive alone.
    AgentConfig cfg = makeCfg(mock.baseUrl(), kToolCallingModel);
    cfg.maxHistoryMessages = 2;
    ToolAgent agent(cfg);
    agent.initForTest();
    agent.processQuery("say hi", 3);

    std::set<std::string> answerable;
    for (const auto& m : agent.history()) {
        for (const auto& tc : m.toolCalls) answerable.insert(tc.id);
        if (m.role == MessageRole::TOOL && m.toolCallId.has_value()) {
            EXPECT_NE(answerable.find(*m.toolCallId), answerable.end())
                << "orphaned tool_call_id " << *m.toolCallId << " survived pruning";
        }
    }
}

TEST(NativeToolCallsTest, CancelBetweenParallelCallsLeavesNoUnansweredToolCall) {
    // The tool itself cancels the run, so the second parallel call never
    // executes — its advertised tool_call must not linger in history.
    class CancellingAgent : public Agent {
    public:
        using Agent::Agent;
        int runs = 0;

    protected:
        void registerTools() override {
            toolRegistry().registerTool(
                "echo", "Echo", [this](const json&) -> json {
                    ++runs;
                    requestCancel();
                    return json{{"ok", true}};
                });
            toolRegistry().registerTool(
                "add", "Add", [this](const json&) -> json {
                    ++runs;
                    return json{{"sum", 0}};
                });
        }

    public:
        void initForTest() { init(); }
    };

    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_a","type":"function","function":{"name":"echo","arguments":"{}"}},)"
        R"({"id":"call_b","type":"function","function":{"name":"add","arguments":"{}"}}])"));

    CancellingAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();
    agent.processQuery("go", 3);

    EXPECT_EQ(agent.runs, 1) << "cancel must stop the second parallel call";

    std::set<std::string> advertised;
    std::set<std::string> answered;
    for (const auto& m : agent.history()) {
        for (const auto& tc : m.toolCalls) advertised.insert(tc.id);
        if (m.role == MessageRole::TOOL && m.toolCallId.has_value()) {
            answered.insert(*m.toolCallId);
        }
    }
    EXPECT_EQ(advertised, answered)
        << "every advertised tool_call must have a reply in stored history";
    EXPECT_EQ(advertised.count("call_b"), 0u) << "the cancelled call must be dropped";
}

// ---------------------------------------------------------------------------
// Regressions found in review
// ---------------------------------------------------------------------------

TEST(NativeToolCallsTest, MalformedArgsInABatchRunNothingAndDoNotThrow) {
    // Call A is well-formed, call B is truncated. Decoding happens up front, so
    // A's side effects never happen — and processQuery reports via "result"
    // rather than throwing out of the public API.
    bench::MockLlmServer mock;
    const std::string body = toolCallsResponse(
        R"([{"id":"call_a","type":"function","function":{"name":"echo","arguments":"{\"message\":\"hi\"}"}},)"
        R"({"id":"call_b","type":"function","function":{"name":"add","arguments":"{\"a\":"}}])");
    mock.pushResponse(body);
    mock.pushResponse(body);

    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();

    json result;
    ASSERT_NO_THROW(result = agent.processQuery("go", 2));
    EXPECT_TRUE(agent.executed.empty())
        << "no tool may run when a sibling call in the batch is malformed";
    EXPECT_NE(result["result"].get<std::string>().find("LLM error"), std::string::npos);
    // The turn was still recorded — the history write must not be skipped.
    EXPECT_FALSE(agent.history().empty());
}

TEST(NativeToolCallsTest, NeverPolicyIgnoresVolunteeredToolCalls) {
    // llama.cpp --jinja can return tool_calls even with no `tools` in the
    // request. An agent that opted out must stay on the prompt-JSON path.
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"id":"call_x","type":"function","function":{"name":"echo","arguments":"{}"}}])",
        R"("{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"prose wins\"}")"));

    AgentConfig cfg = makeCfg(mock.baseUrl(), kToolCallingModel);
    cfg.nativeToolCalls = NativeToolCalls::Never;
    ToolAgent agent(cfg);
    agent.initForTest();
    json result = agent.processQuery("hi", 2);

    EXPECT_TRUE(agent.executed.empty()) << "opted-out agent must not run a volunteered call";
    EXPECT_EQ(result["result"], "prose wins");
    for (const auto& m : agent.history()) {
        EXPECT_TRUE(m.toolCalls.empty()) << "no tool_calls may enter an opted-out history";
    }
}

TEST(NativeToolCallsTest, NeverPolicySurvivesMalformedVolunteeredToolCalls) {
    // A malformed tool_calls field must not kill a turn whose `content` is a
    // perfectly good prompt-JSON answer.
    bench::MockLlmServer mock;
    mock.pushResponse(toolCallsResponse(
        R"([{"type":"function","function":{"name":"echo"}}])",
        R"("{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"still fine\"}")"));

    AgentConfig cfg = makeCfg(mock.baseUrl(), kNonToolCallingModel);
    cfg.nativeToolCalls = NativeToolCalls::Never;
    ToolAgent agent(cfg);
    agent.initForTest();

    EXPECT_EQ(agent.processQuery("hi", 2)["result"], "still fine");
}

TEST(NativeToolCallsTest, NoEnabledToolsKeepsTheResponseFormatTemplate) {
    // A tool-calling model with nothing to call gets no `tools` array, so the
    // prompt-JSON template must stay — otherwise it has no protocol at all.
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);

    class Bare : public Agent { public: using Agent::Agent; };
    Bare agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.processQuery("hi", 1);

    json body = json::parse(mock.receivedBodies().back());
    EXPECT_FALSE(body.contains("tools"));
    EXPECT_NE(systemPromptOf(body).find("==== RESPONSE FORMAT ===="), std::string::npos);
}

TEST(NativeToolCallsTest, SetHistoryRepairsAnOrphanedToolMessage) {
    bench::MockLlmServer mock;
    mock.pushResponse(kPlainAnswer);
    ToolAgent agent(makeCfg(mock.baseUrl(), kToolCallingModel));
    agent.initForTest();

    Message orphan;
    orphan.role = MessageRole::TOOL;
    orphan.name = "echo";
    orphan.toolCallId = "call_gone";
    orphan.content = "{}";
    agent.setHistory({orphan});

    EXPECT_TRUE(agent.history().empty())
        << "a tool reply with no assistant tool_calls turn must not be replayed";
}

TEST(ToolSchemaTest, ToolNameThatOpenAiWouldRejectThrowsWithGuidance) {
    ToolRegistry registry;
    // What mcp_<server>_<tool> produces when the server name has a dot.
    registry.registerTool("mcp_my.server_do_thing", "MCP tool",
                          [](const json&) -> json { return json{}; });
    try {
        registry.buildOpenAiToolSchemas();
        FAIL() << "expected a throw for an invalid OpenAI function name";
    } catch (const std::invalid_argument& e) {
        const std::string msg = e.what();
        EXPECT_NE(msg.find("mcp_my.server_do_thing"), std::string::npos) << msg;
        EXPECT_NE(msg.find("NativeToolCalls::Never"), std::string::npos) << msg;
    }
}

TEST(ToolSchemaTest, OverlongToolNameIsRejected) {
    ToolRegistry registry;
    registry.registerTool(std::string(65, 'a'), "Long", [](const json&) -> json { return json{}; });
    EXPECT_THROW(registry.buildOpenAiToolSchemas(), std::invalid_argument);
}

TEST(SseToolCallsTest, ParallelCallsInOneDeltaWithoutIndexDoNotMerge) {
    // Servers that omit `index` send the whole array in a single delta. Slotting
    // them all at 0 would concatenate the names and the argument strings.
    SseParser parser([](const std::string&) {});
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":["
        "{\"id\":\"call_a\",\"function\":{\"name\":\"echo\",\"arguments\":\"{\\\"m\\\":1}\"}},"
        "{\"id\":\"call_b\",\"function\":{\"name\":\"add\",\"arguments\":\"{\\\"a\\\":2}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";
    feedByteByByte(parser, sse);

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 2u) << "index-less parallel calls merged into one";
    EXPECT_EQ(calls[0].id, "call_a");
    EXPECT_EQ(calls[0].name, "echo");
    EXPECT_EQ(calls[0].arguments, R"({"m":1})");
    EXPECT_EQ(calls[1].id, "call_b");
    EXPECT_EQ(calls[1].name, "add");
    EXPECT_EQ(calls[1].arguments, R"({"a":2})");
}

TEST(SseToolCallsTest, ServerRepeatingIdAndNameDoesNotDuplicateTheName) {
    // Some servers restate id AND name on every delta for a call. Appending the
    // name blindly would produce "echoecho" and a tool-not-found on every turn.
    SseParser parser([](const std::string&) {});
    const std::string sse =
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\","
        "\"function\":{\"name\":\"echo\",\"arguments\":\"{\\\"m\\\":\"}}]}}]}\n\n"
        "data: {\"choices\":[{\"delta\":{\"tool_calls\":[{\"index\":0,\"id\":\"c1\","
        "\"function\":{\"name\":\"echo\",\"arguments\":\"1}\"}}]}}]}\n\n"
        "data: [DONE]\n\n";
    feedByteByByte(parser, sse);

    auto calls = parser.toolCalls();
    ASSERT_EQ(calls.size(), 1u);
    EXPECT_EQ(calls[0].name, "echo");
    EXPECT_EQ(calls[0].arguments, R"({"m":1})");
}

// ---------------------------------------------------------------------------
// Tool schema builder
// ---------------------------------------------------------------------------

TEST(ToolSchemaTest, EmptyRegistryProducesAnEmptyArray) {
    ToolRegistry registry;
    json schemas = registry.buildOpenAiToolSchemas();
    EXPECT_TRUE(schemas.is_array());
    EXPECT_TRUE(schemas.empty());
}

TEST(ToolSchemaTest, ParameterlessToolGetsAnEmptyPropertiesObject) {
    ToolRegistry registry;
    registry.registerTool("ping", "Ping", [](const json&) -> json { return json{}; });

    json schemas = registry.buildOpenAiToolSchemas();
    ASSERT_EQ(schemas.size(), 1u);
    const auto& params = schemas[0]["function"]["parameters"];
    EXPECT_EQ(params["type"], "object");
    EXPECT_TRUE(params["properties"].is_object());
    EXPECT_TRUE(params["properties"].empty());
    EXPECT_TRUE(params["required"].is_array());
    EXPECT_TRUE(params["required"].empty());
}

TEST(ToolSchemaTest, UnknownParamTypeFallsBackToString) {
    ToolRegistry registry;
    registry.registerTool("t", "T", [](const json&) -> json { return json{}; },
                          {{"x", ToolParamType::UNKNOWN, true, ""}});

    json schemas = registry.buildOpenAiToolSchemas();
    EXPECT_EQ(schemas[0]["function"]["parameters"]["properties"]["x"]["type"], "string");
    // An empty description is omitted rather than sent as "".
    EXPECT_FALSE(schemas[0]["function"]["parameters"]["properties"]["x"].contains("description"));
}

TEST(ToolSchemaTest, AllParamTypesMapToJsonSchemaTypes) {
    ToolRegistry registry;
    registry.registerTool("t", "T", [](const json&) -> json { return json{}; },
                          {{"s", ToolParamType::STRING, true, ""},
                           {"i", ToolParamType::INTEGER, true, ""},
                           {"n", ToolParamType::NUMBER, true, ""},
                           {"b", ToolParamType::BOOLEAN, true, ""},
                           {"a", ToolParamType::ARRAY, true, ""},
                           {"o", ToolParamType::OBJECT, true, ""}});

    const json schemas = registry.buildOpenAiToolSchemas();
    const auto& props = schemas[0]["function"]["parameters"]["properties"];
    EXPECT_EQ(props["s"]["type"], "string");
    EXPECT_EQ(props["i"]["type"], "integer");
    EXPECT_EQ(props["n"]["type"], "number");
    EXPECT_EQ(props["b"]["type"], "boolean");
    EXPECT_EQ(props["a"]["type"], "array");
    EXPECT_EQ(props["o"]["type"], "object");
}
