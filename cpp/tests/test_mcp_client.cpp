// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/mcp_client.h>

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
