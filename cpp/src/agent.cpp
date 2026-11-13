// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/agent.h"
#include "gaia/http_client.h"
#include "gaia/model_registry.h"
#include "gaia/process.h"
#include "gaia/security.h"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <thread>
#include <deque>
#include <iostream>
#include <iterator>
#include <limits>
#include <optional>
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

    config_.apiKey = resolveApiKey(config_);
    lemonade_.setApiKey(config_.apiKey);
    lemonade_.setAbortCheck([this] { return cancelRequested(); });

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
    const std::string key = resolveApiKey(newConfig);
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        config_ = newConfig;
        config_.apiKey = key;
        systemPromptDirty_ = true;
    }
    // Update LemonadeClient outside configMutex_ to avoid holding the lock
    // across external calls (guards against future LemonadeClient → Agent callbacks).
    lemonade_.setApiKey(key);
    lemonade_.setBaseUrl(newConfig.baseUrl);
    lemonade_.setModel(newConfig.modelId);
    lemonade_.setContextSize(newConfig.contextSize);
    lemonade_.setDebug(newConfig.debug);
}

void Agent::setModel(const std::string& modelId) {
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        config_.modelId = modelId;
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

void Agent::setAllowedToolsStore(std::shared_ptr<AllowedToolsStore> store) {
    if (!store) throw std::invalid_argument("setAllowedToolsStore needs a store");
    allowedToolsStore_ = std::move(store);
    tools_.setAllowedToolsStore(allowedToolsStore_);
}

// ---------------------------------------------------------------------------
// Skill sets — see include/gaia/skill_sets.h and src/gaia/skills/sets.py
// ---------------------------------------------------------------------------

const SkillSets& Agent::skillSets() const {
    std::string manifest;
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        if (skillSets_.has_value()) return *skillSets_;
        manifest = config_.skillManifest;
    }

    // Parse outside the lock — it touches the filesystem and can throw. An
    // empty manifest path means "look beside the running executable"; an agent
    // that ships none simply declares no skills.
    if (manifest.empty()) manifest = findAgentManifestNearExecutable();
    SkillSets parsed = manifest.empty() ? SkillSets() : parseSkillSetsFile(manifest);

    std::lock_guard<std::mutex> lock(configMutex_);
    // Never reassigned once set, so the returned reference stays valid and
    // stable for the agent's lifetime after the lock is released.
    if (!skillSets_.has_value()) skillSets_ = std::move(parsed);
    return *skillSets_;
}

std::optional<std::string> Agent::activeSkillSet() const {
    std::lock_guard<std::mutex> lock(configMutex_);
    return activeSkillSet_;
}

std::vector<std::string> Agent::skillSetLoaded() const {
    std::lock_guard<std::mutex> lock(configMutex_);
    return skillSetLoaded_;
}

namespace {

/// True when the option carries something other than whitespace — sets.py
/// treats a blank --skill-set as unset rather than as a set named "".
bool hasText(const std::optional<std::string>& value) {
    return value.has_value() &&
           value->find_first_not_of(" \t\r\n\f\v") != std::string::npos;
}

}  // namespace

std::optional<std::string> Agent::requestedSkillSet(
    const std::optional<std::string>& requested) const {
    if (requested.has_value()) return requested;
    std::lock_guard<std::mutex> lock(configMutex_);
    if (config_.skillSet.empty()) return std::nullopt;
    return config_.skillSet;
}

SkillSetResolution Agent::resolveSkillSet(
    const std::optional<std::string>& requested) const {
    const std::optional<std::string> explicitChoice = requestedSkillSet(requested);
    // Only consult the hook when nothing explicit was asked for — an explicit
    // request must never be second-guessed by agent state.
    const std::optional<std::string> selected =
        hasText(explicitChoice) ? std::nullopt : selectSkillSet();
    return skillSets().resolve(explicitChoice, selected);
}

std::vector<std::string> Agent::loadSkillSet(
    const std::optional<std::string>& requested) {
    const SkillSets& declarations = skillSets();
    const std::optional<std::string> explicitChoice = requestedSkillSet(requested);

    if (!declarations) {
        // Never drop an explicit request on the floor — resolve() throws with
        // the actionable "this agent declares no skill_sets" message.
        if (hasText(explicitChoice)) declarations.resolve(explicitChoice);
        return {};
    }

    // Pass the resolved choice, not `requested` — otherwise resolveSkillSet()
    // reads config_.skillSet a second time.
    const SkillSetResolution resolution = resolveSkillSet(explicitChoice);

    if (skillLoader_ == nullptr) {
        // A previous set is still registered in a loader that has since been
        // detached. Recording the new set here would strand those skills with
        // no way to ever retire them — the "reports one set, carries another's
        // skills" state the all-or-nothing rule exists to prevent.
        if (!skillSetLoaded_.empty()) {
            throw SkillSetError(
                "Cannot switch to skill set '" + resolution.name.value_or("(none)") +
                "': the loader that registered skill set '" +
                activeSkillSet_.value_or("(none)") +
                "' has been detached, so that set's skills cannot be retired. "
                "Re-attach it with Agent::setSkillLoader() before switching.");
        }
        // Resolution works without a loader; registration does not. Say so
        // rather than returning an empty list that reads like "this set is
        // empty" — that is the silent capability loss the set contract exists
        // to prevent. P3.3 (#2800) installs the loader.
        if (!resolution.skills.empty()) {
            console_->printWarning(
                "Skill set '" + resolution.name.value_or("(none)") + "' resolved to " +
                std::to_string(resolution.skills.size()) +
                " skill(s), but no skill loader is installed, so none were "
                "registered. Call Agent::setSkillLoader() before loadSkillSet().");
        }
        std::lock_guard<std::mutex> lock(configMutex_);
        activeSkillSet_ = resolution.name;
        skillSetLoaded_.clear();
        return {};
    }

    // What the incoming set wants, and what the outgoing one brought in. The
    // difference is all a switch is allowed to unload.
    std::set<std::string> wanted;
    for (const SkillRef& ref : resolution.skills) wanted.insert(ref.name);
    const std::vector<std::string> previouslyLoaded = skillSetLoaded_;

    // Load the new set BEFORE dropping the old one, tracking what this call
    // actually brought in so a failure can be undone completely.
    std::vector<std::string> loaded;
    std::vector<std::string> newlyLoaded;
    try {
        for (const SkillRef& ref : resolution.skills) {
            const bool alreadyPresent = skillLoader_->isLoaded(ref.name);
            if (!skillLoader_->loadSkill(ref.name)) {
                if (!ref.required) {
                    console_->printWarning(
                        "Optional skill '" + ref.name + "' declared by skill set '" +
                        resolution.name.value_or("(none)") +
                        "' was not found in any skills root — skipped.");
                    continue;
                }
                throw SkillSetError(
                    "Skill '" + ref.name + "' declared by skill set '" +
                    resolution.name.value_or("(none)") +
                    "' was not found in any skills root. Install it, remove it from "
                    "the set in gaia-agent.yaml, or mark it 'required: false' if the "
                    "agent works without it. See " +
                    SETS_DOCS_URL + ".");
            }
            if (!alreadyPresent) newlyLoaded.push_back(ref.name);
            loaded.push_back(ref.name);
            reportVersionPin(ref);
        }
    } catch (...) {
        // Roll back to the pre-call state: drop only what this call added, and
        // leave activeSkillSet_ / skillSetLoaded_ untouched so the agent keeps
        // reporting the set it is actually carrying.
        for (const std::string& name : newlyLoaded) {
            // A throwing unload must not abort the rollback or displace the
            // original, actionable failure.
            try {
                skillLoader_->unloadSkill(name);
            } catch (const std::exception& unloadError) {
                console_->printError("Could not roll back skill '" + name +
                                     "' after a failed set load: " + unloadError.what());
            }
        }
        console_->printError("Skill set '" + resolution.name.value_or("(none)") +
                             "' failed to load; the agent is unchanged and skill set '" +
                             activeSkillSet_.value_or("(none)") + "' remains active.");
        throw;
    }

    // The new set is fully loaded — now retire the previous one's leftovers.
    // Scoped to set-loaded names, so a skill loaded outside a set is never a
    // set's to unload, and an always-on skill (in `wanted` for every set)
    // survives the switch.
    for (const std::string& stale : previouslyLoaded) {
        if (wanted.count(stale) == 0) skillLoader_->unloadSkill(stale);
    }

    {
        std::lock_guard<std::mutex> lock(configMutex_);
        activeSkillSet_ = resolution.name;
        skillSetLoaded_ = loaded;
    }
    rebuildSystemPrompt();
    return loaded;
}

void Agent::reportVersionPin(const SkillRef& ref) const {
    // #2864: a version pin is a declaration surface until the marketplace phase
    // (#2467) can install versioned skills. Accepting one and saying nothing is
    // the silent-fallback failure CLAUDE.md prohibits, so name the pin and what
    // is actually on disk and let the operator judge.
    if (!ref.version.has_value() || skillLoader_ == nullptr) return;
    const std::string onDisk = skillLoader_->loadedVersion(ref.name);
    console_->printWarning(
        "Skill '" + ref.name + "' declares version pin '" + *ref.version +
        "', which GAIA does not yet enforce; loaded the on-disk version " +
        (onDisk.empty() ? "(the skill declares none)" : "'" + onDisk + "'") +
        " unchecked. Verify it satisfies the pin, or drop the pin from "
        "gaia-agent.yaml until versioned installs land. See " +
        SETS_DOCS_URL + ".");
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

    // Response format — omitted only when the request will actually carry a
    // `tools` array, which is what makes tool-calling models emit tool_calls
    // instead of prose JSON. With no enabled tool there is no `tools` field, so
    // dropping the template too would leave the model with no protocol at all.
    const bool willSendTools = cfg.useNativeToolCalls() && !tools_.enabledTools().empty();

    // The prose list helps a full model weigh tools before calling one (Python
    // keeps it too); a compact model re-reads it every step for little gain.
    std::string toolsDesc = willSendTools && cfg.compactFeatures() ? std::string() : tools_.formatForPrompt();
    if (!toolsDesc.empty()) {
        oss << "==== AVAILABLE TOOLS ====\n" << toolsDesc << "\n";
    }
    if (!willSendTools) {
        oss << (cfg.responseMode == ResponseMode::Conversational
                    ? CONVERSATIONAL_FORMAT_TEMPLATE
                    : RESPONSE_FORMAT_TEMPLATE);
    }

    return oss.str();
}

// ---- LLM Communication ----

namespace {
/// A 200 reply the agent cannot read: a proxy's HTML page, an empty or cut-off
/// body, no choices. The same request may well succeed when sent again.
struct UnreadableLlmReply : std::runtime_error {
    using std::runtime_error::runtime_error;
};

/// An id for a tool call the server sent without one. The id only has to match
/// between the call echoed back and its result, and the agent writes both.
std::string assignedToolCallId() {
    static std::atomic<unsigned long long> next{0};
    return "call_gaia_" + std::to_string(++next);
}

/// Give stream-assembled tool calls an id when the server sent none, and reject
/// one that never received a name.
void validateStreamedToolCalls(std::vector<ToolCall>& calls) {
    for (size_t i = 0; i < calls.size(); ++i) {
        if (calls[i].id.empty()) calls[i].id = assignedToolCallId();
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
        const auto id = entry.is_object() ? entry.find("id") : entry.end();
        const bool hasId = entry.is_object() && id != entry.end() && id->is_string() &&
                           !id->get_ref<const std::string&>().empty();
        if (entry.is_object() && !hasId) {
            json withId = entry;
            withId["id"] = assignedToolCallId();
            calls.push_back(ToolCall::fromJson(withId));
        } else {
            calls.push_back(ToolCall::fromJson(entry));
        }
    }
    return calls;
}

UsageStats extractUsage(const json& responseJson);

/// What a streamed reply's events say beyond its text.
struct StreamSummary {
    UsageStats usage;          // sent in the last event when the request asks for it
    std::string finishReason;  // "stop", "length", "tool_calls"
    bool reasoned = false;     // the model sent reasoning, whether or not it answered
};

StreamSummary summarizeStream(const std::string& raw) {
    StreamSummary summary;
    for (size_t pos = 0; pos < raw.size();) {
        size_t end = raw.find('\n', pos);
        if (end == std::string::npos) end = raw.size();
        const std::string line = raw.substr(pos, end - pos);
        pos = end + 1;
        if (line.rfind("data:", 0) != 0) continue;
        const json event = json::parse(line.substr(5), nullptr, false);
        if (!event.is_object()) continue;
        if (event.contains("usage") && event["usage"].is_object()) summary.usage = extractUsage(event);
        const auto choices = event.find("choices");
        if (choices == event.end() || !choices->is_array() || choices->empty()) continue;
        const json& choice = (*choices)[0];
        if (!choice.is_object()) continue;
        const auto finish = choice.find("finish_reason");
        if (finish != choice.end() && finish->is_string()) summary.finishReason = finish->get<std::string>();
        const auto delta = choice.find("delta");
        if (delta == choice.end() || !delta->is_object()) continue;
        for (const char* key : {"reasoning_content", "reasoning"}) {
            const auto text = delta->find(key);
            if (text != delta->end() && text->is_string() && !text->get_ref<const std::string&>().empty()) {
                summary.reasoned = true;
            }
        }
    }
    return summary;
}

UsageStats extractUsage(const json& responseJson) {
    UsageStats usage;
    if (responseJson.contains("usage") && responseJson["usage"].is_object()) {
        const auto& u = responseJson["usage"];
        usage.promptTokens = u.value("prompt_tokens", 0);
        usage.completionTokens = u.value("completion_tokens", 0);
        usage.totalTokens = u.value("total_tokens", 0);
        const auto details = u.find("prompt_tokens_details");
        if (details != u.end() && details->is_object()) {
            usage.cachedTokens = details->value("cached_tokens", 0);
        }
        usage.peakContextTokens = usage.promptTokens + usage.completionTokens;
    }
    return usage;
}
} // namespace

namespace {
bool runsOnNpu(const std::string& modelId, const std::string& recipe) {
    if (recipe == "flm") return true;
    constexpr const char kSuffix[] = "-flm";
    if (modelId.size() < sizeof kSuffix - 1) return false;
    std::string tail = modelId.substr(modelId.size() - (sizeof kSuffix - 1));
    for (auto& c : tail) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    return tail == kSuffix;
}
} // namespace

Agent::LlmResult Agent::callLlm(const std::vector<Message>& messages, const std::string& sysPrompt,
                                const AgentConfig& cfg, bool allowTools) {
    // Two agents on the NPU at once each run several times slower than in turn. Taken
    // before the load, so one agent can't swap the NPU's model under another's reply.
    std::optional<MachineLock> npuLock;
    if (runsOnNpu(cfg.modelId, lemonade_.recipeOf(cfg.modelId))) {
        npuLock.emplace("npu");
        if (!npuLock->acquire(0)) {
            console_->printWarning("Another agent is using the NPU; waiting for its model call to finish.");
            while (!npuLock->acquire(200)) {
                if (cancelRequested()) throw std::runtime_error("Cancelled while waiting for the NPU");
            }
        }
    }

    // Re-check before every request: another client can reload the model at a smaller ctx mid-turn.
    if (!cfg.modelId.empty()) {
        try {
            lemonade_.ensureModelLoaded();
        } catch (const HttpError& e) {
            throw HttpError("Could not load model " + cfg.modelId + ": " + e.what(), e.url(), e.status(), e.body());
        }
    }

    // Build OpenAI-compatible request.
    // NOTE: n_ctx is intentionally omitted — context size is set at model load
    // time via LemonadeClient::loadModel() / ensureModelLoaded(), not per-request.
    json requestBody;
    requestBody["model"] = cfg.modelId;
    requestBody["max_tokens"] = cfg.replyTokenLimit();
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
    const bool nativeTools = allowTools && cfg.useNativeToolCalls();
    if (cfg.useNativeToolCalls()) {
        json toolSchemas = tools_.buildOpenAiToolSchemas();
        if (!toolSchemas.empty()) {
            requestBody["tools"] = std::move(toolSchemas);
            // A tool-free call still declares the tools: strict servers reject a
            // history holding tool_calls when the request has no `tools` field.
            requestBody["tool_choice"] = nativeTools ? cfg.toolChoice : "none";
        }
    }

    if (cfg.debug) {
        std::cerr << "[LLM] POST /chat/completions, messages=" << msgArray.size()
                  << ", native_tools=" << (requestBody.contains("tools") ? "yes" : "no")
                  << std::endl;
    }

    // ---- Streaming path ----
    if (cfg.streaming) {
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
            const UsageStats usage = summarizeStream(rawResponse).usage;
            if (cfg.debug) {
                std::cerr << "[LLM] streamed tool_calls=" << streamedToolCalls.size() << std::endl;
            }
            return {accumulated, usage, std::move(streamedToolCalls)};
        }

        if (!accumulated.empty()) {
            console_->printStreamEnd();
            return {accumulated, summarizeStream(rawResponse).usage, {}};
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

        const StreamSummary summary = summarizeStream(rawResponse);
        if (summary.finishReason == "length") {
            throw std::runtime_error(
                "The model reached its reply limit of " + std::to_string(cfg.replyTokenLimit()) +
                " tokens before giving an answer" +
                (summary.reasoned ? " (its reasoning used the whole limit)" : "") +
                ". Raise the limit (--max-tokens; at most half the context window, --ctx-size) or ask for a shorter answer.");
        }
        throw UnreadableLlmReply(summary.reasoned ? "The model sent reasoning but no answer"
                                                  : "Streaming response contained no tokens");
    }

    // ---- Non-streaming path ----
    std::string responseBody = lemonade_.chatCompletions(requestBody);

    // Parse response
    json responseJson;
    try {
        responseJson = json::parse(responseBody);
    } catch (const json::parse_error& e) {
        std::string preview = responseBody.substr(0, 200);
        throw UnreadableLlmReply(std::string("Failed to parse LLM response: ") + e.what() +
                                 " | body: " + preview);
    }

    if (responseJson.contains("choices") && !responseJson["choices"].empty()) {
        const auto& choice = responseJson["choices"][0];
        std::string finish;
        if (choice.is_object() && choice.contains("finish_reason") &&
            choice["finish_reason"].is_string()) {
            finish = choice["finish_reason"].get<std::string>();
        }
        if (choice.contains("message") && choice["message"].is_object()) {
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
                        parseToolCallsArray(message["tool_calls"]), finish};
            }
            if (message.contains("content") && message["content"].is_string()) {
                return {message["content"].get<std::string>(), extractUsage(responseJson), {},
                        finish};
            }
            // No text and no calls (a cut-off reasoning block, or a reply that
            // announces tool calls and carries none): an empty reply, which the
            // loop asks about once before stopping.
            if (!message.contains("content") || message["content"].is_null()) {
                return {std::string(), extractUsage(responseJson), {}, finish};
            }
        }
    }
    // Include truncated response body in error for debugging
    throw UnreadableLlmReply("Unexpected LLM response format: " + responseBody.substr(0, 200));
}

// ---- Resilient model calls ----

namespace {

// Transient: the request may succeed unchanged if sent again.
bool isTransientLlmFailure(const std::exception& e) {
    if (dynamic_cast<const UnreadableLlmReply*>(&e)) return true;
    if (const auto* he = dynamic_cast<const HttpError*>(&e)) {
        const int s = he->status();
        if (s == 0) {
            const std::string what = he->what();
            return what.find(" failed: ") != std::string::npos ||
                   what.find("streaming failed") != std::string::npos;
        }
        return s == 408 || s == 409 || s == 425 || s == 429 || s >= 500;
    }
    return false;
}

bool isContextOverflow(const std::exception& e) {
    std::string what = e.what();
    if (const auto* he = dynamic_cast<const HttpError*>(&e)) what += he->body();
    for (const char* marker : {"exceeds the available context size", "context window too small",
                               "maximum context length", "context_length_exceeded",
                               "context length", "too many tokens",
                               "exceed_context_size", "got too long",
                               // FastFlowLM's wording for a full window
                               "Max length reached"}) {
        if (what.find(marker) != std::string::npos) return true;
    }
    return false;
}

// A reply made of a model's unused special tokens (`<unused49><unused49>...`) is noise from a
// server whose copy of the model went bad, never an answer.
bool isPlaceholderRun(const std::string& text) {
    static const std::regex token(R"(<unused\d+>)");
    if (text.size() < 64) return false;
    size_t covered = 0;
    for (auto it = std::sregex_iterator(text.begin(), text.end(), token); it != std::sregex_iterator(); ++it) {
        covered += static_cast<size_t>(it->length(0));
    }
    return covered * 10 >= text.size() * 9;
}

// Shorten the model's own long replies, oldest first, keeping the newest message whole.
size_t trimLongReplies(std::vector<Message>& messages) {
    constexpr size_t kKeepHead = 600, kKeepTail = 300, kLong = 1500;
    size_t freed = 0;
    for (size_t i = 0; i + 1 < messages.size(); ++i) {
        Message& m = messages[i];
        if (m.role != MessageRole::ASSISTANT || m.content.size() <= kLong) continue;
        size_t head = kKeepHead, tail = m.content.size() - kKeepTail;
        while (head > 0 && (static_cast<unsigned char>(m.content[head]) & 0xC0) == 0x80) --head;
        while (tail < m.content.size() && (static_cast<unsigned char>(m.content[tail]) & 0xC0) == 0x80) ++tail;
        const std::string shorter = m.content.substr(0, head) +
                                    "\n[...middle of this reply removed to fit the context window...]\n" +
                                    m.content.substr(tail);
        freed += m.content.size() - shorter.size();
        m.content = shorter;
    }
    return freed;
}

} // namespace

std::string stripReasoning(const std::string& text) {
    std::string out = text;
    // A reasoning model may emit <think>…</think> inline; it is never the answer.
    for (;;) {
        const size_t open = out.find("<think>");
        if (open == std::string::npos) break;
        const size_t close = out.find("</think>", open);
        if (close == std::string::npos) {
            out.erase(open);
            break;
        }
        out.erase(open, close + 8 - open);
    }
    // Some templates open the block in the prompt, so the reply starts inside it
    // and only the close appears; text that merely mentions the tag is kept.
    const size_t strayClose = out.find("</think>");
    if (strayClose != std::string::npos && text.find("<think>") == std::string::npos &&
        out.find("</think>", strayClose + 8) == std::string::npos) {
        out.erase(0, strayClose + 8);
    }
    const size_t first = out.find_first_not_of(" \t\r\n");
    return first == std::string::npos ? std::string() : out.substr(first);
}

Agent::LlmResult Agent::callLlmResilient(std::vector<Message>& messages,
                                         const std::string& sysPrompt, const AgentConfig& cfg,
                                         bool allowTools) {
    int shrinkStage = 0;
    int attempt = 0;
    for (;;) {
        try {
            LlmResult r = callLlm(messages, sysPrompt, cfg, allowTools);
            r.content = stripReasoning(r.content);
            if (isPlaceholderRun(r.content)) {
                // Not retried: the same server returns the same noise until the model is reloaded.
                throw std::runtime_error(
                    "The model " + cfg.modelId + " returned only placeholder tokens (" + r.content.substr(0, 24) +
                    "... repeated), so the server's copy of the model is in a bad state. Unload and reload the "
                    "model in Lemonade, then try again.");
            }
            return r;
        } catch (const std::exception& e) {
            if (cancelRequested()) throw;
            if (isContextOverflow(e)) {
                // Free more each time: older tool output, then all of it, then long replies.
                bool freed = false;
                while (!freed && shrinkStage < 4) {
                    const int stage = shrinkStage++;
                    freed = (stage < 3 ? elideOldToolOutputs(messages, static_cast<size_t>(2 - stage))
                                       : trimLongReplies(messages)) > 0;
                }
                if (freed) {
                    console_->printWarning(
                        "Context window full; shortened earlier tool output and replies, "
                        "retrying.");
                    continue;
                }
                throw std::runtime_error(
                    "The conversation no longer fits the model's context window, even after "
                    "shortening earlier tool output. Start a new chat, ask about fewer or "
                    "smaller files at a time, or load the model with a larger context window. "
                    "Server said: " + std::string(e.what()).substr(0, 300));
            }
            if (attempt >= cfg.llmRetries || !isTransientLlmFailure(e)) throw;
            ++attempt;
            const int waitSec = std::min(30, 2 << (attempt - 1));
            console_->printWarning(std::string("Model call failed (") + e.what() +
                                   "); retry " + std::to_string(attempt) + "/" +
                                   std::to_string(cfg.llmRetries) + " in " +
                                   std::to_string(waitSec) + "s");
            for (int i = 0; i < waitSec * 10 && !cancelRequested(); ++i) {
                std::this_thread::sleep_for(std::chrono::milliseconds(100));
            }
            if (cancelRequested()) throw;
        }
    }
}

// ---- Tool output store ----

namespace {
constexpr size_t kMaxStoredToolOutputs = 64;
constexpr int kReadToolOutputDefaultChars = 12000;

// Move pos back to the start of the UTF-8 character it falls in.
size_t utf8Boundary(const std::string& s, size_t pos) {
    if (pos >= s.size()) return s.size();
    while (pos > 0 && (static_cast<unsigned char>(s[pos]) & 0xC0) == 0x80) --pos;
    return pos;
}
} // namespace

std::string Agent::recordToolOutput(const json& toolResult, int maxChars) {
    // Plain strings are re-encoded too, so invalid UTF-8 never reaches a request.
    std::string full = toolResult.dump(-1, ' ', false, json::error_handler_t::replace);
    if (toolResult.is_string()) full = json::parse(full).get<std::string>();
    if (static_cast<int>(full.size()) <= maxChars) return full;

    int id;
    {
        std::lock_guard<std::mutex> lock(toolOutputsMutex_);
        id = nextToolOutputId_++;
        toolOutputs_[id] = full;
        while (toolOutputs_.size() > kMaxStoredToolOutputs) toolOutputs_.erase(toolOutputs_.begin());
    }
    const size_t headEnd = utf8Boundary(full, static_cast<size_t>(maxChars) * 6 / 10);
    const size_t tailStart =
        utf8Boundary(full, full.size() - (static_cast<size_t>(maxChars) - headEnd));
    return full.substr(0, headEnd) + "\n...[output " + std::to_string(id) +
           " clipped: chars " + std::to_string(headEnd) + "-" + std::to_string(tailStart) +
           " of " + std::to_string(full.size()) +
           " omitted. Call read_tool_output with output_id=" + std::to_string(id) +
           " and offset=" + std::to_string(headEnd) + " to read them]...\n" +
           full.substr(tailStart);
}

size_t Agent::elideOldToolOutputs(std::vector<Message>& messages, size_t keepRecent) {
    std::vector<size_t> toolIdx;
    for (size_t i = 0; i < messages.size(); ++i) {
        if (messages[i].role == MessageRole::TOOL && messages[i].content.size() > 400) {
            toolIdx.push_back(i);
        }
    }
    if (toolIdx.size() <= keepRecent) return 0;
    size_t freed = 0;
    for (size_t k = 0; k + keepRecent < toolIdx.size(); ++k) {
        Message& m = messages[toolIdx[k]];
        int id;
        {
            std::lock_guard<std::mutex> lock(toolOutputsMutex_);
            id = nextToolOutputId_++;
            toolOutputs_[id] = m.content;
            while (toolOutputs_.size() > kMaxStoredToolOutputs) toolOutputs_.erase(toolOutputs_.begin());
        }
        const std::string stub = "[earlier output of " + m.name.value_or("tool") + " (" +
                                 std::to_string(m.content.size()) +
                                 " chars) removed to fit the context window; read_tool_output "
                                 "output_id=" + std::to_string(id) + " returns it]";
        freed += m.content.size() - std::min(m.content.size(), stub.size());
        m.content = stub;
    }
    return freed;
}

void Agent::registerBuiltinTools() {
    if (tools_.hasTool("read_tool_output")) return;
    ToolInfo info;
    info.name = "read_tool_output";
    info.description =
        "Read part of an earlier tool output that was clipped or removed from the "
        "conversation. Use the output_id named in the clipped output.";
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        {"output_id", ToolParamType::INTEGER, true, "The id named in the clipped output."},
        {"offset", ToolParamType::INTEGER, false, "Character offset to start at (default 0)."},
        {"length", ToolParamType::INTEGER, false,
         "Characters to return (default 12000, max 50000)."},
    };
    info.callback = [this](const json& args) -> json {
        const int id = args.at("output_id").get<int>();
        const long long offset = std::max<long long>(0, args.value("offset", 0LL));
        const long long length =
            std::clamp<long long>(args.value("length", kReadToolOutputDefaultChars), 1, 50000);
        std::lock_guard<std::mutex> lock(toolOutputsMutex_);
        auto it = toolOutputs_.find(id);
        if (it == toolOutputs_.end()) {
            return json{{"status", "error"},
                        {"error", "No stored output with id " + std::to_string(id) +
                                      "; only the last " +
                                      std::to_string(kMaxStoredToolOutputs) +
                                      " clipped outputs are kept. Re-run the tool instead."}};
        }
        const std::string& full = it->second;
        const size_t start = utf8Boundary(full, static_cast<size_t>(offset));
        const size_t end = utf8Boundary(full, start + static_cast<size_t>(length));
        const size_t n = end - start;
        return json{{"status", "success"},
                    {"output_id", id},
                    {"offset", start},
                    {"returned", n},
                    {"total", full.size()},
                    {"remaining", full.size() - start - n},
                    {"text", full.substr(start, n)}};
    };
    tools_.registerTool(std::move(info));
}

// ---- Tool Execution ----

namespace {
std::optional<std::string> toolFailure(const json& r);  // defined with the loop helpers
}  // namespace

namespace {
// A tool call's arguments for the step log: enough to see what was asked, not a whole file body.
std::string clippedArgs(const json& args) {
    constexpr size_t kMax = 300;
    std::string text = args.dump(-1, ' ', false, json::error_handler_t::replace);
    if (text.size() <= kMax) return text;
    size_t cut = kMax;
    while (cut > 0 && (static_cast<unsigned char>(text[cut]) & 0xC0) == 0x80) --cut;
    return text.substr(0, cut) + "...";
}
} // namespace

json Agent::executeTool(const std::string& toolName, const json& toolArgs) {
    const auto started = std::chrono::steady_clock::now();
    const auto startedOnDisk = std::filesystem::file_time_type::clock::now();
    json result;
    // Whatever a tool or its checks throw is the tool's result, so the model
    // hears about it and the turn goes on.
    try {
        result = tools_.executeTool(toolName, toolArgs);
    } catch (const std::exception& e) {
        result = json{{"status", "error"}, {"error", std::string("Tool '") + toolName + "' failed: " + e.what()}};
    }
    const double wall =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
    const std::string resolved = tools_.resolveName(toolName);
    std::string output = result.is_string() ? result.get<std::string>()
                                            : result.dump(-1, ' ', false, json::error_handler_t::replace);
    if (output.size() > 20000) output.resize(utf8Boundary(output, 20000));
    turnToolCalls_.push_back({resolved.empty() ? toolName : resolved, toolArgs,
                              !toolFailure(result).has_value(), turnStep_, wall, startedOnDisk,
                              std::move(output)});
    if (stepLogger_) {
        const ToolCallRecord& call = turnToolCalls_.back();
        stepLogger_({{"kind", "tool"},
                     {"step", call.step},
                     {"name", call.name},
                     {"args", clippedArgs(call.args)},
                     {"wall_s", std::round(wall * 1000) / 1000},
                     {"ok", call.succeeded}});
    }
    return result;
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

        const json caps = client->capabilities();
        mcpClients_.emplace(name, std::move(client));
        if (caps.contains("resources")) registerMcpResourceTools();
        if (caps.contains("prompts")) registerMcpPromptTools();

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

// Resources and prompts are read through generic tools that name the server, so
// a server with hundreds of resources adds two tools, not hundreds.
void Agent::registerMcpResourceTools() {
    if (tools_.hasTool("list_mcp_resources")) return;
    auto serverArg = [this](const json& a) -> MCPClient* {
        auto it = mcpClients_.find(a.value("server", ""));
        return it == mcpClients_.end() ? nullptr : it->second.get();
    };
    auto servers = [this] {
        std::string s;
        for (const auto& [n, c] : mcpClients_) {
            if (c->capabilities().contains("resources")) s += (s.empty() ? "" : ", ") + n;
        }
        return s;
    };

    ToolInfo list;
    list.name = "list_mcp_resources";
    list.description = "List the documents and data (resources) a connected MCP server offers.";
    list.parameters = {{"server", ToolParamType::STRING, true, "MCP server name."}};
    list.policy = ToolPolicy::ALLOW;
    list.callback = [serverArg, servers](const json& a) -> json {
        MCPClient* c = serverArg(a);
        if (!c) return {{"status", "error"}, {"error", "No such MCP server. Servers with resources: " + servers()}};
        try {
            return {{"status", "success"}, {"resources", c->listResources()}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    tools_.registerTool(std::move(list));

    ToolInfo read;
    read.name = "read_mcp_resource";
    read.description = "Read one resource (by its uri from list_mcp_resources) from an MCP server.";
    read.parameters = {{"server", ToolParamType::STRING, true, "MCP server name."},
                       {"uri", ToolParamType::STRING, true, "Resource uri."}};
    read.policy = ToolPolicy::ALLOW;
    read.callback = [serverArg, servers](const json& a) -> json {
        MCPClient* c = serverArg(a);
        if (!c) return {{"status", "error"}, {"error", "No such MCP server. Servers with resources: " + servers()}};
        try {
            return {{"status", "success"}, {"contents", c->readResource(a.value("uri", ""))}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    tools_.registerTool(std::move(read));
}

void Agent::registerMcpPromptTools() {
    if (tools_.hasTool("list_mcp_prompts")) return;
    auto serverArg = [this](const json& a) -> MCPClient* {
        auto it = mcpClients_.find(a.value("server", ""));
        return it == mcpClients_.end() ? nullptr : it->second.get();
    };

    ToolInfo list;
    list.name = "list_mcp_prompts";
    list.description = "List the prompt templates a connected MCP server offers.";
    list.parameters = {{"server", ToolParamType::STRING, true, "MCP server name."}};
    list.policy = ToolPolicy::ALLOW;
    list.callback = [serverArg](const json& a) -> json {
        MCPClient* c = serverArg(a);
        if (!c) return {{"status", "error"}, {"error", "No such MCP server: " + a.value("server", "")}};
        try {
            return {{"status", "success"}, {"prompts", c->listPrompts()}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    tools_.registerTool(std::move(list));

    ToolInfo get;
    get.name = "get_mcp_prompt";
    get.description = "Fill in an MCP server's prompt template and return its messages.";
    get.parameters = {{"server", ToolParamType::STRING, true, "MCP server name."},
                      {"name", ToolParamType::STRING, true, "Prompt name."},
                      {"arguments", ToolParamType::OBJECT, false, "Values for the prompt's arguments."}};
    get.policy = ToolPolicy::ALLOW;
    get.callback = [serverArg](const json& a) -> json {
        MCPClient* c = serverArg(a);
        if (!c) return {{"status", "error"}, {"error", "No such MCP server: " + a.value("server", "")}};
        try {
            return {{"status", "success"},
                    {"prompt", c->getPrompt(a.value("name", ""), a.value("arguments", json::object()))}};
        } catch (const std::exception& e) {
            return {{"status", "error"}, {"error", e.what()}};
        }
    };
    tools_.registerTool(std::move(get));
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

using ToolCallWindow = std::deque<std::pair<std::string, json>>;

// Longest repeating cycle of calls the loop guard recognises (A, B, A, B, ...).
constexpr int kMaxLoopPeriod = 3;

// Loop detection: the length of the cycle the pending call would close for the
// maxConsecutiveRepeats-th time back to back (1 = the same call over and over),
// or 0 when it is not a loop. A model alternating a search with a read never
// repeats a call twice in a row, so period 1 alone lets it run on.
int loopPeriod(const ToolCallWindow& history,
               const std::string& toolName, const json& toolArgs,
               int maxConsecutiveRepeats) {
    const int n = static_cast<int>(history.size()) + 1;  // the pending call is the last
    auto same = [&](int i, int j) {
        const std::string& nameI = i == n - 1 ? toolName : history[static_cast<size_t>(i)].first;
        const std::string& nameJ = j == n - 1 ? toolName : history[static_cast<size_t>(j)].first;
        if (nameI != nameJ) return false;
        const json& argsI = i == n - 1 ? toolArgs : history[static_cast<size_t>(i)].second;
        const json& argsJ = j == n - 1 ? toolArgs : history[static_cast<size_t>(j)].second;
        return argsI == argsJ;
    };
    for (int period = 1; period <= kMaxLoopPeriod && period <= n; ++period) {
        bool shorter = false;  // a repeat inside the block is a shorter period
        for (int i = n - period; i < n && !shorter; ++i) {
            for (int j = i + 1; j < n && !shorter; ++j) shorter = same(i, j);
        }
        if (shorter) continue;
        int repeats = 1;
        while (n - (repeats + 1) * period >= 0) {
            bool equal = true;
            for (int k = 0; k < period && equal; ++k) {
                equal = same(n - (repeats + 1) * period + k, n - period + k);
            }
            if (!equal) break;
            ++repeats;
        }
        if (repeats >= maxConsecutiveRepeats) return period;
    }
    return 0;
}

// Keeps only the calls loopPeriod() reads, so a long query's history
// does not hold every tool's arguments for its whole lifetime.
void recordToolCall(ToolCallWindow& history, const std::string& toolName,
                    const json& toolArgs, int maxConsecutiveRepeats) {
    history.emplace_back(toolName, toolArgs);
    const size_t keep =
        static_cast<size_t>(std::max(kMaxLoopPeriod * maxConsecutiveRepeats - 1, 0));
    while (history.size() > keep) history.pop_front();
}

// The error a tool result reports, whatever shape it uses ({"status":"error"},
// {"ok":false}, {"error":...}, MCP {"isError":true}); nullopt for success.
std::optional<std::string> toolFailure(const json& r) {
    if (!r.is_object()) return std::nullopt;
    auto message = [&r]() -> std::string {
        if (r.contains("error")) {
            const auto& e = r["error"];
            if (e.is_string()) return e.get<std::string>();
            if (e.is_object() && e.contains("message") && e["message"].is_string()) {
                return e["message"].get<std::string>();
            }
            if (!e.is_null()) return e.dump();
        }
        return "the tool reported a failure without a message";
    };
    if (r.value("status", "") == "error") return message();
    if (r.contains("ok") && r["ok"].is_boolean() && !r["ok"].get<bool>()) return message();
    if (r.contains("isError") && r["isError"].is_boolean() && r["isError"].get<bool>()) {
        return message();
    }
    if (r.contains("error") && !r["error"].is_null() && !r["error"].is_boolean() &&
        r.value("status", "") != "success") {
        return message();
    }
    return std::nullopt;
}

// Steps-left levels at which the model hears about its budget. The fraction
// keeps a level from firing early on a budget too small to hold it.
struct BudgetLevel {
    int stepsLeft;
    double fractionLeft;
};
constexpr BudgetLevel kBudgetLevels[] = {{20, 0.50}, {10, 0.25}, {5, 0.10}};

// Below this many steps there is no room to act on a notice.
constexpr int kMinStepsForBudgetNotices = 8;

std::string budgetNotice(int level, int used, int total, int left) {
    const std::string l = std::to_string(left), t = std::to_string(total);
    if (level == 20) {
        return "Step budget: " + std::to_string(used) + " of " + t + " used, " + l +
               " left. Stop gathering context. If the task asks for files or other "
               "deliverables, start producing them now from what you already have, and mark "
               "any value you could not verify as unverified. If the part of this task that "
               "changes something has not started yet, start it now.";
    }
    if (level == 10) {
        return "Step budget: " + l + " of " + t +
               " steps left. Write the requested deliverables now with what you have, marking "
               "unverified values as unverified. A finished deliverable with its gaps flagged "
               "is worth more than a better one never written; refine afterwards if there is "
               "room.";
    }
    return "Step budget: " + l + " of " + t +
           " steps left. Do not research further. Write anything requested that does not "
           "exist yet, then answer with your findings and what is still missing. After the "
           "last step no tool can be called and anything not already written is lost.";
}

std::string loopNudge(const std::string& toolName, int repeats, int period) {
    if (period > 1) {
        return json{{"status", "error"},
                    {"error", "Not run: you have called " + toolName + " " +
                                  std::to_string(repeats - 1) +
                                  " times with the same arguments, in a repeating cycle of " +
                                  std::to_string(period) +
                                  " calls, and it returned no new progress. Do not repeat it — "
                                  "try a different approach, or give your final answer."}}
            .dump();
    }
    return json{{"status", "error"},
                {"error", "Not run: this is the " + std::to_string(repeats) +
                              "th identical call to " + toolName +
                              " in a row, and it returned the same result each time. "
                              "Use what it already returned, try a different approach, or "
                              "give your final answer."}}
        .dump();
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

// A stopped request left with no reply reads as pending, and the model carries it out next turn.
const char* const kStoppedReplyNote =
    "(The user stopped this reply before it finished. Don't continue it unless they ask again.)";

void appendStopNote(std::vector<Message>& messages) {
    if (!messages.empty() && messages.back().role == MessageRole::ASSISTANT &&
        messages.back().toolCalls.empty() && !messages.back().parts.has_value()) {
        Message& last = messages.back();
        if (!last.content.empty()) last.content += "\n\n";
        last.content += kStoppedReplyNote;
        return;
    }
    Message note;
    note.role = MessageRole::ASSISTANT;
    note.content = kStoppedReplyNote;
    messages.push_back(std::move(note));
}

// Values of a call written as <parameter=key> tags are text; each gets the type its tool declares.
json typeTextArgs(const ToolRegistry& tools, const std::string& toolName, json args) {
    const ToolInfo* tool = tools.findTool(toolName);
    if (!tool) tool = tools.findTool(tools.resolveName(toolName));
    if (!tool) return args;
    for (const auto& param : tool->parameters) {
        const auto it = args.find(param.name);
        if (it == args.end() || !it->is_string()) continue;
        const json typed = json::parse(it->get<std::string>(), nullptr, false);
        const bool fits = (param.type == ToolParamType::INTEGER && typed.is_number_integer()) ||
                          (param.type == ToolParamType::NUMBER && typed.is_number()) ||
                          (param.type == ToolParamType::BOOLEAN && typed.is_boolean()) ||
                          (param.type == ToolParamType::ARRAY && typed.is_array()) ||
                          (param.type == ToolParamType::OBJECT && typed.is_object());
        if (fits) *it = typed;
    }
    return args;
}
} // namespace

void Agent::setHistory(std::vector<Message> history) {
    if (inFlight_.load()) {
        throw std::runtime_error("Cannot set history while processQuery() is running");
    }
    repairToolCallPairing(history);
    conversationHistory_ = std::move(history);
}

bool Agent::autoNativeToolCalls(const AgentConfig& cfg) {
    if (auto known = knownToolCalling(cfg.modelId)) return *known;
    const std::string key = cfg.baseUrl + "\n" + cfg.modelId;
    if (auto it = serverToolCalling_.find(key); it != serverToolCalling_.end()) return it->second;
    bool labelled = false;
    try {
        const json catalog = lemonade_.listModels(true);
        if (!catalog.is_object() || !catalog.contains("data") || !catalog["data"].is_array()) return false;
        for (const auto& model : catalog["data"]) {
            if (!model.is_object() || model.value("id", "") != cfg.modelId) continue;
            const auto labels = model.find("labels");
            labelled = labels != model.end() && labels->is_array() &&
                       std::find(labels->begin(), labels->end(), "tool-calling") != labels->end();
            break;
        }
    } catch (const std::exception& e) {
        // A server with no catalog says nothing either way: keep the text
        // protocol every server accepts, and ask again next turn.
        if (cfg.debug) std::cerr << "[agent] no model catalog at " << cfg.baseUrl << ": " << e.what() << "\n";
        return false;
    }
    serverToolCalling_[key] = labelled;
    return labelled;
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
    // A cancel ends a shell command a tool is running, not only the next step.
    CommandCancelScope stopCommands([this] { return cancelRequested(); });
    turnToolCalls_.clear();
    turnStep_ = 0;

    // Reset cancel flag at the start of each query.
    cancelled_.store(false);  // cancelCheck_ is the caller's and stays

    // Snapshot config at start of query for thread-safe consistency throughout.
    AgentConfig cfg;
    {
        std::lock_guard<std::mutex> lock(configMutex_);
        cfg = config_;
    }

    // Settled once so the prompt and every request of the turn agree.
    if (cfg.nativeToolCalls == NativeToolCalls::Auto) {
        cfg.nativeToolCalls = autoNativeToolCalls(cfg) ? NativeToolCalls::Always : NativeToolCalls::Never;
    }

    int stepsLimit = (maxSteps > 0) ? maxSteps : cfg.maxSteps;

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
    turnRequest_ = userInput;
    onTurnStart();

    console_->printProcessingStart(userInput, stepsLimit, cfg.modelId);
    if (cfg.maxTokens > cfg.contextSize / 2) {
        console_->printWarning("Reply limit " + std::to_string(cfg.maxTokens) + " is more than half the " +
                               std::to_string(cfg.contextSize) + "-token context window; replies are capped at " +
                               std::to_string(cfg.contextSize / 2) + ". Raise --ctx-size to allow more.");
    }

    int stepsTaken = 0;
    std::string finalAnswer;
    int errorCount = 0;
    json llmCallLog = json::array();
    auto logLlmCall = [&llmCallLog, this](int step, std::chrono::steady_clock::time_point started,
                                          const UsageStats& u) {
        const double wall =
            std::chrono::duration<double>(std::chrono::steady_clock::now() - started).count();
        llmCallLog.push_back({{"step", step},
                              {"wall_s", std::round(wall * 1000) / 1000},
                              {"input_tokens", u.promptTokens},
                              {"cached_tokens", u.cachedTokens},
                              {"output_tokens", u.completionTokens}});
        if (stepLogger_) {
            json line = llmCallLog.back();
            line["kind"] = "llm";
            stepLogger_(line);
        }
    };
    std::string lastError;
    ToolCallWindow toolCallHistory; // recent (name, args) for loop detection
    UsageStats totalUsage;
    int budgetLevelSent = std::numeric_limits<int>::max();  // this level and every higher one was sent
    std::vector<std::string> budgetNotices;
    bool failed = false;
    bool incomplete = false;  // stopped short, but with an answer
    bool lastToolFailed = false;
    bool loopWarned = false;
    bool emptyNudged = false;
    bool emptyAtLimit = false;  // an empty reply that had used the whole reply limit
    // After a compact model's runaway reply, the next ask is short so another costs a minute,
    // not four. A reasoning model needs its full limit to think, so it keeps it.
    AgentConfig shortCfg = cfg;
    shortCfg.maxTokens = std::min(cfg.maxTokens, 1024);
    bool shortNext = false;
    int lastLimit = cfg.replyTokenLimit();
    std::string llmError;       // the model call failure that ended the loop
    constexpr size_t kMaxToolCallsPerReply = 24;
    int malformedStreak = 0;
    std::string stopReason;  // why the loop ended without an answer: loop | empty
    bool answerReviewed = false;
    // One chance for the agent to send a final answer back (reviewFinalAnswer).
    auto reviewOnce = [&](const std::string& answer) -> std::optional<Message> {
        if (answerReviewed || stepsTaken >= stepsLimit) return std::nullopt;
        answerReviewed = true;
        std::optional<std::string> note = reviewFinalAnswer(answer);
        if (!note) return std::nullopt;
        if (stepLogger_) stepLogger_({{"kind", "review"}, {"step", stepsTaken}, {"note", *note}});
        console_->printWarning("Answer sent back: " + *note);
        Message m;
        m.role = MessageRole::USER;
        m.content = "[Check before answering] " + *note;
        return m;
    };

    while (stepsTaken < stepsLimit && finalAnswer.empty()) {
        // ---- Cancel check ----
        if (cancelRequested()) {
            console_->printWarning("Cancelled by user");
            finalAnswer = "[Cancelled after " + std::to_string(stepsTaken) + " step(s)]";
            break;
        }

        ++stepsTaken;
        turnStep_ = stepsTaken;
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
        }

        // Recomposed each step so mid-loop tool changes are visible, but always
        // from the turn's config snapshot — the same one callLlm() uses to
        // decide whether to send `tools`.
        const std::string turnSystemPrompt = composeSystemPromptFor(cfg);

        const int stepsLeft = stepsLimit - stepsTaken + 1;
        if (cfg.stepBudgetNotices && stepsTaken > 1 && stepsLimit >= kMinStepsForBudgetNotices) {
            // Most urgent level first, so crossing two at once sends the one true now.
            for (auto level = std::rbegin(kBudgetLevels); level != std::rend(kBudgetLevels); ++level) {
                const int trigger = std::min(
                    level->stepsLeft, std::max(1, static_cast<int>(level->fractionLeft * stepsLimit)));
                if (level->stepsLeft >= budgetLevelSent || stepsLeft > trigger) continue;
                budgetLevelSent = level->stepsLeft;
                Message notice;
                notice.role = MessageRole::USER;
                notice.content = budgetNotice(level->stepsLeft, stepsTaken - 1, stepsLimit, stepsLeft);
                budgetNotices.push_back(notice.content);
                messages.push_back(notice);
                break;
            }
        }

        // Skip progress spinner when streaming — tokens serve as live progress.
        if (!cfg.streaming) console_->startProgress("Thinking");
        LlmResult llmResult;
        const auto llmStarted = std::chrono::steady_clock::now();
        try {
            const AgentConfig& stepCfg = shortNext ? shortCfg : cfg;
            lastLimit = stepCfg.replyTokenLimit();
            shortNext = false;
            llmResult = callLlmResilient(messages, turnSystemPrompt, stepCfg);
        } catch (const std::exception& e) {
            if (!cfg.streaming) console_->stopProgress();
            if (cancelRequested()) {
                console_->printWarning("Cancelled by user");
                finalAnswer = "[Cancelled after " + std::to_string(stepsTaken) + " step(s)]";
                break;
            }
            console_->printError(std::string("LLM error: ") + e.what());
            llmError = e.what();
            if (stepLogger_) {
                stepLogger_({{"kind", "llm_error"}, {"step", stepsTaken}, {"error", llmError.substr(0, 600)}});
            }
            // A reply the agent could not use, after work was done: ask once for a
            // closing answer. A server or window failure would only fail again.
            const bool gathered = std::any_of(messages.begin(), messages.end(), [](const Message& m) {
                return m.role == MessageRole::TOOL;
            });
            if (gathered && !dynamic_cast<const HttpError*>(&e) &&
                llmError.find("no longer fits the model's context window") == std::string::npos) {
                stopReason = "error";
                break;
            }
            finalAnswer = "Unable to complete task due to LLM error: " + llmError;
            failed = true;
            break;
        }
        if (!cfg.streaming) console_->stopProgress();

        const std::string& response = llmResult.content;
        totalUsage += llmResult.usage;
        logLlmCall(stepsTaken, llmStarted, llmResult.usage);

        // Debug: show response
        if (cfg.showPrompts) {
            console_->printResponse(response, "LLM Response");
        }

        // A reply cut at the length limit loses its last call; the earlier ones are whole.
        std::string batchNote;
        if (!llmResult.toolCalls.empty() && llmResult.finishReason == "length") {
            const size_t sent = llmResult.toolCalls.size();
            while (!llmResult.toolCalls.empty()) {
                try {
                    llmResult.toolCalls.back().parsedArgs();
                    break;
                } catch (const std::exception&) {
                    llmResult.toolCalls.pop_back();
                }
            }
            if (llmResult.toolCalls.size() < sent) {
                console_->printWarning("The model's reply was cut off at the length limit of " +
                                       std::to_string(cfg.replyTokenLimit()) + " tokens mid tool call.");
                batchNote = "[Your reply was cut off at the length limit of " +
                            std::to_string(cfg.replyTokenLimit()) + " tokens, so " +
                            std::to_string(sent - llmResult.toolCalls.size()) +
                            " unfinished tool call(s) at the end did not run. Send fewer calls "
                            "per reply and keep arguments short; write long content in parts, and refer to "
                            "a file by its path instead of pasting its contents.]";
            }
            if (llmResult.toolCalls.empty()) {
                if (++malformedStreak > 2) {
                    finalAnswer = "Unable to complete task: the model's reply was cut off at "
                                  "the length limit of " + std::to_string(cfg.replyTokenLimit()) +
                                  " tokens in the middle of a tool call three times in a row. "
                                  "Raise the limit (--max-tokens; at most half the context window, --ctx-size) or ask for a smaller step.";
                    failed = true;
                    break;
                }
                Message note;
                note.role = MessageRole::USER;
                note.content = batchNote;
                messages.push_back(note);
                continue;
            }
        }
        if (llmResult.toolCalls.size() > kMaxToolCallsPerReply) {
            batchNote += (batchNote.empty() ? "" : " ") + std::string("[Only the first ") +
                         std::to_string(kMaxToolCallsPerReply) + " of your " +
                         std::to_string(llmResult.toolCalls.size()) +
                         " tool calls ran. Send the rest in later replies.]";
            llmResult.toolCalls.resize(kMaxToolCallsPerReply);
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
                    decodeError = tc.name + ": " + e.what();
                    break;
                }
            }

            Message nativeAssistantMsg;
            nativeAssistantMsg.role = MessageRole::ASSISTANT;
            nativeAssistantMsg.content = response;
            nativeAssistantMsg.toolCalls = llmResult.toolCalls;
            // Never replay arguments the server's chat template may fail to parse.
            if (!decodeError.empty()) {
                for (auto& tc : nativeAssistantMsg.toolCalls) tc.arguments = "{}";
            }
            messages.push_back(nativeAssistantMsg);

            // Nothing ran: answer every call with the problem so the model can resend.
            if (!decodeError.empty()) {
                console_->printWarning("Malformed tool call: " + decodeError);
                if (++malformedStreak > 2) {
                    finalAnswer = "Unable to complete task: the model sent malformed tool "
                                  "arguments three times in a row (" + decodeError + ").";
                    failed = true;
                    break;
                }
                bool explained = false;
                for (const auto& tc : llmResult.toolCalls) {
                    Message reply;
                    reply.role = MessageRole::TOOL;
                    reply.name = tc.name;
                    reply.toolCallId = tc.id;
                    // The reason once; a large batch must not fill the window with copies.
                    reply.content =
                        json{{"status", "error"},
                             {"error", explained
                                           ? std::string("Not run: see the first result of this batch.")
                                           : "Not run: the arguments in this batch were not valid "
                                             "JSON (" + decodeError.substr(0, 400) +
                                             "). Resend the call with a valid JSON object."}}
                            .dump(-1, ' ', false, json::error_handler_t::replace);
                    explained = true;
                    messages.push_back(reply);
                }
                continue;
            }
            malformedStreak = 0;

            // structuredEvents consumers (TUI/WebUI) expect a thought per step;
            // a native turn's reasoning is whatever prose came with the calls.
            if (!config_.streaming || config_.structuredEvents) {
                console_->printThought(response);
            }

            bool loopDetected = false;
            for (size_t i = 0; i < llmResult.toolCalls.size(); ++i) {
                const auto& tc = llmResult.toolCalls[i];
                if (cancelRequested()) break;

                const json& toolArgs = decodedArgs[i];

                if (const int period = loopPeriod(toolCallHistory, tc.name, toolArgs,
                                                  cfg.maxConsecutiveRepeats)) {
                    if (loopWarned) {
                        console_->printWarning("Detected repeated tool call loop. Breaking out.");
                        loopDetected = true;
                        stopReason = "loop";
                        break;
                    }
                    loopWarned = true;
                    console_->printWarning("Repeated tool call; asking the model to change course.");
                    Message reply;
                    reply.role = MessageRole::TOOL;
                    reply.name = tc.name;
                    reply.toolCallId = tc.id;
                    reply.content = loopNudge(tc.name, cfg.maxConsecutiveRepeats, period);
                    messages.push_back(reply);
                    toolCallHistory.clear();
                    continue;
                }

                console_->printToolUsage(tc.name);
                console_->prettyPrintJson(toolArgs, "Tool Args");
                console_->startProgress("Executing " + tc.name);

                json toolResult = executeTool(tc.name, toolArgs);

                console_->stopProgress();
                console_->printToolComplete();
                console_->prettyPrintJson(toolResult, "Tool Result");

                recordToolCall(toolCallHistory, tc.name, toolArgs, cfg.maxConsecutiveRepeats);

                Message toolMsg;
                toolMsg.role = MessageRole::TOOL;
                toolMsg.name = tc.name;
                toolMsg.toolCallId = tc.id;
                toolMsg.content = recordToolOutput(toolResult, cfg.toolResultCharLimit());
                messages.push_back(toolMsg);

                lastToolFailed = false;
                if (auto err = toolFailure(toolResult)) {
                    ++errorCount;
                    lastError = *err;
                    executionState_ = AgentState::ERROR_RECOVERY;
                    lastToolFailed = true;
                }
            }

            // A loop stop is followed by a closing call, so every advertised call needs a reply.
            for (size_t j = 0; loopDetected && j < llmResult.toolCalls.size(); ++j) {
                const auto& tc = llmResult.toolCalls[j];
                const bool answered = std::any_of(
                    messages.begin(), messages.end(), [&tc](const Message& m) {
                        return m.role == MessageRole::TOOL && m.toolCallId == tc.id;
                    });
                if (!answered) {
                    Message reply;
                    reply.role = MessageRole::TOOL;
                    reply.name = tc.name;
                    reply.toolCallId = tc.id;
                    reply.content = R"({"status":"error","error":"Not run."})";
                    messages.push_back(reply);
                }
            }

            if (loopDetected) break;
            if (!batchNote.empty()) {
                Message note;
                note.role = MessageRole::USER;
                note.content = batchNote;
                messages.push_back(note);
            }
            continue;
        }

        // An empty reply is usually a cut-off reasoning block; ask once more.
        if (response.find_first_not_of(" \t\r\n") == std::string::npos) {
            const bool atLimit = llmResult.finishReason == "length";
            emptyAtLimit = emptyAtLimit || atLimit;
            shortNext = atLimit && cfg.compactFeatures();
            if (emptyNudged) {
                stopReason = "empty";
                break;
            }
            emptyNudged = true;
            console_->printWarning(atLimit ? "The model used its whole reply limit of " +
                                                 std::to_string(lastLimit) +
                                                 " tokens without answering; asking again."
                                           : "The model's reply was empty; asking again.");
            Message retry;
            retry.role = MessageRole::USER;
            retry.content = std::string(atLimit
                                            ? "[Your last reply hit the length limit before any "
                                              "answer or tool call; the reasoning used all of it.] "
                                            : "[Your last reply was empty, possibly cut off while "
                                              "reasoning.] ") +
                            "Continue: call a tool or give your final answer, keeping any "
                            "reasoning short. If you were copying a file's contents, stop and refer "
                            "to the file by its path.";
            messages.push_back(retry);
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

        if (parsed.malformedToolCall.has_value()) {
            console_->printWarning("Malformed tool call: " + *parsed.malformedToolCall);
            if (++malformedStreak > 2) {
                finalAnswer = "Unable to complete task: the model wrote a tool call that could "
                              "not be run three times in a row (" + *parsed.malformedToolCall + ")";
                failed = true;
                break;
            }
            Message retry;
            retry.role = MessageRole::USER;
            retry.content = "[Your tool call was not run] " + *parsed.malformedToolCall +
                            " Send the complete call again, or give your final answer.";
            messages.push_back(retry);
            continue;
        }

        // ---- Handle final answer ----
        if (parsed.answer.has_value()) {
            if (auto pushback = reviewOnce(parsed.answer.value())) {
                messages.push_back(*pushback);
                continue;
            }
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
            if (parsed.toolArgsAreText) toolArgs = typeTextArgs(tools_, toolName, std::move(toolArgs));
            malformedStreak = 0;

            // Loop detection — a call, or a short cycle of calls, repeated maxConsecutiveRepeats times
            if (const int period = loopPeriod(toolCallHistory, toolName, toolArgs,
                                              cfg.maxConsecutiveRepeats)) {
                if (loopWarned) {
                    console_->printWarning("Detected repeated tool call loop. Breaking out.");
                    stopReason = "loop";
                    break;
                }
                loopWarned = true;
                console_->printWarning("Repeated tool call; asking the model to change course.");
                Message nudge;
                nudge.role = MessageRole::TOOL;
                nudge.name = toolName;
                nudge.content = loopNudge(toolName, cfg.maxConsecutiveRepeats, period);
                messages.push_back(nudge);
                toolCallHistory.clear();
                continue;
            }

            console_->printToolUsage(toolName);
            console_->prettyPrintJson(toolArgs, "Tool Args");
            console_->startProgress("Executing " + toolName);

            json toolResult = executeTool(toolName, toolArgs);

            console_->stopProgress();
            console_->printToolComplete();
            console_->prettyPrintJson(toolResult, "Tool Result");

            recordToolCall(toolCallHistory, toolName, toolArgs, cfg.maxConsecutiveRepeats);

            // Add tool result to messages
            Message toolMsg;
            toolMsg.role = MessageRole::TOOL;
            toolMsg.name = toolName;
            toolMsg.content = recordToolOutput(toolResult, cfg.toolResultCharLimit());
            messages.push_back(toolMsg);

            lastToolFailed = false;
            if (auto err = toolFailure(toolResult)) {
                ++errorCount;
                lastError = *err;
                executionState_ = AgentState::ERROR_RECOVERY;
                lastToolFailed = true;
            }

            continue;
        }

        // No tool call and no answer — treat response as conversational
        if (!parsed.toolName.has_value() && !parsed.answer.has_value()) {
            if (auto pushback = reviewOnce(response)) {
                messages.push_back(*pushback);
                continue;
            }
            finalAnswer = response;
            if (!config_.streaming || config_.structuredEvents) console_->printFinalAnswer(finalAnswer, totalUsage);
            break;
        }
    }

    // Out of steps, stopped in a loop, or no reply: one tool-free call for an honest wrap-up.
    if (finalAnswer.empty() && !cancelRequested()) {
        if (stopReason.empty()) stopReason = "cap";
        const char* why = stopReason == "loop"  ? "[Stopped: repeated tool calls]"
                          : stopReason == "empty" ? "[Stopped: empty replies]"
                          : stopReason == "error" ? "[Stopped: your last reply could not be used]"
                                                  : "[Step limit reached]";
        console_->printWarning(std::string(why) + " Asking for a closing answer.");
        if (cfg.stepBudgetNotices) {
            Message closing;
            closing.role = MessageRole::USER;
            closing.content =
                std::string(why) +
                " You cannot call any more tools for this request. In plain text, give your "
                "final answer: what you completed, the results so far, and what is still "
                "unfinished. Do not claim work you did not do.";
            messages.push_back(closing);
            try {
                const auto closingStarted = std::chrono::steady_clock::now();
                LlmResult closingResult =
                    callLlmResilient(messages, composeSystemPromptFor(cfg), shortNext ? shortCfg : cfg, false);
                totalUsage += closingResult.usage;
                logLlmCall(stepsTaken, closingStarted, closingResult.usage);
                std::string text = closingResult.content;
                const ParsedResponse p = parseLlmResponse(text);
                if (!cfg.useNativeToolCalls() && p.answer.has_value()) {
                    text = *p.answer;
                } else if (p.toolName.has_value() || p.malformedToolCall.has_value()) {
                    text.clear();  // a tool call written as text is not an answer
                }
                if (!text.empty()) {
                    Message reply;
                    reply.role = MessageRole::ASSISTANT;
                    reply.content = text;
                    messages.push_back(reply);
                    finalAnswer = text;
                    if (!cfg.streaming || cfg.structuredEvents) {
                        console_->printFinalAnswer(finalAnswer, totalUsage);
                    }
                }
            } catch (const std::exception& e) {
                console_->printError(std::string("Closing answer failed: ") + e.what());
            }
        }
        if (finalAnswer.empty()) {
            finalAnswer = stopReason == "loop"
                              ? "Task stopped due to repeated tool call loop."
                          : stopReason == "error"
                              ? "Unable to complete task due to LLM error: " + llmError
                          : stopReason == "empty"
                              ? (emptyAtLimit
                                     ? "Task stopped: the model used its whole reply limit of " +
                                           std::to_string(lastLimit) +
                                           " tokens without giving an answer. Raise the limit "
                                           "(--max-tokens; at most half the context window, "
                                           "--ctx-size) or pick a model that reasons less."
                                     : std::string("Task stopped: the model returned empty replies."))
                              : "Reached maximum steps limit (" + std::to_string(stepsLimit) + " steps).";
            console_->printWarning(finalAnswer);
            failed = true;
        } else if (stopReason != "loop" || lastToolFailed) {
            // A loop the model then answered from is a finished turn, as in the Python agent.
            incomplete = true;
        }
    }
    if (finalAnswer.empty() && cancelRequested()) {
        finalAnswer = "[Cancelled after " + std::to_string(stepsTaken) + " step(s)]";
    }

    console_->printCompletion(stepsTaken, stepsLimit);

    // A budget notice is this turn's guidance; saved, the next turn opens with a stale "5 steps left".
    messages.erase(std::remove_if(messages.begin(), messages.end(),
                                  [&budgetNotices](const Message& m) {
                                      return m.role == MessageRole::USER &&
                                             std::find(budgetNotices.begin(), budgetNotices.end(),
                                                       m.content) != budgetNotices.end();
                                  }),
                   messages.end());

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
    const bool stopped = cancelRequested();
    if (stopped) appendStopNote(messages);
    conversationHistory_ = messages;

    json result = {
        {"result", finalAnswer},
        {"steps_taken", stepsTaken},
        {"steps_limit", stepsLimit},
        {"status", stopped ? "cancelled" : failed ? "error" : incomplete ? "incomplete" : "success"},
        {"tool_errors", errorCount},
        {"tool_calls", turnToolCalls_.size()},
        {"llm_calls", llmCallLog.size()}
    };
    json toolCallLog = json::array();
    for (const auto& call : turnToolCalls_) {
        // Arguments are what make a trace readable; capped so one huge write can't bloat the log.
        std::string args = call.args.dump(-1, ' ', false, json::error_handler_t::replace);
        constexpr size_t kMaxLoggedArgs = 2000;
        if (args.size() > kMaxLoggedArgs) {
            size_t cut = kMaxLoggedArgs;
            while (cut > 0 && (static_cast<unsigned char>(args[cut]) & 0xC0) == 0x80) --cut;
            args = args.substr(0, cut) + "... (" + std::to_string(args.size()) + " bytes)";
        }
        toolCallLog.push_back({{"step", call.step},
                               {"name", call.name},
                               {"args", std::move(args)},
                               {"wall_s", std::round(call.wallSeconds * 1000) / 1000},
                               {"ok", call.succeeded}});
    }
    result["telemetry"] = {{"llm_calls", std::move(llmCallLog)},
                           {"tool_calls", std::move(toolCallLog)}};
    if (totalUsage.totalTokens > 0) {
        result["usage"] = totalUsage.toJson();
    }
    return result;
}

} // namespace gaia
