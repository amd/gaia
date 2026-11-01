// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// ApiServer implementation — OpenAI-compatible REST API wrapping a GAIA Agent.
// Uses cpp-httplib (same dependency as LemonadeClient in gaia_core).

#include "api_server.h"

#include <atomic>
#include <cctype>
#include <chrono>
#include <iostream>
#include <mutex>
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

/// Generate a unique ID for chat completion responses.
/// Combines a millisecond timestamp with a monotonic counter so two
/// requests landing in the same millisecond still get distinct IDs.
static std::string generateCompletionId() {
    static std::atomic<uint64_t> counter{0};
    auto now = std::chrono::system_clock::now().time_since_epoch();
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(now).count();
    return "chatcmpl-" + std::to_string(ms) + "-" +
           std::to_string(counter.fetch_add(1, std::memory_order_relaxed));
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

constexpr std::size_t kMaxRequestBytes = 16 * 1024 * 1024;

static void sendError(httplib::Response& res, int status, const std::string& message,
                      const std::string& type, const std::string& code = "") {
    res.status = status;
    res.set_content(errorJson(message, type, code).dump(-1, ' ', false, json::error_handler_t::replace),
                    "application/json");
}

static void sendBusy(httplib::Response& res) {
    sendError(res, 409, "Agent is busy processing another request. Concurrent requests are not supported.",
              "conflict", "agent_busy");
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
    bool bound = false;
    // One agent call at a time: a chat turn and a direct tool call share the agent's state.
    std::mutex busy;

    Impl(Agent& a, int p) : agent(a), port(p) {}

    // ---- Origin policy ----

    // Only pages served from this machine may call the API: the tools read the
    // user's files, so a website the user visits must not reach them.
    static bool isLoopbackHost(std::string host) {
        if (!host.empty() && host.front() == '[') {
            const auto close = host.find(']');
            host = close == std::string::npos ? host : host.substr(1, close - 1);
        } else if (const auto colon = host.rfind(':'); colon != std::string::npos && host.find(':') == colon) {
            host = host.substr(0, colon);
        }
        for (auto& c : host) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return host == "127.0.0.1" || host == "localhost" || host == "::1";
    }

    static bool isLoopbackOrigin(const std::string& origin) {
        const auto scheme = origin.find("://");
        if (scheme == std::string::npos) return false;
        std::string host = origin.substr(scheme + 3);
        if (const auto slash = host.find('/'); slash != std::string::npos) host.resize(slash);
        return isLoopbackHost(host);
    }

    void addCorsHeaders(httplib::Response& res) {
        // Pre-routing already refused any non-loopback Origin.
        res.set_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization");
    }

    // ---- Route setup ----

    void setupRoutes() {
        server.set_payload_max_length(kMaxRequestBytes);
        // httplib answers an oversized or unreadable request itself; give it the API's error shape.
        server.set_error_handler([](const httplib::Request&, httplib::Response& res) {
            if (!res.body.empty()) return;
            if (res.status == 413) {
                sendError(res, 413, "Request body is larger than " + std::to_string(kMaxRequestBytes / (1024 * 1024)) +
                                        " MB.", "invalid_request_error", "payload_too_large");
            } else {
                sendError(res, res.status, "Request failed with HTTP " + std::to_string(res.status) + ".",
                          res.status < 500 ? "invalid_request_error" : "server_error");
            }
        });
        server.set_pre_routing_handler([](const httplib::Request& req, httplib::Response& res) {
            // Host guards against DNS rebinding; Origin against cross-site pages.
            const std::string host = req.get_header_value("Host");
            const std::string origin = req.get_header_value("Origin");
            if ((!host.empty() && !isLoopbackHost(host)) || (!origin.empty() && !isLoopbackOrigin(origin))) {
                res.status = 403;
                res.set_content(errorJson("Requests are accepted only from this machine.", "forbidden").dump(),
                                "application/json");
                return httplib::Server::HandlerResponse::Handled;
            }
            if (!origin.empty()) res.set_header("Access-Control-Allow-Origin", origin);
            // A chunked upload has no length to hold against the body limit.
            if (req.method == "POST" && req.get_header_value("Transfer-Encoding").find("chunked") != std::string::npos) {
                sendError(res, 411, "Send the request body with a Content-Length.", "invalid_request_error");
                return httplib::Server::HandlerResponse::Handled;
            }
            // The body is not read yet here, so judge by the headers. A non-JSON type is
            // what a cross-site form or no-preflight fetch would send.
            const std::string type = req.get_header_value("Content-Type");
            const std::string length = req.get_header_value("Content-Length");
            const bool hasBody = !length.empty() && length != "0";
            if (req.method == "POST" && (hasBody || !type.empty()) && type.rfind("application/json", 0) != 0) {
                res.status = 415;
                res.set_content(errorJson("Content-Type must be application/json.", "invalid_request_error").dump(),
                                "application/json");
                return httplib::Server::HandlerResponse::Handled;
            }
            return httplib::Server::HandlerResponse::Unhandled;
        });

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

    /// Run one agent turn under the busy guard. Returns false (and fills @p res)
    /// when the turn did not produce an answer.
    bool runTurn(httplib::Response& res, const std::string& userInput, json& result) {
        std::unique_lock<std::mutex> lock(busy, std::try_to_lock);
        if (!lock.owns_lock()) {
            sendBusy(res);
            return false;
        }
        try {
            result = agent.processQuery(userInput);
        } catch (const std::runtime_error& e) {
            // Agent is not re-entrant — another caller holds it.
            if (std::string(e.what()).find("already running") != std::string::npos) {
                sendBusy(res);
            } else {
                sendError(res, 500, std::string("Agent error: ") + e.what(), "server_error");
            }
            return false;
        } catch (const std::exception& e) {
            sendError(res, 500, std::string("Internal error: ") + e.what(), "server_error");
            return false;
        }
        if (result.contains("error")) {
            // The model could not be reached or read: an upstream failure, not an answer.
            const std::string why = result["error"].is_string() ? result["error"].get<std::string>()
                                                                : result["error"].dump();
            sendError(res, 502, "The model did not answer: " + why, "upstream_error", "llm_unavailable");
            return false;
        }
        return true;
    }

    static json usageOf(const json& result) {
        json usage = {{"prompt_tokens", 0}, {"completion_tokens", 0}, {"total_tokens", 0}};
        if (result.contains("usage") && result["usage"].is_object()) {
            for (const char* k : {"prompt_tokens", "completion_tokens", "total_tokens"}) {
                if (result["usage"].contains(k) && result["usage"][k].is_number_integer()) usage[k] = result["usage"][k];
            }
        }
        return usage;
    }

    void handleNonStreamingCompletion(httplib::Response& res,
                                      const std::string& userInput,
                                      const std::string& model,
                                      const std::string& completionId,
                                      int64_t created) {
        json result;
        if (!runTurn(res, userInput, result)) return;
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
                        {"content", result.value("result", "")}
                    }},
                    {"finish_reason", "stop"}
                }
            })},
            {"usage", usageOf(result)}
        };
        res.status = 200;
        res.set_content(response.dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
    }

    void handleStreamingCompletion(httplib::Response& res,
                                   const std::string& userInput,
                                   const std::string& model,
                                   const std::string& completionId,
                                   int64_t created) {
        // Agent::processQuery returns a complete result, so the answer goes out
        // as one SSE chunk followed by [DONE]. Failures stay plain HTTP errors.
        json result;
        if (!runTurn(res, userInput, result)) return;
        const std::string content = result.value("result", "");

        res.set_header("Cache-Control", "no-cache");
        res.set_header("Connection", "keep-alive");

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
            })},
            {"usage", usageOf(result)}
        };

        std::string body;
        body += "data: " + chunk.dump(-1, ' ', false, json::error_handler_t::replace) + "\n\n";
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

        std::unique_lock<std::mutex> lock(busy, std::try_to_lock);
        if (!lock.owns_lock()) {
            sendBusy(res);
            return;
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

        bool removed = false;
        try {
            removed = sessionStore->remove(sessionId);
        } catch (const std::invalid_argument& e) {
            // validateId() rejects IDs with path separators, dots, etc.
            // Surface a 400 instead of letting it become an opaque 500.
            res.status = 400;
            res.set_content(
                errorJson(e.what(), "invalid_request_error").dump(),
                "application/json");
            return;
        }
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

int ApiServer::bind() {
    if (impl_->bound) return impl_->port;
    if (impl_->port == 0) {
        impl_->port = impl_->server.bind_to_any_port("127.0.0.1");
    } else if (!impl_->server.bind_to_port("127.0.0.1", impl_->port)) {
        impl_->port = -1;
    }
    if (impl_->port <= 0) {
        throw std::runtime_error("ApiServer failed to bind on 127.0.0.1. Check that the port is not already in use.");
    }
    impl_->bound = true;
    return impl_->port;
}

void ApiServer::run() {
    bind();
    std::cerr << "[ApiServer] Listening on port " << impl_->port << std::endl;
    std::cerr << "[ApiServer] Endpoints:" << std::endl;
    std::cerr << "  POST /v1/chat/completions  -- agent query" << std::endl;
    std::cerr << "  GET  /v1/tools             -- list tools" << std::endl;
    std::cerr << "  POST /v1/tools/:name       -- execute tool" << std::endl;
    std::cerr << "  GET  /health               -- health check" << std::endl;
    std::cerr << "  GET  /sessions             -- list sessions" << std::endl;
    std::cerr << "  DELETE /sessions/:id       -- delete session" << std::endl;

    if (!impl_->server.listen_after_bind()) {
        throw std::runtime_error(
            "ApiServer stopped listening on port " + std::to_string(impl_->port) + " unexpectedly.");
    }
}

void ApiServer::stop() {
    impl_->server.stop();
}

} // namespace gaia
