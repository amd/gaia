// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaia-cpp — the native GAIA agent.
//
//   gaia-cpp                                  Interactive session
//   gaia-cpp -p "prompt" [--output-format json]   One headless run (prompt from stdin when omitted)
//   gaia-cpp serve [--host H] [--port N]      OpenAI-compatible server (for Lemonade / apps)

#include <atomic>
#include <chrono>
#include <cmath>
#include <csignal>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <optional>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include <gaia/api_server.h>
#include <gaia/clean_console.h>
#include <gaia/console.h>
#include <gaia/repl.h>
#include <gaia/session.h>

#include "gaia_agent.h"

#ifdef _WIN32
#include <windows.h>
#include <psapi.h>
#else
#include <sys/resource.h>
#endif

#ifndef GAIA_CPP_VERSION
#define GAIA_CPP_VERSION "0.0.0-dev"
#endif

namespace fs = std::filesystem;

namespace {

// This process's own peak resident memory in MB (children not included).
double selfPeakRssMb() {
#ifdef _WIN32
    PROCESS_MEMORY_COUNTERS pmc{};
    if (!GetProcessMemoryInfo(GetCurrentProcess(), &pmc, sizeof(pmc))) return -1;
    return static_cast<double>(pmc.PeakWorkingSetSize) / (1024.0 * 1024.0);
#else
    struct rusage ru {};
    if (getrusage(RUSAGE_SELF, &ru) != 0) return -1;
#ifdef __APPLE__
    return static_cast<double>(ru.ru_maxrss) / (1024.0 * 1024.0);  // bytes on macOS
#else
    return static_cast<double>(ru.ru_maxrss) / 1024.0;  // kilobytes on Linux
#endif
#endif
}

constexpr const char* kFireworksUrl = "https://api.fireworks.ai/inference/v1";

struct Cli {
    enum class Mode { Interactive, Print, Serve } mode = Mode::Interactive;
    std::optional<std::string> prompt;
    std::string promptFile;
    std::string outputFormat = "text";
    std::string model;
    std::string baseUrl;
    std::string provider;
    std::string apiKeyEnv;
    std::string nativeTools;
    std::string cwd;
    std::string mcpConfig;
    std::string embeddingModel;
    std::string configFile;
    std::string host = "127.0.0.1";
    std::string serveKeyEnv;
    std::vector<std::string> allowedOrigins;
    int port = 8200;
    int maxSteps = 0;
    int ctxSize = 0;
    int maxTokens = 0;
    std::optional<double> temperature;
    bool skipPermissions = false;
    bool noWeb = false;
    bool noSkills = false;
    bool noMemory = false;
    bool fullAccess = false;
    std::vector<std::string> allowDirs;
    bool debug = false;
    bool exposeToolRoutes = false;
};

void printUsage() {
    std::cout <<
        "gaia-cpp " GAIA_CPP_VERSION " — GAIA agent (native)\n\n"
        "Usage:\n"
        "  gaia-cpp [options]                    Interactive session\n"
        "  gaia-cpp -p [PROMPT] [options]        One headless run; PROMPT or stdin\n"
        "  gaia-cpp serve [options]              OpenAI-compatible server\n\n"
        "Model:\n"
        "  -m, --model ID            Model id (env GAIA_MODEL_ID)\n"
        "  --base-url URL            OpenAI-compatible endpoint (env GAIA_BASE_URL,\n"
        "                            LEMONADE_BASE_URL; default Lemonade on :13305)\n"
        "  --provider NAME           lemonade (default) or fireworks\n"
        "  --api-key-env VAR         Variable holding the endpoint key (default\n"
        "                            GAIA_API_KEY, then LEMONADE_API_KEY)\n"
        "  --native-tools MODE       auto | always | never (OpenAI tool calls)\n"
        "  --ctx-size N              Context window to load the model with\n"
        "  --max-tokens N            Max tokens per reply\n"
        "  --temperature T           Sampling temperature\n"
        "Run:\n"
        "  -p, --print [PROMPT]      Headless: answer on stdout, exit 0/1\n"
        "  --prompt-file FILE        Read the prompt from FILE\n"
        "  --output-format FMT       text (default) or json\n"
        "  --cwd DIR                 Work in DIR\n"
        "  --max-steps N             Step budget (default 50)\n"
        "  --mcp-config FILE         Connect the MCP servers in FILE (mcp.json)\n"
        "  --embedding-model ID      Embedding model for document search (default with\n"
        "                            --provider fireworks; otherwise keyword search)\n"
        "  --config FILE             Settings file (default $GAIA_CPP_CONFIG, else\n"
        "                            ~/.config/gaia-cpp/config.json when present)\n"
        "  --no-web                  Leave out web_fetch\n"
        "  --no-skills               Leave out installed SKILL.md skills\n"
        "  --no-memory               Don't remember or recall facts across sessions\n"
        "  --allow-dir DIR           Also let the tools use DIR (repeatable). By default\n"
        "                            they are confined to the working directory\n"
        "  --full-access             Let the tools reach any path the user can\n"
        "  --dangerously-skip-permissions\n"
        "                            Run gated tools (shell, writes, web) without asking.\n"
        "                            Without it, headless runs deny them.\n"
        "Serve:\n"
        "  --host H                  Bind address (default 127.0.0.1)\n"
        "  --port N                  Port (default 8200; 0 = any free port)\n"
        "  --server-key-env VAR      Require 'Authorization: Bearer $VAR' from clients\n"
        "  --allow-origin ORIGIN     Allow a browser origin (repeatable)\n"
        "  --expose-tool-routes      Enable POST /v1/tools/:name (direct tool runs)\n"
        "Other:\n"
        "  --debug, -v/--version, -h/--help\n\n"
        "Environment: GAIA_BASE_URL, GAIA_MODEL_ID, GAIA_API_KEY, LEMONADE_BASE_URL,\n"
        "LEMONADE_API_KEY, FIREWORKS_API_KEY (with --provider fireworks).\n";
}

[[noreturn]] void usageError(const std::string& msg) {
    std::cerr << "gaia-cpp: " << msg << "\nRun 'gaia-cpp --help' for usage.\n";
    std::exit(2);
}

int toInt(const std::string& flag, const std::string& v) {
    try {
        size_t used = 0;
        const int n = std::stoi(v, &used);
        if (used != v.size()) throw std::invalid_argument(v);
        return n;
    } catch (const std::exception&) {
        usageError(flag + " needs a whole number, got '" + v + "'");
    }
}

Cli parseArgs(int argc, char** argv) {
    Cli cli;
    auto need = [&](int& i, const std::string& flag) -> std::string {
        if (i + 1 >= argc) usageError(flag + " needs a value");
        return argv[++i];
    };
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "-h" || a == "--help") { printUsage(); std::exit(0); }
        else if (a == "-v" || a == "--version") { std::cout << "gaia-cpp " GAIA_CPP_VERSION "\n"; std::exit(0); }
        else if (a == "serve" && i == 1) cli.mode = Cli::Mode::Serve;
        else if (a == "-p" || a == "--print") {
            cli.mode = Cli::Mode::Print;
            if (i + 1 < argc && argv[i + 1][0] != '-') cli.prompt = argv[++i];
        }
        else if (a == "--prompt-file") { cli.mode = Cli::Mode::Print; cli.promptFile = need(i, a); }
        else if (a == "--output-format") cli.outputFormat = need(i, a);
        else if (a == "-m" || a == "--model") cli.model = need(i, a);
        else if (a == "--base-url") cli.baseUrl = need(i, a);
        else if (a == "--provider") cli.provider = need(i, a);
        else if (a == "--api-key-env") cli.apiKeyEnv = need(i, a);
        else if (a == "--native-tools") cli.nativeTools = need(i, a);
        else if (a == "--ctx-size") cli.ctxSize = toInt(a, need(i, a));
        else if (a == "--max-tokens") cli.maxTokens = toInt(a, need(i, a));
        else if (a == "--temperature") {
            const std::string v = need(i, a);
            try { cli.temperature = std::stod(v); } catch (...) { usageError("--temperature needs a number"); }
        }
        else if (a == "--max-steps") cli.maxSteps = toInt(a, need(i, a));
        else if (a == "--cwd") cli.cwd = need(i, a);
        else if (a == "--mcp-config") cli.mcpConfig = need(i, a);
        else if (a == "--embedding-model") cli.embeddingModel = need(i, a);
        else if (a == "--config") cli.configFile = need(i, a);
        else if (a == "--no-web") cli.noWeb = true;
        else if (a == "--no-skills") cli.noSkills = true;
        else if (a == "--no-memory") cli.noMemory = true;
        else if (a == "--allow-dir") cli.allowDirs.push_back(need(i, a));
        else if (a == "--full-access") cli.fullAccess = true;
        else if (a == "--dangerously-skip-permissions") cli.skipPermissions = true;
        else if (a == "--host") cli.host = need(i, a);
        else if (a == "--port") cli.port = toInt(a, need(i, a));
        else if (a == "--server-key-env") cli.serveKeyEnv = need(i, a);
        else if (a == "--allow-origin") cli.allowedOrigins.push_back(need(i, a));
        else if (a == "--expose-tool-routes") cli.exposeToolRoutes = true;
        else if (a == "--debug") cli.debug = true;
        else usageError("unknown argument '" + a + "'");
    }
    if (cli.outputFormat != "text" && cli.outputFormat != "json") {
        usageError("--output-format must be text or json");
    }
    return cli;
}

fs::path defaultConfigPath() {
    const std::string env = gaia::getEnvVar("GAIA_CPP_CONFIG");
    if (!env.empty()) return env;
#ifdef _WIN32
    const std::string base = gaia::getEnvVar("APPDATA");
    return base.empty() ? fs::path() : fs::path(base) / "gaia-cpp" / "config.json";
#else
    std::string base = gaia::getEnvVar("XDG_CONFIG_HOME");
    if (base.empty()) {
        const std::string home = gaia::getEnvVar("HOME");
        if (home.empty()) return {};
        base = home + "/.config";
    }
    return fs::path(base) / "gaia-cpp" / "config.json";
#endif
}

void applyProvider(gaia::json& merged, const std::string& provider, const std::string& where) {
    if (provider == "fireworks") {
        merged["baseUrl"] = kFireworksUrl;
        merged["apiKeyEnv"] = "FIREWORKS_API_KEY";
        merged["nativeToolCalls"] = "always";
        merged["contextSize"] = 131072;
        merged["embeddingModel"] = "nomic-ai/nomic-embed-text-v1.5";
    } else if (provider != "lemonade") {
        throw std::runtime_error(where + ": provider must be lemonade or fireworks, got '" +
                                 provider + "'");
    }
}

// Settings precedence: built-in defaults < settings file < environment < flags.
struct Settings {
    gaia::AgentConfig cfg;
    std::string mcpConfig;
    std::string embeddingModel;  // empty = keyword-only document retrieval
};

Settings buildSettings(const Cli& cli) {
    gaia::json merged = {{"contextSize", 32768},
                         {"maxTokens", 8192},
                         {"temperature", 0.2},
                         {"modelId", "Gemma-4-E4B-it-GGUF"},
                         {"baseUrl", gaia::defaultBaseUrl()},
                         {"responseMode", "conversational"}};
    std::string mcpConfig;

    const fs::path file = cli.configFile.empty() ? defaultConfigPath() : fs::path(cli.configFile);
    std::error_code ec;
    if (!file.empty() && (!cli.configFile.empty() || fs::exists(file, ec))) {
        std::ifstream in(file);
        if (!in) throw std::runtime_error("Cannot read settings file " + file.string());
        gaia::json j;
        try {
            in >> j;
        } catch (const std::exception& e) {
            throw std::runtime_error("Settings file " + file.string() +
                                     " is not valid JSON: " + e.what());
        }
        if (!j.is_object()) {
            throw std::runtime_error("Settings file " + file.string() + " must hold a JSON object");
        }
        if (j.contains("mcpConfig")) {
            fs::path m = j["mcpConfig"].get<std::string>();
            if (m.is_relative()) m = file.parent_path() / m;
            mcpConfig = m.string();
            j.erase("mcpConfig");
        }
        if (j.contains("provider")) {
            applyProvider(merged, j["provider"].get<std::string>(), "Settings file " + file.string());
            j.erase("provider");
        }
        merged.update(j);
    }

    const std::string envModel = gaia::getEnvVar("GAIA_MODEL_ID");
    if (!envModel.empty()) merged["modelId"] = envModel;
    const std::string envUrl = gaia::getEnvVar("GAIA_BASE_URL");
    if (!envUrl.empty()) merged["baseUrl"] = envUrl;
    for (const auto& [var, field] : {std::pair<const char*, const char*>{"GAIA_MAX_STEPS", "maxSteps"},
                                     {"GAIA_CONTEXT_SIZE", "contextSize"},
                                     {"GAIA_MAX_TOKENS", "maxTokens"}}) {
        const std::string v = gaia::getEnvVar(var);
        if (!v.empty()) merged[field] = toInt(var, v);
    }

    if (!cli.provider.empty()) applyProvider(merged, cli.provider, "--provider");
    if (!cli.baseUrl.empty()) merged["baseUrl"] = cli.baseUrl;
    if (!cli.model.empty()) merged["modelId"] = cli.model;
    if (!cli.apiKeyEnv.empty()) merged["apiKeyEnv"] = cli.apiKeyEnv;
    if (!cli.nativeTools.empty()) merged["nativeToolCalls"] = cli.nativeTools;
    if (cli.ctxSize > 0) merged["contextSize"] = cli.ctxSize;
    if (cli.maxTokens > 0) merged["maxTokens"] = cli.maxTokens;
    if (cli.maxSteps > 0) merged["maxSteps"] = cli.maxSteps;
    if (cli.temperature) merged["temperature"] = *cli.temperature;
    if (!cli.mcpConfig.empty()) mcpConfig = cli.mcpConfig;
    if (!cli.embeddingModel.empty()) merged["embeddingModel"] = cli.embeddingModel;

    Settings s;
    if (merged.contains("embeddingModel")) {
        s.embeddingModel = merged["embeddingModel"].get<std::string>();
        merged.erase("embeddingModel");
    }
    s.cfg = gaia::AgentConfig::fromJson(merged);
    s.cfg.debug = cli.debug;
    s.mcpConfig = mcpConfig;
    return s;
}

// The Agent constructor lets GAIA_* variables override its config; re-apply the
// resolved settings so a flag always beats the environment.
void pinConfig(gaia::Agent& agent, const gaia::AgentConfig& cfg) {
    agent.setConfig(cfg);
}

void connectMcp(gaia::Agent& agent, const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("Cannot open MCP config " + path);
    gaia::json doc;
    try {
        in >> doc;
    } catch (const std::exception& e) {
        throw std::runtime_error("MCP config " + path + " is not valid JSON: " + e.what());
    }
    const char* key = doc.contains("mcpServers") ? "mcpServers" : "servers";
    if (!doc.contains(key) || !doc[key].is_object()) {
        throw std::runtime_error("MCP config " + path +
                                 " has no \"mcpServers\" object naming the servers to connect");
    }
    for (auto& [name, server] : doc[key].items()) {
        if (server.value("disabled", false)) continue;
        if (!agent.connectMcpServer(name, server)) {
            throw std::runtime_error("Could not connect MCP server '" + name + "' from " + path +
                                     " (details above). Fix or disable it, then retry.");
        }
    }
}

std::string readAll(std::istream& in) {
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

// The working directory first (relative paths resolve there), then --allow-dir.
gaia::GaiaAgent::Options agentOptions(const Cli& cli, const Settings& settings) {
    gaia::GaiaAgent::Options o;
    o.embeddingModel = settings.embeddingModel;
    o.webTools = !cli.noWeb;
    o.skills = !cli.noSkills;
    o.memory = !cli.noMemory;
    o.fullAccess = cli.fullAccess;
    std::error_code ec;
    o.workspaceRoots.push_back(fs::current_path(ec).string());
    for (const auto& d : cli.allowDirs) {
        if (!fs::is_directory(d, ec)) usageError("--allow-dir " + d + " is not a folder");
        o.workspaceRoots.push_back(fs::absolute(d).string());
    }
    return o;
}

std::atomic<gaia::Agent*> g_agent{nullptr};
std::atomic<gaia::ApiServer*> g_server{nullptr};

std::atomic<int> g_signals{0};

// First signal: stop gracefully. A second one exits at once, since a blocking
// model call or tool can't see the cancel flag until it returns.
extern "C" void onSignal(int) {
    if (g_signals.fetch_add(1) > 0) std::_Exit(130);
    if (auto* s = g_server.load()) s->stop();
    if (auto* a = g_agent.load()) a->requestCancel();
}

int runPrint(const Cli& cli) {
    std::string prompt;
    if (cli.prompt) prompt = *cli.prompt;
    else if (!cli.promptFile.empty()) {
        std::ifstream in(cli.promptFile, std::ios::binary);
        if (!in) throw std::runtime_error("Cannot read prompt file " + cli.promptFile);
        prompt = readAll(in);
    } else {
        prompt = readAll(std::cin);
    }
    if (prompt.find_first_not_of(" \t\r\n") == std::string::npos) {
        usageError("-p needs a prompt: pass it as an argument, with --prompt-file, or on stdin");
    }

    const Settings settings = buildSettings(cli);
    gaia::AgentConfig cfg = settings.cfg;
    cfg.silentMode = true;  // stdout carries only the answer
    gaia::GaiaAgent agent(cfg, agentOptions(cli, settings));
    pinConfig(agent, cfg);
    agent.setOutputHandler(std::make_unique<gaia::CleanConsole>());
    agent.setToolConfirmCallback([skip = cli.skipPermissions](const std::string& tool, const gaia::json&) {
        if (!skip) {
            std::cerr << "[gaia-cpp] denied " << tool
                      << " (headless run; pass --dangerously-skip-permissions to allow)\n";
        }
        return skip ? gaia::ToolConfirmResult::ALLOW_ONCE : gaia::ToolConfirmResult::DENY;
    });
    if (!settings.mcpConfig.empty()) {
        std::streambuf* saved = std::cout.rdbuf(std::cerr.rdbuf());
        try { connectMcp(agent, settings.mcpConfig); } catch (...) { std::cout.rdbuf(saved); throw; }
        std::cout.rdbuf(saved);
    }

    g_agent = &agent;
    const auto t0 = std::chrono::steady_clock::now();
    // Progress goes to stderr; stdout carries only the answer.
    std::streambuf* stdoutBuf = std::cout.rdbuf(std::cerr.rdbuf());
    gaia::json result;
    try {
        result = agent.processQuery(prompt);
    } catch (...) {
        std::cout.rdbuf(stdoutBuf);
        g_agent = nullptr;
        throw;
    }
    std::cout.rdbuf(stdoutBuf);
    const double secs =
        std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();
    g_agent = nullptr;

    const std::string status = result.value("status", "success");
    if (cli.outputFormat == "json") {
        gaia::json out = {{"result", result.value("result", "")},
                          {"status", status},
                          {"steps", result.value("steps_taken", 0)},
                          {"tool_errors", result.value("tool_errors", 0)},
                          {"model", cfg.modelId},
                          {"duration_s", std::round(secs * 10) / 10},
                          {"peak_rss_mb", std::round(selfPeakRssMb() * 10) / 10}};
        if (result.contains("usage")) out["usage"] = result["usage"];
        std::cout << out.dump(-1, ' ', false, gaia::json::error_handler_t::replace) << std::endl;
    } else {
        std::cout << result.value("result", "") << std::endl;
    }
    return status == "success" ? 0 : 1;
}

int runServe(const Cli& cli) {
    const Settings settings = buildSettings(cli);
    gaia::AgentConfig cfg = settings.cfg;
    cfg.silentMode = true;
    gaia::GaiaAgent agent(cfg, agentOptions(cli, settings));
    pinConfig(agent, cfg);
    agent.setOutputHandler(std::make_unique<gaia::CleanConsole>());
    // No person at the keyboard: the client that started the server owns safety.
    agent.setToolConfirmCallback([](const std::string&, const gaia::json&) {
        return gaia::ToolConfirmResult::ALLOW_ONCE;
    });
    if (!settings.mcpConfig.empty()) connectMcp(agent, settings.mcpConfig);

    gaia::ServerOptions opts;
    opts.host = cli.host;
    opts.port = cli.port;
    opts.allowedOrigins = cli.allowedOrigins;
    opts.exposeToolRoutes = cli.exposeToolRoutes;
    opts.agentVersion = GAIA_CPP_VERSION;
    if (!cli.serveKeyEnv.empty()) {
        opts.apiKey = gaia::getEnvVar(cli.serveKeyEnv.c_str());
        if (opts.apiKey.empty()) {
            usageError("--server-key-env names " + cli.serveKeyEnv + ", which is not set");
        }
    }
    // No session store: each request carries its conversation, and the REPL's
    // saved chats must not be readable or deletable over HTTP.
    gaia::ApiServer server(agent, opts);
    g_server = &server;
    g_agent = &agent;
    // One JSON line on stdout once bound, so a supervisor learns the real port.
    std::thread announce([&server, &cli, &cfg] {
        for (int i = 0; i < 500 && !server.isRunning(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        if (!server.isRunning()) return;
        const std::string url = "http://" + cli.host + ":" + std::to_string(server.port());
        std::cout << gaia::json{{"event", "ready"}, {"url", url}, {"model", cfg.modelId},
                                {"version", GAIA_CPP_VERSION}}.dump()
                  << std::endl;
        std::cerr << "gaia-cpp " GAIA_CPP_VERSION " serving " << cfg.modelId << " on " << url
                  << " (Ctrl+C to stop)\n";
    });
    try {
        server.run();
    } catch (...) {
        announce.join();
        throw;
    }
    announce.join();
    g_server = nullptr;
    g_agent = nullptr;
    return 0;
}

int runInteractive(const Cli& cli) {
    const Settings settings = buildSettings(cli);
    gaia::GaiaAgent agent(settings.cfg, agentOptions(cli, settings));
    pinConfig(agent, settings.cfg);
    if (cli.skipPermissions) {
        agent.setToolConfirmCallback([](const std::string&, const gaia::json&) {
            return gaia::ToolConfirmResult::ALLOW_ONCE;
        });
    }
    if (!settings.mcpConfig.empty()) connectMcp(agent, settings.mcpConfig);
    gaia::ReplRunner repl(agent);
    repl.setSessionStore(std::make_shared<gaia::SessionStore>());
    repl.run();
    return 0;
}

} // namespace

int main(int argc, char** argv) {
    try {
        const Cli cli = parseArgs(argc, argv);
        if (!cli.cwd.empty()) {
            std::error_code ec;
            fs::current_path(cli.cwd, ec);
            if (ec) usageError("--cwd " + cli.cwd + ": " + ec.message());
        }
        std::signal(SIGINT, onSignal);
        std::signal(SIGTERM, onSignal);
        switch (cli.mode) {
            case Cli::Mode::Print: return runPrint(cli);
            case Cli::Mode::Serve: return runServe(cli);
            case Cli::Mode::Interactive: return runInteractive(cli);
        }
    } catch (const std::exception& e) {
        std::cerr << "gaia-cpp: " << e.what() << "\n";
        return 1;
    }
    return 1;
}
