// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/repl.h"
#include "gaia/agent.h"
#include "gaia/clean_console.h"
#include "gaia/session.h"

#ifdef GAIA_HAS_TUI
#include "gaia/tui_console.h"
#endif

#include <atomic>
#include <csignal>
#include <iostream>
#include <string>
#include <thread>

#ifdef _WIN32
#include <io.h>
#define GAIA_ISATTY _isatty
#define GAIA_FILENO _fileno
#else
#include <unistd.h>
#define GAIA_ISATTY isatty
#define GAIA_FILENO fileno
#endif

namespace gaia {

// ---------------------------------------------------------------------------
// Signal handling — file-scope atomic pointer for Ctrl-C cancellation
// ---------------------------------------------------------------------------

namespace {

/// Global pointer to the active agent, used by the SIGINT handler.
/// Only one ReplRunner::run() should be active at a time.
std::atomic<Agent*> g_activeAgent{nullptr};

/// Previous SIGINT handler, restored when run() exits.
void (*g_previousSigintHandler)(int) = SIG_DFL;

/// SIGINT handler that cancels the active agent instead of terminating.
void sigintHandler(int /*sig*/) {
    Agent* agent = g_activeAgent.load();
    if (agent) {
        agent->requestCancel();
    }
}

/// Trim leading and trailing whitespace from a string.
std::string trim(const std::string& s) {
    auto start = s.find_first_not_of(" \t\r\n");
    if (start == std::string::npos) return "";
    auto end = s.find_last_not_of(" \t\r\n");
    return s.substr(start, end - start + 1);
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

ReplRunner::ReplRunner(Agent& agent, const std::string& prompt)
    : agent_(agent), prompt_(prompt) {
    registerBuiltinCommands();
}

// ---------------------------------------------------------------------------
// Built-in command registration
// ---------------------------------------------------------------------------

void ReplRunner::registerBuiltinCommands() {
    addCommand("/clear", "Clear conversation history",
        [this](const std::string& args, Agent& agent) { cmdClear(args, agent); });

    addCommand("/help", "Show available commands",
        [this](const std::string& args, Agent& agent) { cmdHelp(args, agent); });

    addCommand("/model", "Show or change the active model",
        [this](const std::string& args, Agent& agent) { cmdModel(args, agent); });

    addCommand("/history", "List saved sessions",
        [this](const std::string& args, Agent& agent) { cmdHistory(args, agent); });

    addCommand("/exit", "Exit the REPL",
        [this](const std::string& args, Agent& agent) { cmdExit(args, agent); });
}

// ---------------------------------------------------------------------------
// Built-in command handlers
// ---------------------------------------------------------------------------

void ReplRunner::cmdClear(const std::string& /*args*/, Agent& agent) {
    agent.clearHistory();
    std::cout << "Conversation history cleared." << std::endl;
}

void ReplRunner::cmdHelp(const std::string& /*args*/, Agent& /*agent*/) {
    std::cout << "\nAvailable commands:\n";
    for (const auto& [name, entry] : commands_) {
        std::cout << "  " << name << "  -  " << entry.description << "\n";
    }
    std::cout << std::endl;
}

void ReplRunner::cmdModel(const std::string& args, Agent& agent) {
    std::string modelName = trim(args);
    if (modelName.empty()) {
        std::cout << "Current model: " << agent.config().modelId << std::endl;
    } else {
        agent.setModel(modelName);
        std::cout << "Model set to: " << modelName << std::endl;
    }
}

void ReplRunner::cmdHistory(const std::string& /*args*/, Agent& /*agent*/) {
    if (!sessionStore_) {
        std::cout << "No session store configured." << std::endl;
        return;
    }

    auto sessions = sessionStore_->list();
    if (sessions.empty()) {
        std::cout << "No saved sessions." << std::endl;
        return;
    }

    std::cout << "\nSaved sessions:\n";
    for (const auto& info : sessions) {
        std::cout << "  " << info.id
                  << "  (" << info.messageCount << " messages";
        if (!info.preview.empty()) {
            std::cout << ", \"" << info.preview << "\"";
        }
        std::cout << ")\n";
    }
    std::cout << std::endl;
}

void ReplRunner::cmdExit(const std::string& /*args*/, Agent& /*agent*/) {
    exitRequested_ = true;
}

// ---------------------------------------------------------------------------
// Command dispatch
// ---------------------------------------------------------------------------

bool ReplRunner::tryDispatchCommand(const std::string& input) {
    if (input.empty() || input[0] != '/') {
        return false;
    }

    // Extract command name and args: "/model qwen3" -> name="/model", args="qwen3"
    std::string::size_type spacePos = input.find(' ');
    std::string cmdName;
    std::string cmdArgs;

    if (spacePos == std::string::npos) {
        cmdName = input;
    } else {
        cmdName = input.substr(0, spacePos);
        cmdArgs = trim(input.substr(spacePos + 1));
    }

    auto it = commands_.find(cmdName);
    if (it == commands_.end()) {
        std::cout << "Unknown command: " << cmdName
                  << ". Type /help for available commands." << std::endl;
        return true; // It was a command attempt, just unknown
    }

    it->second.callback(cmdArgs, agent_);
    return true;
}

// ---------------------------------------------------------------------------
// Command registration
// ---------------------------------------------------------------------------

void ReplRunner::addCommand(const std::string& name, const std::string& description,
                            SlashCommandCallback callback) {
    commands_[name] = CommandEntry{description, std::move(callback)};
}

bool ReplRunner::hasCommand(const std::string& name) const {
    return commands_.find(name) != commands_.end();
}

// ---------------------------------------------------------------------------
// Session management
// ---------------------------------------------------------------------------

void ReplRunner::setSessionStore(std::shared_ptr<SessionStore> store) {
    sessionStore_ = std::move(store);
}

void ReplRunner::setResumeId(const std::string& sessionId) {
    resumeId_ = sessionId;
}

void ReplRunner::saveSession() {
    if (!sessionStore_ || sessionId_.empty()) {
        return;
    }
    const auto& history = agent_.history();
    if (history.empty()) {
        return;
    }
    try {
        sessionStore_->save(sessionId_, history);
    } catch (const std::exception& e) {
        std::cerr << "Warning: failed to save session: " << e.what() << std::endl;
    }
}

bool ReplRunner::isInteractiveTerminal() {
    return GAIA_ISATTY(GAIA_FILENO(stdout)) != 0;
}

void ReplRunner::configureOutputHandler() {
    bool shouldUseTui = tuiOverride_ ? useTui_ : isInteractiveTerminal();

#ifdef GAIA_HAS_TUI
    if (shouldUseTui) {
        agent_.setOutputHandler(std::make_unique<TuiConsole>());
        return;
    }
#else
    (void)shouldUseTui; // suppress unused warning
#endif
    // Fallback: CleanConsole for piped output or --no-tui
    agent_.setOutputHandler(std::make_unique<CleanConsole>());
}

// ---------------------------------------------------------------------------
// Banner
// ---------------------------------------------------------------------------

void ReplRunner::printBanner() {
    std::cout << "\n";
    std::cout << "GAIA Agent  |  Model: " << agent_.config().modelId << "\n";
    std::cout << "Type /help for commands, /exit to quit.\n";
    std::cout << std::endl;
}

// ---------------------------------------------------------------------------
// run() — main interactive loop
// ---------------------------------------------------------------------------

void ReplRunner::run() {
    exitRequested_ = false;

    // Configure output handler (TuiConsole vs CleanConsole)
    configureOutputHandler();

    // Print welcome banner
    if (showBanner_) {
        printBanner();
    }

    // Resume session if requested
    if (!resumeId_.empty() && sessionStore_) {
        try {
            auto history = sessionStore_->load(resumeId_);
            agent_.setHistory(std::move(history));
            sessionId_ = resumeId_;
            std::cout << "Resumed session: " << resumeId_ << std::endl;
        } catch (const std::exception& e) {
            std::cout << "Failed to resume session: " << e.what() << std::endl;
        }
    }

    // Generate a new session ID if not resuming
    if (sessionId_.empty() && sessionStore_) {
        sessionId_ = SessionStore::generateId();
    }

    // Install SIGINT handler for Ctrl-C cancellation
    g_activeAgent.store(&agent_);
    g_previousSigintHandler = std::signal(SIGINT, sigintHandler);

    // Main input loop
    std::string input;
    while (!exitRequested_) {
        std::cout << prompt_ << std::flush;

        if (!std::getline(std::cin, input)) {
            // EOF (Ctrl-D on Unix, Ctrl-Z+Enter on Windows)
            std::cout << std::endl;
            break;
        }

        input = trim(input);
        if (input.empty()) {
            continue;
        }

        // Check for bare exit/quit
        if (input == "exit" || input == "quit") {
            break;
        }

        // Try slash command dispatch
        if (tryDispatchCommand(input)) {
            continue;
        }

        // Regular query — run agent in a worker thread so SIGINT can
        // cancel it via requestCancel() without killing the process.
        {
            json result;
            std::exception_ptr eptr;

            std::thread worker([&]() {
                try {
                    result = agent_.processQuery(input);
                } catch (...) {
                    eptr = std::current_exception();
                }
            });

            worker.join();

            if (eptr) {
                try {
                    std::rethrow_exception(eptr);
                } catch (const std::exception& e) {
                    std::cout << "Error: " << e.what() << std::endl;
                }
            } else if (result.contains("result") && result["result"].is_string()) {
                // Final answer is already printed by the console handler
                // in most configurations. Only print if silent mode.
                if (agent_.config().silentMode) {
                    std::cout << result["result"].get<std::string>() << std::endl;
                }
            }
        }
    }

    // Restore previous signal handler
    std::signal(SIGINT, g_previousSigintHandler);
    g_activeAgent.store(nullptr);

    // Save session on exit
    saveSession();

    std::cout << "Goodbye!" << std::endl;
}

// ---------------------------------------------------------------------------
// runOnce() — single query mode
// ---------------------------------------------------------------------------

int ReplRunner::runOnce(const std::string& query) {
    try {
        auto result = agent_.processQuery(query);

        if (result.contains("status") && result["status"] == "error") {
            return 1;
        }

        if (result.contains("result") && result["result"].is_string()) {
            std::cout << result["result"].get<std::string>() << std::endl;
        }

        return 0;
    } catch (const std::exception& e) {
        std::cout << "Error: " << e.what() << std::endl;
        return 1;
    }
}

} // namespace gaia
