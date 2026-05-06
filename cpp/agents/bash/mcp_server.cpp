// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "mcp_server.h"

#include "gaia/agent.h"
#include "gaia/tool_registry.h"

#include <iostream>
#include <string>

namespace gaia {

McpServer::McpServer(Agent& agent) : agent_(agent) {}

// ---------------------------------------------------------------------------
// run() — main stdio loop
// ---------------------------------------------------------------------------

void McpServer::run() {
    // All debug/status output goes to stderr — stdout is the MCP transport.
    std::cerr << "[gaia-bash] MCP server started, reading from stdin..." << std::endl;

    std::string line;
    while (std::getline(std::cin, line)) {
        if (line.empty()) continue;

        try {
            json request = json::parse(line);
            json response = handleRequest(request);
            std::cout << response.dump() << std::endl;
            std::cout.flush();
        } catch (const json::parse_error& e) {
            json error = {
                {"jsonrpc", "2.0"},
                {"id", nullptr},
                {"error", {{"code", -32700}, {"message", std::string("Parse error: ") + e.what()}}}
            };
            std::cout << error.dump() << std::endl;
            std::cout.flush();
        } catch (const std::exception& e) {
            json error = {
                {"jsonrpc", "2.0"},
                {"id", nullptr},
                {"error", {{"code", -32603}, {"message", std::string("Internal error: ") + e.what()}}}
            };
            std::cout << error.dump() << std::endl;
            std::cout.flush();
        }
    }

    std::cerr << "[gaia-bash] MCP server shutting down (stdin closed)" << std::endl;
}

// ---------------------------------------------------------------------------
// handleRequest — dispatch by method
// ---------------------------------------------------------------------------

json McpServer::handleRequest(const json& request) {
    auto id = request.value("id", json(nullptr));
    std::string method = request.value("method", std::string(""));
    json params = request.value("params", json::object());

    json result;

    if (method == "initialize") {
        result = handleInitialize(params);
    } else if (method == "tools/list") {
        result = handleToolsList(params);
    } else if (method == "tools/call") {
        result = handleToolsCall(params);
    } else if (method == "prompts/list") {
        result = handlePromptsList(params);
    } else if (method == "prompts/get") {
        result = handlePromptsGet(params);
    } else if (method == "notifications/initialized") {
        // Client acknowledgement — no response needed, but return empty result
        return json{{"jsonrpc", "2.0"}, {"id", id}, {"result", json::object()}};
    } else {
        return json{
            {"jsonrpc", "2.0"},
            {"id", id},
            {"error", {{"code", -32601}, {"message", "Method not found: " + method}}}
        };
    }

    return json{{"jsonrpc", "2.0"}, {"id", id}, {"result", result}};
}

// ---------------------------------------------------------------------------
// initialize
// ---------------------------------------------------------------------------

json McpServer::handleInitialize(const json& /*params*/) {
    return json{
        {"protocolVersion", "2024-11-05"},
        {"capabilities", {
            {"tools", json::object()},
            {"prompts", json::object()}
        }},
        {"serverInfo", {
            {"name", "gaia-bash"},
            {"version", "0.1.0"}
        }}
    };
}

// ---------------------------------------------------------------------------
// tools/list
// ---------------------------------------------------------------------------

json McpServer::handleToolsList(const json& /*params*/) {
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
    std::string name = params.value("name", std::string(""));
    json arguments = params.value("arguments", json::object());

    if (name.empty()) {
        return json{
            {"content", json::array({json{{"type", "text"}, {"text", "Error: tool name is required"}}})},
            {"isError", true}
        };
    }

    std::cerr << "[gaia-bash] tools/call: " << name << std::endl;

    json result = agent_.toolRegistry().executeTool(name, arguments);

    // Check if the tool returned an error
    bool isError = result.contains("status") && result["status"] == "error";

    std::string resultText = result.dump(2);

    return json{
        {"content", json::array({json{{"type", "text"}, {"text", resultText}}})},
        {"isError", isError}
    };
}

// ---------------------------------------------------------------------------
// prompts/list
// ---------------------------------------------------------------------------

json McpServer::handlePromptsList(const json& /*params*/) {
    json prompts = json::array();

    prompts.push_back(json{
        {"name", "review-script"},
        {"description", "Multi-pass code review of a bash script (correctness, security, portability, performance, style)"},
        {"arguments", json::array({json{{"name", "path"}, {"description", "Path to the script to review"}, {"required", true}}})}
    });

    prompts.push_back(json{
        {"name", "generate-bats-test"},
        {"description", "Generate BATS test cases for a bash script"},
        {"arguments", json::array({json{{"name", "path"}, {"description", "Path to the script to test"}, {"required", true}}})}
    });

    prompts.push_back(json{
        {"name", "explain-command"},
        {"description", "Explain a bash command or one-liner in detail"},
        {"arguments", json::array({json{{"name", "command"}, {"description", "The command to explain"}, {"required", true}}})}
    });

    prompts.push_back(json{
        {"name", "posix-check"},
        {"description", "Check a bash script for POSIX compliance and flag bashisms"},
        {"arguments", json::array({json{{"name", "path"}, {"description", "Path to the script to check"}, {"required", true}}})}
    });

    return json{{"prompts", prompts}};
}

// ---------------------------------------------------------------------------
// prompts/get
// ---------------------------------------------------------------------------

json McpServer::handlePromptsGet(const json& params) {
    std::string name = params.value("name", std::string(""));
    json arguments = params.value("arguments", json::object());

    std::string promptText;

    if (name == "review-script") {
        std::string path = arguments.value("path", std::string(""));
        promptText = "Perform a thorough multi-pass code review of the bash script at '" + path +
                     "'. Analyze for: 1) Correctness (logic errors, edge cases), "
                     "2) Security (injection, unquoted vars, eval), "
                     "3) Portability (bashisms in #!/bin/sh), "
                     "4) Performance (unnecessary subshells, useless cat), "
                     "5) Style (ShellCheck compliance, naming).";
    } else if (name == "generate-bats-test") {
        std::string path = arguments.value("path", std::string(""));
        promptText = "Generate comprehensive BATS test cases for the bash script at '" + path +
                     "'. Cover: happy path, error cases (missing args, bad input), "
                     "edge cases (empty input, spaces in filenames), and exit code verification.";
    } else if (name == "explain-command") {
        std::string command = arguments.value("command", std::string(""));
        promptText = "Explain this bash command in detail, breaking down each part: " + command;
    } else if (name == "posix-check") {
        std::string path = arguments.value("path", std::string(""));
        promptText = "Check the bash script at '" + path +
                     "' for POSIX compliance. Flag any bashisms ([[ ]], arrays, <<<, "
                     "${var,,}, process substitution) and suggest portable alternatives.";
    } else {
        return json{
            {"description", "Unknown prompt: " + name},
            {"messages", json::array()}
        };
    }

    // Execute the prompt through the agent
    std::cerr << "[gaia-bash] prompts/get: " << name << std::endl;

    json result = agent_.processQuery(promptText);
    std::string answer = result.value("result", std::string(""));

    return json{
        {"description", "Result of " + name},
        {"messages", json::array({
            json{{"role", "user"}, {"content", json{{"type", "text"}, {"text", promptText}}}},
            json{{"role", "assistant"}, {"content", json{{"type", "text"}, {"text", answer}}}}
        })}
    };
}

} // namespace gaia
