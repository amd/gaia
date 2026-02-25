// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/agent.h"

#include <iostream>
#include <regex>
#include <sstream>
#include <stdexcept>

#include <httplib.h>

namespace gaia {

// Response format template (mirrors Python Agent._response_format_template)
const std::string Agent::RESPONSE_FORMAT_TEMPLATE = R"(
==== RESPONSE FORMAT ====
You must respond ONLY in valid JSON. No text before { or after }.

**To call a tool:**
{"thought": "reasoning", "goal": "objective", "tool": "tool_name", "tool_args": {"arg1": "value1"}}

**To call a tool with an initial plan:**
{"thought": "reasoning", "goal": "objective", "plan": [{"tool": "t1", "tool_args": {}}, {"tool": "t2", "tool_args": {}}], "tool": "t1", "tool_args": {}}

**To provide a final answer:**
{"thought": "reasoning", "goal": "achieved", "answer": "response to user"}

**RULES:**
1. ALWAYS use tools for real data - NEVER hallucinate
2. Call ONE tool at a time - observe the result, reason about it, then decide the next action
3. You may include a "plan" to show your intended steps, but always execute only the "tool" field
4. After each tool result, you can change, skip, or add steps - the plan is a roadmap, not a script
5. After all tools complete, provide an "answer" summarizing results
)";

Agent::Agent(const AgentConfig& config)
    : config_(config) {

    // Create console based on config
    if (config_.silentMode) {
        console_ = std::make_unique<SilentConsole>();
    } else {
        console_ = std::make_unique<TerminalConsole>();
    }

    // NOTE: Do NOT call registerTools() here. Virtual dispatch does not work
    // during base class construction in C++. Subclasses must call init() after
    // their constructor completes, or tools should be registered in the
    // subclass constructor.

    // System prompt will be composed lazily
    systemPromptDirty_ = true;
}

Agent::~Agent() {
    disconnectAllMcp();
}

void Agent::setOutputHandler(std::unique_ptr<OutputHandler> handler) {
    console_ = std::move(handler);
}

std::string Agent::systemPrompt() const {
    if (systemPromptDirty_) {
        cachedSystemPrompt_ = composeSystemPrompt();
        systemPromptDirty_ = false;
    }
    return cachedSystemPrompt_;
}

void Agent::rebuildSystemPrompt() {
    systemPromptDirty_ = true;
}

std::string Agent::composeSystemPrompt() const {
    std::ostringstream oss;

    // Agent-specific prompt
    std::string custom = getSystemPrompt();
    if (!custom.empty()) {
        oss << custom << "\n\n";
    }

    // Tool descriptions
    std::string toolsDesc = tools_.formatForPrompt();
    if (!toolsDesc.empty()) {
        oss << "==== AVAILABLE TOOLS ====\n" << toolsDesc << "\n";
    }

    // Response format
    oss << RESPONSE_FORMAT_TEMPLATE;

    return oss.str();
}

// ---- LLM Communication ----

std::string Agent::callLlm(const std::vector<Message>& messages, const std::string& sysPrompt) {
    // Build OpenAI-compatible request
    json requestBody;
    requestBody["model"] = config_.modelId;
    requestBody["max_tokens"] = 4096;

    json msgArray = json::array();

    // Add system message
    if (!sysPrompt.empty()) {
        msgArray.push_back({{"role", "system"}, {"content", sysPrompt}});
    }

    // Add conversation messages
    for (const auto& msg : messages) {
        msgArray.push_back(msg.toJson());
    }

    requestBody["messages"] = msgArray;

    // Parse base URL
    std::string baseUrl = config_.baseUrl;
    std::string host, path;
    int port = 80;
    bool useSSL = false;

    // Extract scheme
    if (baseUrl.substr(0, 8) == "https://") {
        useSSL = true;
        baseUrl = baseUrl.substr(8);
        port = 443;
    } else if (baseUrl.substr(0, 7) == "http://") {
        baseUrl = baseUrl.substr(7);
    }

    // Extract host:port and path
    auto slashPos = baseUrl.find('/');
    if (slashPos != std::string::npos) {
        host = baseUrl.substr(0, slashPos);
        path = baseUrl.substr(slashPos);
    } else {
        host = baseUrl;
        path = "";
    }

    // Extract port from host
    auto colonPos = host.find(':');
    if (colonPos != std::string::npos) {
        port = std::stoi(host.substr(colonPos + 1));
        host = host.substr(0, colonPos);
    }

    // Append /chat/completions endpoint
    if (path.empty() || path.back() != '/') {
        path += "/chat/completions";
    } else {
        path += "chat/completions";
    }

    if (config_.debug) {
        std::cerr << "[LLM] Calling " << host << ":" << port << path << std::endl;
        std::cerr << "[LLM] Messages: " << msgArray.size() << std::endl;
    }

    // Make HTTP request
    std::string responseBody;
    if (useSSL) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
        httplib::SSLClient cli(host, port);
        cli.set_connection_timeout(30);
        cli.set_read_timeout(120);
        auto res = cli.Post(path, requestBody.dump(), "application/json");
        if (!res) {
            throw std::runtime_error("LLM HTTP request failed (SSL)");
        }
        responseBody = res->body;
#else
        throw std::runtime_error("SSL not supported. Use http:// base URL.");
#endif
    } else {
        httplib::Client cli(host, port);
        cli.set_connection_timeout(30);
        cli.set_read_timeout(120);
        auto res = cli.Post(path, requestBody.dump(), "application/json");
        if (!res) {
            throw std::runtime_error("LLM HTTP request failed: connection error to " +
                                     host + ":" + std::to_string(port));
        }
        if (res->status != 200) {
            throw std::runtime_error("LLM HTTP request failed with status " +
                                     std::to_string(res->status) + ": " + res->body);
        }
        responseBody = res->body;
    }

    // Parse response
    try {
        json responseJson = json::parse(responseBody);
        if (responseJson.contains("choices") && !responseJson["choices"].empty()) {
            auto& choice = responseJson["choices"][0];
            if (choice.contains("message") && choice["message"].contains("content")) {
                return choice["message"]["content"].get<std::string>();
            }
        }
        // Include truncated response body in error for debugging
        std::string preview = responseBody.substr(0, 200);
        throw std::runtime_error("Unexpected LLM response format: " + preview);
    } catch (const json::parse_error& e) {
        std::string preview = responseBody.substr(0, 200);
        throw std::runtime_error(std::string("Failed to parse LLM response: ") + e.what() + " | body: " + preview);
    }
}

// ---- Tool Execution ----

json Agent::executeTool(const std::string& toolName, const json& toolArgs) {
    return tools_.executeTool(toolName, toolArgs);
}

json Agent::resolvePlanParameters(const json& toolArgs, const std::vector<json>& stepResults) {
    if (toolArgs.is_object()) {
        json resolved = json::object();
        for (auto& [key, value] : toolArgs.items()) {
            resolved[key] = resolvePlanParameters(value, stepResults);
        }
        return resolved;
    }

    if (toolArgs.is_array()) {
        json resolved = json::array();
        for (const auto& item : toolArgs) {
            resolved.push_back(resolvePlanParameters(item, stepResults));
        }
        return resolved;
    }

    if (toolArgs.is_string()) {
        std::string val = toolArgs.get<std::string>();

        // Handle $PREV.field
        if (val.substr(0, 6) == "$PREV." && !stepResults.empty()) {
            std::string field = val.substr(6);
            const auto& prev = stepResults.back();
            if (prev.is_object() && prev.contains(field)) {
                return prev[field];
            }
        }

        // Handle $STEP_N.field
        std::regex stepRe(R"(\$STEP_(\d+)\.(.+))");
        std::smatch match;
        if (std::regex_match(val, match, stepRe) && !stepResults.empty()) {
            int idx = std::stoi(match[1].str());
            std::string field = match[2].str();
            if (idx >= 0 && idx < static_cast<int>(stepResults.size())) {
                const auto& stepResult = stepResults[static_cast<size_t>(idx)];
                if (stepResult.is_object() && stepResult.contains(field)) {
                    return stepResult[field];
                }
            }
        }
    }

    return toolArgs;
}

// ---- MCP Integration ----

bool Agent::connectMcpServer(const std::string& name, const json& config) {
    try {
        auto client = std::make_unique<MCPClient>(MCPClient::fromConfig(name, config, 30, config_.debug));
        if (!client->connect()) {
            console_->printError("Failed to connect to MCP server '" + name + "': " + client->lastError());
            return false;
        }

        // Store config for potential reconnect later
        mcpServerConfigs_[name] = config;

        // List tools and register them
        auto mcpTools = client->listTools();
        for (const auto& mcpTool : mcpTools) {
            ToolInfo toolInfo = mcpTool.toToolInfo(name);

            // Capture server name and tool name; use callMcpTool for auto-reconnect
            std::string serverName = name;
            std::string originalToolName = mcpTool.name;
            toolInfo.callback = [this, serverName, originalToolName](const json& args) -> json {
                return callMcpTool(serverName, originalToolName, args);
            };

            try {
                tools_.registerTool(std::move(toolInfo));
            } catch (const std::runtime_error&) {
                // Tool already registered, skip
            }
        }

        console_->printInfo("Connected to MCP server '" + name + "' with " +
                           std::to_string(mcpTools.size()) + " tools");

        mcpClients_.emplace(name, std::move(client));

        // Rebuild system prompt to include new tools
        rebuildSystemPrompt();
        return true;

    } catch (const std::exception& e) {
        console_->printError("Error connecting to MCP server '" + name + "': " + e.what());
        return false;
    }
}

json Agent::callMcpTool(const std::string& serverName, const std::string& toolName, const json& args) {
    auto it = mcpClients_.find(serverName);
    if (it == mcpClients_.end()) {
        return json{{"error", "MCP server '" + serverName + "' not found"}};
    }

    MCPClient* client = it->second.get();

    // First attempt — happy path
    if (client->isConnected()) {
        try {
            return client->callTool(toolName, args);
        } catch (const std::runtime_error& e) {
            console_->printWarning("MCP tool call failed: " + std::string(e.what()) +
                                   " -- attempting reconnect to '" + serverName + "'");
        }
    } else {
        console_->printWarning("MCP server '" + serverName + "' disconnected -- attempting reconnect");
    }

    // Reconnect once and retry
    if (!reconnectMcpServer(serverName)) {
        return json{{"error", "MCP server '" + serverName + "' disconnected and reconnect failed"}};
    }

    try {
        return mcpClients_[serverName]->callTool(toolName, args);
    } catch (const std::runtime_error& e) {
        return json{{"error", "MCP tool call failed after reconnect: " + std::string(e.what())}};
    }
}

bool Agent::reconnectMcpServer(const std::string& name) {
    auto cfgIt = mcpServerConfigs_.find(name);
    if (cfgIt == mcpServerConfigs_.end()) return false;

    // Drop the old (dead) client
    mcpClients_.erase(name);

    try {
        auto client = std::make_unique<MCPClient>(
            MCPClient::fromConfig(name, cfgIt->second, 30, config_.debug));
        if (!client->connect()) {
            console_->printError("MCP reconnect failed for '" + name + "': " + client->lastError());
            return false;
        }
        mcpClients_.emplace(name, std::move(client));
        console_->printInfo("Reconnected to MCP server '" + name + "'");
        return true;
    } catch (const std::exception& e) {
        console_->printError("MCP reconnect exception for '" + name + "': " + e.what());
        return false;
    }
}

void Agent::disconnectMcpServer(const std::string& name) {
    auto it = mcpClients_.find(name);
    if (it != mcpClients_.end()) {
        it->second->disconnect();
        mcpClients_.erase(it);
    }
}

void Agent::disconnectAllMcp() {
    for (auto& [name, client] : mcpClients_) {
        client->disconnect();
    }
    mcpClients_.clear();
}

// ---- Main Execution Loop ----

json Agent::processQuery(const std::string& userInput, int maxSteps) {
    int stepsLimit = (maxSteps > 0) ? maxSteps : config_.maxSteps;

    // Reset state
    executionState_ = AgentState::PLANNING;
    currentPlan_ = json();
    currentStep_ = 0;
    totalPlanSteps_ = 0;
    planIterations_ = 0;

    // Build conversation
    std::vector<Message> messages;

    // Prepopulate with history
    for (const auto& msg : conversationHistory_) {
        messages.push_back(msg);
    }

    // Add user message
    Message userMsg;
    userMsg.role = MessageRole::USER;
    userMsg.content = userInput;
    messages.push_back(userMsg);

    console_->printProcessingStart(userInput, stepsLimit, config_.modelId);

    int stepsTaken = 0;
    std::string finalAnswer;
    int errorCount = 0;
    std::string lastError;
    std::vector<json> stepResults;
    std::vector<std::pair<std::string, json>> toolCallHistory; // (name, args) for loop detection

    while (stepsTaken < stepsLimit && finalAnswer.empty()) {
        ++stepsTaken;
        console_->printStepHeader(stepsTaken, stepsLimit);

        // ---- Error Recovery ----
        if (executionState_ == AgentState::ERROR_RECOVERY) {
            console_->printStateInfo("ERROR RECOVERY: Handling previous error");

            Message errorMsg;
            errorMsg.role = MessageRole::USER;
            errorMsg.content =
                "TOOL EXECUTION FAILED!\n\n"
                "Error: " + lastError + "\n\n"
                "Original task: " + userInput + "\n\n"
                "Please analyze the error and try an alternative approach.\n"
                R"(Respond with {"thought": "...", "goal": "...", "tool": "...", "tool_args": {...}})";
            messages.push_back(errorMsg);

            executionState_ = AgentState::PLANNING;
            stepResults.clear();
        }

        // Call LLM (retry once on failure)
        console_->startProgress("Thinking");
        std::string response;
        try {
            response = callLlm(messages, systemPrompt());
        } catch (const std::exception& e) {
            console_->stopProgress();
            console_->printWarning(std::string("LLM call failed, retrying: ") + e.what());

            // Retry once
            console_->startProgress("Retrying");
            try {
                response = callLlm(messages, systemPrompt());
            } catch (const std::exception& e2) {
                console_->stopProgress();
                console_->printError(std::string("LLM error: ") + e2.what());
                finalAnswer = std::string("Unable to complete task due to LLM error: ") + e2.what();
                break;
            }
        }
        console_->stopProgress();

        // Debug: show response
        if (config_.showPrompts) {
            console_->printResponse(response, "LLM Response");
        }

        // Add LLM response to messages
        Message assistantMsg;
        assistantMsg.role = MessageRole::ASSISTANT;
        assistantMsg.content = response;
        messages.push_back(assistantMsg);

        // Parse response
        ParsedResponse parsed = parseLlmResponse(response);

        // Display reasoning
        console_->printThought(parsed.thought);
        console_->printGoal(parsed.goal);

        // ---- Handle final answer ----
        if (parsed.answer.has_value()) {
            finalAnswer = parsed.answer.value();
            console_->printFinalAnswer(finalAnswer);
            break;
        }

        // ---- Display plan if provided (advisory only — not auto-executed) ----
        if (parsed.plan.has_value() && parsed.plan.value().is_array()) {
            console_->printPlan(parsed.plan.value(), -1);
        }

        // ---- Handle tool call ----
        if (parsed.toolName.has_value()) {
            std::string toolName = parsed.toolName.value();
            json toolArgs = parsed.toolArgs.value_or(json::object());

            // Loop detection
            if (toolCallHistory.size() >= 4) {
                bool allSame = true;
                for (size_t i = toolCallHistory.size() - 3; i < toolCallHistory.size(); ++i) {
                    if (toolCallHistory[i].first != toolName) {
                        allSame = false;
                        break;
                    }
                }
                if (allSame && toolCallHistory.back().first == toolName) {
                    console_->printWarning("Detected repeated tool call loop. Breaking out.");
                    finalAnswer = "Task stopped due to repeated tool call loop.";
                    break;
                }
            }

            console_->printToolUsage(toolName);
            console_->prettyPrintJson(toolArgs, "Tool Args");
            console_->startProgress("Executing " + toolName);

            json toolResult = executeTool(toolName, toolArgs);

            console_->stopProgress();
            console_->printToolComplete();
            console_->prettyPrintJson(toolResult, "Tool Result");

            toolCallHistory.emplace_back(toolName, toolArgs);
            stepResults.push_back(toolResult);

            // Add tool result to messages
            Message toolMsg;
            toolMsg.role = MessageRole::TOOL;
            toolMsg.name = toolName;
            std::string resultStr = toolResult.dump();
            if (resultStr.size() > 20000) {
                resultStr = resultStr.substr(0, 10000) + "\n...[truncated]...\n" +
                            resultStr.substr(resultStr.size() - 5000);
            }
            toolMsg.content = resultStr;
            messages.push_back(toolMsg);

            // Check for error
            bool isError = toolResult.is_object() &&
                           toolResult.value("status", "") == "error";
            if (isError) {
                ++errorCount;
                lastError = toolResult.value("error", "Unknown error");
                executionState_ = AgentState::ERROR_RECOVERY;
            }

            continue;
        }

        // No tool call and no answer — treat response as conversational
        if (!parsed.toolName.has_value() && !parsed.answer.has_value()) {
            finalAnswer = response;
            console_->printFinalAnswer(finalAnswer);
            break;
        }
    }

    // Max steps reached without answer
    if (finalAnswer.empty()) {
        finalAnswer = "Reached maximum steps limit (" + std::to_string(stepsLimit) + " steps).";
        console_->printWarning(finalAnswer);
    }

    console_->printCompletion(stepsTaken, stepsLimit);

    // Store conversation history for session persistence.
    // Convert TOOL messages to USER messages so the LLM server can replay
    // them without requiring tool_call_id / tool_calls pairing.
    for (auto& msg : messages) {
        if (msg.role == MessageRole::TOOL) {
            std::string toolName = msg.name.value_or("tool");
            msg.role = MessageRole::USER;
            msg.content = "[Result from " + toolName + "]: " + msg.content;
            msg.name = std::nullopt;
            msg.toolCallId = std::nullopt;
        }
    }

    // Prune to maxHistoryMessages
    if (config_.maxHistoryMessages > 0 &&
        static_cast<int>(messages.size()) > config_.maxHistoryMessages) {
        messages.erase(messages.begin(),
                       messages.begin() + (static_cast<int>(messages.size()) - config_.maxHistoryMessages));
    }
    conversationHistory_ = messages;

    return json{
        {"result", finalAnswer},
        {"steps_taken", stepsTaken},
        {"steps_limit", stepsLimit}
    };
}

} // namespace gaia
