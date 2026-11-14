// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// OpenAI-compatible HTTP server that serves any gaia::Agent.

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia {

class Agent;
class SessionStore;

/// Server options. Defaults are the safe ones: loopback only, no CORS,
/// no direct tool execution.
struct ServerOptions {
    /// Interface to bind. Binding a non-loopback address without an apiKey
    /// is refused by run().
    std::string host = "127.0.0.1";

    /// Port to bind. 0 picks a free port (see ApiServer::port()).
    int port = 8200;

    /// When non-empty, every route except GET /health requires
    /// `Authorization: Bearer <apiKey>`.
    std::string apiKey;

    /// Browser origins allowed to call the server (exact match, e.g.
    /// "http://localhost:5173"). Requests carrying any other Origin header are
    /// rejected with 403; allowed ones get CORS headers. Empty = no browser
    /// access at all.
    std::vector<std::string> allowedOrigins;

    /// Register POST /v1/tools/:name, which runs a tool directly with no LLM
    /// in the loop. Off by default: it is a remote shell for any agent that
    /// has one.
    bool exposeToolRoutes = false;

    /// Model id advertised by /v1/models and echoed in completions.
    /// Empty = the agent's configured model id.
    std::string modelName;

    /// Version of the served agent, reported by GET /health.
    std::string agentVersion;

    /// Reject (400) requests whose `messages` contain role=system. By default
    /// they are dropped: the agent owns its system prompt.
    bool rejectSystemMessages = false;

    /// Maximum request body size in bytes (images arrive inline as base64).
    size_t maxRequestBytes = 32u * 1024u * 1024u;
};

/// OpenAI-compatible server wrapping one Agent.
///
/// Endpoints:
///   POST   /v1/chat/completions  stateless multi-turn chat (stream + non-stream)
///   GET    /v1/models            the served model id
///   GET    /v1/tools             registered tools
///   POST   /v1/tools/:name       direct tool execution (only with exposeToolRoutes)
///   GET    /health               liveness + version (never needs auth)
///   GET    /sessions             list sessions (when a store is set)
///   DELETE /sessions/:id         delete a session
///
/// Each completion replaces the agent's history with the request's prior
/// turns, so the agent is stateless across requests. One completion runs at a
/// time; a concurrent one gets 409 `agent_busy`.
///
/// The constructor installs the server's own OutputHandler on the agent so it
/// can stream; do not replace it while the server exists. Token-level content
/// streaming needs AgentConfig::streaming; without it the answer arrives as
/// one delta after the tool activity events.
class GAIA_API ApiServer {
public:
    ApiServer(Agent& agent, ServerOptions options);

    /// Legacy constructor: default options on `port`.
    explicit ApiServer(Agent& agent, int port = 8200);

    ~ApiServer();

    ApiServer(const ApiServer&) = delete;
    ApiServer& operator=(const ApiServer&) = delete;

    void setSessionStore(std::shared_ptr<SessionStore> store);

    /// Bind and serve (blocking). Throws std::runtime_error if the port cannot
    /// be bound or the options are unsafe.
    void run();

    /// Stop serving and cancel any in-flight completion. Only touches atomics
    /// and closes the listening socket, so it is safe from a signal-handling
    /// thread. run() returns once in-flight requests finish.
    void stop();

    /// True once run() has bound and is accepting connections.
    bool isRunning() const;

    /// The bound port (the real one when ServerOptions::port was 0).
    int port() const;

    /// gaia_core version string reported by /health.
    static const char* version();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace gaia
