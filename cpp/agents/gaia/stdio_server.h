// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The stdio protocol the GAIA terminal UI speaks to its agent child, so
// `gaiacpp --stdio` can be that child. Contract:
// tests/fixtures/stdio/gaia_stdio_wire.json.
//
// stdin, one line each: a plain query, {"gaia_query": "..."}, or
// {"gaia_control": verb, ...}. Controls are read on their own thread so an
// approval answer or a cancel lands while a turn is running.
//
// stdout, JSON Lines of canonical events, with exactly one terminal event
// (final or error) per query. Nothing else is written there.

#pragma once

#include <atomic>
#include <condition_variable>
#include <deque>
#include <functional>
#include <iosfwd>
#include <memory>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <gaia/security.h>
#include <gaia/types.h>

namespace gaia {

class GaiaAgent;

struct StdioOptions {
    bool fullAccess = false;   // approval prompts off
    bool acceptEdits = false;  // file edits inside editRoots run without asking
    bool dev = false;          // step-by-step status lines
    std::string agentVersion;
    std::vector<std::string> editRoots;  // empty = the working directory
    bool memory = true;                  // false = the memory dump says memory is off
    std::string memoryPath;              // empty = MemoryStore::defaultPath()
    // What --native-tools asked for; a model switch falls back to it.
    NativeToolCalls nativeTools = NativeToolCalls::Auto;
    std::ostream* log = nullptr;  // diagnostics; null = std::cerr
    // Whether anyone can still read stdout. Asked once stdin has closed, so a
    // host that left stops the work at once; unset = stdout is trusted until a
    // write to it fails.
    std::function<bool()> hostAlive;
    // Leave the process the moment the host is known to be gone, without
    // waiting for a model call or a tool that cannot be interrupted.
    bool exitWhenHostGone = false;
};

/// "fireworks" or "amd" for a cloud model Lemonade routes to, "" for a local one.
std::string cloudProviderOf(const std::string& modelId);

/// Point `cfg` at `modelId` the way this transport runs it: cloud models call
/// tools natively, and the answer streams only when tool calls are native.
void applyStdioModel(AgentConfig& cfg, const std::string& modelId, NativeToolCalls launchMode);

/// The working directory in UTF-8, the spelling editIsInside() reads.
std::string workingFolderUtf8();

/// True when "accept edits" may write `target` without asking: inside one of
/// `roots`, and not somewhere a write changes what runs (.git, a dotfile or
/// dot-folder, a folder on PATH, a file cmd.exe would run in place of a
/// program). A home folder or a drive root never counts as a root.
bool editIsInside(const std::string& target, const std::vector<std::string>& roots);

class StdioServer {
public:
    StdioServer(GaiaAgent& agent, std::istream& in, std::ostream& out, StdioOptions options);
    /// Waits for the stdin reader, so `in` must reach end of input first.
    ~StdioServer();

    /// Serve until stdin closes, or until the host is gone. Never throws: an
    /// internal error is logged and returned as exit code 1.
    int run();

    /// True once a write to stdout failed: the host left, maybe without closing stdin.
    bool hostGone() const { return hostGone_.load(); }

    /// Write one event line (thread-safe).
    void write(const json& event);

    bool dev() const { return options_.dev; }

    /// Every event `type` this server writes.
    static const std::vector<std::string>& eventTypes();
    /// Every `gaia_control` verb it acts on.
    static const std::vector<std::string>& controlVerbs();
    /// Every `tool_decision` answer it accepts.
    static const std::vector<std::string>& decisions();

private:
    struct Item {
        enum Kind { Query, Clear, Stop } kind;
        std::string text;
        bool cancelled = false;
    };

    void pumpStdin();
    /// @throws std::invalid_argument when a field has the wrong type.
    void applyControl(const json& message);
    void cancelInFlightLocked(const std::string& why);
    void hostLeft(const std::string& why);
    void watchHost();
    /// Exactly one terminal event per query, whatever fails.
    void runQuery(const std::string& query);
    json runTurn(const std::string& query);
    json runModelCommand(const std::string& arg);
    json warmUp();
    json memoryDump();
    json switchableModels();
    ToolConfirmResult confirm(const std::string& tool, const json& args);
    json statusEvent();
    std::ostream& log();

    GaiaAgent& agent_;
    std::istream& in_;
    std::ostream& out_;
    StdioOptions options_;
    std::mutex outMutex_;
    std::mutex logMutex_;
    std::atomic<bool> hostGone_{false};
    std::thread reader_;

    std::mutex mutex_;
    std::condition_variable queueCv_;
    std::deque<Item> queue_;

    // Permission state, shared between the turn thread and the stdin reader.
    std::condition_variable decisionCv_;
    std::shared_ptr<AllowedToolsStore> grants_;
    bool fullAccess_;
    bool acceptEdits_;
    std::condition_variable doneCv_;
    bool inFlight_ = false;  // a query was taken off the queue and has no terminal event yet
    bool finished_ = false;
    bool inputClosed_ = false;
    std::atomic<bool> cancelled_{false};
    std::string pendingConfirmId_;
    std::string decision_;
    unsigned long long turnCounter_ = 0;
    std::string runId_;
};

}  // namespace gaia
