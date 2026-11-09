// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MCP (Model Context Protocol) client for interacting with MCP servers.
// Ported from Python:
//   - src/gaia/mcp/client/mcp_client.py (MCPClient, MCPTool)
//   - src/gaia/mcp/client/transports/base.py (MCPTransport)
//   - src/gaia/mcp/client/transports/stdio.py (StdioTransport)
//   - src/gaia/mcp/client/transports/http.py  (HttpTransport)
//
// The MCP client speaks JSON-RPC 2.0 over a pluggable transport: a subprocess
// on stdin/stdout (StdioTransport) or MCP Streamable HTTP (HttpTransport).

#pragma once

#include <map>
#include <memory>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "types.h"
#include "gaia/http_client.h"
#include "gaia/export.h"

namespace gaia {

using json = nlohmann::json;

/// Decide whether an MCP tool must be gated behind user confirmation.
///
/// Fails closed: a tool is exempt ONLY when the server proves it read-only.
/// All of these must hold:
///   1. the tool has a name;
///   2. `annotations["readOnlyHint"]` is the JSON boolean `true` (a string
///      "true", a missing key, or a non-object `annotations` does not count); and
///   3. the tool name contains no state-changing verb.
///
/// Everything else — no annotations, `readOnlyHint: false`, a non-boolean
/// value, or a read-only claim contradicted by a name like `delete_file` —
/// requires confirmation. MCP tool names are chosen by the server, so they can
/// never be matched against a static allowlist; this classifier is what stands
/// between an injected `mcp_*_delete_file` call and execution.
///
/// Note that condition 2 trusts the server's own claim; condition 3 is the
/// counterweight, and it only knows the verbs listed in mcp_client.cpp. Treat
/// the exemption as protection against a careless server, not a hostile one.
///
/// Name matching is case-insensitive and ASCII-only. It splits snake_case,
/// kebab-case, camelCase and screaming-camel (`WRITEFile`) names and matches
/// verbs both as whole words and — for verbs of 4+ characters — as prefixes,
/// so `WRITEfile`, `write2file` and `deletes` are all caught.
GAIA_API bool mcpToolRequiresConfirmation(const std::string& toolName, const json& annotations);

/// Represents an MCP tool with its JSON Schema.
/// Mirrors Python MCPTool dataclass.
struct GAIA_API MCPToolSchema {
    std::string name;
    std::string description;
    json inputSchema; // JSON Schema for tool parameters

    /// MCP tool annotations (`annotations` in the tools/list response).
    /// Always an object: a missing or non-object value is stored as `{}`, which
    /// classifies as "requires confirmation" — never as trusted.
    json annotations = json::object();

    /// Whether this tool must be gated behind user confirmation.
    /// Convenience wrapper over mcpToolRequiresConfirmation().
    bool requiresConfirmation() const;

    /// Convert to GAIA ToolInfo format.
    /// Mirrors Python MCPTool.to_gaia_format().
    /// Stamps `policy = CONFIRM` unless requiresConfirmation() is false.
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

    /// Send a JSON-RPC notification (no id, no reply expected).
    /// The default throws std::logic_error: a transport that cannot notify must
    /// not let MCPClient::connect() believe the handshake completed.
    virtual void sendNotification(const std::string& method, const json& params = json::object());
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
    void sendNotification(const std::string& method, const json& params = json::object()) override;
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

/// MCP Streamable HTTP transport (MCP spec 2025-03-26 and later).
/// Mirrors Python HTTPTransport.
///
/// Every JSON-RPC message is a POST to one endpoint URL with
/// `Accept: application/json, text/event-stream`. The server answers with a
/// JSON body or an SSE stream; the event whose `id` matches the request is the
/// reply, and server messages sent ahead of it are skipped. The
/// `Mcp-Session-Id` returned by `initialize` and the negotiated
/// `MCP-Protocol-Version` are sent on every later request.
///
/// Every failure throws (HttpError or std::runtime_error) naming the URL, the
/// HTTP status when there is one, and what to check.
class GAIA_API HttpTransport : public MCPTransport {
public:
    /// @param url      The server's MCP endpoint, http:// or https://.
    /// @param headers  Sent on every request (e.g. Authorization). Values are
    ///                 used verbatim; `${VAR}` expansion is fromConfig()'s job.
    /// @throws std::invalid_argument if the URL is not http(s) or timeout <= 0.
    HttpTransport(std::string url, HttpHeaders headers = {}, int timeout = 30,
                  bool debug = false);
    ~HttpTransport() override;

    HttpTransport(const HttpTransport&) = delete;
    HttpTransport& operator=(const HttpTransport&) = delete;

    /// Marks the transport ready; HTTP has nothing to open until `initialize`.
    bool connect() override;
    void disconnect() override;
    json sendRequest(const std::string& method, const json& params = json::object()) override;
    /// POSTs the notification; the server must accept it with a 2xx (spec: 202).
    void sendNotification(const std::string& method, const json& params = json::object()) override;
    bool isConnected() const override;

    const std::string& url() const { return url_; }
    /// Session id the server assigned on `initialize` (empty if it assigned none).
    const std::string& sessionId() const { return sessionId_; }
    /// Protocol version the server answered `initialize` with (empty before).
    const std::string& protocolVersion() const { return protocolVersion_; }

private:
    HttpHeaders requestHeaders() const;
    [[noreturn]] void rethrowHttpError(const HttpError& e, const std::string& method);

    std::string url_;
    int timeout_;
    bool debug_;
    int requestId_ = 0;
    bool connected_ = false;
    std::string sessionId_;
    std::string protocolVersion_;
    std::unique_ptr<HttpClient> http_;
};

/// Expand `${VAR}` references in an MCP config value from the environment.
/// A referenced variable that is unset is an error naming it, never an empty
/// substitution (which would send `Authorization: Bearer ` and surface later
/// as a confusing 401). A `$` not followed by `{` is kept literally.
/// @param context  Where the value came from, for the error message.
/// @throws std::invalid_argument on an unset variable or an unterminated `${`.
GAIA_API std::string expandMcpEnvVars(const std::string& value, const std::string& context);

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

    /// Create client from config dict (Anthropic / Claude Code format).
    ///
    /// stdio: `{"command": "...", "args": [...], "env": {...}}`, optionally
    ///        with `"type": "stdio"`.
    /// HTTP:  `{"type": "http" | "streamable-http", "url": "...",
    ///          "headers": {"Authorization": "Bearer ${LEMONADE_API_KEY}"}}`;
    ///        `type` may be omitted when the entry has a `url` and no
    ///        `command`. Header values expand `${VAR}` (see expandMcpEnvVars()).
    /// @throws std::invalid_argument on an unknown `type`, a missing `command`
    ///         or `url`, an entry carrying both without a `type`, non-string
    ///         headers, or an unset `${VAR}`.
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
