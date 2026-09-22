// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Core Agent class with state machine and execution loop.
// Ported from Python: src/gaia/agents/base/agent.py
//
// The Agent manages:
//   - LLM conversation via HTTP (OpenAI-compatible API)
//   - Tool registration and execution
//   - Multi-step plan management with state machine
//   - JSON response parsing with fallback strategies
//   - Error recovery and loop detection

#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <vector>

#include "console.h"
#include "json_utils.h"
#include "lemonade_client.h"
#include "mcp_client.h"
#include "mcp_registry.h"
#include "security.h"
#include "skill_sets.h"
#include "tool_registry.h"
#include "types.h"
#include "gaia/export.h"

namespace gaia {

/// Base Agent class providing the core conversation loop and tool execution.
/// Subclass and override registerTools() and getSystemPrompt() for domain agents.
///
/// Mirrors Python Agent class with:
///   - State machine (PLANNING -> EXECUTING_PLAN -> COMPLETION)
///   - processQuery() main loop
///   - JSON parsing with multi-strategy fallback
///   - Error recovery with loop detection
class GAIA_API Agent {
public:
    explicit Agent(const AgentConfig& config = {});
    virtual ~Agent();

    // Non-copyable, non-movable (mutex member prevents move)
    Agent(const Agent&) = delete;
    Agent& operator=(const Agent&) = delete;
    Agent(Agent&&) = delete;
    Agent& operator=(Agent&&) = delete;

    /// Process a user query through the agent loop.
    /// This is the main entry point — mirrors Python Agent.process_query().
    ///
    /// @param userInput The user's query string
    /// @param maxSteps Override max steps (0 = use config default)
    /// @return JSON result with "result" key containing the final answer
    json processQuery(const std::string& userInput, int maxSteps = 0);

    /// VLM convenience overload: text + images in a single user turn.
    /// Images are sent as base64 data-URIs inside an OpenAI-compatible
    /// image_url content part. Stateful and symmetric with the string
    /// overload: history is appended with text-only stripped messages.
    json processQuery(const std::string& userInput,
                      const std::vector<Image>& images,
                      int maxSteps = 0);

    /// Low-level overload: caller composes the turn as a vector of
    /// Messages (which may include pre-set `parts` for mixed content).
    /// The messages are appended to conversationHistory_ (stripped of
    /// image parts on store). Throws std::invalid_argument on empty input.
    json processQuery(const std::vector<Message>& messages, int maxSteps = 0);

    /// Connect to an MCP server and register its tools.
    /// Mirrors Python MCPClientMixin.connect_mcp_server().
    ///
    /// Each discovered tool is registered with ToolPolicy::CONFIRM unless the
    /// server proves it read-only — see mcpToolRequiresConfirmation() in
    /// mcp_client.h. A silentMode agent has no confirm callback, so gated MCP
    /// tools are denied (fail-closed) until one is installed via
    /// setToolConfirmCallback() or the tool is pre-approved in AllowedToolsStore.
    ///
    /// @param name Friendly name for the server
    /// @param config Config with "command" and optional "args"
    /// @return true if connection succeeded
    bool connectMcpServer(const std::string& name, const json& config);

    /// Connect to an MCP server named by its configured id, resolving the
    /// launch config through MCPRegistry (`$GAIA_CONFIG_DIR`, else `~/.gaia`).
    /// This is what backs a skill declaring `mcp:connect:<id>`.
    ///
    /// Resolution failures throw rather than returning false: an id that
    /// cannot be resolved means the caller asked for capability that is not
    /// there, and silently continuing produces an agent that has quietly lost
    /// its tools. Connection failures keep the connectMcpServer() contract and
    /// return false after printing the error.
    ///
    /// @param id Server id as it appears under "mcpServers" in the config file
    /// @return true if connection succeeded
    /// @throws MCPRegistryError if the id is unknown, no config file exists,
    ///         the config is malformed, or the entry is not launchable.
    bool connectMcpServerById(const std::string& id);

    /// connectMcpServerById() against an explicit registry (tests, embedders
    /// that keep their MCP config somewhere other than the config directory).
    bool connectMcpServerById(const std::string& id, const MCPRegistry& registry);

    /// Disconnect from an MCP server.
    void disconnectMcpServer(const std::string& name);

    /// Disconnect from all MCP servers.
    void disconnectAllMcp();

    /// Get the tool registry (for inspection/testing).
    const ToolRegistry& tools() const { return tools_; }

    /// Set the confirmation callback for CONFIRM-policy tools.
    /// Delegates to ToolRegistry::setConfirmCallback().
    void setToolConfirmCallback(ToolConfirmCallback cb);

    /// Set the default policy for local tools registered without an explicit policy.
    /// Delegates to ToolRegistry::setDefaultPolicy(). Affects only tools
    /// registered afterwards.
    /// For MCP tools discovered by a later connectMcpServer() call this can only
    /// raise the classifier's verdict (ALLOW < CONFIRM < DENY) — a gated MCP
    /// tool is never lowered back to ALLOW.
    void setDefaultPolicy(ToolPolicy policy);

    /// Get the output handler.
    OutputHandler& console() { return *console_; }

    /// Set a custom output handler.
    void setOutputHandler(std::unique_ptr<OutputHandler> handler);

    /// Get the composed system prompt.
    std::string systemPrompt() const;

    /// Rebuild system prompt (call after adding tools dynamically).
    void rebuildSystemPrompt();

    /// Clear conversation history (start a fresh topic).
    void clearHistory() { conversationHistory_.clear(); }

    /// Get a snapshot of the current conversation history (for session persistence).
    /// Returns a copy to avoid races with processQuery() on another thread.
    std::vector<Message> history() const {
        std::lock_guard<std::mutex> lock(configMutex_);
        return conversationHistory_;
    }

    /// Replace conversation history (for session resume).
    /// Must NOT be called while processQuery() is running (guarded by inFlight_).
    /// Broken native tool-call pairs are repaired on ingestion: a session file
    /// written by another build (or hand-edited) can carry a role=tool message
    /// whose assistant tool_calls turn is missing, which the server rejects on
    /// the very first request of the next turn.
    void setHistory(std::vector<Message> history);

    /// Request cancellation of the current processQuery() run.
    /// The agent loop checks this flag between steps and exits early
    /// with a partial result. Safe to call from any thread.
    /// The flag is automatically reset at the start of the next processQuery().
    void requestCancel() { cancelled_.store(true); }

    /// Check whether a cancel has been requested.
    bool isCancelled() const { return cancelled_.load(); }

    /// Get a mutable reference to the tool registry (for subclass tool registration).
    ToolRegistry& toolRegistry() { return tools_; }

    /// Get the Lemonade client (for explicit model loading at startup).
    LemonadeClient& lemonade() { return lemonade_; }

    // ---- Skill sets ----

    /// This agent's parsed `skills:` / `skill_sets:` declarations.
    ///
    /// Read once from `AgentConfig::skillManifest` — or from the gaia-agent.yaml
    /// found beside the running executable — and cached. Falsy (and empty) when
    /// the agent has no manifest, so nothing changes for an agent that does not
    /// use skills.
    ///
    /// @throws SkillValidationError if the manifest is unreadable or its skill
    ///         blocks are malformed. An agent whose own manifest cannot be read
    ///         is broken, not degraded.
    const SkillSets& skillSets() const;

    /// The skill set this agent resolved, or nullopt if it has not loaded one.
    std::optional<std::string> activeSkillSet() const;

    /// Resolve the active set: `requested` -> selectSkillSet() -> the default.
    ///
    /// @param requested Explicit override. Defaults to `AgentConfig::skillSet`.
    /// @throws SkillSetError if the resolved name is not a declared set.
    SkillSetResolution resolveSkillSet(
        const std::optional<std::string>& requested = std::nullopt) const;

    /// Resolve and load this agent's declared skills. Returns what loaded.
    ///
    /// A no-op returning {} when the agent declares no skills. Otherwise it
    /// loads the always-on `skills:` list plus the resolved set. A skill
    /// declared `required: false` that is missing is reported and skipped;
    /// every other failure propagates, because an agent launched with the wrong
    /// capabilities is worse than one that refuses to launch.
    ///
    /// Call it again with a different name to switch sets mid-session. The
    /// previous set's skills are unloaded only *after* the new set has loaded,
    /// and only the ones the previous set brought in — an always-on skill, and
    /// a skill some other caller loaded by hand, both survive the switch.
    ///
    /// **All-or-nothing.** A failure part-way through leaves the agent exactly
    /// as it was: the previous set still loaded, activeSkillSet() still
    /// accurate. A half-switched agent reporting one set while carrying
    /// another's skills is worse than a thrown error.
    ///
    /// The switch is not sticky: a later bare loadSkillSet() re-runs the full
    /// resolution and returns to whatever that yields. Pass the name again to
    /// stay on it.
    ///
    /// With no loader installed (see setSkillLoader()) the set still resolves
    /// and activeSkillSet() still reports it, but nothing registers and this
    /// returns {} after warning. Treat activeSkillSet() as "which set was
    /// chosen", not as proof the skills are present.
    ///
    /// **Not thread-safe.** Like connectMcpServer() and setHistory(), this
    /// mutates agent state the caller must serialize: do not call it
    /// concurrently with itself, with setSkillLoader(), or while
    /// processQuery() is running. A lock here would not help — the loader may
    /// call back into the agent (registering tools, rebuilding the prompt),
    /// and configMutex_ is not recursive. Reading activeSkillSet() /
    /// skillSetLoaded() from another thread is safe.
    ///
    /// @throws SkillSetError on an undeclared set name — never a silent
    ///         fallback to the default — or when a set is already loaded and
    ///         the loader that registered it has since been detached.
    std::vector<std::string> loadSkillSet(
        const std::optional<std::string>& requested = std::nullopt);

    /// Names the active set brought in, in load order. Excludes anything loaded
    /// outside a set — that is what makes a switch retire only its own skills.
    /// Returned by value: a switch replaces the list, so a reference into it
    /// would dangle.
    std::vector<std::string> skillSetLoaded() const;

    /// Install the loader that actually registers skills (P3.3 / #2800).
    /// Not owned; must outlive the agent. Passing nullptr detaches it.
    ///
    /// **Not thread-safe** — setup-time call, serialized with loadSkillSet()
    /// by the caller. See loadSkillSet().
    void setSkillLoader(SkillLoader* loader) { skillLoader_ = loader; }

    // ---- Dynamic reconfiguration ----

    /// Get a copy of the current config (thread-safe snapshot).
    AgentConfig config() const;

    /// Replace the entire config. Validates before applying; propagates to LemonadeClient.
    /// Throws std::invalid_argument if the config is invalid.
    /// Changes take effect on the next processQuery() call.
    void setConfig(const AgentConfig& newConfig);

    /// Change the active model. Resets modelEnsured_ so the next processQuery() reloads it.
    void setModel(const std::string& modelId);

    /// Convenience setters — take effect on the next processQuery() call.
    void setMaxSteps(int maxSteps);
    void setMaxTokens(int maxTokens);
    void setTemperature(double temperature);
    void setDebug(bool debug);

protected:
    /// Initialize the agent after construction.
    /// Call this at the end of subclass constructors to register tools.
    /// This exists because virtual dispatch doesn't work from base constructors in C++.
    void init() {
        registerTools();
        systemPromptDirty_ = true;
    }

    /// Register domain-specific tools.
    /// Override in subclasses to add tools.
    virtual void registerTools() {}

    /// Return agent-specific system prompt additions.
    /// Override to customize agent behavior.
    virtual std::string getSystemPrompt() const { return ""; }

    /// Selector hook: which skill set fits this launch's runtime state?
    ///
    /// Called only when no explicit set was requested — an explicit choice must
    /// never be second-guessed by agent state. Override to key the active set
    /// off something the agent already knows: a connected account's type, a
    /// workspace mode, a device profile.
    ///
    /// Returning nullopt (or an empty string) means "no opinion", which defers
    /// to the manifest's `default_skill_set`. Returning a name this agent does
    /// not declare fails loudly rather than falling back — a selector that
    /// computed an undeclared name is a wiring bug, not a reason to guess.
    virtual std::optional<std::string> selectSkillSet() const { return std::nullopt; }

private:
    /// Unified entry point for all processQuery overloads. Owns the full
    /// conversation turn: concurrency guard, empty-input validation,
    /// ensureModelLoaded, history prepend, LLM loop, and end-of-turn
    /// history write (text-only; image parts stripped).
    json processQueryInternal(const std::vector<Message>& userMessages, int maxSteps);

    // ---- LLM Communication ----

    struct LlmResult {
        std::string content;
        UsageStats usage;
        /// Native OpenAI tool calls, when the model answered with them.
        /// Always empty on the prompt-JSON path.
        std::vector<ToolCall> toolCalls;
    };

    /// Send messages to the LLM and get a response with usage stats.
    /// Uses OpenAI-compatible chat completions API.
    /// @param cfg  Config snapshot from the current processQuery() call.
    LlmResult callLlm(const std::vector<Message>& messages, const std::string& systemPrompt,
                      const AgentConfig& cfg);

    // ---- Execution Helpers ----

    /// Execute a single tool call.
    json executeTool(const std::string& toolName, const json& toolArgs);

    /// Resolve plan parameter placeholders ($PREV.field, $STEP_N.field).
    json resolvePlanParameters(const json& toolArgs, const std::vector<json>& stepResults);

    /// Compose the full system prompt from parts, using a live config snapshot.
    std::string composeSystemPrompt() const;

    /// Compose against an explicit config. The agent loop uses this with its
    /// turn snapshot so the prompt and the request body can never disagree
    /// about which tool protocol is active (a concurrent setModel() would
    /// otherwise flip one and not the other mid-turn).
    std::string composeSystemPromptFor(const AgentConfig& cfg) const;

    /// Call an MCP tool with automatic reconnect on connection failure.
    json callMcpTool(const std::string& serverName, const std::string& toolName, const json& args);

    /// Attempt to reconnect to a previously registered MCP server using its stored config.
    bool reconnectMcpServer(const std::string& name);

    /// The explicit skill-set choice: `requested`, else `AgentConfig::skillSet`.
    /// One place, so a caller cannot read the config field twice and disagree.
    std::optional<std::string> requestedSkillSet(
        const std::optional<std::string>& requested) const;

    /// Report a declared `version:` pin against the version actually on disk.
    /// GAIA cannot enforce pins until versioned installs land (#2467), so it
    /// says so loudly rather than accepting the pin in silence (#2864).
    void reportVersionPin(const SkillRef& ref) const;

    // ---- State ----
    AgentConfig config_;
    ToolRegistry tools_;
    std::unique_ptr<OutputHandler> console_;
    LemonadeClient lemonade_;
    std::atomic<bool> modelEnsured_{false};

    // Concurrency guard — Agent is NOT re-entrant. A second processQuery
    // call on the same Agent (from any thread) throws std::runtime_error.
    std::atomic<bool> inFlight_{false};

    // Cancel flag — set by requestCancel(), checked between loop steps.
    // Reset at the start of each processQuery().
    std::atomic<bool> cancelled_{false};

    AgentState executionState_ = AgentState::PLANNING;
    json currentPlan_;
    int currentStep_ = 0;
    int totalPlanSteps_ = 0;
    int planIterations_ = 0;

    std::vector<std::string> errorHistory_;
    std::vector<Message> conversationHistory_;

    // Security: persistent allowed-tools store (shared with tools_)
    std::shared_ptr<AllowedToolsStore> allowedToolsStore_;

    // MCP clients and their configs (configs stored for reconnect)
    std::map<std::string, std::unique_ptr<MCPClient>> mcpClients_;
    std::map<std::string, json> mcpServerConfigs_;

    // Cached system prompt
    mutable std::string cachedSystemPrompt_;
    mutable bool systemPromptDirty_ = true;

    // ---- Skill sets ----
    // Parsed lazily from the manifest on first skillSets() call, then cached.
    mutable std::optional<SkillSets> skillSets_;
    // The set currently active, and exactly which skills it brought in. The
    // second is what makes a switch retire only its own skills: anything loaded
    // outside a set is absent from it and therefore never unloaded.
    //
    // Written only by loadSkillSet(), which the caller serializes (see its
    // docs). The two writes take configMutex_ so the accessors can be read from
    // any thread; the writer's own reads are unlocked because it is the only
    // writer. skillLoader_ is mutation-side only and needs no lock.
    std::optional<std::string> activeSkillSet_;
    std::vector<std::string> skillSetLoaded_;
    SkillLoader* skillLoader_ = nullptr;

    // Mutex protecting config_ for concurrent setters / processQuery()
    mutable std::mutex configMutex_;

    // Response format templates (shared across all agents). Neither is sent
    // when native tool calling is active — the model then uses the OpenAI
    // function-calling protocol it was trained on.
    static const std::string RESPONSE_FORMAT_TEMPLATE;              // ResponseMode::Planning
    static const std::string CONVERSATIONAL_FORMAT_TEMPLATE;        // ResponseMode::Conversational
};

} // namespace gaia
