// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Reusable interactive REPL runner for any GAIA agent.
// Provides slash command framework, Ctrl-C cancellation, and session persistence.

#pragma once

#include <functional>
#include <map>
#include <memory>
#include <string>

#include "gaia/export.h"

namespace gaia {

// Forward declarations
class Agent;
class SessionStore;

/// Callback type for slash commands. Receives the argument string
/// (everything after the command name, trimmed) and the Agent reference.
using SlashCommandCallback = std::function<void(const std::string& args, Agent& agent)>;

/// Reusable interactive REPL runner for any GAIA agent.
///
/// Provides a two-thread architecture:
///   - Main thread: reads user input
///   - Worker thread: runs agent.processQuery()
///
/// Features:
///   - Slash command framework with built-in commands (/clear, /help, /model, /history)
///   - Agent-registered custom commands (e.g. /lint, /review)
///   - Ctrl-C cancels current agent run (via Agent::requestCancel()), doesn't kill process
///   - Session persistence via SessionStore
///   - Single-query mode (run one query, print result, exit)
///
/// Usage:
/// @code
///   Agent myAgent(config);
///   ReplRunner repl(myAgent);
///   repl.addCommand("/lint", "Run linter", [](const std::string& args, Agent& a) { ... });
///   repl.run();  // blocking — runs until /exit or EOF
/// @endcode
class GAIA_API ReplRunner {
public:
    /// Construct a REPL for the given agent.
    /// @param agent The agent to run queries against.
    /// @param prompt The input prompt string (default: "> ").
    explicit ReplRunner(Agent& agent, const std::string& prompt = "> ");

    /// Run the interactive REPL loop (blocking).
    /// Returns when the user types /exit, "exit", "quit", or sends EOF (Ctrl-D).
    void run();

    /// Run a single query, print the result, and return the exit code.
    /// @param query The query string to process.
    /// @return 0 on success, 1 on failure.
    int runOnce(const std::string& query);

    /// Register a custom slash command.
    /// @param name Command name including the slash (e.g. "/lint").
    /// @param description Help text shown by /help.
    /// @param callback Function to invoke when the command is used.
    void addCommand(const std::string& name, const std::string& description,
                    SlashCommandCallback callback);

    /// Set the session store for save/load/resume.
    /// When set, conversations are auto-saved on exit.
    void setSessionStore(std::shared_ptr<SessionStore> store);

    /// Set the session ID to resume (loads history on first run()).
    void setResumeId(const std::string& sessionId);

    /// Set whether to show the welcome banner on run().
    void setShowBanner(bool show) { showBanner_ = show; }

    /// Force TUI mode on or off. When false, uses CleanConsole even if
    /// FTXUI is available. When not called, auto-detects based on whether
    /// stdout is an interactive terminal (isatty).
    void setUseTui(bool useTui) { useTui_ = useTui; tuiOverride_ = true; }

    /// Check whether stdout is an interactive terminal.
    static bool isInteractiveTerminal();

    /// Try to dispatch input as a slash command.
    /// @return true if the input was a command (handled), false if it's a query for the LLM.
    bool tryDispatchCommand(const std::string& input);

    /// Check whether a given command name is registered.
    /// @param name Command name including the slash (e.g. "/clear").
    /// @return true if the command is registered.
    bool hasCommand(const std::string& name) const;

    /// Get the number of registered commands.
    size_t commandCount() const { return commands_.size(); }

private:
    Agent& agent_;
    std::string prompt_;
    bool showBanner_ = true;

    // Slash commands: name -> {description, callback}
    struct CommandEntry {
        std::string description;
        SlashCommandCallback callback;
    };
    std::map<std::string, CommandEntry> commands_;

    // Session
    std::shared_ptr<SessionStore> sessionStore_;
    std::string sessionId_;
    std::string resumeId_;

    // Built-in command handlers
    void cmdClear(const std::string& args, Agent& agent);
    void cmdHelp(const std::string& args, Agent& agent);
    void cmdModel(const std::string& args, Agent& agent);
    void cmdHistory(const std::string& args, Agent& agent);
    void cmdExit(const std::string& args, Agent& agent);

    /// Register all built-in slash commands.
    void registerBuiltinCommands();

    /// Print the welcome banner.
    void printBanner();

    /// Save the current session (if store is set).
    void saveSession();

    bool exitRequested_ = false;
    bool useTui_ = true;
    bool tuiOverride_ = false;

    /// Configure the agent's output handler based on TUI availability.
    void configureOutputHandler();
};

} // namespace gaia
