// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MCP stdio server that exposes an Agent's tools via JSON-RPC.
// Implements the Model Context Protocol server side for integration
// with Claude Code, OpenCode, and other MCP-compatible agents.

#pragma once

#include <string>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

class Agent;

/// MCP stdio server that exposes an Agent's tools via JSON-RPC 2.0.
///
/// Reads JSON-RPC requests from stdin, processes them, writes responses
/// to stdout. Implements the MCP protocol:
///   - initialize: handshake with capabilities
///   - tools/list: returns registered tools as MCP tool definitions
///   - tools/call: executes a tool and returns the result
///   - prompts/list: returns available prompt templates
///   - prompts/get: returns a prompt with parameter substitution
///
/// Usage:
/// @code
///   BashAgent agent(config);
///   McpServer server(agent);
///   server.run();  // blocking, reads stdin until EOF
/// @endcode
///
/// Configure in Claude Code (~/.claude/settings.json):
/// @code
///   {"mcpServers": {"gaia-bash": {"command": "gaia-bash", "args": ["--mcp"]}}}
/// @endcode
class GAIA_API McpServer {
public:
    explicit McpServer(Agent& agent);

    /// Run the server (blocking). Reads stdin line-by-line, writes to stdout.
    void run();

private:
    Agent& agent_;

    /// Process a single JSON-RPC request and return the response.
    json handleRequest(const json& request);

    // Method handlers
    json handleInitialize(const json& params);
    json handleToolsList(const json& params);
    json handleToolsCall(const json& params);
    json handlePromptsList(const json& params);
    json handlePromptsGet(const json& params);

    /// Convert a ToolInfo to MCP tool definition format.
    static json toolInfoToMcp(const ToolInfo& tool);

    /// Convert ToolParamType to JSON Schema type string.
    static std::string paramTypeToJsonSchema(ToolParamType type);
};

} // namespace gaia
