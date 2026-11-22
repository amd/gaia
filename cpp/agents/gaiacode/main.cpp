// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaiacode: GAIA's native coding agent.
//
//   gaiacode -p "task" [--output-format json]   One headless run (task from stdin when omitted)
//   gaiacode --stdio                            The GAIA terminal UI's agent child (JSON Lines)
//   gaiacode --host H --port P                  The GAIA daemon's "gaia" sidecar, for the Agent UI
//
// No model is proven for coding yet, so a headless or stdio run must name one.

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
#include <set>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

#include <gaia/clean_console.h>
#include <gaia/types.h>

#include "../gaiacpp/bash_tools.h"
#include "../gaiacpp/sidecar.h"
#include "../gaiacpp/stdio_server.h"
#include "code_agent.h"

#ifndef GAIACODE_VERSION
#define GAIACODE_VERSION "0.0.0-dev"
#endif

namespace fs = std::filesystem;

namespace {

struct Cli {
    std::optional<std::string> prompt;
    bool print = false;
    bool stdio = false;
    bool acceptEdits = false;
    bool dev = false;
    std::string host = "127.0.0.1";
    int port = 0;
    std::string promptFile;
    std::string model;
    std::string baseUrl;
    std::string apiKeyEnv;
    std::string nativeTools;
    std::string repo;
    std::set<std::string> githubWrites;
    bool noGithub = false;
    bool noSkills = false;
    std::string cwd;
    std::vector<std::string> allowDirs;
    std::string outputFormat = "text";
    bool skipPermissions = false;
    bool fullAccess = false;
    int maxSteps = 0;
};

const char* kUsage =
    "gaiacode " GAIACODE_VERSION " — GAIA's native coding agent (in development)\n"
    "\n"
    "Usage:\n"
    "  gaiacode -p [TASK] --model ID [options]   One headless run; TASK from stdin when omitted\n"
    "  gaiacode --stdio --model ID [options]     Agent child of the GAIA terminal UI\n"
    "  gaiacode --host H --port P                The GAIA daemon's sidecar for the Agent UI\n"
    "                                            (started by the daemon, which passes its token)\n"
    "\n"
    "Options:\n"
    "  -p, --print [TASK]           Run TASK headless and print the answer\n"
    "  --prompt-file FILE           Read the task from FILE\n"
    "  -m, --model ID               Model to run (required: none is proven for coding yet)\n"
    "  --base-url URL               OpenAI-compatible endpoint (default: Lemonade)\n"
    "  --api-key-env NAME           Environment variable holding the endpoint's API key\n"
    "  --native-tools MODE          auto (default) | always | never (OpenAI tool calls)\n"
    "  --cwd DIR                    Work in DIR (default: the current folder)\n"
    "  --allow-dir DIR              Let the tools also reach DIR (repeatable)\n"
    "  --full-access                Let the tools reach any path; with --stdio, approval prompts off\n"
    "  --accept-edits               With --stdio: file edits in the folder run without asking\n"
    "  --dev                        With --stdio: step-by-step status lines\n"
    "  --output-format FMT          text (default) or json\n"
    "  --dangerously-skip-permissions  Run tools that would ask (shell, writes, run_checks)\n"
    "  --max-steps N                Step limit for the run\n"
    "  --repo [HOST/]OWNER/NAME     GitHub repository to work on (default: the upstream, else origin, remote)\n"
    "  --allow-github-writes KINDS  GitHub writes this run may make: comment, pr (comma-separated)\n"
    "  --no-github                  No GitHub tools\n"
    "  --no-skills                  Load no skills\n"
    "  --version, -h/--help\n";

[[noreturn]] void usageError(const std::string& why) {
    std::cerr << "gaiacode: " << why << "\n\n" << kUsage;
    std::exit(2);
}

std::string need(int& i, int argc, char** argv, const std::string& flag) {
    if (i + 1 >= argc) usageError(flag + " needs a value");
    return argv[++i];
}

// The daemon starts its "gaia" sidecar as `<binary> --host H --port P` with its token in the environment.
bool sidecarLaunch() {
    return !gaia::getEnvVar("GAIA_GAIA_SIDECAR_TOKEN_FILE").empty() ||
           !gaia::getEnvVar("GAIA_GAIA_SIDECAR_TOKEN").empty();
}

Cli parse(int argc, char** argv) {
    Cli cli;
    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "-h" || a == "--help") {
            std::cout << kUsage;
            std::exit(0);
        } else if (a == "--version") {
            std::cout << "gaiacode " << GAIACODE_VERSION << "\n";
            std::exit(0);
        } else if (a == "-p" || a == "--print") {
            cli.print = true;
            if (i + 1 < argc && argv[i + 1][0] != '-') cli.prompt = argv[++i];
        } else if (a == "--stdio") {
            cli.stdio = true;
        } else if (a == "--accept-edits") {
            cli.acceptEdits = true;
        } else if (a == "--dev") {
            cli.dev = true;
        } else if (a == "--host") {
            cli.host = need(i, argc, argv, a);
        } else if (a == "--port") {
            const std::string v = need(i, argc, argv, a);
            try {
                cli.port = std::stoi(v);
            } catch (const std::exception&) {
                usageError("--port must be a whole number, got " + v);
            }
            if (cli.port < 0 || cli.port > 65535) usageError("--port must be between 0 (any free port) and 65535");
        } else if (a == "--prompt-file") {
            cli.print = true;
            cli.promptFile = need(i, argc, argv, a);
        } else if (a == "-m" || a == "--model") {
            cli.model = need(i, argc, argv, a);
        } else if (a == "--base-url") {
            cli.baseUrl = need(i, argc, argv, a);
        } else if (a == "--api-key-env") {
            cli.apiKeyEnv = need(i, argc, argv, a);
        } else if (a == "--repo") {
            cli.repo = need(i, argc, argv, a);
        } else if (a == "--allow-github-writes") {
            std::istringstream kinds(need(i, argc, argv, a));
            for (std::string kind; std::getline(kinds, kind, ',');) {
                if (kind != "comment" && kind != "pr") {
                    usageError("--allow-github-writes takes comment and pr, got " + kind);
                }
                cli.githubWrites.insert(kind);
            }
        } else if (a == "--no-github") {
            cli.noGithub = true;
        } else if (a == "--no-skills") {
            cli.noSkills = true;
        } else if (a == "--native-tools") {
            cli.nativeTools = need(i, argc, argv, a);
        } else if (a == "--cwd") {
            cli.cwd = need(i, argc, argv, a);
        } else if (a == "--allow-dir") {
            cli.allowDirs.push_back(need(i, argc, argv, a));
        } else if (a == "--full-access") {
            cli.fullAccess = true;
        } else if (a == "--output-format") {
            cli.outputFormat = need(i, argc, argv, a);
        } else if (a == "--dangerously-skip-permissions") {
            cli.skipPermissions = true;
        } else if (a == "--max-steps") {
            const std::string v = need(i, argc, argv, a);
            try {
                cli.maxSteps = std::stoi(v);
            } catch (const std::exception&) {
                usageError("--max-steps must be a whole number, got " + v);
            }
            if (cli.maxSteps < 1) usageError("--max-steps must be at least 1");
        } else {
            usageError("unknown option " + a);
        }
    }
    if (cli.outputFormat != "text" && cli.outputFormat != "json") usageError("--output-format must be text or json");
    if (cli.print && cli.stdio) usageError("-p and --stdio are two different modes; pass one");
    if (!cli.stdio && (cli.acceptEdits || cli.dev)) usageError("--accept-edits and --dev go with --stdio");
    if (cli.model.empty()) cli.model = gaia::getEnvVar("GAIA_MODEL_ID");
    if (!cli.print && !cli.stdio) {
        if (!sidecarLaunch()) {
            usageError("gaiacode has no interactive mode of its own: pass -p for one run, or use it from GAIA's "
                       "terminal UI or Agent UI");
        }
        return cli;  // the chat names its model
    }
    if (cli.model.empty()) {
        usageError("name the model with --model (or GAIA_MODEL_ID): no model is proven for gaiacode yet, so "
                   "there is no default");
    }
    return cli;
}

std::string readAll(std::istream& in) {
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

std::atomic<gaia::Agent*> g_agent{nullptr};
std::atomic<int> g_signals{0};

// First signal stops gracefully; a second exits at once.
extern "C" void onSignal(int) {
    if (g_signals.fetch_add(1) > 0) std::_Exit(130);
    if (auto* a = g_agent.load()) a->requestCancel();
}

// What every app-driven chat gets: approval prompts guard the shell, edits and GitHub writes.
gaia::code::CodeAgent::Options appOptions(const Cli& cli, std::vector<std::string> roots,
                                          std::shared_ptr<gaia::ShellSession> shell) {
    gaia::code::CodeAgent::Options options;
    options.workspaceRoots = std::move(roots);
    options.shell = std::move(shell);
    options.github = !cli.noGithub;
    options.repo = cli.repo;
    // The app's approval prompt guards each write; --allow-github-writes narrows which kinds exist.
    options.githubWrites = cli.githubWrites.empty() ? std::set<std::string>{"comment", "pr"} : cli.githubWrites;
    options.skills = !cli.noSkills;
    return options;
}

int runSidecar(const Cli& cli) {
    gaia::SidecarOptions so;
    so.host = cli.host;
    so.port = cli.port;
    so.defaultModel = cli.model;
    so.program = "gaiacode";
    so.version = GAIACODE_VERSION;
    so.makeAgent = [cli](const gaia::AgentConfig& cfg, const gaia::SidecarAgentSpec& spec) {
        gaia::code::CodeAgent::Options options = appOptions(cli, spec.workspaceRoots, spec.shell);
        options.internetOff = spec.internetOff;
        return std::unique_ptr<gaia::Agent>(std::make_unique<gaia::code::CodeAgent>(cfg, options));
    };
    return gaia::runSidecar(so);
}

int runStdio(const Cli& cli) {
#ifndef _WIN32
    // A host that closed stdout shows up as a failed write, not as a signal that kills.
    std::signal(SIGPIPE, SIG_IGN);
#endif
    // stdout is the wire; anything else printed goes to stderr.
    std::ostream wire(std::cout.rdbuf());
    std::cout.rdbuf(std::cerr.rdbuf());
    try {
        std::error_code ec;
        if (!cli.cwd.empty()) {
            fs::current_path(fs::u8path(cli.cwd), ec);
            if (ec) throw std::runtime_error("Cannot work in --cwd " + cli.cwd + ": " + ec.message());
        }
        gaia::AgentConfig cfg;
        if (!cli.baseUrl.empty()) cfg.baseUrl = cli.baseUrl;
        if (!cli.apiKeyEnv.empty()) cfg.apiKeyEnv = cli.apiKeyEnv;
        if (!cli.nativeTools.empty()) cfg.nativeToolCalls = gaia::nativeToolCallsFromString(cli.nativeTools);
        cfg.maxSteps = cli.maxSteps > 0 ? cli.maxSteps : 200;
        cfg.silentMode = true;
        cfg.debug = cli.dev;
        const gaia::NativeToolCalls launchMode = cfg.nativeToolCalls;
        gaia::applyStdioModel(cfg, cli.model, launchMode);

        const std::string folder = gaia::workingFolderUtf8();
        std::vector<std::string> roots = {folder};
        for (const auto& d : cli.allowDirs) {
            if (!fs::is_directory(fs::u8path(d), ec)) usageError("--allow-dir " + d + " is not a folder");
            roots.push_back(fs::absolute(fs::u8path(d)).u8string());
        }
        // Here --full-access turns approval prompts off; it does not widen the folders.
        gaia::code::CodeAgent agent(cfg, appOptions(cli, roots, gaia::BashTools::newSession(folder)));
        agent.setConfig(cfg);  // flags beat the GAIA_* variables the constructor reads

        gaia::StdioOptions so;
        so.fullAccess = cli.fullAccess || cli.skipPermissions;
        so.acceptEdits = cli.acceptEdits;
        so.dev = cli.dev;
        so.program = "gaiacode";
        so.agentVersion = GAIACODE_VERSION;
        so.editRoots = {folder};
        so.memory = false;  // gaiacode keeps no memory
        so.nativeTools = launchMode;
        so.hostAlive = gaia::stdoutHasReader;
        so.exitWhenHostGone = true;
        gaia::StdioServer server(agent, std::cin, wire, so);
        g_agent = &agent;
        const int code = server.run();
        g_agent = nullptr;
        // The reader may still be blocked on stdin, which a normal exit would wait for.
        if (code != 0 || server.hostGone()) {
            std::cerr.flush();
            std::_Exit(code);
        }
        return code;
    } catch (const std::exception& e) {
        // The UI reads the failure off the wire; main() prints it on stderr.
        wire << gaia::json{{"type", "error"}, {"detail", std::string("gaiacode could not start: ") + e.what()}}
                    .dump(-1, ' ', false, gaia::json::error_handler_t::replace)
             << std::endl;
        throw;
    }
}

int run(const Cli& cli) {
    std::string prompt;
    if (cli.prompt) {
        prompt = *cli.prompt;
    } else if (!cli.promptFile.empty()) {
        std::ifstream in(cli.promptFile, std::ios::binary);
        if (!in) throw std::runtime_error("Cannot read prompt file " + cli.promptFile);
        prompt = readAll(in);
    } else {
        prompt = readAll(std::cin);
    }
    if (prompt.find_first_not_of(" \t\r\n") == std::string::npos) {
        usageError("-p needs a task: pass it as an argument, with --prompt-file, or on stdin");
    }

    std::error_code ec;
    if (!cli.cwd.empty()) {
        fs::current_path(fs::u8path(cli.cwd), ec);
        if (ec) throw std::runtime_error("Cannot work in --cwd " + cli.cwd + ": " + ec.message());
    }
    gaia::code::CodeAgent::Options options;
    options.fullAccess = cli.fullAccess;
    options.workspaceRoots.push_back(fs::current_path(ec).u8string());
    for (const auto& d : cli.allowDirs) {
        if (!fs::is_directory(d, ec)) usageError("--allow-dir " + d + " is not a folder");
        options.workspaceRoots.push_back(fs::absolute(d).u8string());
    }
    options.shell = gaia::BashTools::newSession(fs::current_path(ec).u8string());
    options.github = !cli.noGithub;
    options.repo = cli.repo;
    options.githubWrites = cli.githubWrites;
    options.skills = !cli.noSkills;

    gaia::AgentConfig cfg;
    cfg.modelId = cli.model;
    if (!cli.baseUrl.empty()) cfg.baseUrl = cli.baseUrl;
    if (!cli.apiKeyEnv.empty()) cfg.apiKeyEnv = cli.apiKeyEnv;
    if (!cli.nativeTools.empty()) {
        try {
            cfg.nativeToolCalls = gaia::nativeToolCallsFromString(cli.nativeTools);
        } catch (const std::invalid_argument&) {
            usageError("--native-tools must be auto, always or never, got " + cli.nativeTools);
        }
    }
    cfg.maxSteps = cli.maxSteps > 0 ? cli.maxSteps : 200;
    cfg.silentMode = true;  // stdout carries only the answer

    gaia::code::CodeAgent agent(cfg, options);
    agent.setConfig(cfg);  // flags beat the GAIA_* variables the constructor reads
    agent.setOutputHandler(std::make_unique<gaia::CleanConsole>());
    agent.setToolConfirmCallback([skip = cli.skipPermissions](const std::string& tool, const gaia::json&) {
        if (!skip) {
            std::cerr << "[gaiacode] denied " << tool
                      << " (headless run; pass --dangerously-skip-permissions to allow)\n";
        }
        return skip ? gaia::ToolConfirmResult::ALLOW_ONCE : gaia::ToolConfirmResult::DENY;
    });

    g_agent = &agent;
    const auto t0 = std::chrono::steady_clock::now();
    std::streambuf* stdoutBuf = std::cout.rdbuf(std::cerr.rdbuf());  // progress to stderr
    gaia::json result;
    try {
        result = agent.processQuery(prompt);
    } catch (...) {
        std::cout.rdbuf(stdoutBuf);
        g_agent = nullptr;
        throw;
    }
    std::cout.rdbuf(stdoutBuf);
    g_agent = nullptr;
    const double secs = std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count();

    const std::string status = result.value("status", "success");
    if (cli.outputFormat == "json") {
        const gaia::json usage = result.value("usage", gaia::json::object());
        // The field names of gaiacpp's and gaia's -p JSON, so one reader takes all three.
        gaia::json out = {{"status", status},
                          {"is_error", status != "success"},
                          {"result", result.value("result", "")},
                          {"model", cfg.modelId},
                          {"duration_s", std::round(secs * 10) / 10},
                          {"num_steps", result.value("steps_taken", 0)},
                          {"num_llm_calls", result.value("llm_calls", 0)},
                          {"num_tool_calls", result.value("tool_calls", 0)},
                          {"error_count", result.value("tool_errors", 0)},
                          {"input_tokens", usage.value("prompt_tokens", 0)},
                          {"output_tokens", usage.value("completion_tokens", 0)}};
        std::cout << out.dump(-1, ' ', false, gaia::json::error_handler_t::replace) << std::endl;
    } else {
        std::cout << result.value("result", "") << std::endl;
    }
    return status == "success" ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
    const Cli cli = parse(argc, argv);
    try {
        if (!cli.print && !cli.stdio) return runSidecar(cli);
        std::signal(SIGINT, onSignal);
        std::signal(SIGTERM, onSignal);
#ifdef SIGBREAK
        std::signal(SIGBREAK, onSignal);  // what a parent sends a console child on Windows
#endif
        if (cli.stdio) return runStdio(cli);
        return run(cli);
    } catch (const std::exception& e) {
        std::cerr << "gaiacode: " << e.what() << "\n";
        return 1;
    }
}
