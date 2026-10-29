// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaia-agent — the GAIA flagship as one native binary.
//
//   gaia-agent                      Interactive chat (TUI when built in); with stdin
//                                   piped, the flagship stdio protocol the Go TUI speaks
//   gaia-agent "question"           Answer one question and exit
//   gaia-agent --json-events        JSONL events on stdout, one query per stdin line
//   gaia-agent --serve [--port N]   OpenAI-compatible HTTP API
//   gaia-agent --mcp                MCP server over stdio
//   gaia-agent --list-skills        What was discovered, loaded, and refused
//
// Run --help for every flag.

#include <chrono>
#include <cstdio>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <sstream>
#include <memory>
#include <string>
#include <vector>

#ifdef _WIN32
#include <io.h>
#define GAIA_ISATTY _isatty
#define GAIA_FILENO _fileno
#else
#include <unistd.h>
#define GAIA_ISATTY isatty
#define GAIA_FILENO fileno
#endif

#include "api_server.h"
#include "flagship_agent.h"
#include "mcp_server.h"
#include "stdio_server.h"

#include <gaia/clean_console.h>
#include <gaia/console.h>
#include <gaia/flagship_config.h>
#include <gaia/json_event_handler.h>
#include <gaia/repl.h>
#include <gaia/session.h>
#include <gaia/types.h>

extern const gaia::EmbeddedSkill kGaiaEmbeddedSkills[];
extern const std::size_t kGaiaEmbeddedSkillCount;

namespace fs = std::filesystem;
namespace color = gaia::color;

namespace {

const char* const kVersion = "0.1.0";

// The name this copy was installed under (gaia-native, gaia-agent, ...).
std::string g_prog = "gaia-agent";

void printUsage() {
    const char* prog = g_prog.c_str();
    std::cout << color::BOLD << g_prog << color::RESET << " " << kVersion
              << " — the GAIA flagship agent, native build\n\n"
              << color::BOLD << "Usage:" << color::RESET << "\n"
              << "  " << prog << "                       Interactive chat\n"
              << "  " << prog << " \"<question>\"          Answer one question and exit\n"
              << "  " << prog << " -p \"<question>\"       Print only the answer (scripts, pipes, evals)\n"
              << "  " << prog << " --stdio               The flagship stdio protocol (default when stdin is piped)\n"
              << "  " << prog << " --json-events         Legacy JSONL events; one query per stdin line (or --query)\n"
              << "  " << prog << " --serve [--port N]    OpenAI-compatible API (default port 8150)\n"
              << "  " << prog << " --mcp                 MCP server over stdio\n"
              << "  " << prog << " --list-skills         Skills found, loaded, and refused\n"
              << "  " << prog << " --list-sessions       Saved chats\n\n"
              << color::BOLD << "Options:" << color::RESET << "\n"
              << "  --model <id>        Model (default: config.json default_model, else Gemma-4-E4B-it-GGUF)\n"
              << "  --skill <name>      Load a skill at start (repeatable)\n"
              << "  --fast              Conversation only: no tools, skills or memory tools\n"
              << "  --allow <folder>    Folder the file tools may use (repeatable; default: home + working folder)\n"
              << "  --no-web            Leave out the web tools\n"
              << "  --no-memory         Do not read or write persistent memory this session\n"
              << "  --yes               Approve every tool call without asking\n"
              << "  --full-access       Same as --yes (the TUI's spelling)\n"
              << "  --accept-edits      In --stdio, file edits inside the working folder run without asking\n"
              << "  --resume <id>       Continue a saved chat\n"
              << "  --query <text>      The query for --json-events\n"
              << "  -p, --print         Print only the final answer on stdout; with no question, one\n"
              << "                      answer per line of stdin\n"
              << "  --prompt-file <f>   Read the question from a UTF-8 file\n"
              << "  --max-steps <n>     Step limit for a turn (default 150, or GAIA_AGENT_MAX_STEPS)\n"
              << "  --no-tui / --tui    Force plain console or the TUI\n"
              << "  --debug, --dev      Log requests to stderr\n"
              << "  --trace             Save each turn's model calls, tool calls and timings as JSON in\n"
              << "                      ~/.gaia/native/traces, named after --prompt-file and the time\n"
              << "  --trace-file <f>    Save the trace to <f> instead\n"
              << "  --version           Print the version\n\n"
              << color::BOLD << "Environment:" << color::RESET << "\n"
              << "  LEMONADE_BASE_URL, LEMONADE_API_KEY, GAIA_AGENT_MAX_STEPS, GAIA_CTX_SIZE, GAIA_FAST,\n"
              << "  GAIA_ALLOWED_PATHS, GAIA_MEMORY_DISABLED, GAIA_NATIVE_MEMORY_DB, GAIA_AUTO_APPROVE_TOOLS,\n"
              << "  GAIA_HOME\n";
}

std::string sessionId() {
    const auto now = std::chrono::system_clock::now();
    const std::time_t t = std::chrono::system_clock::to_time_t(now);
    std::tm tm{};
#ifdef _WIN32
    localtime_s(&tm, &t);
#else
    localtime_r(&t, &tm);
#endif
    char buf[32];
    std::strftime(buf, sizeof(buf), "%Y%m%d-%H%M%S", &tm);
    return std::string("native-") + buf;
}

int listSkills(gaia::FlagshipAgent& agent) {
    auto& mgr = agent.skillManager();
    std::cout << color::BOLD << "Skills" << color::RESET << " (searched: ";
    const auto roots = mgr.rootsSearched();
    for (std::size_t i = 0; i < roots.size(); ++i) std::cout << (i ? ", " : "") << roots[i];
    std::cout << ")\n\n";
    for (const auto& [name, d] : mgr.skills()) {
        std::cout << "  " << (d.refusal.empty() ? color::CYAN : color::RED) << name << color::RESET
                  << color::GRAY << "  [" << d.rootLabel << (agent.skills().isLoaded(name) ? ", loaded" : "")
                  << "]" << color::RESET << "\n";
        if (!d.refusal.empty()) {
            std::cout << "    refused: " << d.refusal << "\n";
            continue;
        }
        const auto missing = mgr.missingTools(d.skill);
        if (!missing.empty()) {
            std::cout << "    not in the catalogue: needs ";
            for (std::size_t i = 0; i < missing.size(); ++i) std::cout << (i ? ", " : "") << missing[i];
            std::cout << ", which this build does not have\n";
        }
    }
    for (const auto& [where, why] : mgr.invalid()) {
        std::cout << "  " << color::RED << "invalid" << color::RESET << " " << where << ": " << why << "\n";
    }
    for (const auto& [name, where] : mgr.shadowed()) {
        std::cout << "  " << color::GRAY << "shadowed " << name << " at " << where << color::RESET << "\n";
    }
    return 0;
}

int listSessions() {
    gaia::SessionStore store;
    auto sessions = store.list();
    if (sessions.empty()) {
        std::cout << "No saved sessions.\n";
        return 0;
    }
    for (const auto& s : sessions) {
        std::cout << color::CYAN << s.id << color::RESET << "  (" << s.messageCount << " messages, "
                  << s.timestamp << ")\n    " << s.preview << "\n";
    }
    return 0;
}

std::string requireValue(int& i, int argc, char* argv[], const std::string& flag) {
    if (i + 1 >= argc) throw std::invalid_argument(flag + " requires a value");
    return argv[++i];
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc > 0 && argv[0] && *argv[0]) {
        const std::string stem = fs::u8path(argv[0]).stem().u8string();
        if (!stem.empty()) g_prog = stem;
    }
    try {
        std::string query, queryArg, resumeId, modelOverride, promptFile, traceFile;
        int maxSteps = 0;
        std::vector<std::string> skills, allow;
        int port = 8150;
        bool serve = false, mcp = false, jsonEvents = false, printMode = false, noTui = false,
             forceTui = false, debug = false, yes = false, noWeb = false, noMemory = false,
             listSkillsFlag = false, listSessionsFlag = false, stdio = false, acceptEdits = false,
             trace = false;
        bool fast = gaia::envFlag("GAIA_FAST").value_or(false);

        for (int i = 1; i < argc; ++i) {
            const std::string arg = argv[i];
            if (arg == "--help" || arg == "-h") {
                printUsage();
                return 0;
            } else if (arg == "--version") {
                std::cout << g_prog << " " << kVersion << "\n";
                return 0;
            } else if (arg == "--serve") serve = true;
            else if (arg == "--port") {
                const std::string v = requireValue(i, argc, argv, arg);
                std::size_t used = 0;
                try {
                    port = std::stoi(v, &used);
                } catch (const std::exception&) {
                    used = 0;
                }
                if (used != v.size() || port <= 0 || port > 65535) {
                    throw std::invalid_argument("--port needs a number from 1 to 65535, got '" + v + "'");
                }
            }
            else if (arg == "--mcp") mcp = true;
            else if (arg == "--json-events") jsonEvents = true;
            else if (arg == "--query") queryArg = requireValue(i, argc, argv, arg);
            else if (arg == "--print" || arg == "-p") printMode = true;
            else if (arg == "--prompt-file") promptFile = requireValue(i, argc, argv, arg);
            else if (arg == "--trace") trace = true;
            else if (arg == "--trace-file") traceFile = requireValue(i, argc, argv, arg);
            else if (arg == "--max-steps") {
                const std::string v = requireValue(i, argc, argv, arg);
                std::size_t used = 0;
                try {
                    maxSteps = std::stoi(v, &used);
                } catch (const std::exception&) {
                    used = 0;
                }
                if (used != v.size() || maxSteps <= 0) {
                    throw std::invalid_argument("--max-steps needs a positive number, got '" + v + "'");
                }
            }
            else if (arg == "--no-tui") noTui = true;
            else if (arg == "--tui") forceTui = true;
            else if (arg == "--debug" || arg == "--dev") debug = true;
            else if (arg == "--yes" || arg == "-y" || arg == "--full-access") yes = true;
            else if (arg == "--accept-edits") acceptEdits = true;
            else if (arg == "--stdio") stdio = true;
            else if (arg == "--use-claude" || arg == "--claude-model") {
                throw std::invalid_argument(
                    arg + " is not available in the native build, which runs local models through Lemonade. "
                          "Pick a local model, or use the Python GAIA agent for Claude.");
            }
            else if (arg == "--fast") fast = true;
            else if (arg == "--no-web") noWeb = true;
            else if (arg == "--no-memory") noMemory = true;
            else if (arg == "--model") modelOverride = requireValue(i, argc, argv, arg);
            else if (arg == "--skill") skills.push_back(requireValue(i, argc, argv, arg));
            else if (arg == "--allow") allow.push_back(requireValue(i, argc, argv, arg));
            else if (arg == "--resume") resumeId = requireValue(i, argc, argv, arg);
            else if (arg == "--list-skills") listSkillsFlag = true;
            else if (arg == "--list-sessions") listSessionsFlag = true;
            else if (!arg.empty() && arg[0] == '-') {
                std::cerr << color::RED << "Unknown option: " << arg << color::RESET << "\n\n";
                printUsage();
                return 2;
            } else {
                query += (query.empty() ? "" : " ") + arg;
            }
        }
        if (listSessionsFlag) return listSessions();
        if (!promptFile.empty()) {
            std::ifstream in(fs::u8path(promptFile), std::ios::binary);
            if (!in) throw std::invalid_argument("cannot read --prompt-file " + promptFile);
            std::ostringstream text;
            text << in.rdbuf();
            query = text.str();
            if (query.size() >= 3 && query.compare(0, 3, "\xEF\xBB\xBF") == 0) query.erase(0, 3);
        }
        yes = yes || gaia::envFlag("GAIA_AUTO_APPROVE_TOOLS").value_or(false);
        const bool stdinIsTerminal = GAIA_ISATTY(GAIA_FILENO(stdin)) != 0;
        if (!stdio && !serve && !mcp && !jsonEvents && !printMode && query.empty() && !listSkillsFlag &&
            !stdinIsTerminal) {
            stdio = true;  // what the Go TUI's flagship launch looks like
        }

        gaia::AgentConfig config = gaia::flagshipConfig();
        if (!modelOverride.empty()) {
            config.modelId = modelOverride;
            config.nativeToolCalls = gaia::flagshipNativeToolCalls(modelOverride);
        }
        config.debug = debug;
        if (maxSteps > 0) config.maxSteps = maxSteps;
        if (serve || mcp || stdio || printMode) config.silentMode = true;
        if (stdio) config.streaming = config.useNativeToolCalls();
        if (trace && traceFile.empty()) {
            // Folder + prompt file name maps a batch run's traces to tasks; the time keeps runs from overwriting.
            std::string stem = "trace";
            if (!promptFile.empty()) {
                const fs::path p = fs::absolute(fs::u8path(promptFile));
                stem = p.parent_path().filename().u8string() + "-" + p.stem().u8string();
            }
            traceFile = (fs::u8path(gaia::gaiaHomeDir()) / "native" / "traces" /
                         (stem + "-" + sessionId().substr(7) + ".json"))
                            .u8string();
        }
        config.traceFile = traceFile;
        if (jsonEvents) {
            config.streaming = false;
            config.structuredEvents = true;
        }

        gaia::FlagshipOptions options;
        options.fast = fast;
        options.web = !noWeb;
        options.workDir = fs::current_path().u8string();
        options.sessionId = sessionId();
        for (std::size_t i = 0; i < kGaiaEmbeddedSkillCount; ++i) options.embeddedSkills.push_back(kGaiaEmbeddedSkills[i]);
        if (!allow.empty()) {
            for (const auto& a : allow) options.allowedPaths.push_back(fs::absolute(fs::u8path(a)).u8string());
        } else if (auto env = gaia::envAllowedPaths()) {
            options.allowedPaths = *env;
        } else {
            options.allowedPaths = {gaia::userHomeDir()};
            if (options.workDir.rfind(gaia::userHomeDir(), 0) != 0) options.allowedPaths.push_back(options.workDir);
        }
        const fs::path native = fs::u8path(gaia::gaiaHomeDir()) / "native";
        if (!noMemory && !gaia::MemoryStore::disabledByEnv()) options.memoryDb = gaia::MemoryStore::defaultPath();
        options.scratchpadDb = (native / "scratchpad.db").u8string();
        fs::create_directories(native);

        // Questions from request_user_input reach a person only on a plain terminal session.
        if (GAIA_ISATTY(GAIA_FILENO(stdin)) && (noTui || printMode) && !stdio && !serve && !mcp && !jsonEvents) {
            options.askUser = [](const std::string& q, const std::vector<std::string>& choices) -> std::optional<std::string> {
                std::cerr << "\n" << q;
                for (std::size_t i = 0; i < choices.size(); ++i) std::cerr << "\n  " << (i + 1) << ". " << choices[i];
                std::cerr << "\n> " << std::flush;
                std::string line;
                if (!std::getline(std::cin, line)) return std::nullopt;
                if (!choices.empty() && !line.empty() && std::all_of(line.begin(), line.end(), ::isdigit)) {
                    const std::size_t n = static_cast<std::size_t>(std::stoul(line));
                    if (n >= 1 && n <= choices.size()) return choices[n - 1];
                }
                return line;
            };
        }
        gaia::FlagshipAgent agent(config, options);
        agent.loadStartupSkills(skills);
        if (listSkillsFlag) return listSkills(agent);

        if (stdio) {
            gaia::StdioServer server(agent, std::cin, std::cout, yes, acceptEdits, kVersion);
            return server.run();
        }

        // Without a person at a terminal to ask, a confirm-gated tool runs only under --yes.
        if (yes) {
            agent.setToolConfirmCallback([](const std::string&, const gaia::json&) {
                return gaia::ToolConfirmResult::ALLOW_ONCE;
            });
        } else if (serve || mcp || jsonEvents || !stdinIsTerminal) {
            agent.setToolConfirmCallback([](const std::string& tool, const gaia::json&) {
                std::cerr << "[gaia-agent] refused " << tool
                          << ": it needs approval and nobody is at a terminal to give it. Re-run with "
                             "--yes to approve tool calls.\n";
                return gaia::ToolConfirmResult::DENY;
            });
        }

        // stdout belongs to the protocol in these modes; answers travel inside it.
        if (serve || mcp) agent.setOutputHandler(std::make_unique<gaia::SilentConsole>(true));
        if (serve) {
            gaia::ApiServer server(agent, port);
            server.setSessionStore(std::make_shared<gaia::SessionStore>());
            std::cerr << g_prog << " API on http://127.0.0.1:" << port << " (model " << config.modelId << ")\n";
            server.run();
            return 0;
        }
        if (mcp) {
            gaia::McpServer server(agent, kVersion);
            server.run();
            return 0;
        }
        if (jsonEvents) {
            agent.setOutputHandler(std::make_unique<gaia::JsonEventOutputHandler>());
            const std::string once = queryArg.empty() ? query : queryArg;
            if (!once.empty()) {
                agent.processQuery(once);
                return 0;
            }
            for (std::string line; std::getline(std::cin, line);) {
                if (!line.empty()) agent.processQuery(line);
            }
            return 0;
        }

        if (printMode) {
            // The answer alone on stdout, printed once here for every way a turn can end.
            agent.setOutputHandler(std::make_unique<gaia::SilentConsole>(/*silenceFinalAnswer=*/true));
            auto answer = [&agent](const std::string& q) {
                const gaia::json r = agent.processQuery(q);
                if (r.contains("error")) {
                    std::cerr << g_prog << ": " << r.value("result", "") << "\n";
                    return 1;
                }
                std::cout << r.value("result", "") << std::endl;
                return 0;
            };
            if (!query.empty()) return answer(query);
            int rc = 0;
            for (std::string line; std::getline(std::cin, line);) {
                if (!line.empty()) rc |= answer(line);
            }
            return rc;
        }
        gaia::ReplRunner repl(agent);
        repl.setSessionStore(std::make_shared<gaia::SessionStore>());
        if (!resumeId.empty()) repl.setResumeId(resumeId);
        if (noTui || printMode) repl.setUseTui(false);
        else if (forceTui) repl.setUseTui(true);
        repl.addCommand("/skills", "List skills, or /skills <name> to load one",
                        [&repl, &agent](const std::string& args, gaia::Agent&) {
                            const gaia::json r = args.empty() ? agent.skills().listSkillsTool()
                                                              : agent.skills().loadSkillTool(args);
                            if (!args.empty()) agent.rebuildSystemPrompt();
                            repl.emit(r.dump(2));
                        });
        if (agent.memory()) {
            repl.addCommand("/memory", "Show what GAIA remembers (/memory <words> to search)",
                            [&repl, &agent](const std::string& args, gaia::Agent&) {
                                repl.emit(agent.memory()->recall({{"query", args}}).dump(2));
                            });
        }
        if (!query.empty()) return repl.runOnce(query);
        repl.run();
        return 0;
    } catch (const std::exception& e) {
        std::cerr << color::RED << color::BOLD << g_prog << ": " << color::RESET << color::RED << e.what()
                  << color::RESET << "\n";
        return 1;
    }
}
