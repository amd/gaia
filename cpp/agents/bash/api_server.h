// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// HTTP REST API server that wraps an Agent with OpenAI-compatible endpoints.
// Uses cpp-httplib for the HTTP server (same dependency as gaia_core).

#pragma once

#include <memory>
#include <string>

#include "gaia/export.h"

namespace gaia {

class Agent;
class SessionStore;

/// HTTP REST API server that wraps an Agent with OpenAI-compatible endpoints.
/// Uses cpp-httplib for the HTTP server.
///
/// Endpoints:
///   POST /v1/chat/completions  -- agent query (streaming + non-streaming)
///   GET  /v1/tools             -- list registered tools
///   POST /v1/tools/:name       -- execute a tool directly
///   GET  /health               -- health check
///   GET  /sessions             -- list sessions
///   DELETE /sessions/:id       -- delete session
///
/// Threading: httplib::Server runs its own thread pool. Agent::processQuery()
/// is NOT re-entrant (guarded by inFlight_), so concurrent /v1/chat/completions
/// requests will receive a 409 Conflict error. Tool execution and read-only
/// endpoints are safe to call concurrently.
///
/// Usage:
///   BashAgent agent(config);
///   ApiServer server(agent, 8200);
///   server.setSessionStore(store);
///   server.run();  // blocking
class ApiServer {
public:
    ApiServer(Agent& agent, int port = 8200);
    ~ApiServer();

    void setSessionStore(std::shared_ptr<SessionStore> store);

    /// Start the server (blocking).
    void run();

    /// Stop the server (call from another thread or signal handler).
    void stop();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace gaia
