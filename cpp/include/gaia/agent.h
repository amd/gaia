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

#include <memory>
#include <string>
#include <vector>

#include "console.h"
#include "json_utils.h"
#include "mcp_client.h"
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

    // Non-copyable
    Agent(const Agent&) = delete;
    Agent& operator=(const Agent&) = delete;

    /// Process a user query through the agent loop.
    /// This is the main entry point — mirrors Python Agent.process_query().
    ///
    /// @param userInput The user's query string
    /// @param maxSteps Override max steps (0 = use config default)
    /// @return JSON result with "result" key containing the final answer
    json processQuery(const std::string& userInput, int maxSteps = 0);

    /// Connect to an MCP server and register its tools.
    /// Mirrors Python MCPClientMixin.connect_mcp_server().
    ///
    /// @param name Friendly name for the server
    /// @param config Config with "command" and optional "args"
    /// @return true if connection succeeded
    bool connectMcpServer(const std::string& name, const json& config);

    /// Disconnect from an MCP server.
    void disconnectMcpServer(const std::string& name);

    /// Disconnect from all MCP servers.
    void disconnectAllMcp();

    /// Get the tool registry (for inspection/testing).
    const ToolRegistry& tools() const { return tools_; }

    /// Get the output handler.
    OutputHandler& console() { return *console_; }

    /// Set a custom output handler.
    void setOutputHandler(std::unique_ptr<OutputHandler> handler);

    /// Get the composed system prompt.
    std::string systemPrompt() const;

    /// Rebuild system prompt (call after adding tools dynamically).
    void rebuildSystemPrompt();

    /// Get a mutable reference to the tool registry (for subclass tool registration).
    ToolRegistry& toolRegistry() { return tools_; }

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

private:
    // ---- LLM Communication ----

    /// Send messages to the LLM and get a response.
    /// Uses OpenAI-compatible chat completions API.
    std::string callLlm(const std::vector<Message>& messages, const std::string& systemPrompt);

    // ---- Execution Helpers ----

    /// Execute a single tool call.
    json executeTool(const std::string& toolName, const json& toolArgs);

    /// Resolve plan parameter placeholders ($PREV.field, $STEP_N.field).
    json resolvePlanParameters(const json& toolArgs, const std::vector<json>& stepResults);

    /// Compose the full system prompt from parts.
    std::string composeSystemPrompt() const;

    // ---- State ----
    AgentConfig config_;
    ToolRegistry tools_;
    std::unique_ptr<OutputHandler> console_;

    AgentState executionState_ = AgentState::PLANNING;
    json currentPlan_;
    int currentStep_ = 0;
    int totalPlanSteps_ = 0;
    int planIterations_ = 0;

    std::vector<std::string> errorHistory_;
    std::vector<Message> conversationHistory_;

    // MCP clients
    std::map<std::string, std::unique_ptr<MCPClient>> mcpClients_;

    // Cached system prompt
    mutable std::string cachedSystemPrompt_;
    mutable bool systemPromptDirty_ = true;

    // Response format template (shared across all agents)
    static const std::string RESPONSE_FORMAT_TEMPLATE;
};

} // namespace gaia
