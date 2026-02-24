// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/agent.h>

using namespace gaia;

// Helper to create ToolParameter (C++17 compatible)
static ToolParameter makeParam(const std::string& name, ToolParamType type,
                               bool required, const std::string& desc = "") {
    ToolParameter p;
    p.name = name;
    p.type = type;
    p.required = required;
    p.description = desc;
    return p;
}

// ---- Test Agent subclass with mock tools ----

class MockAgent : public Agent {
public:
    explicit MockAgent(const AgentConfig& config = {}) : Agent(config) {
        init();  // Must call init() to trigger registerTools() after construction
    }

    // Track tool calls for verification
    std::vector<std::pair<std::string, json>> toolCallLog;

protected:
    void registerTools() override {
        tools().registerTool("echo", "Echo back the message",
            [this](const json& args) -> json {
                toolCallLog.emplace_back("echo", args);
                return json{{"echoed", args.value("message", "")}};
            },
            {makeParam("message", ToolParamType::STRING, true, "Message")});

        tools().registerTool("add", "Add two numbers",
            [this](const json& args) -> json {
                toolCallLog.emplace_back("add", args);
                int a = args.value("a", 0);
                int b = args.value("b", 0);
                return json{{"sum", a + b}};
            },
            {
                makeParam("a", ToolParamType::INTEGER, true, "First number"),
                makeParam("b", ToolParamType::INTEGER, true, "Second number")
            });

        tools().registerTool("fail", "Always fails",
            [this](const json& args) -> json {
                toolCallLog.emplace_back("fail", args);
                return json{{"status", "error"}, {"error", "Intentional failure"}};
            });
    }

    std::string getSystemPrompt() const override {
        return "You are a test agent with echo, add, and fail tools.";
    }

public:
    // Expose tools for testing
    ToolRegistry& tools() {
        return toolRegistry();
    }
};

// ---- Tests ----

TEST(AgentTest, Construction) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    // Tools should be registered
    EXPECT_TRUE(agent.tools().hasTool("echo"));
    EXPECT_TRUE(agent.tools().hasTool("add"));
    EXPECT_TRUE(agent.tools().hasTool("fail"));
    EXPECT_EQ(agent.tools().size(), 3u);
}

TEST(AgentTest, SystemPromptComposition) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    std::string prompt = agent.systemPrompt();

    // Should contain the custom prompt
    EXPECT_TRUE(prompt.find("test agent") != std::string::npos);

    // Should contain tool descriptions
    EXPECT_TRUE(prompt.find("echo") != std::string::npos);
    EXPECT_TRUE(prompt.find("add") != std::string::npos);

    // Should contain response format
    EXPECT_TRUE(prompt.find("RESPONSE FORMAT") != std::string::npos);
}

TEST(AgentTest, DirectToolExecution) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    // Test direct tool execution through registry
    json result = agent.tools().executeTool("echo", {{"message", "hello"}});
    EXPECT_EQ(result["echoed"], "hello");

    result = agent.tools().executeTool("add", {{"a", 3}, {"b", 5}});
    EXPECT_EQ(result["sum"], 8);
}

TEST(AgentTest, ToolExecutionError) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    json result = agent.tools().executeTool("fail", json::object());
    EXPECT_EQ(result["status"], "error");
}

TEST(AgentTest, ToolNotFound) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    json result = agent.tools().executeTool("nonexistent", json::object());
    EXPECT_EQ(result["status"], "error");
    EXPECT_TRUE(result["error"].get<std::string>().find("not found") != std::string::npos);
}

TEST(AgentTest, CustomOutputHandler) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    // Set a custom silent console
    agent.setOutputHandler(std::make_unique<SilentConsole>(true));

    // Should still work
    EXPECT_TRUE(agent.tools().hasTool("echo"));
}

TEST(AgentTest, RebuildSystemPrompt) {
    AgentConfig config;
    config.silentMode = true;
    MockAgent agent(config);

    std::string prompt1 = agent.systemPrompt();

    // Add a new tool
    agent.tools().registerTool("newTool", "A new tool",
        [](const json&) -> json { return json{}; });

    // Prompt should be stale
    agent.rebuildSystemPrompt();
    std::string prompt2 = agent.systemPrompt();

    // New prompt should contain the new tool
    EXPECT_TRUE(prompt2.find("newTool") != std::string::npos);
    EXPECT_TRUE(prompt2.size() > prompt1.size());
}

// Note: processQuery tests require an LLM server running,
// so they are not included here. Use integration tests for full loop testing.
// The tests above verify the components independently.
