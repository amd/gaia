// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MCP stdio server that exposes an Agent's tools via JSON-RPC 2.0.

#pragma once

#include <iostream>
#include <string>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

class Agent;

/// MCP stdio server that exposes an Agent's tools via JSON-RPC 2.0.
///
/// Reads one JSON-RPC message per line from `in`, writes responses to `out`.
/// Implements initialize, ping, tools/list and tools/call. Notifications
/// (messages without an id) get no response, as JSON-RPC requires.
///
/// Usage:
/// @code
///   FlagshipAgent agent(config, options);
///   McpServer server(agent, "0.1.0");
///   server.run();  // blocking, reads stdin until EOF
/// @endcode
///
/// An MCP client launches it as:
/// @code
///   {"mcpServers": {"gaia": {"command": "gaia-native", "args": ["--mcp"]}}}
/// @endcode
class McpServer {
public:
    McpServer(Agent& agent, std::string version, std::istream& in = std::cin, std::ostream& out = std::cout);

    /// Run the server (blocking) until `in` reaches EOF.
    void run();

    /// Handle one JSON-RPC message. Returns null for a notification.
    json handleMessage(const json& message);

private:
    Agent& agent_;
    std::string version_;
    std::istream& in_;
    std::ostream& out_;

    json handleInitialize(const json& params);
    json handleToolsList();
    json handleToolsCall(const json& params);

    static json toolInfoToMcp(const ToolInfo& tool);
    static std::string paramTypeToJsonSchema(ToolParamType type);
};

} // namespace gaia
