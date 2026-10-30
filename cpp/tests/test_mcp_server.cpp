// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The MCP stdio server, driven line by line the way an MCP client drives it.

#include <gtest/gtest.h>

#include "mcp_server.h"

#include <gaia/agent.h>

#include <sstream>

using namespace gaia;

namespace {

class EchoAgent : public Agent {
public:
    EchoAgent() : Agent(makeConfig()) {
        toolRegistry().registerTool(
            "echo", "Echo the text back",
            [](const json& args) { return json{{"echo", args.value("text", "")}}; },
            {{"text", ToolParamType::STRING, true, "Text to echo"}});
        toolRegistry().registerTool(
            "fail", "Always fails", [](const json&) { return json{{"status", "error"}, {"error", "boom"}}; });
    }

private:
    static AgentConfig makeConfig() {
        AgentConfig c;
        c.silentMode = true;
        c.legacyEnvOverrides = false;
        return c;
    }
};

std::vector<json> converse(const std::string& input) {
    EchoAgent agent;
    std::istringstream in(input);
    std::ostringstream out;
    McpServer server(agent, "9.9.9", in, out);
    server.run();
    std::vector<json> replies;
    std::istringstream lines(out.str());
    for (std::string line; std::getline(lines, line);) {
        if (!line.empty()) replies.push_back(json::parse(line));
    }
    return replies;
}

}  // namespace

TEST(McpServerTest, HandshakeReportsVersionAndOnlyTools) {
    auto r = converse(R"({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}})" "\n");
    ASSERT_EQ(r.size(), 1u);
    EXPECT_EQ(r[0]["id"], 1);
    EXPECT_EQ(r[0]["result"]["serverInfo"]["version"], "9.9.9");
    EXPECT_TRUE(r[0]["result"]["capabilities"].contains("tools"));
    EXPECT_FALSE(r[0]["result"]["capabilities"].contains("prompts"));
}

TEST(McpServerTest, NotificationsGetNoReply) {
    auto r = converse(
        R"({"jsonrpc":"2.0","method":"notifications/initialized"})" "\n"
        R"({"jsonrpc":"2.0","method":"notifications/cancelled","params":{"requestId":1}})" "\n"
        R"({"jsonrpc":"2.0","id":"p","method":"ping"})" "\n");
    ASSERT_EQ(r.size(), 1u);
    EXPECT_EQ(r[0]["id"], "p");
    EXPECT_TRUE(r[0]["result"].is_object());
}

TEST(McpServerTest, ListsAndCallsTools) {
    auto r = converse(
        R"({"jsonrpc":"2.0","id":1,"method":"tools/list"})" "\n"
        R"({"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"echo","arguments":{"text":"hi"}}})" "\r\n"
        R"({"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"fail"}})" "\n");
    ASSERT_EQ(r.size(), 3u);
    const auto& tools = r[0]["result"]["tools"];
    ASSERT_EQ(tools.size(), 2u);
    EXPECT_EQ(tools[0]["name"], "echo");
    EXPECT_EQ(tools[0]["inputSchema"]["required"][0], "text");
    EXPECT_FALSE(r[1]["result"]["isError"].get<bool>());
    EXPECT_NE(r[1]["result"]["content"][0]["text"].get<std::string>().find("hi"), std::string::npos);
    EXPECT_TRUE(r[2]["result"]["isError"].get<bool>());
}

TEST(McpServerTest, ErrorsCarryTheRequestId) {
    auto r = converse(
        "not json\n"
        R"({"jsonrpc":"2.0","id":7,"method":"prompts/list"})" "\n"
        R"({"jsonrpc":"2.0","id":8,"method":"tools/call","params":{}})" "\n"
        R"({"jsonrpc":"2.0","id":9,"method":42})" "\n");
    ASSERT_EQ(r.size(), 4u);
    EXPECT_EQ(r[0]["error"]["code"], -32700);
    EXPECT_TRUE(r[0]["id"].is_null());
    EXPECT_EQ(r[1]["id"], 7);
    EXPECT_EQ(r[1]["error"]["code"], -32601);
    EXPECT_EQ(r[2]["id"], 8);
    EXPECT_EQ(r[2]["error"]["code"], -32602);
    EXPECT_EQ(r[3]["id"], 9);
    EXPECT_EQ(r[3]["error"]["code"], -32600);
}
