// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/mcp_stdio_server.h"

#include "gaia/agent.h"
#include "gaia/tool_registry.h"

#include <iostream>
#include <string>
#include <utility>

namespace gaia {

McpStdioServer::McpStdioServer(Agent& agent, std::string name, std::string version, std::istream& in, std::ostream& out)
    : agent_(agent), name_(std::move(name)), version_(std::move(version)), in_(in), out_(out) {}

namespace {

json rpcError(const json& id, int code, const std::string& message) {
    return json{{"jsonrpc", "2.0"}, {"id", id}, {"error", {{"code", code}, {"message", message}}}};
}

}  // namespace

void McpStdioServer::run() {
    // Status goes to stderr: `out` is the MCP transport.
    std::cerr << "[" << name_ << "] MCP server started, reading from stdin" << std::endl;

    std::string line;
    while (std::getline(in_, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.empty()) continue;

        json response;
        json message;
        try {
            message = json::parse(line);
        } catch (const json::parse_error& e) {
            response = rpcError(nullptr, -32700, std::string("Parse error: ") + e.what());
        }
        if (response.is_null()) response = handleMessage(message);
        if (!response.is_null()) {
            out_ << response.dump(-1, ' ', false, json::error_handler_t::replace) << std::endl;
        }
    }

    std::cerr << "[" << name_ << "] MCP server shutting down (stdin closed)" << std::endl;
}

json McpStdioServer::handleMessage(const json& message) {
    if (!message.is_object()) return rpcError(nullptr, -32600, "Invalid request: expected a JSON object");
    if (!message.contains("id")) return json();  // notifications/initialized, notifications/cancelled, ...
    const json id = message["id"];
    if (!message.contains("method") || !message["method"].is_string()) {
        return rpcError(id, -32600, "Invalid request: method must be a string");
    }
    const std::string method = message["method"].get<std::string>();
    const json params = message.contains("params") && message["params"].is_object() ? message["params"]
                                                                                    : json::object();
    try {
        json result;
        if (method == "initialize") {
            result = {{"protocolVersion", "2024-11-05"},
                      {"capabilities", {{"tools", json::object()}}},
                      {"serverInfo", {{"name", name_}, {"version", version_}}}};
        } else if (method == "ping") {
            result = json::object();
        } else if (method == "tools/list") {
            result = handleToolsList();
        } else if (method == "tools/call") {
            if (!params.contains("name") || !params["name"].is_string() ||
                params["name"].get<std::string>().empty()) {
                return rpcError(id, -32602, "Invalid params: tools/call needs a tool name");
            }
            result = handleToolsCall(params);
        } else {
            return rpcError(id, -32601, "Method not found: " + method);
        }
        return json{{"jsonrpc", "2.0"}, {"id", id}, {"result", result}};
    } catch (const std::exception& e) {
        return rpcError(id, -32603, std::string("Internal error: ") + e.what());
    }
}

json McpStdioServer::handleToolsList() {
    // The same schemas the model is sent, so both callers see one contract.
    json tools = json::array();
    for (const auto& schema : agent_.tools().buildOpenAiToolSchemas()) {
        const json& fn = schema.at("function");
        tools.push_back({{"name", fn.at("name")},
                         {"description", fn.value("description", "")},
                         {"inputSchema", fn.value("parameters", json{{"type", "object"}, {"properties", json::object()}})}});
    }
    return json{{"tools", tools}};
}

json McpStdioServer::handleToolsCall(const json& params) {
    const std::string name = params["name"].get<std::string>();
    const json arguments = params.contains("arguments") && params["arguments"].is_object() ? params["arguments"]
                                                                                          : json::object();
    std::cerr << "[" << name_ << "] tools/call: " << name << std::endl;

    const json result = agent_.toolRegistry().executeTool(name, arguments);

    // Two error shapes: {"status":"error"} or {"error":"..."} without "success".
    const bool isError = result.is_object() && ((result.contains("status") && result["status"] == "error") ||
                                                (result.contains("error") && !result.contains("success")));
    const std::string text = result.is_string() ? result.get<std::string>()
                                                : result.dump(2, ' ', false, json::error_handler_t::replace);
    return json{{"content", json::array({json{{"type", "text"}, {"text", text}}})}, {"isError", isError}};
}

} // namespace gaia
