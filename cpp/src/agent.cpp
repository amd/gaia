// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/agent.h"
#include "gaia/security.h"

#include <iostream>
#include <regex>
#include <sstream>
#include <stdexcept>

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
    : config_(config),
      lemonade_(LemonadeClientConfig{config.baseUrl, config.modelId, config.contextSize, config.debug}) {

    // GAIA_BASE_URL / GAIA_CPP_BASE_URL (deprecated fallback)
    std::string envUrl = getEnvVar("GAIA_BASE_URL");
    if (envUrl.empty()) {
        envUrl = getEnvVar("GAIA_CPP_BASE_URL");
        if (!envUrl.empty()) {
            std::cerr << "[GAIA] GAIA_CPP_BASE_URL is deprecated; use GAIA_BASE_URL instead\n";
        }
    }
    if (!envUrl.empty()) {
        config_.baseUrl = envUrl;
        lemonade_.setBaseUrl(config_.baseUrl);
    }

    // GAIA_MODEL_ID
    std::string envModel = getEnvVar("GAIA_MODEL_ID");
    if (!envModel.empty()) {
        config_.modelId = envModel;
        lemonade_.setModel(config_.modelId);
    }

    // GAIA_MAX_STEPS
    std::string envMaxSteps = getEnvVar("GAIA_MAX_STEPS");
    if (!envMaxSteps.empty()) {
        try {
            int val = std::stoi(envMaxSteps);
            if (val > 0) { config_.maxSteps = val; }
            else { std::cerr << "[GAIA] GAIA_MAX_STEPS must be > 0; ignoring value " << val << "\n"; }
        } catch (const std::exception&) {
            std::cerr << "[GAIA] GAIA_MAX_STEPS='" << envMaxSteps << "' is not a valid integer; ignoring\n";
        }
    }

    // GAIA_CONTEXT_SIZE / GAIA_CPP_CTX_SIZE (deprecated fallback)
    std::string envCtx = getEnvVar("GAIA_CONTEXT_SIZE");
    if (envCtx.empty()) {
        envCtx = getEnvVar("GAIA_CPP_CTX_SIZE");
        if (!envCtx.empty()) {
            std::cerr << "[GAIA] GAIA_CPP_CTX_SIZE is deprecated; use GAIA_CONTEXT_SIZE instead\n";
        }
    }
    if (!envCtx.empty()) {
        try {
            int val = std::stoi(envCtx);
            if (val > 0) {
                config_.contextSize = val;
                lemonade_.setContextSize(config_.contextSize);
            } else {
                std::cerr << "[GAIA] GAIA_CONTEXT_SIZE must be > 0; ignoring value " << val << "\n";
            }
        } catch (const std::exception&) {
            std::cerr << "[GAIA] GAIA_CONTEXT_SIZE='" << envCtx << "' is not a valid integer; ignoring\n";
        }
    }

    // GAIA_MAX_TOKENS
    std::string envMaxTokens = getEnvVar("GAIA_MAX_TOKENS");
    if (!envMaxTokens.empty()) {
        try {
            int val = std::stoi(envMaxTokens);
            if (val > 0) { config_.maxTokens = val; }
            else { std::cerr << "[GAIA] GAIA_MAX_TOKENS must be > 0; ignoring value " << val << "\n"; }
        } catch (const std::exception&) {
            std::cerr << "[GAIA] GAIA_MAX_TOKENS='" << envMaxTokens << "' is not a valid integer; ignoring\n";
        }
    }

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

    // Create shared allowed-tools store and inject into the registry
    allowedToolsStore_ = std::make_shared<AllowedToolsStore>();
    tools_.setAllowedToolsStore(allowedToolsStore_);

    // Auto-install terminal confirm callback for interactive agents
    if (!config_.silentMode) {
        tools_.setConfirmCallback(makeStdinConfirmCallback());
    }

    // System prompt will be composed lazily
    systemPromptDirty_ = true;
}

AgentConfig Agent::config() const {
    std::lock_guard<std::mutex> lock(configMutex_);
    return config_;
}

void Agent::setConfig(const AgentConfig& newConfig) {
    newConfig.validate();
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        config_ = newConfig;
        modelEnsured_ = false;
        systemPromptDirty_ = true;
    }
    // Update LemonadeClient outside configMutex_ to avoid holding the lock
    // across external calls (guards against future LemonadeClient → Agent callbacks).
    lemonade_.setBaseUrl(newConfig.baseUrl);
    lemonade_.setModel(newConfig.modelId);
    lemonade_.setContextSize(newConfig.contextSize);
    lemonade_.setDebug(newConfig.debug);
}

void Agent::setModel(const std::string& modelId) {
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        config_.modelId = modelId;
        modelEnsured_ = false;
    }
    lemonade_.setModel(modelId);
}

void Agent::setMaxSteps(int maxSteps) {
    if (maxSteps <= 0)
        throw std::invalid_argument("maxSteps must be > 0");
    std::lock_guard<std::mutex> lock(configMutex_);
    config_.maxSteps = maxSteps;
}

void Agent::setMaxTokens(int maxTokens) {
    if (maxTokens <= 0)
        throw std::invalid_argument("maxTokens must be > 0");
    std::lock_guard<std::mutex> lock(configMutex_);
    config_.maxTokens = maxTokens;
}

void Agent::setTemperature(double temperature) {
    if (temperature < 0.0 || temperature > 2.0)
        throw std::invalid_argument("temperature must be in [0.0, 2.0]");
    std::lock_guard<std::mutex> lock(configMutex_);
    config_.temperature = temperature;
}

void Agent::setDebug(bool debug) {
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        config_.debug = debug;
    }
    lemonade_.setDebug(debug);
}

void Agent::setToolConfirmCallback(ToolConfirmCallback cb) {
    tools_.setConfirmCallback(std::move(cb));
}

void Agent::setDefaultPolicy(ToolPolicy policy) {
    tools_.setDefaultPolicy(policy);
}

Agent::~Agent() {
    disconnectAllMcp();
}

void Agent::setOutputHandler(std::unique_ptr<OutputHandler> handler) {
    console_ = std::move(handler);
}

std::string Agent::systemPrompt() const {
    // Check under lock; return cached if still fresh.
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        if (!systemPromptDirty_) {
            return cachedSystemPrompt_;
        }
    }
    // Recompute WITHOUT holding configMutex_ — composeSystemPrompt() calls the
    // virtual getSystemPrompt(), and a subclass override may legally call back
    // into Agent methods (e.g. config()) that also acquire configMutex_.
    // Holding the lock here would cause a deadlock in that case.
    std::string newPrompt = composeSystemPrompt();
    // Re-acquire lock and re-check: a concurrent thread may have already
    // recomputed and stored a fresh prompt since we released the lock above.
    std::lock_guard<std::mutex> lock(configMutex_);
    if (systemPromptDirty_) {
        cachedSystemPrompt_ = std::move(newPrompt);
        systemPromptDirty_ = false;
    }
    return cachedSystemPrompt_;
}

void Agent::rebuildSystemPrompt() {
    std::lock_guard<std::mutex> lock(configMutex_);
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

std::string Agent::callLlm(const std::vector<Message>& messages, const std::string& sysPrompt,
                           const AgentConfig& cfg) {
    // Build OpenAI-compatible request.
    // NOTE: n_ctx is intentionally omitted — context size is set at model load
    // time via LemonadeClient::loadModel() / ensureModelLoaded(), not per-request.
    json requestBody;
    requestBody["model"] = cfg.modelId;
    requestBody["max_tokens"] = cfg.maxTokens;
    requestBody["temperature"] = cfg.temperature;

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

    if (cfg.debug) {
        std::cerr << "[LLM] POST /chat/completions, messages=" << msgArray.size() << std::endl;
    }

    // ---- Streaming path ----
    if (config_.streaming) {
        std::string accumulated;
        std::string rawResponse = lemonade_.chatCompletionsStreaming(
            requestBody,
            [this, &accumulated](const std::string& token) {
                accumulated += token;
                console_->printStreamToken(token);
            }
        );

        if (!accumulated.empty()) {
            console_->printStreamEnd();
            return accumulated;
        }

        // Fallback: server returned a non-streaming response despite "stream":true.
        // Parse the raw response body as a regular chat completions reply.
        if (!rawResponse.empty()) {
            try {
                const json responseJson = json::parse(rawResponse);
                if (responseJson.contains("choices") && !responseJson["choices"].empty()) {
                    const auto& choice = responseJson["choices"][0];
                    if (choice.contains("message") && choice["message"].contains("content")) {
                        return choice["message"]["content"].get<std::string>();
                    }
                }
            } catch (...) {}
        }

        throw std::runtime_error("Streaming response contained no tokens");
    }

    // ---- Non-streaming path (unchanged) ----
    std::string responseBody = lemonade_.chatCompletions(requestBody);

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
    bool debugMode;
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        debugMode = config_.debug;
    }
    try {
        auto client = std::make_unique<MCPClient>(MCPClient::fromConfig(name, config, 30, debugMode));
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

            // Bake the current default policy into the ToolInfo at registration time.
            toolInfo.policy = tools_.defaultPolicy();

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

    bool debugMode;
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        debugMode = config_.debug;
    }

    // Drop the old (dead) client
    mcpClients_.erase(name);

    try {
        auto client = std::make_unique<MCPClient>(
            MCPClient::fromConfig(name, cfgIt->second, 30, debugMode));
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
    // Snapshot config at start of query for thread-safe consistency throughout.
    AgentConfig cfg;
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        cfg = config_;
    }

    int stepsLimit = (maxSteps > 0) ? maxSteps : cfg.maxSteps;

    // Ensure the model is loaded with the requested context size (once per agent lifetime).
    // Context size is a server-side setting applied at load time, not per-request.
    if (!modelEnsured_ && !cfg.modelId.empty()) {
        try {
            lemonade_.ensureModelLoaded(); // uses stored model_ and contextSize_
            modelEnsured_ = true;
        } catch (const std::exception& e) {
            console_->printWarning(std::string("Could not ensure model loaded: ") + e.what());
        }
    }

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

    console_->printProcessingStart(userInput, stepsLimit, cfg.modelId);

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

        // Call LLM (retry once on failure).
        // Skip progress spinner when streaming — tokens serve as live progress.
        if (!config_.streaming) console_->startProgress("Thinking");
        std::string response;
        try {
            response = callLlm(messages, systemPrompt(), cfg);
        } catch (const std::exception& e) {
            if (!config_.streaming) console_->stopProgress();
            console_->printWarning(std::string("LLM call failed, retrying: ") + e.what());

            // Retry once
            if (!config_.streaming) console_->startProgress("Retrying");
            try {
                response = callLlm(messages, systemPrompt(), cfg);
            } catch (const std::exception& e2) {
                if (!config_.streaming) console_->stopProgress();
                console_->printError(std::string("LLM error: ") + e2.what());
                finalAnswer = std::string("Unable to complete task due to LLM error: ") + e2.what();
                break;
            }
        }
        if (!config_.streaming) console_->stopProgress();

        // Debug: show response
        if (cfg.showPrompts) {
            console_->printResponse(response, "LLM Response");
        }

        // Add LLM response to messages
        Message assistantMsg;
        assistantMsg.role = MessageRole::ASSISTANT;
        assistantMsg.content = response;
        messages.push_back(assistantMsg);

        // Parse response
        ParsedResponse parsed = parseLlmResponse(response);

        // Display reasoning.
        // Skip when streaming — the raw tokens were already printed during callLlm().
        if (!config_.streaming) {
            console_->printThought(parsed.thought);
            console_->printGoal(parsed.goal);
        }

        // ---- Handle final answer ----
        if (parsed.answer.has_value()) {
            finalAnswer = parsed.answer.value();
            if (!config_.streaming) console_->printFinalAnswer(finalAnswer);
            break;
        }

        // ---- Display plan if provided (advisory only — not auto-executed) ----
        if (parsed.plan.has_value() && parsed.plan.value().is_array()) {
            ++planIterations_;
            console_->printPlan(parsed.plan.value(), -1);
            if (planIterations_ > cfg.maxPlanIterations) {
                Message forceMsg;
                forceMsg.role = MessageRole::USER;
                forceMsg.content =
                    "You have been planning too long without completing the task. "
                    "Please provide a final answer now based on the information you have gathered.";
                messages.push_back(forceMsg);
            }
        }

        // ---- Handle tool call ----
        if (parsed.toolName.has_value()) {
            std::string toolName = parsed.toolName.value();
            json toolArgs = parsed.toolArgs.value_or(json::object());
            if (toolArgs.is_null()) toolArgs = json::object();

            // Loop detection — same tool+args repeated maxConsecutiveRepeats times
            {
                int repeatThreshold = cfg.maxConsecutiveRepeats - 1;
                if (static_cast<int>(toolCallHistory.size()) >= repeatThreshold) {
                    bool allSame = true;
                    for (size_t i = toolCallHistory.size() - static_cast<size_t>(repeatThreshold);
                         i < toolCallHistory.size(); ++i) {
                        if (toolCallHistory[i].first != toolName ||
                            toolCallHistory[i].second != toolArgs) {
                            allSame = false;
                            break;
                        }
                    }
                    if (allSame) {
                        console_->printWarning("Detected repeated tool call loop. Breaking out.");
                        finalAnswer = "Task stopped due to repeated tool call loop.";
                        break;
                    }
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
            if (resultStr.size() > 4000) {
                resultStr = resultStr.substr(0, 2000) + "\n...[truncated]...\n" +
                            resultStr.substr(resultStr.size() - 1500);
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
            if (!config_.streaming) console_->printFinalAnswer(finalAnswer);
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
    if (cfg.maxHistoryMessages > 0 &&
        static_cast<int>(messages.size()) > cfg.maxHistoryMessages) {
        messages.erase(messages.begin(),
                       messages.begin() + (static_cast<int>(messages.size()) - cfg.maxHistoryMessages));
    }
    conversationHistory_ = messages;

    return json{
        {"result", finalAnswer},
        {"steps_taken", stepsTaken},
        {"steps_limit", stepsLimit}
    };
}

} // namespace gaia
