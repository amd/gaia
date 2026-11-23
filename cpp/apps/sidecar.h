// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A native harness (gaiacpp, gaiacode) as the GAIA daemon's "gaia" sidecar: the HTTP + SSE contract the
// Agent UI speaks to the Python flagship (hub/agents/gaia/python/gaiapy/
// server.py), at API 2.14 — chat with streaming, tool activity, approvals,
// Stop and bypass. Started by the daemon as `<program> --host H --port P`.

#pragma once

#include <atomic>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/process.h>

namespace gaia {

/// What one chat's agent is built from; the per-chat switches outlive a rebuild.
struct SidecarAgentSpec {
    std::vector<std::string> workspaceRoots;  // UTF-8
    std::shared_ptr<std::atomic<bool>> internetOff;
    std::shared_ptr<std::atomic<bool>> memoryOff;
    std::shared_ptr<std::atomic<bool>> memoryPrivate;
    std::shared_ptr<ShellSession> shell;
};

struct SidecarOptions {
    std::string host = "127.0.0.1";
    int port = 0;
    std::string defaultModel;  // used when a /query names none
    /// The model when neither the query nor defaultModel names one; empty = such a query is refused.
    std::string fallbackModel;
    /// Names the program in its log and error lines, e.g. "gaiacpp".
    std::string program;
    /// What /version reports.
    std::string version;
    /// Builds each chat's agent. Required.
    std::function<std::unique_ptr<Agent>(const AgentConfig&, const SidecarAgentSpec&)> makeAgent;
};

/// Serve until stopped. Throws std::runtime_error when the token is missing
/// or the port can't be bound.
int runSidecar(const SidecarOptions& options);

} // namespace gaia
