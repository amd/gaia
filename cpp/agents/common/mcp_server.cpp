// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "mcp_server.h"

#include "gaia/agent.h"
#include "gaia/tool_registry.h"

#include <iostream>
#include <string>
#include <utility>

namespace gaia {

McpServer::McpServer(Agent& agent, std::string version, std::istream& in, std::ostream& out)
    : agent_(agent), version_(std::move(version)), in_(in), out_(out) {}

namespace {

json rpcError(const json& id, int code, const std::string& message) {
    return json{{"jsonrpc", "2.0"}, {"id", id}, {"error", {{"code", code}, {"message", message}}}};
}

}  // namespace

// ---------------------------------------------------------------------------
// run() — main stdio loop
// ---------------------------------------------------------------------------

void McpServer::run() {
    // All status output goes to stderr — stdout is the MCP transport.
    std::cerr << "[gaia-agent] MCP server started, reading from stdin..." << std::endl;

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
        if (!response.is_null()) out_ << response.dump() << std::endl;
    }

    std::cerr << "[gaia-agent] MCP server shutting down (stdin closed)" << std::endl;
}

// ---------------------------------------------------------------------------
// handleMessage — dispatch by method
// ---------------------------------------------------------------------------

json McpServer::handleMessage(const json& message) {
    if (!message.is_object()) return rpcError(nullptr, -32600, "Invalid request: expected a JSON object");
    const bool notification = !message.contains("id");
    const json id = notification ? json(nullptr) : message["id"];
    if (notification) return json();  // notifications/initialized, notifications/cancelled, ...
    if (!message.contains("method") || !message["method"].is_string()) {
        return rpcError(id, -32600, "Invalid request: method must be a string");
    }
    const std::string method = message["method"].get<std::string>();
    const json params = message.contains("params") && message["params"].is_object() ? message["params"]
                                                                                    : json::object();
    try {
        json result;
        if (method == "initialize") {
            result = handleInitialize(params);
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

// ---------------------------------------------------------------------------
// initialize
// ---------------------------------------------------------------------------

json McpServer::handleInitialize(const json& /*params*/) {
    return json{
        {"protocolVersion", "2024-11-05"},
        {"capabilities", {{"tools", json::object()}}},
        {"serverInfo", {{"name", "gaia-agent"}, {"version", version_}}}
    };
}

// ---------------------------------------------------------------------------
// tools/list
// ---------------------------------------------------------------------------

json McpServer::handleToolsList() {
    json tools = json::array();
    for (const auto& [name, info] : agent_.tools().allTools()) {
        if (!info.enabled) continue;
        tools.push_back(toolInfoToMcp(info));
    }
    return json{{"tools", tools}};
}

json McpServer::toolInfoToMcp(const ToolInfo& tool) {
    // Build JSON Schema for inputSchema
    json properties = json::object();
    json required = json::array();

    for (const auto& param : tool.parameters) {
        json prop = {
            {"type", paramTypeToJsonSchema(param.type)},
            {"description", param.description}
        };
        properties[param.name] = prop;
        if (param.required) {
            required.push_back(param.name);
        }
    }

    json inputSchema = {
        {"type", "object"},
        {"properties", properties}
    };
    if (!required.empty()) {
        inputSchema["required"] = required;
    }

    return json{
        {"name", tool.name},
        {"description", tool.description},
        {"inputSchema", inputSchema}
    };
}

std::string McpServer::paramTypeToJsonSchema(ToolParamType type) {
    switch (type) {
        case ToolParamType::STRING:  return "string";
        case ToolParamType::INTEGER: return "integer";
        case ToolParamType::NUMBER:  return "number";
        case ToolParamType::BOOLEAN: return "boolean";
        case ToolParamType::ARRAY:   return "array";
        case ToolParamType::OBJECT:  return "object";
        case ToolParamType::UNKNOWN: return "string";
    }
    return "string";
}

// ---------------------------------------------------------------------------
// tools/call
// ---------------------------------------------------------------------------

json McpServer::handleToolsCall(const json& params) {
    const std::string name = params["name"].get<std::string>();
    const json arguments = params.contains("arguments") && params["arguments"].is_object() ? params["arguments"]
                                                                                          : json::object();

    std::cerr << "[gaia-agent] tools/call: " << name << std::endl;

    json result = agent_.toolRegistry().executeTool(name, arguments);

    // Two error shapes: {"status":"error"} or {"error":"..."} without "success".
    const bool isError = (result.contains("status") && result["status"] == "error")
                      || (result.contains("error") && !result.contains("success"));

    return json{
        {"content", json::array({json{{"type", "text"}, {"text", result.dump(2)}}})},
        {"isError", isError}
    };
}

} // namespace gaia
