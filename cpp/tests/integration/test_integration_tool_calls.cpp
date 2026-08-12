// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Native OpenAI tool calling against a LIVE Lemonade server (issue #2794).
//
// A mocked server can only prove we *sent* a tools array; it can never prove
// the server accepts its shape. These tests exist to catch a schema that a real
// OpenAI-compatible backend rejects (the #1655 lesson in CLAUDE.md).
//
// Requires: lemonade-server running at GAIA_CPP_BASE_URL
//           (default http://localhost:13305/api/v1) with a tool-calling model
//           available. Defaults to Gemma-4-E4B-it-GGUF; override with
//           GAIA_CPP_TOOL_MODEL.
//
// Build:
//   cmake -B build -S cpp -DGAIA_BUILD_INTEGRATION_TESTS=ON
//   cmake --build build --config Release
// Run:
//   ctest --test-dir build -C Release -R ToolCalls --output-on-failure

#include <gtest/gtest.h>

#include <gaia/agent.h>
#include <gaia/lemonade_client.h>
#include <gaia/model_registry.h>
#include <gaia/tool_registry.h>
#include <gaia/types.h>

#include <algorithm>
#include <cctype>
#include <string>
#include <vector>

namespace {

std::string toolModel() {
    return gaia::getEnvVar("GAIA_CPP_TOOL_MODEL", "Gemma-4-E4B-it-GGUF");
}

std::string baseUrl() {
    return gaia::getEnvVar("GAIA_CPP_BASE_URL", "http://localhost:13305/api/v1");
}

gaia::AgentConfig toolConfig(int maxSteps = 5) {
    gaia::AgentConfig cfg;
    cfg.baseUrl = baseUrl();
    cfg.modelId = toolModel();
    cfg.maxSteps = maxSteps;
    cfg.silentMode = true;
    cfg.temperature = 0.0;  // deterministic tool selection
    return cfg;
}

std::string toLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

/// Agent exposing one unambiguous tool the model cannot answer without.
class WeatherAgent : public gaia::Agent {
public:
    WeatherAgent() : Agent(toolConfig()) { init(); }

    std::vector<std::string> calledCities;

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "get_weather", "Get the current weather for a city",
            [this](const gaia::json& args) -> gaia::json {
                calledCities.push_back(args.value("city", ""));
                return gaia::json{{"city", args.value("city", "")},
                                  {"temperature_c", 21},
                                  {"conditions", "sunny"}};
            },
            {{"city", gaia::ToolParamType::STRING, true, "City name, e.g. Austin"}});
    }

    std::string getSystemPrompt() const override {
        return "You are a weather assistant. Use the get_weather tool to answer "
               "questions about weather. Never guess the weather yourself.";
    }
};

} // namespace

// ---------------------------------------------------------------------------
// The emitted schema must be ACCEPTED by the server, not merely well-formed
// ---------------------------------------------------------------------------

TEST(ToolCallsIntegrationTest, EmittedToolsSchemaIsAcceptedByLemonade) {
    // Posts the exact schema buildOpenAiToolSchemas() produces and asserts the
    // server returns a 200 with a usable completion. A 400 here means the
    // schema shape is wrong — the failure mode a mocked test cannot see.
    gaia::ToolRegistry registry;
    registry.registerTool(
        "get_weather", "Get the current weather for a city",
        [](const gaia::json&) -> gaia::json { return gaia::json{}; },
        {{"city", gaia::ToolParamType::STRING, true, "City name"},
         {"units", gaia::ToolParamType::STRING, false, "celsius or fahrenheit"}});
    registry.registerTool(
        "list_cities", "List known cities",
        [](const gaia::json&) -> gaia::json { return gaia::json{}; });

    gaia::LemonadeClient client(
        gaia::LemonadeClientConfig{baseUrl(), toolModel(), 8192, false});
    ASSERT_NO_THROW(client.ensureModelLoaded())
        << "Lemonade must be running with " << toolModel() << " available";

    gaia::json body;
    body["model"] = toolModel();
    body["max_tokens"] = 256;
    body["temperature"] = 0.0;
    body["messages"] = gaia::json::array(
        {{{"role", "system"}, {"content", "Use tools when they apply."}},
         {{"role", "user"}, {"content", "What is the weather in Austin?"}}});
    body["tools"] = registry.buildOpenAiToolSchemas();
    body["tool_choice"] = "auto";

    std::string response;
    ASSERT_NO_THROW(response = client.chatCompletions(body))
        << "Server rejected the emitted tools schema:\n" << body["tools"].dump(2);

    gaia::json parsed = gaia::json::parse(response);
    ASSERT_TRUE(parsed.contains("choices")) << response.substr(0, 600);
    ASSERT_FALSE(parsed["choices"].empty()) << response.substr(0, 600);
    ASSERT_TRUE(parsed["choices"][0].contains("message")) << response.substr(0, 600);
}

TEST(ToolCallsIntegrationTest, ServerReturnsParseableToolCallsForTheSchema) {
    gaia::ToolRegistry registry;
    registry.registerTool(
        "get_weather", "Get the current weather for a city",
        [](const gaia::json&) -> gaia::json { return gaia::json{}; },
        {{"city", gaia::ToolParamType::STRING, true, "City name, e.g. Austin"}});

    gaia::LemonadeClient client(
        gaia::LemonadeClientConfig{baseUrl(), toolModel(), 8192, false});
    ASSERT_NO_THROW(client.ensureModelLoaded());

    gaia::json body;
    body["model"] = toolModel();
    body["max_tokens"] = 256;
    body["temperature"] = 0.0;
    body["messages"] = gaia::json::array(
        {{{"role", "system"},
          {"content", "You are a weather assistant. Always call get_weather; never guess."}},
         {{"role", "user"}, {"content", "What is the weather in Austin?"}}});
    body["tools"] = registry.buildOpenAiToolSchemas();
    body["tool_choice"] = "required";

    gaia::json parsed = gaia::json::parse(client.chatCompletions(body));
    const auto& message = parsed["choices"][0]["message"];
    ASSERT_TRUE(message.contains("tool_calls"))
        << "Model did not emit native tool_calls for the required tool_choice:\n"
        << message.dump(2);
    ASSERT_FALSE(message["tool_calls"].empty());

    // Every entry must survive our strict parser — no silent skipping.
    for (const auto& entry : message["tool_calls"]) {
        gaia::ToolCall tc;
        ASSERT_NO_THROW(tc = gaia::ToolCall::fromJson(entry)) << entry.dump(2);
        EXPECT_EQ(tc.name, "get_weather");
        gaia::json args;
        ASSERT_NO_THROW(args = tc.parsedArgs()) << tc.arguments;
        EXPECT_TRUE(args.contains("city")) << args.dump();
    }
}

// ---------------------------------------------------------------------------
// The full agent loop over native tool calls
// ---------------------------------------------------------------------------

TEST(ToolCallsIntegrationTest, AgentLoopExecutesANativeToolCallEndToEnd) {
    WeatherAgent agent;
    ASSERT_TRUE(agent.config().useNativeToolCalls())
        << "Expected the native path to be active for " << toolModel();

    auto result = agent.processQuery("What is the weather in Austin right now?");

    ASSERT_FALSE(agent.calledCities.empty())
        << "The model never invoked get_weather. Answer was: " << result.value("result", "");
    EXPECT_NE(toLower(agent.calledCities[0]).find("austin"), std::string::npos)
        << "Called with: " << agent.calledCities[0];

    const std::string answer = result.value("result", "");
    EXPECT_FALSE(answer.empty());
    // The tool result must have reached the model — 21C / sunny came only from it.
    EXPECT_TRUE(answer.find("21") != std::string::npos ||
                toLower(answer).find("sunny") != std::string::npos)
        << "Final answer did not reflect the tool result: " << answer;
}

TEST(ToolCallsIntegrationTest, StreamingAgentLoopExecutesANativeToolCall) {
    // The streaming path reassembles tool_calls from SSE deltas — a completely
    // separate code path from the non-streaming parse above.
    class StreamingWeatherAgent : public WeatherAgent {
    public:
        StreamingWeatherAgent() {
            gaia::AgentConfig cfg = config();
            cfg.streaming = true;
            setConfig(cfg);
        }
    };

    StreamingWeatherAgent agent;
    auto result = agent.processQuery("What is the weather in Austin right now?");

    ASSERT_FALSE(agent.calledCities.empty())
        << "Streaming path never surfaced a tool call. Answer: "
        << result.value("result", "");
    EXPECT_NE(toLower(agent.calledCities[0]).find("austin"), std::string::npos);
}

// ---------------------------------------------------------------------------
// The prompt-JSON fallback still works against the same live server
// ---------------------------------------------------------------------------

TEST(ToolCallsIntegrationTest, PromptJsonPathStillWorksWhenNativeIsDisabled) {
    class LegacyWeatherAgent : public WeatherAgent {
    public:
        LegacyWeatherAgent() {
            gaia::AgentConfig cfg = config();
            cfg.nativeToolCalls = gaia::NativeToolCalls::Never;
            setConfig(cfg);
        }
    };

    LegacyWeatherAgent agent;
    ASSERT_FALSE(agent.config().useNativeToolCalls());
    EXPECT_NE(agent.systemPrompt().find("==== RESPONSE FORMAT ===="), std::string::npos);

    auto result = agent.processQuery("What is the weather in Austin right now?");
    ASSERT_FALSE(agent.calledCities.empty())
        << "Prompt-JSON fallback stopped working. Answer: " << result.value("result", "");
}
