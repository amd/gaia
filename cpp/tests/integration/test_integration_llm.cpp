// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// C++ GAIA agent integration tests.
// Requires: lemonade-server running at GAIA_CPP_BASE_URL (default: http://localhost:8000/api/v1)
//           with GAIA_CPP_TEST_MODEL (default: Qwen3-4B-GGUF) loaded.
//
// Build:
//   cmake -B build -S cpp -DGAIA_BUILD_INTEGRATION_TESTS=ON
//   cmake --build build --config Release
//
// Run:
//   GAIA_CPP_TEST_MODEL=Qwen3-4B-GGUF ctest --test-dir build -C Release --output-on-failure

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/types.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <string>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static std::string testModel() {
    const char* env = std::getenv("GAIA_CPP_TEST_MODEL");
    return env ? std::string(env) : "Qwen3-4B-GGUF";
}

static std::string testBaseUrl() {
    const char* env = std::getenv("GAIA_CPP_BASE_URL");
    return env ? std::string(env) : "http://localhost:8000/api/v1";
}

static std::string toLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return std::tolower(c); });
    return s;
}

// Base config used by all integration agents
static gaia::AgentConfig baseConfig(int maxSteps = 5) {
    gaia::AgentConfig cfg;
    cfg.baseUrl    = testBaseUrl();
    cfg.modelId    = testModel();
    cfg.maxSteps   = maxSteps;
    cfg.silentMode = true;
    return cfg;
}

// ---------------------------------------------------------------------------
// Test 1: Basic chat — LLM responds without tools
// ---------------------------------------------------------------------------

class BasicChatAgent : public gaia::Agent {
public:
    BasicChatAgent() : Agent(baseConfig(3)) { init(); }
protected:
    std::string getSystemPrompt() const override {
        return "You are a minimal test assistant. Answer exactly as instructed.";
    }
};

TEST(LLMIntegrationTest, BasicChat) {
    BasicChatAgent agent;
    auto result = agent.processQuery("Reply with the single word: pong");

    ASSERT_TRUE(result.contains("result")) << "Result key missing";
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty()) << "Expected non-empty response";
    EXPECT_NE(toLower(answer).find("pong"), std::string::npos)
        << "Expected 'pong' in response, got: " << answer;
}

// ---------------------------------------------------------------------------
// Test 2: Tool calling — LLM must call a registered tool
// ---------------------------------------------------------------------------

class EchoToolAgent : public gaia::Agent {
public:
    bool toolWasCalled = false;

    EchoToolAgent() : Agent(baseConfig(5)) { init(); }

protected:
    std::string getSystemPrompt() const override {
        return "You are a test assistant. When asked to echo text, you MUST call the echo_text tool.";
    }

    void registerTools() override {
        toolRegistry().registerTool(
            "echo_text",
            "Echo back the provided text exactly as given.",
            [this](const gaia::json& args) -> gaia::json {
                toolWasCalled = true;
                return {{"echoed", args.value("text", "")}};
            },
            {{"text", gaia::ToolParamType::STRING, /*required=*/true, "Text to echo back"}}
        );
    }
};

TEST(LLMIntegrationTest, ToolCalling) {
    EchoToolAgent agent;
    auto result = agent.processQuery("Please echo the text: cpp_integration_marker");

    ASSERT_TRUE(result.contains("result"));
    EXPECT_TRUE(agent.toolWasCalled)
        << "Expected echo_text tool to be called";

    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty());
    EXPECT_NE(answer.find("cpp_integration_marker"), std::string::npos)
        << "Expected echo marker in final answer, got: " << answer;
}

// ---------------------------------------------------------------------------
// Test 3: Multi-tool plan — agent chains two add calls
// ---------------------------------------------------------------------------

class MathAgent : public gaia::Agent {
public:
    int callCount = 0;

    MathAgent() : Agent(baseConfig(10)) { init(); }

protected:
    std::string getSystemPrompt() const override {
        return "You are a math assistant. Use the add tool for all arithmetic. "
               "Do not compute answers yourself.";
    }

    void registerTools() override {
        toolRegistry().registerTool(
            "add",
            "Add two integers and return their sum.",
            [this](const gaia::json& args) -> gaia::json {
                ++callCount;
                int a = args.value("a", 0);
                int b = args.value("b", 0);
                return {{"sum", a + b}};
            },
            {
                {"a", gaia::ToolParamType::INTEGER, /*required=*/true, "First integer"},
                {"b", gaia::ToolParamType::INTEGER, /*required=*/true, "Second integer"}
            }
        );
    }
};

TEST(LLMIntegrationTest, SingleToolArithmetic) {
    MathAgent agent;
    auto result = agent.processQuery("What is 6 + 7?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty());
    EXPECT_NE(answer.find("13"), std::string::npos)
        << "Expected '13' (6+7) in answer, got: " << answer;
    EXPECT_GT(agent.callCount, 0) << "Expected add tool to be called at least once";
}

// ---------------------------------------------------------------------------
// Test 4: Custom system prompt controls agent persona
// ---------------------------------------------------------------------------

class FormalAgent : public gaia::Agent {
public:
    FormalAgent() : Agent(baseConfig(3)) { init(); }
protected:
    std::string getSystemPrompt() const override {
        return "You are a formal Victorian-era butler. Always begin responses with 'Indeed, '.";
    }
};

TEST(LLMIntegrationTest, CustomSystemPrompt) {
    FormalAgent agent;
    auto result = agent.processQuery("What is 2 + 2?");

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty());
    // The model should follow the persona instruction
    EXPECT_NE(toLower(answer).find("indeed"), std::string::npos)
        << "Expected persona prefix 'Indeed' in response, got: " << answer;
}

// ---------------------------------------------------------------------------
// Test 5: AgentConfig - silentMode, maxSteps enforced
// ---------------------------------------------------------------------------

TEST(LLMIntegrationTest, MaxStepsEnforced) {
    // Set maxSteps=1 so the agent cannot complete a multi-step plan
    gaia::AgentConfig cfg = baseConfig(1);
    cfg.silentMode = true;

    class TinyAgent : public gaia::Agent {
    public:
        explicit TinyAgent(const gaia::AgentConfig& c) : Agent(c) { init(); }
    protected:
        std::string getSystemPrompt() const override { return "Answer questions."; }
    };

    TinyAgent agent(cfg);
    auto result = agent.processQuery("What is the capital of France?");

    ASSERT_TRUE(result.contains("steps_taken"));
    EXPECT_LE(result["steps_taken"].get<int>(), 1)
        << "Expected at most 1 step taken";
}
