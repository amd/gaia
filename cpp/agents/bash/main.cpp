// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// CLI entry point for gaia-bash — the GAIA Bash Agent.
//
// Usage:
//   gaia-bash                     Interactive TUI mode (default)
//   gaia-bash "query"             Single query mode
//   gaia-bash --print             Pipe mode (no TUI, CleanConsole)
//   gaia-bash --serve [--port N]  API server mode
//   gaia-bash --mcp               MCP server mode (stdio JSON-RPC)
//   gaia-bash --resume <id>       Resume a saved session
//   gaia-bash --list-sessions     List saved sessions and exit
//   gaia-bash --model <name>      Override the default model
//   gaia-bash --no-tui            Force CleanConsole output
//   gaia-bash --json-events       Emit JSONL events to stdout (for TUI/WebUI)
//   gaia-bash --query "text"      Non-interactive single query (pair with --json-events)
//   gaia-bash --debug             Enable debug logging

#include <iostream>
#include <memory>
#include <string>
#include <vector>

#include "api_server.h"
#include "bash_agent.h"
#include "bash_tools.h"
#include "mcp_server.h"

#include <gaia/clean_console.h>
#include <gaia/json_event_handler.h>
#include <gaia/repl.h>
#include <gaia/session.h>
#include <gaia/types.h>

namespace color = gaia::color;

// ---------------------------------------------------------------------------
// Argument parsing helpers
// ---------------------------------------------------------------------------

/// Print usage information and exit.
static void printUsage(const char* progName) {
    std::cout << color::BOLD << "gaia-bash" << color::RESET
              << " — GAIA Bash Agent\n\n"
              << color::BOLD << "Usage:" << color::RESET << "\n"
              << "  " << progName << "                     Interactive mode (default)\n"
              << "  " << progName << " \"<query>\"            Single query mode\n"
              << "  " << progName << " --print               Pipe mode (no TUI)\n"
              << "  " << progName << " --serve [--port N]    API server (default port 8200)\n"
              << "  " << progName << " --mcp                 MCP server (stdio JSON-RPC)\n"
              << "  " << progName << " --resume <id>         Resume a saved session\n"
              << "  " << progName << " --list-sessions       List saved sessions\n"
              << "  " << progName << " --model <name>        Override model\n"
              << "  " << progName << " --no-tui              Force plain console output\n"
              << "  " << progName << " --json-events         JSONL events to stdout (for TUI/WebUI)\n"
              << "  " << progName << " --query \"<text>\"       Non-interactive query (use with --json-events)\n"
              << "  " << progName << " --debug               Enable debug logging\n"
              << "  " << progName << " --help                Show this help\n";
}

/// List saved sessions and exit.
static int listSessions() {
    gaia::SessionStore store;
    auto sessions = store.list();

    if (sessions.empty()) {
        std::cout << color::GRAY << "No saved sessions." << color::RESET << "\n";
        return 0;
    }

    std::cout << color::BOLD << "Saved sessions:" << color::RESET << "\n\n";
    for (const auto& s : sessions) {
        std::cout << color::CYAN << "  " << s.id << color::RESET
                  << color::GRAY << "  (" << s.messageCount << " messages, "
                  << s.timestamp << ")" << color::RESET << "\n"
                  << "    " << s.preview << "\n\n";
    }
    return 0;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(int argc, char* argv[]) {
    try {
        // Parse arguments
        std::string query;
        std::string resumeId;
        std::string modelOverride;
        int port = 0;
        bool printMode = false;
        bool serveMode = false;
        bool mcpMode = false;
        bool noTui = false;
        bool jsonEvents = false;
        std::string queryArg;
        bool debug = false;
        bool showHelp = false;
        bool listSessionsFlag = false;

        for (int i = 1; i < argc; ++i) {
            std::string arg = argv[i];

            if (arg == "--help" || arg == "-h") {
                showHelp = true;
            } else if (arg == "--print") {
                printMode = true;
            } else if (arg == "--serve") {
                serveMode = true;
            } else if (arg == "--port") {
                if (i + 1 < argc) {
                    try {
                        port = std::stoi(argv[++i]);
                    } catch (...) {
                        std::cerr << color::RED << "Error: --port requires a numeric value"
                                  << color::RESET << "\n";
                        return 1;
                    }
                } else {
                    std::cerr << color::RED << "Error: --port requires a value"
                              << color::RESET << "\n";
                    return 1;
                }
            } else if (arg == "--mcp") {
                mcpMode = true;
            } else if (arg == "--resume") {
                if (i + 1 < argc) {
                    resumeId = argv[++i];
                } else {
                    std::cerr << color::RED << "Error: --resume requires a session ID"
                              << color::RESET << "\n";
                    return 1;
                }
            } else if (arg == "--list-sessions") {
                listSessionsFlag = true;
            } else if (arg == "--model") {
                if (i + 1 < argc) {
                    modelOverride = argv[++i];
                } else {
                    std::cerr << color::RED << "Error: --model requires a model name"
                              << color::RESET << "\n";
                    return 1;
                }
            } else if (arg == "--no-tui") {
                noTui = true;
            } else if (arg == "--json-events") {
                jsonEvents = true;
            } else if (arg == "--query") {
                if (i + 1 < argc) {
                    queryArg = argv[++i];
                } else {
                    std::cerr << color::RED << "Error: --query requires a value"
                              << color::RESET << "\n";
                    return 1;
                }
            } else if (arg == "--debug") {
                debug = true;
            } else if (arg[0] == '-') {
                std::cerr << color::RED << "Unknown option: " << arg
                          << color::RESET << "\n";
                printUsage(argv[0]);
                return 1;
            } else {
                // Positional argument = query
                if (query.empty()) {
                    query = arg;
                } else {
                    // Append additional positional args with spaces
                    query += " ";
                    query += arg;
                }
            }
        }

        // Handle help
        if (showHelp) {
            printUsage(argv[0]);
            return 0;
        }

        // Handle --list-sessions
        if (listSessionsFlag) {
            return listSessions();
        }

        // Handle --serve (API server mode)
        if (serveMode) {
            int serverPort = (port > 0) ? port : 8200;

            gaia::AgentConfig apiConfig;
            apiConfig.debug = debug;
            apiConfig.contextSize = 32768;
            apiConfig.modelId = "Gemma-4-E4B-it-GGUF";
            if (!modelOverride.empty()) apiConfig.modelId = modelOverride;

            gaia::BashAgent apiAgent(apiConfig);
            // API server has no stdin — auto-allow all tool confirmations
            apiAgent.setToolConfirmCallback(
                [](const std::string&, const gaia::json&) {
                    return gaia::ToolConfirmResult::ALLOW_ONCE;
                });
            gaia::ApiServer server(apiAgent, serverPort);
            server.setSessionStore(std::make_shared<gaia::SessionStore>());

            std::cerr << color::GREEN << color::BOLD << "gaia-bash"
                      << color::RESET << " API server starting on port "
                      << serverPort << "\n";
            server.run();  // blocking
            return 0;
        }

        // Handle --mcp (MCP stdio server mode)
        if (mcpMode) {
            gaia::AgentConfig mcpConfig;
            mcpConfig.debug = debug;
            mcpConfig.silentMode = true;  // no console output on stdout
            mcpConfig.contextSize = 32768;
            mcpConfig.modelId = "Gemma-4-E4B-it-GGUF";
            if (!modelOverride.empty()) mcpConfig.modelId = modelOverride;

            gaia::BashAgent mcpAgent(mcpConfig);
            // In MCP mode, the external agent handles safety — auto-allow all tools
            mcpAgent.setToolConfirmCallback(
                [](const std::string&, const gaia::json&) {
                    return gaia::ToolConfirmResult::ALLOW_ONCE;
                });
            gaia::McpServer mcpServer(mcpAgent);

            mcpServer.run();  // blocking, reads stdin
            return 0;
        }

        // Handle --json-events mode (JSONL subprocess for TUI/WebUI)
        if (jsonEvents) {
            gaia::AgentConfig jeConfig;
            jeConfig.debug = debug;
            jeConfig.contextSize = 32768;
            jeConfig.streaming = false;       // avoid raw JSON tokens in output
            jeConfig.structuredEvents = true;  // emit thought/goal/answer events
            if (!modelOverride.empty()) jeConfig.modelId = modelOverride;

            gaia::BashAgent jeAgent(jeConfig);
            jeAgent.setOutputHandler(std::make_unique<gaia::JsonEventOutputHandler>());
            // In JSON events mode, auto-allow all tools (TUI handles confirmation)
            jeAgent.setToolConfirmCallback(
                [](const std::string&, const gaia::json&) {
                    return gaia::ToolConfirmResult::ALLOW_ONCE;
                });

            // --query "text" runs a single query
            std::string jeQuery = queryArg.empty() ? query : queryArg;
            if (!jeQuery.empty()) {
                jeAgent.processQuery(jeQuery);
                return 0;
            }

            // Interactive JSONL mode: read queries from stdin, emit events to stdout.
            // Each line on stdin is a user query; events go to stdout as JSONL.
            std::string line;
            while (std::getline(std::cin, line)) {
                if (line.empty()) continue;
                jeAgent.processQuery(line);
            }
            return 0;
        }

        // Build agent config
        gaia::AgentConfig config;
        config.debug = debug;
        config.contextSize = 32768;  // bash agent needs 32K for system prompt + tools
        config.modelId = "Gemma-4-E4B-it-GGUF";

        if (!modelOverride.empty()) {
            config.modelId = modelOverride;
        }

        // --print implies --no-tui and auto-allows tools (no interactive stdin)
        if (printMode) {
            noTui = true;
        }

        // Create agent
        gaia::BashAgent agent(config);

        // In pipe/print mode, auto-allow all tools since there's no stdin for confirmation
        if (printMode) {
            agent.setToolConfirmCallback(
                [](const std::string&, const gaia::json&) {
                    return gaia::ToolConfirmResult::ALLOW_ONCE;
                });
        }

        // Set up the REPL
        gaia::ReplRunner repl(agent);
        repl.setSessionStore(std::make_shared<gaia::SessionStore>());

        if (!resumeId.empty()) {
            repl.setResumeId(resumeId);
        }

        if (noTui) {
            repl.setUseTui(false);
        }

        // Register bash-specific slash commands
        repl.addCommand("/run", "Execute a bash command directly",
            [](const std::string& args, gaia::Agent& a) {
                if (args.empty()) {
                    a.console().printWarning("Usage: /run <command>");
                    return;
                }
                // Execute directly via bash_execute tool
                gaia::json toolArgs = {{"command", args}};
                auto result = a.toolRegistry().executeTool("bash_execute", toolArgs);
                if (result.contains("error")) {
                    a.console().printError(result["error"].get<std::string>());
                } else {
                    std::string output;
                    if (result.contains("stdout") && !result["stdout"].get<std::string>().empty()) {
                        output = result["stdout"].get<std::string>();
                    }
                    if (result.contains("stderr") && !result["stderr"].get<std::string>().empty()) {
                        if (!output.empty()) output += "\n";
                        output += result["stderr"].get<std::string>();
                    }
                    if (!output.empty()) {
                        a.console().printInfo(output);
                    }
                    int exitCode = result.value("exit_code", -1);
                    if (exitCode != 0) {
                        a.console().printWarning("Exit code: " + std::to_string(exitCode));
                    }
                }
            });

        repl.addCommand("/env", "Show environment info (shell, OS, tools)",
            [](const std::string& /*args*/, gaia::Agent& a) {
                auto result = a.toolRegistry().executeTool("env_inspect", gaia::json::object());
                if (result.contains("error")) {
                    a.console().printError(result["error"].get<std::string>());
                } else {
                    // Print formatted environment info directly to stdout
                    // (printInfo is a no-op in CleanConsole, so use cout)
                    if (result.contains("shell")) {
                        std::cout << gaia::color::CYAN << "  Shell: "
                                  << gaia::color::RESET << result["shell"].get<std::string>() << std::endl;
                    }
                    if (result.contains("os")) {
                        std::string os = result["os"].get<std::string>();
                        auto cr = os.find('\r');
                        if (cr != std::string::npos) os = os.substr(0, cr);
                        std::cout << gaia::color::CYAN << "  OS:    "
                                  << gaia::color::RESET << os << std::endl;
                    }
                    if (result.contains("tools") && result["tools"].is_object()) {
                        std::string installed, missing;
                        for (auto& [name, avail] : result["tools"].items()) {
                            if (avail.get<bool>()) {
                                if (!installed.empty()) installed += ", ";
                                installed += name;
                            } else {
                                if (!missing.empty()) missing += ", ";
                                missing += name;
                            }
                        }
                        if (!installed.empty()) {
                            std::cout << gaia::color::GREEN << "  Tools: "
                                      << gaia::color::RESET << installed << std::endl;
                        }
                        if (!missing.empty()) {
                            a.console().printWarning("Not found: " + missing);
                        }
                    }
                }
            });

        // Single query mode
        if (!query.empty()) {
            return repl.runOnce(query);
        }

        // Interactive mode
        repl.run();
        return 0;

    } catch (const std::exception& e) {
        std::cerr << color::RED << color::BOLD << "Fatal error: "
                  << color::RESET << color::RED << e.what()
                  << color::RESET << "\n";
        return 1;
    }
}
