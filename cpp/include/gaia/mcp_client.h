// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MCP (Model Context Protocol) client for interacting with MCP servers.
// Ported from Python:
//   - src/gaia/mcp/client/mcp_client.py (MCPClient, MCPTool)
//   - src/gaia/mcp/client/transports/base.py (MCPTransport)
//   - src/gaia/mcp/client/transports/stdio.py (StdioTransport)
//
// The MCP client manages a subprocess-based MCP server, sends JSON-RPC 2.0
// requests via stdin, and reads responses from stdout.

#pragma once

#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "types.h"
#include "gaia/export.h"

namespace gaia {

using json = nlohmann::json;

/// Represents an MCP tool with its JSON Schema.
/// Mirrors Python MCPTool dataclass.
struct GAIA_API MCPToolSchema {
    std::string name;
    std::string description;
    json inputSchema; // JSON Schema for tool parameters

    /// Convert to GAIA ToolInfo format.
    /// Mirrors Python MCPTool.to_gaia_format().
    ToolInfo toToolInfo(const std::string& serverName) const;
};

/// Abstract transport interface for MCP communication.
/// Mirrors Python MCPTransport ABC.
class GAIA_API MCPTransport {
public:
    virtual ~MCPTransport() = default;

    virtual bool connect() = 0;
    virtual void disconnect() = 0;
    virtual json sendRequest(const std::string& method, const json& params = json::object()) = 0;
    virtual bool isConnected() const = 0;
};

/// Stdio-based transport using subprocess.
/// Mirrors Python StdioTransport.
///
/// Launches an MCP server as a subprocess and communicates via stdin/stdout
/// using newline-delimited JSON-RPC 2.0.
class GAIA_API StdioTransport : public MCPTransport {
public:
    /// Construct with a shell command string (legacy mode).
    explicit StdioTransport(const std::string& command, int timeout = 30, bool debug = false);

    /// Construct with command + args (modern mode, matches Anthropic config format).
    StdioTransport(const std::string& command, const std::vector<std::string>& args,
                   int timeout = 30, bool debug = false);

    /// Construct with command + args + environment overrides.
    /// @param env  Additional environment variables to set in the server process.
    ///             These are merged with the parent process environment.
    StdioTransport(const std::string& command, const std::vector<std::string>& args,
                   const std::map<std::string, std::string>& env,
                   int timeout = 30, bool debug = false);

    ~StdioTransport() override;

    // Non-copyable, movable
    StdioTransport(const StdioTransport&) = delete;
    StdioTransport& operator=(const StdioTransport&) = delete;
    StdioTransport(StdioTransport&& other) noexcept;
    StdioTransport& operator=(StdioTransport&& other) noexcept;

    bool connect() override;
    void disconnect() override;
    json sendRequest(const std::string& method, const json& params = json::object()) override;
    bool isConnected() const override;

private:
    std::string command_;
    std::vector<std::string> args_;
    std::map<std::string, std::string> envVars_; // Additional env vars for the server process
    int timeout_;
    bool debug_;
    int requestId_ = 0;

    // Platform-specific process handle
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

/// MCP client for interacting with an MCP server.
/// Mirrors Python MCPClient.
class GAIA_API MCPClient {
public:
    /// Create client with a transport.
    MCPClient(const std::string& name, std::unique_ptr<MCPTransport> transport,
              bool debug = false);

    /// Create client from a shell command (convenience).
    static MCPClient fromCommand(const std::string& name, const std::string& command,
                                 int timeout = 30, bool debug = false);

    /// Create client from config dict (Anthropic format).
    /// Config must have "command" key, optionally "args" and "env".
    static MCPClient fromConfig(const std::string& name, const json& config,
                                int timeout = 30, bool debug = false);

    ~MCPClient();
    MCPClient(MCPClient&&) noexcept;
    MCPClient& operator=(MCPClient&&) noexcept;

    /// Connect and initialize the MCP server.
    /// @return true if connection and initialization succeeded.
    bool connect();

    /// Disconnect from the MCP server.
    void disconnect();

    /// Check connection status.
    bool isConnected() const;

    /// List available tools from the server.
    /// @param refresh Force refresh from server (default: use cache).
    std::vector<MCPToolSchema> listTools(bool refresh = false);

    /// Call a tool on the MCP server.
    /// @return Tool response as JSON.
    /// @throws std::runtime_error if not connected.
    json callTool(const std::string& toolName, const json& arguments);

    /// Get the server name.
    const std::string& name() const { return name_; }

    /// Get the last error message (if any).
    const std::string& lastError() const { return lastError_; }

private:
    std::string name_;
    std::unique_ptr<MCPTransport> transport_;
    bool debug_;
    json serverInfo_;
    std::optional<std::vector<MCPToolSchema>> cachedTools_;
    std::string lastError_;
};

} // namespace gaia
