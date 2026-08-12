// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/mcp_client.h>
#include <gaia/security.h>
#include <gaia/tool_registry.h>

#include <filesystem>

#include "support/mock_llm_server.h"

using namespace gaia;

// ---- MCPToolSchema Tests ----

TEST(MCPClientTest, MCPToolSchemaToToolInfo) {
    MCPToolSchema schema;
    schema.name = "Shell";
    schema.description = "Execute a shell command";
    schema.inputSchema = {
        {"type", "object"},
        {"properties", {
            {"command", {{"type", "string"}, {"description", "Command to run"}}},
            {"timeout", {{"type", "integer"}, {"description", "Timeout in seconds"}}}
        }},
        {"required", {"command"}}
    };

    ToolInfo info = schema.toToolInfo("windows");

    EXPECT_EQ(info.name, "mcp_windows_Shell");
    EXPECT_EQ(info.description, "[MCP:windows] Execute a shell command");
    EXPECT_TRUE(info.atomic);
    EXPECT_TRUE(info.mcpServer.has_value());
    EXPECT_EQ(info.mcpServer.value(), "windows");
    EXPECT_TRUE(info.mcpToolName.has_value());
    EXPECT_EQ(info.mcpToolName.value(), "Shell");

    // Check parameters
    EXPECT_EQ(info.parameters.size(), 2u);

    // Find command param
    bool foundCommand = false;
    bool foundTimeout = false;
    for (const auto& p : info.parameters) {
        if (p.name == "command") {
            foundCommand = true;
            EXPECT_EQ(p.type, ToolParamType::STRING);
            EXPECT_TRUE(p.required);
        }
        if (p.name == "timeout") {
            foundTimeout = true;
            EXPECT_EQ(p.type, ToolParamType::INTEGER);
            EXPECT_FALSE(p.required);
        }
    }
    EXPECT_TRUE(foundCommand);
    EXPECT_TRUE(foundTimeout);
}

TEST(MCPClientTest, MCPToolSchemaEmptySchema) {
    MCPToolSchema schema;
    schema.name = "simple";
    schema.description = "A simple tool";
    schema.inputSchema = json::object();

    ToolInfo info = schema.toToolInfo("test");
    EXPECT_EQ(info.name, "mcp_test_simple");
    EXPECT_TRUE(info.parameters.empty());
}

// ---- StdioTransport Tests ----

TEST(MCPClientTest, StdioTransportConstruction) {
    // Just verify construction doesn't crash
    StdioTransport transport("echo hello", 10, false);
    EXPECT_FALSE(transport.isConnected());
}

TEST(MCPClientTest, StdioTransportWithArgs) {
    StdioTransport transport("echo", {"hello", "world"}, 10, false);
    EXPECT_FALSE(transport.isConnected());
}

TEST(MCPClientTest, StdioTransportSendWithoutConnect) {
    StdioTransport transport("echo hello", 10, false);
    EXPECT_THROW(transport.sendRequest("test"), std::runtime_error);
}

// ---- MCPClient Tests ----

TEST(MCPClientTest, MCPClientFromConfig) {
    json config = {
        {"command", "echo"},
        {"args", {"hello"}}
    };

    // Just verify construction
    MCPClient client = MCPClient::fromConfig("test", config, 10, false);
    EXPECT_EQ(client.name(), "test");
    EXPECT_FALSE(client.isConnected());
}

TEST(MCPClientTest, MCPClientFromConfigMissingCommand) {
    json config = {{"args", {"hello"}}};
    EXPECT_THROW(MCPClient::fromConfig("test", config), std::invalid_argument);
}

TEST(MCPClientTest, MCPClientFromCommand) {
    MCPClient client = MCPClient::fromCommand("test", "echo hello", 10, false);
    EXPECT_EQ(client.name(), "test");
    EXPECT_FALSE(client.isConnected());
}

TEST(MCPClientTest, MCPClientCallToolWithoutConnect) {
    MCPClient client = MCPClient::fromCommand("test", "echo hello");
    EXPECT_THROW(client.callTool("test", json::object()), std::runtime_error);
}

TEST(MCPClientTest, MCPClientDisconnectSafe) {
    MCPClient client = MCPClient::fromCommand("test", "echo hello");
    // Disconnect when not connected should not crash
    client.disconnect();
    EXPECT_FALSE(client.isConnected());
}

// ---- JSON-RPC Protocol Tests ----

TEST(MCPClientTest, JsonRpcRequestFormat) {
    // Verify the JSON-RPC 2.0 format we send
    json request = {
        {"jsonrpc", "2.0"},
        {"id", 0},
        {"method", "initialize"},
        {"params", {
            {"protocolVersion", "1.0.0"},
            {"clientInfo", {
                {"name", "GAIA C++ MCP Client"},
                {"version", "0.1.0"}
            }},
            {"capabilities", json::object()}
        }}
    };

    EXPECT_EQ(request["jsonrpc"], "2.0");
    EXPECT_EQ(request["method"], "initialize");
    EXPECT_TRUE(request["params"].is_object());
}

TEST(MCPClientTest, JsonRpcResponseParsing) {
    // Simulate a successful initialize response
    json response = {
        {"jsonrpc", "2.0"},
        {"id", 0},
        {"result", {
            {"protocolVersion", "1.0.0"},
            {"serverInfo", {
                {"name", "Windows MCP"},
                {"version", "1.0.0"}
            }}
        }}
    };

    EXPECT_TRUE(response.contains("result"));
    EXPECT_FALSE(response.contains("error"));
    EXPECT_EQ(response["result"]["serverInfo"]["name"], "Windows MCP");
}

TEST(MCPClientTest, JsonRpcErrorParsing) {
    // Simulate an error response
    json response = {
        {"jsonrpc", "2.0"},
        {"id", 0},
        {"error", {
            {"code", -32602},
            {"message", "Invalid params"}
        }}
    };

    EXPECT_TRUE(response.contains("error"));
    EXPECT_EQ(response["error"]["code"], -32602);
    EXPECT_EQ(response["error"]["message"], "Invalid params");
}

TEST(MCPClientTest, ToolsListResponseParsing) {
    // Simulate a tools/list response
    json response = {
        {"jsonrpc", "2.0"},
        {"id", 1},
        {"result", {
            {"tools", {
                {
                    {"name", "Shell"},
                    {"description", "Execute shell command"},
                    {"inputSchema", {
                        {"type", "object"},
                        {"properties", {
                            {"command", {{"type", "string"}}}
                        }},
                        {"required", {"command"}}
                    }}
                },
                {
                    {"name", "Wait"},
                    {"description", "Wait for duration"},
                    {"inputSchema", {
                        {"type", "object"},
                        {"properties", {
                            {"duration", {{"type", "number"}}}
                        }}
                    }}
                }
            }}
        }}
    };

    auto toolsData = response["result"]["tools"];
    EXPECT_EQ(toolsData.size(), 2u);

    // Parse into MCPToolSchema
    std::vector<MCPToolSchema> tools;
    for (const auto& t : toolsData) {
        MCPToolSchema schema;
        schema.name = t["name"].get<std::string>();
        schema.description = t.value("description", "");
        schema.inputSchema = t.value("inputSchema", json::object());
        tools.push_back(schema);
    }

    EXPECT_EQ(tools[0].name, "Shell");
    EXPECT_EQ(tools[1].name, "Wait");
}

// ---------------------------------------------------------------------------
// MCP confirmation gate (CWE-862)
//
// MCP tool names are server-chosen, so no static allowlist can gate them.
// mcpToolRequiresConfirmation() must fail closed: exempt a tool ONLY when the
// server proves it read-only AND the name does not contradict that claim.
// ---------------------------------------------------------------------------

namespace {

json readOnly(bool value) { return json{{"readOnlyHint", value}}; }

} // namespace

TEST(MCPConfirmationTest, NoAnnotationsRequiresConfirmation) {
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json::object()));
    EXPECT_TRUE(mcpToolRequiresConfirmation("list_directory", json::object()));
    // Annotations present but silent about readOnlyHint -> still unproven.
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json{{"title", "Read a file"}}));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json{{"destructiveHint", false}}));
}

TEST(MCPConfirmationTest, ReadOnlyHintTrueOnReadShapedNameIsExempt) {
    EXPECT_FALSE(mcpToolRequiresConfirmation("read_file", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("list_directory", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("get_file_contents", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("search_repositories", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("query", readOnly(true)));
}

TEST(MCPConfirmationTest, ReadOnlyHintFalseRequiresConfirmation) {
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", readOnly(false)));
}

TEST(MCPConfirmationTest, MutatingNameOverridesReadOnlyClaim) {
    // A server can claim anything; the name is the tie-breaker.
    EXPECT_TRUE(mcpToolRequiresConfirmation("delete_file", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("write_file", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("create_or_update_file", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("Shell", readOnly(true)));
}

TEST(MCPConfirmationTest, DesktopCommanderRceToolsRequireConfirmation) {
    // desktop-commander's RCE pair — the reason `start` and `interact` are
    // in the mutating-verb list.
    EXPECT_TRUE(mcpToolRequiresConfirmation("start_process", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("interact_with_process", readOnly(true)));
}

TEST(MCPConfirmationTest, ShellAndDesktopDrivingToolsRequireConfirmation) {
    // Names with no conventional verb, but full RCE / input-injection power.
    EXPECT_TRUE(mcpToolRequiresConfirmation("bash", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("powershell", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("Shortcut", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("Click", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("Type", readOnly(true)));
}

TEST(MCPConfirmationTest, UnnamedToolRequiresConfirmation) {
    // Empty name tokenizes to nothing — must not fall through to "exempt".
    EXPECT_TRUE(mcpToolRequiresConfirmation("", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("   ", readOnly(true)));

    MCPToolSchema unnamed;
    unnamed.annotations = readOnly(true);
    EXPECT_EQ(unnamed.toToolInfo("srv").policy, ToolPolicy::CONFIRM);
}

TEST(MCPConfirmationTest, ConcatenatedAndInflectedVerbsAreCaught) {
    // No local rule can split "WRITEfile" correctly ("writ" + "efile"), so
    // whole-segment prefix matching backstops the camelCase split.
    EXPECT_TRUE(mcpToolRequiresConfirmation("WRITEfile", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("DELETEfile", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("writefile", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("write2file", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("write2File", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("deletes_branch", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("removes_stale_keys", readOnly(true)));

    // Known limit: inflections that drop the trailing "e" ("deletion",
    // "removing") are not prefix-matched. Add the form to the verb list if a
    // server ever ships one.
    EXPECT_FALSE(mcpToolRequiresConfirmation("s3_deletion_job", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("removing_stale_keys", readOnly(true)));
}

TEST(MCPConfirmationTest, ShortVerbsDoNotPrefixMatchReadOnlyNames) {
    // Prefix matching is limited to 4+ character verbs so that read-shaped
    // names containing "add"/"set"/"run" stay unprompted.
    EXPECT_FALSE(mcpToolRequiresConfirmation("get_address", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("get_settings", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("runtime_info", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("list_movies", readOnly(true)));
}

TEST(MCPConfirmationTest, NonBooleanReadOnlyHintRequiresConfirmation) {
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json{{"readOnlyHint", "true"}}));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json{{"readOnlyHint", 1}}));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json{{"readOnlyHint", nullptr}}));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json{{"readOnlyHint", json::object()}}));
}

TEST(MCPConfirmationTest, NonObjectAnnotationsRequireConfirmation) {
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json("readOnly")));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json(42)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json(nullptr)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("read_file", json::array({"readOnlyHint"})));
}

TEST(MCPConfirmationTest, CamelCaseNamesAreTokenized) {
    EXPECT_TRUE(mcpToolRequiresConfirmation("writeFile", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("deleteBranch", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("startProcess", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("readFile", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("listDirectory", readOnly(true)));
}

TEST(MCPConfirmationTest, ScreamingCamelAndMixedSeparatorsAreTokenized) {
    EXPECT_TRUE(mcpToolRequiresConfirmation("WRITEFile", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("DELETEFileContents", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("delete-branch", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("fs.write.file", readOnly(true)));
    EXPECT_TRUE(mcpToolRequiresConfirmation("Write File", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("READFile", readOnly(true)));
    EXPECT_FALSE(mcpToolRequiresConfirmation("get-file-contents", readOnly(true)));
}

TEST(MCPConfirmationTest, ToToolInfoStampsPolicy) {
    MCPToolSchema unproven;
    unproven.name = "read_file";
    EXPECT_TRUE(unproven.requiresConfirmation());
    EXPECT_EQ(unproven.toToolInfo("fs").policy, ToolPolicy::CONFIRM);

    MCPToolSchema proven;
    proven.name = "read_file";
    proven.annotations = readOnly(true);
    EXPECT_FALSE(proven.requiresConfirmation());
    EXPECT_EQ(proven.toToolInfo("fs").policy, ToolPolicy::ALLOW);

    MCPToolSchema lying;
    lying.name = "delete_file";
    lying.annotations = readOnly(true);
    EXPECT_EQ(lying.toToolInfo("fs").policy, ToolPolicy::CONFIRM);
}

// ---- listTools() annotation parsing ----

namespace {

/// Transport that replays a canned tools/list response.
class FakeToolsListTransport : public MCPTransport {
public:
    explicit FakeToolsListTransport(json response) : response_(std::move(response)) {}

    bool connect() override { return true; }
    void disconnect() override {}
    bool isConnected() const override { return true; }
    json sendRequest(const std::string&, const json& = json::object()) override {
        return response_;
    }

private:
    json response_;
};

std::vector<MCPToolSchema> listToolsFrom(const json& toolsArray) {
    json response = {{"jsonrpc", "2.0"}, {"id", 1}, {"result", {{"tools", toolsArray}}}};
    MCPClient client("fake", std::make_unique<FakeToolsListTransport>(response));
    return client.listTools();
}

} // namespace

TEST(MCPConfirmationTest, ListToolsParsesAnnotations) {
    auto tools = listToolsFrom(json::array({
        {{"name", "read_file"}, {"annotations", {{"readOnlyHint", true}}}},
        {{"name", "delete_file"}, {"annotations", {{"readOnlyHint", false}}}},
        {{"name", "no_annotations"}},
    }));

    ASSERT_EQ(tools.size(), 3u);
    EXPECT_EQ(tools[0].annotations, json({{"readOnlyHint", true}}));
    EXPECT_FALSE(tools[0].requiresConfirmation());
    EXPECT_TRUE(tools[1].requiresConfirmation());
    EXPECT_EQ(tools[2].annotations, json::object());
    EXPECT_TRUE(tools[2].requiresConfirmation());
}

TEST(MCPConfirmationTest, ListToolsDegradesNonObjectAnnotationsToConfirm) {
    auto tools = listToolsFrom(json::array({
        {{"name", "read_file"}, {"annotations", "readOnly"}},
        {{"name", "list_dir"}, {"annotations", json::array({"readOnlyHint"})}},
        {{"name", "get_status"}, {"annotations", nullptr}},
    }));

    ASSERT_EQ(tools.size(), 3u);
    for (const auto& tool : tools) {
        EXPECT_TRUE(tool.annotations.is_object()) << tool.name;
        EXPECT_TRUE(tool.annotations.empty()) << tool.name;
        EXPECT_TRUE(tool.requiresConfirmation()) << tool.name;
        EXPECT_EQ(tool.toToolInfo("fake").policy, ToolPolicy::CONFIRM) << tool.name;
    }
}

// ---- Enforcement: a denied MCP tool must not run ----

namespace {

/// Register an MCP-derived tool whose callback trips a canary when it runs.
ToolInfo mcpToolWithCanary(const std::string& name, const json& annotations, bool* canary) {
    MCPToolSchema schema;
    schema.name = name;
    schema.annotations = annotations;
    ToolInfo info = schema.toToolInfo("srv");
    info.callback = [canary](const json&) -> json {
        *canary = true;
        return json{{"status", "success"}};
    };
    return info;
}

} // namespace

TEST(MCPConfirmationTest, DeniedMcpToolDoesNotExecute) {
    bool executed = false;
    ToolRegistry registry;
    registry.registerTool(mcpToolWithCanary("delete_file", readOnly(true), &executed));

    int prompts = 0;
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++prompts;
        return ToolConfirmResult::DENY;
    });

    json result = registry.executeTool("mcp_srv_delete_file", json{{"path", "/etc/passwd"}});

    EXPECT_FALSE(executed) << "denied MCP tool executed anyway";
    EXPECT_EQ(prompts, 1);
    EXPECT_EQ(result["status"], "error");
}

TEST(MCPConfirmationTest, McpToolWithoutConfirmCallbackFailsClosed) {
    bool executed = false;
    ToolRegistry registry; // headless: no confirm callback installed
    registry.registerTool(mcpToolWithCanary("start_process", json::object(), &executed));

    json result = registry.executeTool("mcp_srv_start_process", json{{"command", "rm -rf /"}});

    EXPECT_FALSE(executed) << "MCP tool executed with no confirmation callback present";
    EXPECT_EQ(result["status"], "error");
}

TEST(MCPConfirmationTest, ApprovedMcpToolExecutes) {
    bool executed = false;
    ToolRegistry registry;
    registry.registerTool(mcpToolWithCanary("delete_file", json::object(), &executed));
    registry.setConfirmCallback([](const std::string&, const json&) {
        return ToolConfirmResult::ALLOW_ONCE;
    });

    json result = registry.executeTool("mcp_srv_delete_file", json::object());

    EXPECT_TRUE(executed);
    EXPECT_EQ(result["status"], "success");
}

// End-to-end through the agent loop: an LLM-requested MCP tool that the user
// denies must not reach its callback. Proves the CONFIRM gate is enforced on
// the real execution path (Agent::processQuery -> executeTool), not just when
// ToolRegistry is driven directly.
TEST(MCPConfirmationTest, AgentLoopDoesNotExecuteDeniedMcpTool) {
    bench::MockLlmServer mock;
    // Turn 1: the LLM asks for the MCP tool. Turn 2: it gives up and answers.
    mock.pushResponse(
        R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",)"
        R"(\"tool\":\"mcp_srv_delete_file\",\"tool_args\":{\"path\":\"/etc/passwd\"}}"}}]})");
    mock.pushResponse(
        R"({"choices":[{"message":{"content":"{\"thought\":\"t\",\"goal\":\"g\",\"answer\":\"blocked\"}"}}]})");

    AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.modelId = ""; // empty skips ensureModelLoaded()
    cfg.maxSteps = 2;
    cfg.silentMode = true;

    class Bare : public Agent {
    public:
        using Agent::Agent;
    };
    Bare agent(cfg);

    // Agent installs a store rooted at the real user profile; swap in an empty
    // one so a developer's saved "always allow" can't defeat the assertion.
    const std::string storeDir =
        (std::filesystem::temp_directory_path() / "gaia_mcp_gate_agent_test").string();
    std::filesystem::remove_all(storeDir);
    agent.toolRegistry().setAllowedToolsStore(std::make_shared<AllowedToolsStore>(storeDir));

    bool executed = false;
    agent.toolRegistry().registerTool(
        mcpToolWithCanary("delete_file", readOnly(true), &executed));
    int prompts = 0;
    agent.setToolConfirmCallback([&](const std::string&, const json&) {
        ++prompts;
        return ToolConfirmResult::DENY;
    });

    agent.processQuery("clean up my system", 2);

    EXPECT_FALSE(executed) << "agent loop executed an MCP tool the user denied";
    EXPECT_EQ(prompts, 1) << "agent loop never reached the confirmation gate";

    std::filesystem::remove_all(storeDir);
}

TEST(MCPConfirmationTest, UnprefixedToolNameStillHitsTheGate) {
    // Models routinely emit the bare MCP name; resolveName() maps it back to
    // the registered tool, and the gate must travel with it.
    bool executed = false;
    ToolRegistry registry;
    registry.registerTool(mcpToolWithCanary("delete_file", readOnly(true), &executed));
    int prompts = 0;
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++prompts;
        return ToolConfirmResult::DENY;
    });

    json result = registry.executeTool("delete_file", json::object());

    EXPECT_EQ(prompts, 1) << "unprefixed name bypassed the confirmation gate";
    EXPECT_FALSE(executed);
    EXPECT_EQ(result["status"], "error");
}

TEST(MCPConfirmationTest, AlwaysAllowStoreKeysOnThePrefixedName) {
    // security.mdx tells users to pre-approve "mcp_<server>_<tool>" — verify
    // that is in fact the key the registry checks.
    const std::string dir =
        (std::filesystem::temp_directory_path() / "gaia_mcp_gate_test").string();
    std::filesystem::remove_all(dir);
    auto store = std::make_shared<AllowedToolsStore>(dir);
    store->addAlwaysAllowed("mcp_srv_delete_file");

    bool executed = false;
    int prompts = 0;
    ToolRegistry registry;
    registry.setAllowedToolsStore(store);
    registry.registerTool(mcpToolWithCanary("delete_file", json::object(), &executed));
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++prompts;
        return ToolConfirmResult::DENY;
    });

    json result = registry.executeTool("mcp_srv_delete_file", json::object());

    EXPECT_TRUE(executed);
    EXPECT_EQ(prompts, 0) << "pre-approved MCP tool should not prompt";
    EXPECT_EQ(result["status"], "success");

    std::filesystem::remove_all(dir);
}

TEST(MCPConfirmationTest, StricterPolicyNeverWeakensTheVerdict) {
    // The rule connectMcpServer() applies when folding in the registry default.
    EXPECT_EQ(stricterPolicy(ToolPolicy::CONFIRM, ToolPolicy::ALLOW), ToolPolicy::CONFIRM);
    EXPECT_EQ(stricterPolicy(ToolPolicy::ALLOW, ToolPolicy::CONFIRM), ToolPolicy::CONFIRM);
    EXPECT_EQ(stricterPolicy(ToolPolicy::CONFIRM, ToolPolicy::DENY), ToolPolicy::DENY);
    EXPECT_EQ(stricterPolicy(ToolPolicy::ALLOW, ToolPolicy::ALLOW), ToolPolicy::ALLOW);
}

TEST(MCPConfirmationTest, ProvenReadOnlyMcpToolRunsUnprompted) {
    bool executed = false;
    int prompts = 0;
    ToolRegistry registry;
    registry.registerTool(mcpToolWithCanary("read_file", readOnly(true), &executed));
    registry.setConfirmCallback([&](const std::string&, const json&) {
        ++prompts;
        return ToolConfirmResult::DENY;
    });

    json result = registry.executeTool("mcp_srv_read_file", json::object());

    EXPECT_TRUE(executed);
    EXPECT_EQ(prompts, 0) << "read-only MCP tool should not prompt";
    EXPECT_EQ(result["status"], "success");
}
