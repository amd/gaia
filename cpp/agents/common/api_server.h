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
/// Threading: httplib::Server runs its own thread pool. A chat turn and a
/// direct tool call each hold the agent exclusively; a request that arrives
/// while either is running gets 409 Conflict. Read-only endpoints run
/// concurrently. Request bodies over 16 MB get 413.
///
/// A turn whose model could not be reached or read answers 502 with an
/// OpenAI-style error body, never 200.
///
/// Usage:
///   FlagshipAgent agent(config, options);
///   ApiServer server(agent, 8200);
///   server.setSessionStore(store);
///   server.run();  // blocking
class ApiServer {
public:
    ApiServer(Agent& agent, int port = 8200);
    ~ApiServer();

    void setSessionStore(std::shared_ptr<SessionStore> store);

    /// Bind 127.0.0.1 now; port 0 picks a free one. Returns the bound port.
    /// @throws std::runtime_error when the port cannot be bound.
    int bind();

    /// Start the server (blocking). Binds first when bind() was not called.
    void run();

    /// Stop the server (call from another thread or signal handler).
    void stop();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace gaia
