// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/tool_registry.h>

using namespace gaia;

// Helper to create ToolParameter (C++17 compatible, no designated initializers)
static ToolParameter makeParam(const std::string& name, ToolParamType type,
                               bool required, const std::string& desc = "") {
    ToolParameter p;
    p.name = name;
    p.type = type;
    p.required = required;
    p.description = desc;
    return p;
}

class ToolRegistryTest : public ::testing::Test {
protected:
    ToolRegistry registry;

    void SetUp() override {
        registry.clear();
    }

    // Helper: register a simple echo tool
    void registerEchoTool() {
        registry.registerTool("echo", "Echo back the input",
            [](const json& args) -> json {
                return json{{"echoed", args.value("message", "")}};
            },
            {makeParam("message", ToolParamType::STRING, true, "Message to echo")}
        );
    }
};

TEST_F(ToolRegistryTest, RegisterAndFind) {
    registerEchoTool();

    EXPECT_TRUE(registry.hasTool("echo"));
    EXPECT_EQ(registry.size(), 1u);

    const ToolInfo* info = registry.findTool("echo");
    ASSERT_NE(info, nullptr);
    EXPECT_EQ(info->name, "echo");
    EXPECT_EQ(info->description, "Echo back the input");
    EXPECT_EQ(info->parameters.size(), 1u);
    EXPECT_EQ(info->parameters[0].name, "message");
    EXPECT_EQ(info->parameters[0].type, ToolParamType::STRING);
    EXPECT_TRUE(info->parameters[0].required);
}

TEST_F(ToolRegistryTest, FindNonexistent) {
    EXPECT_EQ(registry.findTool("nonexistent"), nullptr);
    EXPECT_FALSE(registry.hasTool("nonexistent"));
}

TEST_F(ToolRegistryTest, DuplicateRegistration) {
    registerEchoTool();
    EXPECT_THROW(registerEchoTool(), std::runtime_error);
}

TEST_F(ToolRegistryTest, RemoveTool) {
    registerEchoTool();
    EXPECT_TRUE(registry.removeTool("echo"));
    EXPECT_FALSE(registry.hasTool("echo"));
    EXPECT_EQ(registry.size(), 0u);

    // Remove nonexistent
    EXPECT_FALSE(registry.removeTool("nonexistent"));
}

TEST_F(ToolRegistryTest, Clear) {
    registerEchoTool();
    registry.registerTool("other", "Another tool",
        [](const json&) -> json { return json{}; });

    EXPECT_EQ(registry.size(), 2u);
    registry.clear();
    EXPECT_EQ(registry.size(), 0u);
}

TEST_F(ToolRegistryTest, ExecuteTool) {
    registerEchoTool();

    json result = registry.executeTool("echo", {{"message", "hello"}});
    EXPECT_EQ(result["echoed"], "hello");
}

TEST_F(ToolRegistryTest, ExecuteToolNotFound) {
    json result = registry.executeTool("nonexistent", json::object());
    EXPECT_EQ(result["status"], "error");
    EXPECT_TRUE(result["error"].get<std::string>().find("not found") != std::string::npos);
}

TEST_F(ToolRegistryTest, ExecuteToolException) {
    registry.registerTool("failing", "Always fails",
        [](const json&) -> json {
            throw std::runtime_error("intentional failure");
        });

    json result = registry.executeTool("failing", json::object());
    EXPECT_EQ(result["status"], "error");
    EXPECT_TRUE(result["error"].get<std::string>().find("intentional failure") != std::string::npos);
}

TEST_F(ToolRegistryTest, ResolveNameSuffix) {
    // Register an MCP-style tool name
    registry.registerTool("mcp_windows_Shell", "Windows shell",
        [](const json&) -> json { return json{}; });

    // Resolve unprefixed name (common LLM mistake)
    EXPECT_EQ(registry.resolveName("Shell"), "mcp_windows_Shell");
}

TEST_F(ToolRegistryTest, ResolveNameCaseInsensitive) {
    registry.registerTool("mcp_windows_Shell", "Windows shell",
        [](const json&) -> json { return json{}; });

    EXPECT_EQ(registry.resolveName("MCP_WINDOWS_SHELL"), "mcp_windows_Shell");
}

TEST_F(ToolRegistryTest, ResolveNameAmbiguous) {
    registry.registerTool("mcp_server1_Shell", "Shell 1",
        [](const json&) -> json { return json{}; });
    registry.registerTool("mcp_server2_Shell", "Shell 2",
        [](const json&) -> json { return json{}; });

    // Multiple matches - cannot resolve
    EXPECT_EQ(registry.resolveName("Shell"), "");
}

TEST_F(ToolRegistryTest, FormatForPrompt) {
    registry.registerTool("echo", "Echo back the input",
        [](const json&) -> json { return json{}; },
        {makeParam("message", ToolParamType::STRING, true, "msg")});

    registry.registerTool("add", "Add two numbers",
        [](const json&) -> json { return json{}; },
        {
            makeParam("a", ToolParamType::NUMBER, true, "first"),
            makeParam("b", ToolParamType::NUMBER, false, "second")
        });

    std::string prompt = registry.formatForPrompt();
    EXPECT_TRUE(prompt.find("echo(message: string)") != std::string::npos);
    EXPECT_TRUE(prompt.find("add(a: number, b?: number)") != std::string::npos);
}

TEST_F(ToolRegistryTest, AllTools) {
    registerEchoTool();
    const auto& all = registry.allTools();
    EXPECT_EQ(all.size(), 1u);
    EXPECT_TRUE(all.count("echo") > 0);
}

TEST_F(ToolRegistryTest, RegisterWithToolInfo) {
    ToolInfo info;
    info.name = "custom";
    info.description = "Custom tool";
    info.callback = [](const json&) -> json { return json{{"ok", true}}; };
    info.atomic = true;

    registry.registerTool(std::move(info));

    const ToolInfo* found = registry.findTool("custom");
    ASSERT_NE(found, nullptr);
    EXPECT_TRUE(found->atomic);

    json result = registry.executeTool("custom", json::object());
    EXPECT_EQ(result["ok"], true);
}
