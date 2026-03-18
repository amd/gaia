// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/tool_registry.h>
#include <gaia/security.h>

#include <filesystem>
#include <string>

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

// ---- Security / Policy Tests ----

TEST_F(ToolRegistryTest, DefaultAllowPolicy) {
    // ALLOW policy (default) — tool executes normally (backwards compat)
    registerEchoTool();
    json result = registry.executeTool("echo", {{"message", "hi"}});
    EXPECT_EQ(result["echoed"], "hi");
}

TEST_F(ToolRegistryTest, ExecuteToolDenyPolicy) {
    bool callbackInvoked = false;
    ToolInfo info;
    info.name = "secret";
    info.description = "Secret tool";
    info.policy = ToolPolicy::DENY;
    info.callback = [&](const json&) -> json {
        callbackInvoked = true;
        return json{{"ok", true}};
    };
    registry.registerTool(std::move(info));

    json result = registry.executeTool("secret", json::object());
    EXPECT_EQ(result["status"], "error");
    EXPECT_FALSE(callbackInvoked);
}

TEST_F(ToolRegistryTest, ExecuteToolConfirmPolicy_Approved) {
    registry.setConfirmCallback([](const std::string&, const json&) {
        return ToolConfirmResult::ALLOW_ONCE;
    });

    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    json result = registry.executeTool("guarded", json::object());
    EXPECT_EQ(result["ran"], true);
}

TEST_F(ToolRegistryTest, ExecuteToolConfirmPolicy_AlwaysAllow) {
    // Use a temp dir so the test doesn't touch the real store
    auto store = std::make_shared<AllowedToolsStore>(
        std::filesystem::temp_directory_path().string() + "/gaia_test_always_allow");
    store->clearAll();
    registry.setAllowedToolsStore(store);

    int confirmCount = 0;
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++confirmCount;
        return ToolConfirmResult::ALWAYS_ALLOW;
    });

    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    // First call: callback invoked, returns ALWAYS_ALLOW
    json r1 = registry.executeTool("guarded", json::object());
    EXPECT_EQ(r1["ran"], true);
    EXPECT_EQ(confirmCount, 1);
    EXPECT_TRUE(store->isAlwaysAllowed("guarded"));

    // Second call: already in store, callback NOT invoked again
    json r2 = registry.executeTool("guarded", json::object());
    EXPECT_EQ(r2["ran"], true);
    EXPECT_EQ(confirmCount, 1);

    store->clearAll();
}

TEST_F(ToolRegistryTest, ExecuteToolConfirmPolicy_Denied) {
    registry.setConfirmCallback([](const std::string&, const json&) {
        return ToolConfirmResult::DENY;
    });

    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    json result = registry.executeTool("guarded", json::object());
    EXPECT_EQ(result["status"], "error");
    EXPECT_TRUE(result["error"].get<std::string>().find("denied") != std::string::npos);
}

TEST_F(ToolRegistryTest, ExecuteToolConfirmPolicy_NoCallback) {
    // No confirm callback set — fail-closed
    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    json result = registry.executeTool("guarded", json::object());
    EXPECT_EQ(result["status"], "error");
}

TEST_F(ToolRegistryTest, ExecuteToolConfirmPolicy_AlreadyAllowed) {
    auto store = std::make_shared<AllowedToolsStore>(
        std::filesystem::temp_directory_path().string() + "/gaia_test_pre_allowed");
    store->clearAll();
    store->addAlwaysAllowed("guarded");
    registry.setAllowedToolsStore(store);

    int confirmCount = 0;
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++confirmCount;
        return ToolConfirmResult::DENY;
    });

    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    // Already in store — confirm callback must NOT be called
    json result = registry.executeTool("guarded", json::object());
    EXPECT_EQ(result["ran"], true);
    EXPECT_EQ(confirmCount, 0);

    store->clearAll();
}

TEST_F(ToolRegistryTest, ExecuteToolValidateArgs_Sanitized) {
    bool callbackGotSanitized = false;
    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::ALLOW;
    info.validateArgs = [](const std::string&, const json& args) -> json {
        json sanitized = args;
        sanitized["cleaned"] = true;
        return sanitized;
    };
    info.callback = [&](const json& a) -> json {
        callbackGotSanitized = a.value("cleaned", false);
        return json{{"ok", true}};
    };
    registry.registerTool(std::move(info));

    json result = registry.executeTool("guarded", json::object());
    EXPECT_EQ(result["ok"], true);
    EXPECT_TRUE(callbackGotSanitized);
}

TEST_F(ToolRegistryTest, ExecuteToolValidateArgs_Rejected) {
    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::ALLOW;
    info.validateArgs = [](const std::string&, const json&) -> json {
        throw std::invalid_argument("bad path");
    };
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    json result = registry.executeTool("guarded", json::object());
    EXPECT_EQ(result["status"], "error");
    EXPECT_TRUE(result["error"].get<std::string>().find("bad path") != std::string::npos);
}

TEST_F(ToolRegistryTest, DefaultPolicySetterGetter) {
    EXPECT_EQ(registry.defaultPolicy(), ToolPolicy::ALLOW);
    registry.setDefaultPolicy(ToolPolicy::CONFIRM);
    EXPECT_EQ(registry.defaultPolicy(), ToolPolicy::CONFIRM);
}

TEST_F(ToolRegistryTest, RegisterToolConvenienceDefaultsToAllow) {
    // No setDefaultPolicy call — initial default is ALLOW
    registry.registerTool("t", "desc", [](const json&) -> json { return {}; });
    const ToolInfo* info = registry.findTool("t");
    ASSERT_NE(info, nullptr);
    EXPECT_EQ(info->policy, ToolPolicy::ALLOW);
}

TEST_F(ToolRegistryTest, RegisterToolConvenienceInheritsDefaultPolicy) {
    registry.setDefaultPolicy(ToolPolicy::CONFIRM);
    registry.registerTool("my_tool", "A tool", [](const json&) -> json { return {}; });
    const ToolInfo* info = registry.findTool("my_tool");
    ASSERT_NE(info, nullptr);
    EXPECT_EQ(info->policy, ToolPolicy::CONFIRM);
}

TEST_F(ToolRegistryTest, RegisterToolConvenienceExplicitOverridesDefault) {
    registry.setDefaultPolicy(ToolPolicy::CONFIRM);
    registry.registerTool("safe_tool", "A safe tool", [](const json&) -> json { return {}; },
                          {}, false, ToolPolicy::ALLOW);
    const ToolInfo* info = registry.findTool("safe_tool");
    ASSERT_NE(info, nullptr);
    EXPECT_EQ(info->policy, ToolPolicy::ALLOW);
}

TEST_F(ToolRegistryTest, ExecuteToolConfirmPolicy_AlwaysAllow_NoStore) {
    // No store set — ALWAYS_ALLOW still executes the tool, but the permission
    // is not persisted, so the confirm callback fires on every call.
    int confirmCount = 0;
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++confirmCount;
        return ToolConfirmResult::ALWAYS_ALLOW;
    });

    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    json r1 = registry.executeTool("guarded", json::object());
    EXPECT_EQ(r1["ran"], true);
    EXPECT_EQ(confirmCount, 1);

    // No store — callback fires again on the second call
    json r2 = registry.executeTool("guarded", json::object());
    EXPECT_EQ(r2["ran"], true);
    EXPECT_EQ(confirmCount, 2);
}

TEST_F(ToolRegistryTest, RegisterToolConvenienceWithPolicy) {
    registry.registerTool("flush_dns", "Clear DNS cache",
        [](const json&) -> json { return json{{"ok", true}}; },
        {}, false, ToolPolicy::CONFIRM);

    const ToolInfo* info = registry.findTool("flush_dns");
    ASSERT_NE(info, nullptr);
    EXPECT_EQ(info->policy, ToolPolicy::CONFIRM);
}

TEST_F(ToolRegistryTest, ExecuteToolValidateAndConfirm) {
    // Validation runs BEFORE confirmation; confirm callback receives sanitized args
    json argsSeenByConfirm;
    registry.setConfirmCallback([&](const std::string&, const json& a) {
        argsSeenByConfirm = a;
        return ToolConfirmResult::ALLOW_ONCE;
    });

    ToolInfo info;
    info.name = "guarded";
    info.description = "Guarded tool";
    info.policy = ToolPolicy::CONFIRM;
    info.validateArgs = [](const std::string&, const json&) -> json {
        return json{{"sanitized", true}};
    };
    info.callback = [](const json&) -> json { return json{{"ran", true}}; };
    registry.registerTool(std::move(info));

    registry.executeTool("guarded", json::object());
    EXPECT_TRUE(argsSeenByConfirm.value("sanitized", false));
}
