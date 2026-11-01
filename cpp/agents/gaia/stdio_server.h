// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The flagship's stdio protocol — what the Go TUI speaks to `gaia-agent` —
// so this binary is a drop-in for the Python flagship's stdio child. Contract:
// tests/fixtures/stdio/gaia_stdio_wire.json (and docs/spec/agent-ui-query-sse-
// contract.md section 10).
//
// stdin, one line each: a plain query, {"gaia_query": "..."}, or
// {"gaia_control": verb, ...} with verb tool_decision | full_access |
// accept_edits | cancel | clear_history. Controls are read on their own thread
// so a permission answer or a cancel lands while a turn is running.
//
// stdout, JSONL canonical events: status | token | tool_call | tool_result |
// needs_confirmation | final | error — exactly one terminal (final or error)
// per query.

#pragma once

#include <condition_variable>
#include <deque>
#include <iosfwd>
#include <mutex>
#include <set>
#include <string>

#include <gaia/types.h>

namespace gaia {

class FlagshipAgent;

class StdioServer {
public:
    StdioServer(FlagshipAgent& agent, std::istream& in, std::ostream& out, bool fullAccess,
                bool acceptEdits, std::string agentVersion);

    /// Serve until stdin closes. Returns the process exit code.
    int run();

    /// Emit one event line (thread-safe).
    void write(const json& event);

    /// Re-apply a cancel the host sent before the agent began the turn.
    void reassertCancel();

private:
    struct Item {
        enum Kind { Query, Clear, Stop } kind;
        std::string text;
    };

    void pumpStdin();
    /// @throws std::invalid_argument when a field has the wrong type.
    void applyControl(const json& message);
    void rejectControl(const std::string& why);
    /// Exactly one terminal event per query, whatever fails.
    void runQuery(const std::string& query);
    void runQueryOrThrow(const std::string& query);
    void runModelCommand(const std::string& arg);
    ToolConfirmResult confirm(const std::string& tool, const json& args);
    json statusEvent();

    FlagshipAgent& agent_;
    std::istream& in_;
    std::ostream& out_;
    std::string agentVersion_;
    std::mutex outMutex_;

    std::mutex mutex_;
    std::condition_variable queueCv_;
    std::deque<Item> queue_;

    // Permission state, shared between the turn thread and the stdin reader.
    std::condition_variable decisionCv_;
    bool fullAccess_;
    bool acceptEdits_;
    bool turnActive_ = false;
    bool cancelPending_ = false;
    std::string pendingConfirmId_;
    std::string decision_;
    std::set<std::string> sessionGrants_;
    unsigned long long confirmCounter_ = 0;
    std::string runId_;
};

}  // namespace gaia
