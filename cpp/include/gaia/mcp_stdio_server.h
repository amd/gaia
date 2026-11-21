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
/// Every call goes through the agent's ToolRegistry, so tool policy, argument
/// validation and the confirm callback apply exactly as they do in a chat. A
/// CONFIRM tool is refused unless the agent has a confirm callback that allows it.
///
/// An MCP client launches gaiacpp as:
/// @code
///   {"mcpServers": {"gaia": {"command": "gaiacpp", "args": ["--mcp"]}}}
/// @endcode
class GAIA_API McpStdioServer {
public:
    /// `name` and `version` are reported as serverInfo in the handshake.
    McpStdioServer(Agent& agent, std::string name, std::string version, std::istream& in = std::cin,
              std::ostream& out = std::cout);

    /// Run the server (blocking) until `in` reaches EOF.
    void run();

    /// Handle one JSON-RPC message. Returns null for a notification.
    json handleMessage(const json& message);

private:
    Agent& agent_;
    std::string name_;
    std::string version_;
    std::istream& in_;
    std::ostream& out_;

    json handleToolsList();
    json handleToolsCall(const json& params);
};

} // namespace gaia
