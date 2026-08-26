// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/agent.h"
#include "gaia/security.h"

#include <algorithm>
#include <iostream>
#include <regex>
#include <set>
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

// Conversational format template (mirrors Python Agent._CONVERSATIONAL_FORMAT)
const std::string Agent::CONVERSATIONAL_FORMAT_TEMPLATE = R"(
==== RESPONSE FORMAT ====
Respond in plain text for normal conversation.

When you need to call a tool, output ONLY a JSON object on a single line:
{"tool": "tool_name", "tool_args": {"arg1": "value1"}}

Use the full tool name exactly as registered. A name with only the
server prefix (e.g. ending in `_mcp`) is incomplete.

When responding conversationally (no tool call needed), just write plain text.
Do NOT wrap conversational replies in JSON.
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
        // The model decides whether native tool calling is on, which decides
        // whether the response-format template belongs in the prompt.
        systemPromptDirty_ = true;
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
    AgentConfig snapshot;
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        snapshot = config_;
    }
    return composeSystemPromptFor(snapshot);
}

std::string Agent::composeSystemPromptFor(const AgentConfig& cfg) const {
    std::ostringstream oss;

    // Agent-specific prompt
    std::string custom = getSystemPrompt();
    if (!custom.empty()) {
        oss << custom << "\n\n";
    }

    // Tool descriptions. Kept even under native tool calling — Python does the
    // same, and the prose list is what lets the model reason about which tool
    // fits before emitting the call.
    std::string toolsDesc = tools_.formatForPrompt();
    if (!toolsDesc.empty()) {
        oss << "==== AVAILABLE TOOLS ====\n" << toolsDesc << "\n";
    }

    // Response format — omitted only when the request will actually carry a
    // `tools` array, which is what makes tool-calling models emit tool_calls
    // instead of prose JSON. With no enabled tool there is no `tools` field, so
    // dropping the template too would leave the model with no protocol at all.
    const bool willSendTools = cfg.useNativeToolCalls() && !tools_.enabledTools().empty();
    if (!willSendTools) {
        oss << (cfg.responseMode == ResponseMode::Conversational
                    ? CONVERSATIONAL_FORMAT_TEMPLATE
                    : RESPONSE_FORMAT_TEMPLATE);
    }

    return oss.str();
}

// ---- LLM Communication ----

namespace {
/// Reject stream-assembled tool calls that never received an id or a name.
/// Sending them on would produce a tool reply with an empty ``tool_call_id``,
/// which a spec-strict server rejects on the next request.
void validateStreamedToolCalls(const std::vector<ToolCall>& calls) {
    for (size_t i = 0; i < calls.size(); ++i) {
        if (calls[i].id.empty()) {
            throw std::runtime_error(
                "Streamed tool_calls entry " + std::to_string(i) +
                " never received an 'id' across the SSE deltas, so its result "
                "cannot be correlated back to the call. Function name seen: '" +
                calls[i].name + "'.");
        }
        if (calls[i].name.empty()) {
            throw std::runtime_error(
                "Streamed tool_calls entry " + std::to_string(i) + " (id " +
                calls[i].id + ") never received a 'function.name', so there is "
                "no tool to execute.");
        }
    }
}

/// Parse a non-streaming ``message.tool_calls`` array. Throws (with the entry
/// echoed) rather than dropping malformed entries — a silently-skipped call
/// looks to the loop like the model chose not to act.
std::vector<ToolCall> parseToolCallsArray(const json& arr) {
    std::vector<ToolCall> calls;
    if (!arr.is_array()) {
        throw std::runtime_error(
            "Malformed LLM response: 'tool_calls' is a JSON " +
            std::string(arr.type_name()) + ", expected an array.");
    }
    calls.reserve(arr.size());
    for (const auto& entry : arr) {
        calls.push_back(ToolCall::fromJson(entry));
    }
    return calls;
}

UsageStats extractUsage(const json& responseJson) {
    UsageStats usage;
    if (responseJson.contains("usage") && responseJson["usage"].is_object()) {
        const auto& u = responseJson["usage"];
        usage.promptTokens = u.value("prompt_tokens", 0);
        usage.completionTokens = u.value("completion_tokens", 0);
        usage.totalTokens = u.value("total_tokens", 0);
    }
    return usage;
}
} // namespace

Agent::LlmResult Agent::callLlm(const std::vector<Message>& messages, const std::string& sysPrompt,
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

    // Native OpenAI tool calling — the request keeps exactly its legacy shape
    // when this is off, so non-tool-calling models see a byte-identical body.
    const bool nativeTools = cfg.useNativeToolCalls();
    if (nativeTools) {
        json toolSchemas = tools_.buildOpenAiToolSchemas();
        if (!toolSchemas.empty()) {
            requestBody["tools"] = std::move(toolSchemas);
            requestBody["tool_choice"] = cfg.toolChoice;
        }
    }

    if (cfg.debug) {
        std::cerr << "[LLM] POST /chat/completions, messages=" << msgArray.size()
                  << ", native_tools=" << (requestBody.contains("tools") ? "yes" : "no")
                  << std::endl;
    }

    // ---- Streaming path ----
    if (config_.streaming) {
        std::string accumulated;
        std::vector<ToolCall> streamedToolCalls;
        std::string rawResponse = lemonade_.chatCompletionsStreaming(
            requestBody,
            [this, &accumulated](const std::string& token) {
                accumulated += token;
                console_->printStreamToken(token);
            },
            streamedToolCalls
        );

        // Never consume tool_calls we did not ask for. Some servers volunteer
        // them (llama.cpp --jinja) even with no `tools` in the request; acting
        // on that would send an assistant tool_calls turn back with no `tools`
        // field, which strict servers reject — and would break an agent that
        // explicitly opted out via NativeToolCalls::Never.
        if (!nativeTools) streamedToolCalls.clear();

        // A tool-calling model answers with tool_calls deltas and often no
        // content at all, so this is checked before the empty-stream error.
        if (!streamedToolCalls.empty()) {
            validateStreamedToolCalls(streamedToolCalls);
            if (!accumulated.empty()) console_->printStreamEnd();
            UsageStats usage;
            try {
                usage = extractUsage(json::parse(rawResponse));
            } catch (...) {}
            if (cfg.debug) {
                std::cerr << "[LLM] streamed tool_calls=" << streamedToolCalls.size() << std::endl;
            }
            return {accumulated, usage, std::move(streamedToolCalls)};
        }

        if (!accumulated.empty()) {
            console_->printStreamEnd();
            // Streaming responses may include usage in the final chunk;
            // attempt to extract from the raw bytes.
            UsageStats usage;
            try {
                const json responseJson = json::parse(rawResponse);
                usage = extractUsage(responseJson);
            } catch (...) {}
            return {accumulated, usage, {}};
        }

        // Fallback: server returned a non-streaming response despite "stream":true.
        // Parse the raw response body as a regular chat completions reply.
        if (!rawResponse.empty()) {
            try {
                const json responseJson = json::parse(rawResponse);
                if (responseJson.contains("choices") && !responseJson["choices"].empty()) {
                    const auto& choice = responseJson["choices"][0];
                    if (choice.contains("message")) {
                        const auto& message = choice["message"];
                        if (nativeTools && message.contains("tool_calls")
                            && !message["tool_calls"].is_null()
                            && !message["tool_calls"].empty()) {
                            std::string text;
                            if (message.contains("content") && message["content"].is_string()) {
                                text = message["content"].get<std::string>();
                            }
                            return {text, extractUsage(responseJson),
                                    parseToolCallsArray(message["tool_calls"])};
                        }
                        if (message.contains("content") && message["content"].is_string()) {
                            return {message["content"].get<std::string>(),
                                    extractUsage(responseJson), {}};
                        }
                    }
                }
            } catch (const std::runtime_error&) {
                throw; // malformed tool_calls — surface it, don't fall through
            } catch (...) {}
        }

        throw std::runtime_error("Streaming response contained no tokens");
    }

    // ---- Non-streaming path ----
    std::string responseBody = lemonade_.chatCompletions(requestBody);

    // Parse response
    json responseJson;
    try {
        responseJson = json::parse(responseBody);
    } catch (const json::parse_error& e) {
        std::string preview = responseBody.substr(0, 200);
        throw std::runtime_error(std::string("Failed to parse LLM response: ") + e.what() +
                                 " | body: " + preview);
    }

    if (responseJson.contains("choices") && !responseJson["choices"].empty()) {
        const auto& choice = responseJson["choices"][0];
        if (choice.contains("message")) {
            const auto& message = choice["message"];
            // tool_calls first: a native call carries content: null, which the
            // content-only check below would reject as an unexpected format.
            // Gated on nativeTools — a server that volunteers tool_calls we did
            // not request must not knock an opted-out agent off the prompt-JSON
            // path (its `content` is still the answer we want).
            if (nativeTools && message.contains("tool_calls")
                && !message["tool_calls"].is_null()
                && !message["tool_calls"].empty()) {
                std::string text;
                if (message.contains("content") && message["content"].is_string()) {
                    text = message["content"].get<std::string>();
                }
                return {text, extractUsage(responseJson),
                        parseToolCallsArray(message["tool_calls"])};
            }
            if (message.contains("content") && message["content"].is_string()) {
                return {message["content"].get<std::string>(), extractUsage(responseJson), {}};
            }
        }
    }
    // Include truncated response body in error for debugging
    throw std::runtime_error("Unexpected LLM response format: " + responseBody.substr(0, 200));
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

            // toToolInfo() already stamped CONFIRM unless the server proved the
            // tool read-only. That verdict is a floor: a stricter registry
            // default may raise it, but nothing may lower it back to ALLOW.
            toolInfo.policy = stricterPolicy(toolInfo.policy, tools_.defaultPolicy());

            // Capture server name and tool name; use callMcpTool for auto-reconnect
            std::string serverName = name;
            std::string originalToolName = mcpTool.name;
            toolInfo.callback = [this, serverName, originalToolName](const json& args) -> json {
                return callMcpTool(serverName, originalToolName, args);
            };

            try {
                tools_.registerTool(std::move(toolInfo));
            } catch (const std::runtime_error& e) {
                // Name collision — the already-registered tool keeps its own
                // policy, so say which one was dropped rather than swallowing it.
                console_->printError("MCP tool '" + mcpTool.name + "' from server '" + name +
                                     "' was not registered: " + e.what());
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

bool Agent::connectMcpServerById(const std::string& id) {
    MCPRegistry registry;
    return connectMcpServerById(id, registry);
}

bool Agent::connectMcpServerById(const std::string& id, const MCPRegistry& registry) {
    // require() throws MCPRegistryError naming the id, the paths searched, and
    // the ids that are available — deliberately not swallowed here.
    return connectMcpServer(id, registry.require(id));
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

// Public overloads delegate to processQueryInternal as their FIRST action.
// No partial delegation. No direct conversationHistory_ writes outside internal.

json Agent::processQuery(const std::string& userInput, int maxSteps) {
    Message m;
    m.role = MessageRole::USER;
    m.content = userInput;
    return processQueryInternal({m}, maxSteps);
}

json Agent::processQuery(const std::string& userInput,
                         const std::vector<Image>& images,
                         int maxSteps) {
    Message m = Message::fromUser(userInput, images);
    return processQueryInternal({m}, maxSteps);
}

json Agent::processQuery(const std::vector<Message>& messages, int maxSteps) {
    return processQueryInternal(messages, maxSteps);
}

// RAII helper: atomic flip-flop. Flips inFlight_ true via
// compare_exchange_strong; restores on scope exit. Throws if already set.
namespace {
class InFlightGuard {
public:
    explicit InFlightGuard(std::atomic<bool>& flag) : flag_(flag) {
        bool expected = false;
        if (!flag_.compare_exchange_strong(expected, true)) {
            throw std::runtime_error("Agent::processQuery is not re-entrant");
        }
    }
    ~InFlightGuard() { flag_.store(false); }
    InFlightGuard(const InFlightGuard&) = delete;
    InFlightGuard& operator=(const InFlightGuard&) = delete;
private:
    std::atomic<bool>& flag_;
};

// Strip any ContentPart{IMAGE_URL} parts, leaving only text. Concatenates
// all text parts into `content` and clears `parts`. Tool/assistant/system
// messages are unaffected.
Message stripImageParts(Message msg) {
    if (!msg.parts.has_value()) return msg;
    std::string merged;
    for (const auto& p : *msg.parts) {
        if (p.kind == ContentPart::Kind::TEXT) {
            if (!merged.empty()) merged.push_back('\n');
            merged += p.text;
        }
    }
    msg.content = std::move(merged);
    msg.parts.reset();
    return msg;
}

// Build a summary string describing the user's input for printProcessingStart.
std::string summarizeUserInput(const std::vector<Message>& userMessages) {
    if (userMessages.empty()) return "";
    // Use the last user-role message for the banner; if it has parts, extract text.
    for (auto it = userMessages.rbegin(); it != userMessages.rend(); ++it) {
        if (it->role == MessageRole::USER) {
            if (it->parts.has_value()) {
                std::string acc;
                int imgCount = 0;
                for (const auto& p : *it->parts) {
                    if (p.kind == ContentPart::Kind::TEXT) {
                        if (!acc.empty()) acc.push_back(' ');
                        acc += p.text;
                    } else {
                        ++imgCount;
                    }
                }
                if (imgCount > 0) {
                    acc += " [" + std::to_string(imgCount) + " image(s)]";
                }
                return acc;
            }
            return it->content;
        }
    }
    // No USER-role message found — use the first message's content as a
    // last resort (cosmetic only; validation above ensures input is non-empty).
    for (const auto& m : userMessages) {
        if (!m.content.empty()) return m.content;
    }
    return "";
}

// Loop detection: true when the previous (maxConsecutiveRepeats - 1) calls were
// all this exact tool+args, making the pending one the Nth identical call.
bool isRepeatedToolCall(const std::vector<std::pair<std::string, json>>& history,
                        const std::string& toolName, const json& toolArgs,
                        int maxConsecutiveRepeats) {
    const int repeatThreshold = maxConsecutiveRepeats - 1;
    // A threshold of 0 vacuously matches — preserved from the inline version
    // this replaced. AgentConfig::validate() requires maxConsecutiveRepeats >= 2,
    // so it is only reachable from an unvalidated hand-built config.
    if (static_cast<int>(history.size()) < repeatThreshold) return false;
    for (size_t i = history.size() - static_cast<size_t>(repeatThreshold);
         i < history.size(); ++i) {
        if (history[i].first != toolName || history[i].second != toolArgs) {
            return false;
        }
    }
    return true;
}

// Serialize a tool result for the model, clipping the middle of oversized
// payloads so both the head and the tail survive.
std::string truncateToolResult(const json& toolResult) {
    std::string resultStr = toolResult.dump();
    if (resultStr.size() > 4000) {
        resultStr = resultStr.substr(0, 2000) + "\n...[truncated]...\n" +
                    resultStr.substr(resultStr.size() - 1500);
    }
    return resultStr;
}

// Make native tool_calls and their role=tool replies consistent before the
// history is stored. OpenAI-compatible servers reject both halves of a broken
// pair, and history is replayed verbatim on the next turn.
//
// Two ways a pair breaks:
//   - An assistant turn advertises a call that was never answered (the run was
//     cancelled between two parallel calls).
//   - A role=tool reply outlives the assistant turn that requested it (history
//     pruning trims from the head).
void repairToolCallPairing(std::vector<Message>& messages) {
    // Pass 1: which tool_call_ids actually got a reply.
    std::set<std::string> answered;
    for (const auto& msg : messages) {
        if (msg.role == MessageRole::TOOL && msg.toolCallId.has_value()) {
            answered.insert(*msg.toolCallId);
        }
    }

    // Pass 2: drop unanswered calls, then drop replies with no surviving call.
    std::set<std::string> requested;
    std::vector<Message> kept;
    kept.reserve(messages.size());
    for (auto& msg : messages) {
        if (msg.role == MessageRole::ASSISTANT && !msg.toolCalls.empty()) {
            std::vector<ToolCall> live;
            for (auto& tc : msg.toolCalls) {
                if (answered.count(tc.id)) {
                    requested.insert(tc.id);
                    live.push_back(std::move(tc));
                }
            }
            msg.toolCalls = std::move(live);
            // An assistant turn with neither content nor calls says nothing.
            if (msg.toolCalls.empty() && msg.content.empty() && !msg.parts.has_value()) {
                continue;
            }
        } else if (msg.role == MessageRole::TOOL && msg.toolCallId.has_value()) {
            if (!requested.count(*msg.toolCallId)) {
                continue; // its assistant turn is gone
            }
        }
        kept.push_back(std::move(msg));
    }
    messages = std::move(kept);
}
} // namespace

void Agent::setHistory(std::vector<Message> history) {
    if (inFlight_.load()) {
        throw std::runtime_error("Cannot set history while processQuery() is running");
    }
    repairToolCallPairing(history);
    conversationHistory_ = std::move(history);
}

json Agent::processQueryInternal(const std::vector<Message>& userMessages, int maxSteps) {
    // Empty-input validation FIRST — no HTTP call, no /load.
    if (userMessages.empty()) {
        throw std::invalid_argument("Agent::processQuery: empty message list");
    }
    bool anyNonEmpty = false;
    for (const auto& m : userMessages) {
        if (!m.content.empty()) { anyNonEmpty = true; break; }
        if (m.parts.has_value()) {
            // A parts vector counts as non-empty only if it has at least one
            // IMAGE part or a TEXT part with non-empty text. An empty vector
            // or a vector containing only empty-text stubs is rejected.
            for (const auto& part : *m.parts) {
                if (part.kind == ContentPart::Kind::IMAGE_URL) { anyNonEmpty = true; break; }
                if (part.kind == ContentPart::Kind::TEXT && !part.text.empty()) {
                    anyNonEmpty = true; break;
                }
            }
        }
        if (anyNonEmpty) break;
    }
    if (!anyNonEmpty) {
        throw std::invalid_argument("Agent::processQuery: all user messages are empty");
    }

    // Re-entrancy guard (RAII — releases on any exit path incl. exceptions).
    InFlightGuard guard(inFlight_);

    // Reset cancel flag at the start of each query.
    cancelled_.store(false);

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

    // Append caller-supplied user messages verbatim (may contain image parts).
    for (const auto& m : userMessages) {
        messages.push_back(m);
    }

    const std::string userInput = summarizeUserInput(userMessages);

    console_->printProcessingStart(userInput, stepsLimit, cfg.modelId);

    int stepsTaken = 0;
    std::string finalAnswer;
    int errorCount = 0;
    std::string lastError;
    std::vector<json> stepResults;
    std::vector<std::pair<std::string, json>> toolCallHistory; // (name, args) for loop detection
    UsageStats totalUsage;

    while (stepsTaken < stepsLimit && finalAnswer.empty()) {
        // ---- Cancel check ----
        if (cancelled_.load()) {
            console_->printWarning("Cancelled by user");
            finalAnswer = "[Cancelled after " + std::to_string(stepsTaken) + " step(s)]";
            break;
        }

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
                "Please analyze the error and try an alternative approach.\n";
            // The envelope reminder only applies to the prompt-JSON path; under
            // native tool calling it would fight the function-calling protocol.
            if (!cfg.useNativeToolCalls()) {
                errorMsg.content +=
                    R"(Respond with {"thought": "...", "goal": "...", "tool": "...", "tool_args": {...}})";
            }
            messages.push_back(errorMsg);

            executionState_ = AgentState::PLANNING;
            stepResults.clear();
        }

        // Recomposed each step so mid-loop tool changes are visible, but always
        // from the turn's config snapshot — the same one callLlm() uses to
        // decide whether to send `tools`.
        const std::string turnSystemPrompt = composeSystemPromptFor(cfg);

        // Call LLM (retry once on failure).
        // Skip progress spinner when streaming — tokens serve as live progress.
        if (!config_.streaming) console_->startProgress("Thinking");
        LlmResult llmResult;
        try {
            llmResult = callLlm(messages, turnSystemPrompt, cfg);
        } catch (const std::exception& e) {
            if (!config_.streaming) console_->stopProgress();
            console_->printWarning(std::string("LLM call failed, retrying: ") + e.what());

            // Retry once
            if (!config_.streaming) console_->startProgress("Retrying");
            try {
                llmResult = callLlm(messages, turnSystemPrompt, cfg);
            } catch (const std::exception& e2) {
                if (!config_.streaming) console_->stopProgress();
                console_->printError(std::string("LLM error: ") + e2.what());
                finalAnswer = std::string("Unable to complete task due to LLM error: ") + e2.what();
                break;
            }
        }
        if (!config_.streaming) console_->stopProgress();

        const std::string& response = llmResult.content;
        totalUsage += llmResult.usage;

        // Debug: show response
        if (cfg.showPrompts) {
            console_->printResponse(response, "LLM Response");
        }

        // ---- Native OpenAI tool_calls branch ----
        // Emits a spec-correct assistant turn carrying tool_calls, then one
        // role=tool reply per call keyed by tool_call_id. Handles parallel
        // calls: a single response may carry several.
        if (!llmResult.toolCalls.empty()) {
            // Decode every argument payload BEFORE running anything. A batch
            // where call 2 is malformed must not leave call 1's side effects
            // behind an exception that unwinds past the history write — and
            // processQuery() is documented to report errors in "result", not
            // to throw.
            std::vector<json> decodedArgs;
            decodedArgs.reserve(llmResult.toolCalls.size());
            std::string decodeError;
            for (const auto& tc : llmResult.toolCalls) {
                try {
                    decodedArgs.push_back(tc.parsedArgs());
                } catch (const std::exception& e) {
                    decodeError = e.what();
                    break;
                }
            }
            if (!decodeError.empty()) {
                console_->printError("Malformed tool call: " + decodeError);
                finalAnswer = "Unable to complete task due to LLM error: " + decodeError;
                break;
            }

            Message nativeAssistantMsg;
            nativeAssistantMsg.role = MessageRole::ASSISTANT;
            nativeAssistantMsg.content = response;
            nativeAssistantMsg.toolCalls = llmResult.toolCalls;
            messages.push_back(nativeAssistantMsg);

            // structuredEvents consumers (TUI/WebUI) expect a thought per step;
            // a native turn's reasoning is whatever prose came with the calls.
            if (!config_.streaming || config_.structuredEvents) {
                console_->printThought(response);
            }

            bool loopDetected = false;
            for (size_t i = 0; i < llmResult.toolCalls.size(); ++i) {
                const auto& tc = llmResult.toolCalls[i];
                if (cancelled_.load()) break;

                const json& toolArgs = decodedArgs[i];

                if (isRepeatedToolCall(toolCallHistory, tc.name, toolArgs,
                                       cfg.maxConsecutiveRepeats)) {
                    console_->printWarning("Detected repeated tool call loop. Breaking out.");
                    finalAnswer = "Task stopped due to repeated tool call loop.";
                    loopDetected = true;
                    break;
                }

                console_->printToolUsage(tc.name);
                console_->prettyPrintJson(toolArgs, "Tool Args");
                console_->startProgress("Executing " + tc.name);

                json toolResult = executeTool(tc.name, toolArgs);

                console_->stopProgress();
                console_->printToolComplete();
                console_->prettyPrintJson(toolResult, "Tool Result");

                toolCallHistory.emplace_back(tc.name, toolArgs);
                stepResults.push_back(toolResult);

                Message toolMsg;
                toolMsg.role = MessageRole::TOOL;
                toolMsg.name = tc.name;
                toolMsg.toolCallId = tc.id;
                toolMsg.content = truncateToolResult(toolResult);
                messages.push_back(toolMsg);

                if (toolResult.is_object() && toolResult.value("status", "") == "error") {
                    ++errorCount;
                    lastError = toolResult.value("error", "Unknown error");
                    executionState_ = AgentState::ERROR_RECOVERY;
                }
            }

            if (loopDetected) break;
            continue;
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
        // Exception: structuredEvents mode emits both stream tokens AND structured events,
        // so the TUI/WebUI gets live progress AND parsed agent activity.
        if (!config_.streaming || config_.structuredEvents) {
            console_->printThought(parsed.thought);
            console_->printGoal(parsed.goal);
        }

        // ---- Handle final answer ----
        if (parsed.answer.has_value()) {
            finalAnswer = parsed.answer.value();
            if (!config_.streaming || config_.structuredEvents) console_->printFinalAnswer(finalAnswer, totalUsage);
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
            if (isRepeatedToolCall(toolCallHistory, toolName, toolArgs,
                                   cfg.maxConsecutiveRepeats)) {
                console_->printWarning("Detected repeated tool call loop. Breaking out.");
                finalAnswer = "Task stopped due to repeated tool call loop.";
                break;
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
            toolMsg.content = truncateToolResult(toolResult);
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
            if (!config_.streaming || config_.structuredEvents) console_->printFinalAnswer(finalAnswer, totalUsage);
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
    // Prompt-JSON tool results are converted to USER messages so the LLM server
    // can replay them without tool_call_id / tool_calls pairing. Native tool
    // results keep their spec-correct role=tool + tool_call_id shape — the
    // matching assistant turn carrying tool_calls is in history alongside them.
    // Strip image parts (base64 data URIs) so they don't accumulate in
    // history across turns — text-only retention by contract.
    for (auto& msg : messages) {
        if (msg.role == MessageRole::TOOL && !msg.toolCallId.has_value()) {
            std::string toolName = msg.name.value_or("tool");
            msg.role = MessageRole::USER;
            msg.content = "[Result from " + toolName + "]: " + msg.content;
            msg.name = std::nullopt;
        }
        if (msg.parts.has_value()) {
            msg = stripImageParts(std::move(msg));
        }
    }

    // Prune to maxHistoryMessages
    if (cfg.maxHistoryMessages > 0 &&
        static_cast<int>(messages.size()) > cfg.maxHistoryMessages) {
        messages.erase(messages.begin(),
                       messages.begin() + (static_cast<int>(messages.size()) - cfg.maxHistoryMessages));
    }
    repairToolCallPairing(messages);
    conversationHistory_ = messages;

    json result = {
        {"result", finalAnswer},
        {"steps_taken", stepsTaken},
        {"steps_limit", stepsLimit}
    };
    if (totalUsage.totalTokens > 0) {
        result["usage"] = totalUsage.toJson();
    }
    return result;
}

} // namespace gaia
