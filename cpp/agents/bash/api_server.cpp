// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// ApiServer implementation — OpenAI-compatible REST API wrapping a GAIA Agent.
// Uses cpp-httplib (same dependency as LemonadeClient in gaia_core).

#include "api_server.h"

#include <chrono>
#include <iostream>
#include <stdexcept>
#include <string>

#include <httplib.h>

#include <gaia/agent.h>
#include <gaia/session.h>
#include <gaia/tool_registry.h>
#include <gaia/types.h>

namespace gaia {

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Generate a unique-enough ID for chat completion responses.
static std::string generateCompletionId() {
    auto now = std::chrono::system_clock::now().time_since_epoch();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
    return "chatcmpl-" + std::to_string(ms);
}

/// Return the current Unix timestamp.
static int64_t unixTimestamp() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

/// Build a JSON error response body.
static json errorJson(const std::string& message, const std::string& type = "server_error",
                       const std::string& code = "") {
    json err = {
        {"error", {
            {"message", message},
            {"type", type}
        }}
    };
    if (!code.empty()) {
        err["error"]["code"] = code;
    }
    return err;
}

/// Extract the last user message content from an OpenAI-style messages array.
static std::string extractUserInput(const json& messages) {
    // Walk backwards to find the last "user" role message.
    for (auto it = messages.rbegin(); it != messages.rend(); ++it) {
        if (it->value("role", "") == "user") {
            // Content can be a string or an array of content parts.
            const auto& content = (*it)["content"];
            if (content.is_string()) {
                return content.get<std::string>();
            }
            if (content.is_array()) {
                // Concatenate text parts.
                std::string text;
                for (const auto& part : content) {
                    if (part.value("type", "") == "text") {
                        if (!text.empty()) text += "\n";
                        text += part.value("text", "");
                    }
                }
                return text;
            }
        }
    }
    return "";
}

// ---------------------------------------------------------------------------
// PIMPL
// ---------------------------------------------------------------------------

struct ApiServer::Impl {
    Agent& agent;
    int port;
    httplib::Server server;
    std::shared_ptr<SessionStore> sessionStore;

    Impl(Agent& a, int p) : agent(a), port(p) {}

    // ---- CORS ----

    void addCorsHeaders(httplib::Response& res) {
        res.set_header("Access-Control-Allow-Origin", "*");
        res.set_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization");
    }

    // ---- Route setup ----

    void setupRoutes() {
        // CORS preflight for all paths.
        server.Options(R"(.*)", [this](const httplib::Request& /*req*/, httplib::Response& res) {
            addCorsHeaders(res);
            res.status = 204;
        });

        server.Post("/v1/chat/completions",
            [this](const httplib::Request& req, httplib::Response& res) {
                handleChatCompletions(req, res);
            });

        server.Get("/v1/tools",
            [this](const httplib::Request& req, httplib::Response& res) {
                handleListTools(req, res);
            });

        // cpp-httplib path-param capture: /v1/tools/:name
        server.Post(R"(/v1/tools/([^/]+))",
            [this](const httplib::Request& req, httplib::Response& res) {
                handleExecuteTool(req, res);
            });

        server.Get("/health",
            [this](const httplib::Request& req, httplib::Response& res) {
                handleHealth(req, res);
            });

        server.Get("/sessions",
            [this](const httplib::Request& req, httplib::Response& res) {
                handleListSessions(req, res);
            });

        // DELETE /sessions/:id
        server.Delete(R"(/sessions/([^/]+))",
            [this](const httplib::Request& req, httplib::Response& res) {
                handleDeleteSession(req, res);
            });
    }

    // ---- POST /v1/chat/completions ----

    void handleChatCompletions(const httplib::Request& req, httplib::Response& res) {
        addCorsHeaders(res);

        // Parse request body.
        json body;
        try {
            body = json::parse(req.body);
        } catch (const std::exception& e) {
            res.status = 400;
            res.set_content(
                errorJson("Invalid JSON: " + std::string(e.what()), "invalid_request_error").dump(),
                "application/json");
            return;
        }

        // Validate messages field.
        if (!body.contains("messages") || !body["messages"].is_array() ||
            body["messages"].empty()) {
            res.status = 400;
            res.set_content(
                errorJson("'messages' field is required and must be a non-empty array",
                          "invalid_request_error").dump(),
                "application/json");
            return;
        }

        std::string userInput = extractUserInput(body["messages"]);
        if (userInput.empty()) {
            res.status = 400;
            res.set_content(
                errorJson("No user message found in messages array",
                          "invalid_request_error").dump(),
                "application/json");
            return;
        }

        bool stream = body.value("stream", false);
        std::string model = body.value("model", agent.config().modelId);
        std::string completionId = generateCompletionId();
        int64_t created = unixTimestamp();

        if (stream) {
            handleStreamingCompletion(res, userInput, model, completionId, created);
        } else {
            handleNonStreamingCompletion(res, userInput, model, completionId, created);
        }
    }

    void handleNonStreamingCompletion(httplib::Response& res,
                                      const std::string& userInput,
                                      const std::string& model,
                                      const std::string& completionId,
                                      int64_t created) {
        try {
            json result = agent.processQuery(userInput);
            std::string content = result.value("result", "");

            json response = {
                {"id", completionId},
                {"object", "chat.completion"},
                {"created", created},
                {"model", model},
                {"choices", json::array({
                    {
                        {"index", 0},
                        {"message", {
                            {"role", "assistant"},
                            {"content", content}
                        }},
                        {"finish_reason", "stop"}
                    }
                })},
                {"usage", {
                    {"prompt_tokens", 0},
                    {"completion_tokens", 0},
                    {"total_tokens", 0}
                }}
            };

            res.status = 200;
            res.set_content(response.dump(), "application/json");

        } catch (const std::runtime_error& e) {
            std::string what = e.what();
            // Agent is not re-entrant — detect concurrency conflict.
            if (what.find("already running") != std::string::npos) {
                res.status = 409;
                res.set_content(
                    errorJson("Agent is busy processing another request. "
                              "Concurrent requests are not supported.",
                              "conflict", "agent_busy").dump(),
                    "application/json");
            } else {
                res.status = 500;
                res.set_content(
                    errorJson("Agent error: " + what).dump(),
                    "application/json");
            }
        } catch (const std::exception& e) {
            res.status = 500;
            res.set_content(
                errorJson("Internal error: " + std::string(e.what())).dump(),
                "application/json");
        }
    }

    void handleStreamingCompletion(httplib::Response& res,
                                   const std::string& userInput,
                                   const std::string& model,
                                   const std::string& completionId,
                                   int64_t created) {
        // Process the query first (we can't truly stream token-by-token since
        // Agent::processQuery returns a complete result). We simulate SSE by
        // sending the full result as a single chunk followed by [DONE].
        std::string content;
        bool agentBusy = false;
        std::string errorMsg;

        try {
            json result = agent.processQuery(userInput);
            content = result.value("result", "");
        } catch (const std::runtime_error& e) {
            std::string what = e.what();
            if (what.find("already running") != std::string::npos) {
                agentBusy = true;
            }
            errorMsg = what;
        } catch (const std::exception& e) {
            errorMsg = e.what();
        }

        if (agentBusy) {
            res.status = 409;
            res.set_content(
                errorJson("Agent is busy processing another request. "
                          "Concurrent requests are not supported.",
                          "conflict", "agent_busy").dump(),
                "application/json");
            return;
        }

        if (!errorMsg.empty()) {
            res.status = 500;
            res.set_content(
                errorJson("Agent error: " + errorMsg).dump(),
                "application/json");
            return;
        }

        // Send as SSE chunks via chunked transfer encoding.
        res.set_header("Content-Type", "text/event-stream");
        res.set_header("Cache-Control", "no-cache");
        res.set_header("Connection", "keep-alive");

        // Build the SSE data chunk with the full content.
        json chunk = {
            {"id", completionId},
            {"object", "chat.completion.chunk"},
            {"created", created},
            {"model", model},
            {"choices", json::array({
                {
                    {"index", 0},
                    {"delta", {
                        {"role", "assistant"},
                        {"content", content}
                    }},
                    {"finish_reason", nullptr}
                }
            })}
        };

        // Stop chunk.
        json stopChunk = {
            {"id", completionId},
            {"object", "chat.completion.chunk"},
            {"created", created},
            {"model", model},
            {"choices", json::array({
                {
                    {"index", 0},
                    {"delta", json::object()},
                    {"finish_reason", "stop"}
                }
            })}
        };

        std::string body;
        body += "data: " + chunk.dump() + "\n\n";
        body += "data: " + stopChunk.dump() + "\n\n";
        body += "data: [DONE]\n\n";

        res.set_content(body, "text/event-stream");
    }

    // ---- GET /v1/tools ----

    void handleListTools(const httplib::Request& /*req*/, httplib::Response& res) {
        addCorsHeaders(res);

        json tools = json::array();
        for (const auto& [name, info] : agent.tools().allTools()) {
            if (!info.enabled) continue;

            json params = json::array();
            for (const auto& p : info.parameters) {
                params.push_back({
                    {"name", p.name},
                    {"type", paramTypeToString(p.type)},
                    {"required", p.required},
                    {"description", p.description}
                });
            }

            tools.push_back({
                {"name", info.name},
                {"description", info.description},
                {"parameters", params}
            });
        }

        json response = {{"tools", tools}};
        res.status = 200;
        res.set_content(response.dump(), "application/json");
    }

    // ---- POST /v1/tools/:name ----

    void handleExecuteTool(const httplib::Request& req, httplib::Response& res) {
        addCorsHeaders(res);

        // Extract tool name from the regex capture.
        std::string toolName = req.matches[1].str();

        // Parse body as tool arguments.
        json args = json::object();
        if (!req.body.empty()) {
            try {
                args = json::parse(req.body);
            } catch (const std::exception& e) {
                res.status = 400;
                res.set_content(
                    errorJson("Invalid JSON body: " + std::string(e.what()),
                              "invalid_request_error").dump(),
                    "application/json");
                return;
            }
        }

        // Check if tool exists.
        if (!agent.tools().hasTool(toolName)) {
            // Try name resolution (handles common LLM mistakes).
            std::string resolved = agent.tools().resolveName(toolName);
            if (resolved.empty()) {
                res.status = 404;
                res.set_content(
                    errorJson("Tool not found: " + toolName, "not_found").dump(),
                    "application/json");
                return;
            }
            toolName = resolved;
        }

        try {
            // Execute through the mutable toolRegistry() to allow policy checks.
            json result = agent.toolRegistry().executeTool(toolName, args);

            json response = {
                {"tool", toolName},
                {"result", result}
            };
            res.status = 200;
            res.set_content(response.dump(), "application/json");

        } catch (const std::exception& e) {
            res.status = 500;
            res.set_content(
                errorJson("Tool execution error: " + std::string(e.what())).dump(),
                "application/json");
        }
    }

    // ---- GET /health ----

    void handleHealth(const httplib::Request& /*req*/, httplib::Response& res) {
        addCorsHeaders(res);

        AgentConfig cfg = agent.config();
        size_t toolCount = agent.tools().allTools().size();

        json response = {
            {"status", "ok"},
            {"model", cfg.modelId},
            {"tools", static_cast<int>(toolCount)},
            {"port", port}
        };

        res.status = 200;
        res.set_content(response.dump(), "application/json");
    }

    // ---- GET /sessions ----

    void handleListSessions(const httplib::Request& /*req*/, httplib::Response& res) {
        addCorsHeaders(res);

        if (!sessionStore) {
            res.status = 200;
            res.set_content(json::array().dump(), "application/json");
            return;
        }

        auto sessions = sessionStore->list();
        json arr = json::array();
        for (const auto& s : sessions) {
            arr.push_back({
                {"id", s.id},
                {"timestamp", s.timestamp},
                {"preview", s.preview},
                {"message_count", s.messageCount}
            });
        }

        res.status = 200;
        res.set_content(arr.dump(), "application/json");
    }

    // ---- DELETE /sessions/:id ----

    void handleDeleteSession(const httplib::Request& req, httplib::Response& res) {
        addCorsHeaders(res);

        std::string sessionId = req.matches[1].str();

        if (!sessionStore) {
            res.status = 404;
            res.set_content(
                errorJson("Session store not configured", "not_found").dump(),
                "application/json");
            return;
        }

        bool removed = sessionStore->remove(sessionId);
        if (removed) {
            res.status = 200;
            res.set_content(
                json({{"deleted", true}, {"id", sessionId}}).dump(),
                "application/json");
        } else {
            res.status = 404;
            res.set_content(
                errorJson("Session not found: " + sessionId, "not_found").dump(),
                "application/json");
        }
    }
};

// ---------------------------------------------------------------------------
// ApiServer public interface
// ---------------------------------------------------------------------------

ApiServer::ApiServer(Agent& agent, int port)
    : impl_(std::make_unique<Impl>(agent, port)) {
    impl_->setupRoutes();
}

ApiServer::~ApiServer() = default;

void ApiServer::setSessionStore(std::shared_ptr<SessionStore> store) {
    impl_->sessionStore = std::move(store);
}

void ApiServer::run() {
    std::cerr << "[ApiServer] Listening on port " << impl_->port << std::endl;
    std::cerr << "[ApiServer] Endpoints:" << std::endl;
    std::cerr << "  POST /v1/chat/completions  -- agent query" << std::endl;
    std::cerr << "  GET  /v1/tools             -- list tools" << std::endl;
    std::cerr << "  POST /v1/tools/:name       -- execute tool" << std::endl;
    std::cerr << "  GET  /health               -- health check" << std::endl;
    std::cerr << "  GET  /sessions             -- list sessions" << std::endl;
    std::cerr << "  DELETE /sessions/:id       -- delete session" << std::endl;

    if (!impl_->server.listen("127.0.0.1", impl_->port)) {
        throw std::runtime_error(
            "ApiServer failed to bind on port " + std::to_string(impl_->port) +
            ". Check that the port is not already in use.");
    }
}

void ApiServer::stop() {
    impl_->server.stop();
}

} // namespace gaia
