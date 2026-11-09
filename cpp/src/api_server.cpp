// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// ApiServer — OpenAI-compatible HTTP server wrapping a GAIA Agent.

#include "gaia/api_server.h"

#include <algorithm>
#include <atomic>
#include <cctype>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <httplib.h>

#include "gaia/agent.h"
#include "gaia/console.h"
#include "gaia/session.h"
#include "gaia/tool_registry.h"
#include "gaia/types.h"

#ifndef GAIA_CORE_VERSION
#error "GAIA_CORE_VERSION must be defined by the build (see cpp/CMakeLists.txt)"
#endif

namespace gaia {

namespace {

// ---------------------------------------------------------------------------
// Small helpers
// ---------------------------------------------------------------------------

std::string generateCompletionId() {
    static std::atomic<uint64_t> counter{0};
    auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                  std::chrono::system_clock::now().time_since_epoch())
                  .count();
    return "chatcmpl-" + std::to_string(ms) + "-" +
           std::to_string(counter.fetch_add(1, std::memory_order_relaxed));
}

int64_t unixTimestamp() {
    return std::chrono::duration_cast<std::chrono::seconds>(
               std::chrono::system_clock::now().time_since_epoch())
        .count();
}

json errorJson(const std::string& message, const std::string& type,
               const std::string& code = "") {
    json err = {{"error", {{"message", message}, {"type", type}}}};
    if (!code.empty()) err["error"]["code"] = code;
    return err;
}

void sendError(httplib::Response& res, int status, const std::string& message,
               const std::string& type, const std::string& code = "") {
    res.status = status;
    res.set_content(errorJson(message, type, code).dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
}

std::string lower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

// Compares every byte of the longer input so timing doesn't leak the key.
bool constantTimeEquals(const std::string& a, const std::string& b) {
    const size_t n = std::max(a.size(), b.size());
    unsigned char diff = static_cast<unsigned char>(a.size() != b.size());
    for (size_t i = 0; i < n; ++i) {
        unsigned char ca = i < a.size() ? static_cast<unsigned char>(a[i]) : 0;
        unsigned char cb = i < b.size() ? static_cast<unsigned char>(b[i]) : 0;
        diff |= static_cast<unsigned char>(ca ^ cb);
    }
    return diff == 0;
}

std::string trim(const std::string& s) {
    auto b = s.find_first_not_of(" \t\r\n");
    if (b == std::string::npos) return "";
    auto e = s.find_last_not_of(" \t\r\n");
    return s.substr(b, e - b + 1);
}

// localhost, ::1, or a literal IPv4 address in 127.0.0.0/8 — never a name
// that merely starts with "127." (DNS rebinding).
bool isLoopbackHost(const std::string& host) {
    if (host == "localhost" || host == "::1") return true;
    int octets = 0, first = -1;
    size_t i = 0;
    while (i < host.size()) {
        size_t digits = 0;
        int value = 0;
        while (i < host.size() && host[i] >= '0' && host[i] <= '9' && digits < 3) {
            value = value * 10 + (host[i++] - '0');
            ++digits;
        }
        if (digits == 0 || value > 255) return false;
        if (++octets == 1) first = value;
        if (i == host.size()) break;
        if (host[i++] != '.' || i == host.size()) return false;
    }
    return octets == 4 && first == 127;
}

// Hostname part of a Host header: "localhost:8200" -> "localhost", "[::1]:80" -> "::1".
std::string hostHeaderName(const std::string& hostHeader) {
    if (!hostHeader.empty() && hostHeader[0] == '[') {
        auto close = hostHeader.find(']');
        return close == std::string::npos ? hostHeader : hostHeader.substr(1, close - 1);
    }
    return hostHeader.substr(0, hostHeader.find(':'));
}

// Agent signals "already running" only through these messages (src/agent.cpp):
// InFlightGuard in processQuery and the guard in setHistory.
bool isAgentBusyError(const std::exception& e) {
    const std::string what = e.what();
    return what.find("not re-entrant") != std::string::npos ||
           what.find("while processQuery() is running") != std::string::npos;
}

/// A client error that maps to an HTTP 4xx response.
struct RequestError : std::runtime_error {
    int status;
    RequestError(int s, const std::string& msg) : std::runtime_error(msg), status(s) {}
};

std::vector<std::uint8_t> base64Decode(const std::string& in) {
    auto val = [](unsigned char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+' || c == '-') return 62;
        if (c == '/' || c == '_') return 63;
        return -1;
    };
    std::vector<std::uint8_t> out;
    out.reserve(in.size() * 3 / 4);
    uint32_t buf = 0;
    int bits = 0;
    bool padding = false;
    for (unsigned char c : in) {
        if (c == '=') { padding = true; continue; }
        if (c == '\r' || c == '\n') continue;
        int v = val(c);
        if (v < 0 || padding) throw RequestError(400, "image data URI is not valid base64");
        buf = (buf << 6) | static_cast<uint32_t>(v);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out.push_back(static_cast<std::uint8_t>((buf >> bits) & 0xFF));
        }
    }
    return out;
}

Image imageFromPart(const json& part) {
    std::string url;
    const json& iu = part.contains("image_url") ? part["image_url"] : json();
    if (iu.is_string()) url = iu.get<std::string>();
    else if (iu.is_object() && iu.contains("url") && iu["url"].is_string())
        url = iu["url"].get<std::string>();
    else throw RequestError(400, "image_url content part has no url");

    // Only inline images: fetching remote URLs would make the server an SSRF proxy.
    if (url.rfind("data:", 0) != 0) {
        throw RequestError(400, "only data: URIs are supported for image_url; "
                                "send the image inline as base64");
    }
    auto comma = url.find(',');
    if (comma == std::string::npos) throw RequestError(400, "malformed image data URI");
    std::string meta = url.substr(5, comma - 5);
    const std::string suffix = ";base64";
    if (meta.size() < suffix.size() ||
        meta.compare(meta.size() - suffix.size(), suffix.size(), suffix) != 0) {
        throw RequestError(400, "image data URI must be base64-encoded");
    }
    std::string mime = meta.substr(0, meta.size() - suffix.size());
    auto bytes = base64Decode(url.substr(comma + 1));
    if (bytes.size() > GAIA_MAX_IMAGE_BYTES) {
        throw RequestError(413, "image exceeds " + std::to_string(GAIA_MAX_IMAGE_BYTES) +
                                    " bytes");
    }
    try {
        return Image::fromBytes(std::move(bytes), mime);
    } catch (const std::invalid_argument& e) {
        throw RequestError(400, std::string("invalid image: ") + e.what());
    }
}

/// Text and images of one OpenAI message `content`.
void parseContent(const json& content, bool allowImages, std::string& text,
                  std::vector<Image>& images) {
    if (content.is_string()) { text = content.get<std::string>(); return; }
    if (content.is_null()) return;
    if (!content.is_array()) throw RequestError(400, "message content must be a string or array");
    for (const auto& part : content) {
        const std::string type = part.is_object() ? part.value("type", "") : "";
        if (type == "text") {
            if (!text.empty()) text += "\n";
            text += part.value("text", "");
        } else if (type == "image_url" && allowImages) {
            images.push_back(imageFromPart(part));
        } else {
            throw RequestError(400, "unsupported content part type '" + type + "'");
        }
    }
}

struct Conversation {
    std::vector<Message> history;
    Message finalTurn;
};

/// OpenAI `messages` -> prior history + the final user turn.
Conversation parseConversation(const json& messages, bool rejectSystem) {
    if (!messages.is_array() || messages.empty()) {
        throw RequestError(400, "'messages' is required and must be a non-empty array");
    }
    Conversation conv;
    for (size_t i = 0; i < messages.size(); ++i) {
        const json& m = messages[i];
        if (!m.is_object() || !m.contains("role") || !m["role"].is_string()) {
            throw RequestError(400, "messages[" + std::to_string(i) + "] has no role");
        }
        const std::string role = m["role"].get<std::string>();
        const json content = m.contains("content") ? m["content"] : json();
        const bool last = i + 1 == messages.size();

        if (role == "system" || role == "developer") {
            if (rejectSystem) {
                throw RequestError(400, "system messages are not accepted: the agent owns "
                                        "its system prompt");
            }
            continue;
        }
        if (role == "user") {
            std::string text;
            std::vector<Image> images;
            parseContent(content, true, text, images);
            Message msg = Message::fromUser(text, images);
            if (last) conv.finalTurn = std::move(msg);
            else conv.history.push_back(std::move(msg));
            continue;
        }
        if (role == "assistant") {
            if (m.contains("tool_calls") && !m["tool_calls"].empty()) {
                throw RequestError(400, "client-side tool_calls are not supported; "
                                        "the agent runs its own tools");
            }
            std::string text;
            std::vector<Image> none;
            parseContent(content, false, text, none);
            Message msg;
            msg.role = MessageRole::ASSISTANT;
            msg.content = text;
            conv.history.push_back(std::move(msg));
            if (last) break;
            continue;
        }
        throw RequestError(400, "unsupported message role '" + role + "'");
    }
    if (messages.back().value("role", "") != "user") {
        throw RequestError(400, "the last message must have role 'user'");
    }
    return conv;
}

json chunkJson(const std::string& id, int64_t created, const std::string& model,
               json delta, json finishReason) {
    return {{"id", id},
            {"object", "chat.completion.chunk"},
            {"created", created},
            {"model", model},
            {"choices", json::array({{{"index", 0},
                                      {"delta", std::move(delta)},
                                      {"finish_reason", std::move(finishReason)}}})}};
}

std::string sseFrame(const json& j) { return "data: " + j.dump(-1, ' ', false, json::error_handler_t::replace) + "\n\n"; }

std::string truncateForEvent(const std::string& s, size_t max = 4096) {
    if (s.size() <= max) return s;
    while (max > 0 && (static_cast<unsigned char>(s[max]) & 0xC0) == 0x80) --max;
    return s.substr(0, max) + "...[truncated]";
}

// ---------------------------------------------------------------------------
// Streaming plumbing
// ---------------------------------------------------------------------------

/// One streaming completion: agent-thread producer, HTTP-thread consumer.
class StreamSession {
public:
    StreamSession(std::string id, int64_t created, std::string model)
        : id_(std::move(id)), created_(created), model_(std::move(model)) {}

    // ---- producer side (agent thread) ----

    void token(const std::string& tok) {
        std::lock_guard<std::mutex> lk(mu_);
        std::string out;
        for (char c : tok) {
            if (gate_ == Gate::Hold) break;
            if (gate_ == Gate::Undecided) {
                if (std::isspace(static_cast<unsigned char>(c))) continue;
                // A JSON envelope or fenced block is a tool call / planning
                // reply, not answer prose.
                if (c == '{' || c == '`') { gate_ = Gate::Hold; break; }
                gate_ = Gate::Pass;
                atLineStart_ = false;
                if (!contentSent_.empty()) out += "\n\n";
            } else if (atLineStart_ && c == '{') {
                // Prose that turns into a JSON tool call: stop before the JSON.
                gate_ = Gate::Hold;
                break;
            }
            out += c;
            if (c == '\n') atLineStart_ = true;
            else if (!std::isspace(static_cast<unsigned char>(c))) atLineStart_ = false;
        }
        if (!out.empty()) pushContentLocked(out);
    }

    void turnEnd() {
        std::lock_guard<std::mutex> lk(mu_);
        gate_ = Gate::Undecided;
        atLineStart_ = true;
    }

    void event(json ev) {
        std::lock_guard<std::mutex> lk(mu_);
        json c = chunkJson(id_, created_, model_, json::object(), nullptr);
        c["gaia"] = std::move(ev);
        frames_.push_back(sseFrame(c));
        cv_.notify_all();
    }

    void finish() {
        std::lock_guard<std::mutex> lk(mu_);
        finished_ = true;
        cv_.notify_all();
    }

    // ---- consumer side (HTTP thread) ----

    /// Waits up to `wait` for frames; returns them and whether the producer finished.
    std::deque<std::string> take(std::chrono::milliseconds wait, bool& finished) {
        std::unique_lock<std::mutex> lk(mu_);
        cv_.wait_for(lk, wait, [this] { return !frames_.empty() || finished_; });
        std::deque<std::string> out;
        out.swap(frames_);
        finished = finished_ && out.empty();
        return out;
    }

    /// What must still be sent so the streamed content ends with `answer`.
    std::string reconcile(const std::string& answer) {
        std::lock_guard<std::mutex> lk(mu_);
        const std::string want = trim(answer);
        const std::string sent = trim(contentSent_);
        if (want.empty()) return "";
        if (sent.size() >= want.size() &&
            sent.compare(sent.size() - want.size(), want.size(), want) == 0) {
            return "";
        }
        return contentSent_.empty() ? want : "\n\n" + want;
    }

    const std::string& id() const { return id_; }
    int64_t created() const { return created_; }
    const std::string& model() const { return model_; }

private:
    enum class Gate { Undecided, Pass, Hold };

    void pushContentLocked(const std::string& text) {
        contentSent_ += text;
        frames_.push_back(sseFrame(chunkJson(id_, created_, model_,
                                             {{"content", text}}, nullptr)));
        cv_.notify_all();
    }

    std::string id_;
    int64_t created_;
    std::string model_;

    std::mutex mu_;
    std::condition_variable cv_;
    std::deque<std::string> frames_;
    bool finished_ = false;
    Gate gate_ = Gate::Undecided;
    bool atLineStart_ = true;
    std::string contentSent_;
};

/// The agent's OutputHandler while served: forwards to the active stream, if any.
class ServerOutputHandler : public OutputHandler {
public:
    void attach(std::shared_ptr<StreamSession> s) {
        std::lock_guard<std::mutex> lk(mu_);
        session_ = std::move(s);
        pendingTool_.clear();
    }
    void detach() { attach(nullptr); }

    void printProcessingStart(const std::string&, int, const std::string&) override {}
    void printStepHeader(int step, int limit) override {
        emit({{"type", "step"}, {"step", step}, {"limit", limit}});
    }
    void printStateInfo(const std::string& m) override { emit({{"type", "state"}, {"message", m}}); }
    void printThought(const std::string& t) override {
        if (!t.empty()) emit({{"type", "thought"}, {"content", t}});
    }
    void printGoal(const std::string& g) override {
        if (!g.empty()) emit({{"type", "goal"}, {"content", g}});
    }
    void printPlan(const json& plan, int) override { emit({{"type", "plan"}, {"plan", plan}}); }
    void printToolUsage(const std::string& name) override {
        std::lock_guard<std::mutex> lk(mu_);
        pendingTool_ = name;
    }
    void printToolComplete() override {}
    void prettyPrintJson(const json& data, const std::string& title) override {
        std::string tool;
        {
            std::lock_guard<std::mutex> lk(mu_);
            tool = pendingTool_;
        }
        if (title == "Tool Args") {
            emit({{"type", "tool_start"}, {"tool", tool}, {"args", data}});
        } else if (title == "Tool Result") {
            emit({{"type", "tool_end"}, {"tool", tool}, {"result", truncateForEvent(data.dump(-1, ' ', false, json::error_handler_t::replace))}});
        }
    }
    void printError(const std::string& m) override { emit({{"type", "error"}, {"message", m}}); }
    void printWarning(const std::string& m) override { emit({{"type", "warning"}, {"message", m}}); }
    void printInfo(const std::string&) override {}
    void startProgress(const std::string&) override {}
    void stopProgress() override {}
    void printFinalAnswer(const std::string&, const UsageStats&) override {}
    void printCompletion(int, int) override {}

    void printStreamToken(const std::string& token) override {
        if (auto s = current()) s->token(token);
    }
    void printStreamEnd() override {
        if (auto s = current()) s->turnEnd();
    }

private:
    std::shared_ptr<StreamSession> current() {
        std::lock_guard<std::mutex> lk(mu_);
        return session_;
    }
    void emit(json ev) {
        if (auto s = current()) s->event(std::move(ev));
    }

    std::mutex mu_;
    std::shared_ptr<StreamSession> session_;
    std::string pendingTool_;
};

} // namespace

// ---------------------------------------------------------------------------
// Impl
// ---------------------------------------------------------------------------

struct ApiServer::Impl {
    Agent& agent;
    ServerOptions opts;
    httplib::Server server;
    std::shared_ptr<SessionStore> sessionStore;
    ServerOutputHandler* handler = nullptr;  // owned by the agent
    std::atomic<bool> busy{false};
    std::atomic<bool> stopping{false};
    std::atomic<int> boundPort{0};
    int64_t startedAt = unixTimestamp();

    /// Holds the single-completion slot; releases on destruction.
    struct BusySlot {
        std::atomic<bool>& flag;
        explicit BusySlot(std::atomic<bool>& f) : flag(f) {}
        ~BusySlot() { flag.store(false); }
    };

    Impl(Agent& a, ServerOptions o) : agent(a), opts(std::move(o)) {
        auto h = std::make_unique<ServerOutputHandler>();
        handler = h.get();
        agent.setOutputHandler(std::move(h));
    }

    std::string modelName() const {
        return opts.modelName.empty() ? agent.config().modelId : opts.modelName;
    }

    bool originAllowed(const std::string& origin) const {
        return std::find(opts.allowedOrigins.begin(), opts.allowedOrigins.end(), origin) !=
               opts.allowedOrigins.end();
    }

    void addCors(const std::string& origin, httplib::Response& res) const {
        res.set_header("Access-Control-Allow-Origin", origin);
        res.set_header("Vary", "Origin");
        res.set_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization");
    }

    // ---- Gatekeeping (runs before every route) ----

    httplib::Server::HandlerResponse gate(const httplib::Request& req, httplib::Response& res) {
        using HR = httplib::Server::HandlerResponse;

        // DNS-rebinding guard: a loopback server only answers to loopback names.
        if (isLoopbackHost(opts.host)) {
            std::string name = lower(hostHeaderName(req.get_header_value("Host")));
            if (!isLoopbackHost(name)) {
                sendError(res, 403, "Host '" + req.get_header_value("Host") +
                                        "' is not allowed; this server only answers "
                                        "loopback requests",
                          "permission_error", "host_not_allowed");
                return HR::Handled;
            }
        }

        // Browsers always send Origin cross-site; only listed origins get in.
        if (req.has_header("Origin")) {
            std::string origin = req.get_header_value("Origin");
            if (!originAllowed(origin)) {
                sendError(res, 403, "Origin '" + origin + "' is not allowed. Add it to "
                                    "ServerOptions::allowedOrigins to permit browser access.",
                          "permission_error", "origin_not_allowed");
                return HR::Handled;
            }
            addCors(origin, res);
            if (req.method == "OPTIONS") {
                res.status = 204;
                return HR::Handled;
            }
        }

        if (req.method == "GET" && req.path == "/health") return HR::Unhandled;

        if (!opts.apiKey.empty()) {
            const std::string auth = req.get_header_value("Authorization");
            const std::string prefix = "Bearer ";
            const bool ok = auth.size() > prefix.size() &&
                            lower(auth.substr(0, prefix.size())) == "bearer " &&
                            constantTimeEquals(auth.substr(prefix.size()), opts.apiKey);
            if (!ok) {
                res.set_header("WWW-Authenticate", "Bearer");
                sendError(res, 401, "Missing or invalid API key. Send 'Authorization: "
                                    "Bearer <key>'.",
                          "authentication_error", "invalid_api_key");
                return HR::Handled;
            }
        }

        // JSON-only POSTs force a CORS preflight on any cross-site attempt.
        if (req.method == "POST") {
            std::string ct = lower(req.get_header_value("Content-Type"));
            if (ct.rfind("application/json", 0) != 0) {
                sendError(res, 415, "Content-Type must be application/json",
                          "invalid_request_error", "unsupported_media_type");
                return HR::Handled;
            }
        }
        return HR::Unhandled;
    }

    // ---- Routes ----

    void setupRoutes() {
        server.set_payload_max_length(opts.maxRequestBytes);
        server.set_pre_routing_handler(
            [this](const httplib::Request& req, httplib::Response& res) { return gate(req, res); });

        server.Post("/v1/chat/completions",
                    [this](const httplib::Request& req, httplib::Response& res) {
                        handleChatCompletions(req, res);
                    });
        server.Get("/v1/models", [this](const httplib::Request&, httplib::Response& res) {
            json data = {{"object", "list"},
                         {"data", json::array({{{"id", modelName()},
                                                {"object", "model"},
                                                {"created", startedAt},
                                                {"owned_by", "gaia"}}})}};
            res.set_content(data.dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
        });
        server.Get("/v1/tools", [this](const httplib::Request&, httplib::Response& res) {
            handleListTools(res);
        });
        if (opts.exposeToolRoutes) {
            server.Post(R"(/v1/tools/([^/]+))",
                        [this](const httplib::Request& req, httplib::Response& res) {
                            handleExecuteTool(req, res);
                        });
        }
        server.Get("/health", [this](const httplib::Request&, httplib::Response& res) {
            json body = {{"status", "ok"},
                         {"version", GAIA_CORE_VERSION},
                         {"model", modelName()},
                         {"tools", static_cast<int>(agent.tools().allTools().size())},
                         {"busy", busy.load()}};
            if (!opts.agentVersion.empty()) body["agent_version"] = opts.agentVersion;
            res.set_content(body.dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
        });
        server.Get("/sessions", [this](const httplib::Request&, httplib::Response& res) {
            handleListSessions(res);
        });
        server.Delete(R"(/sessions/([^/]+))",
                      [this](const httplib::Request& req, httplib::Response& res) {
                          handleDeleteSession(req, res);
                      });
    }

    // ---- POST /v1/chat/completions ----

    void handleChatCompletions(const httplib::Request& req, httplib::Response& res) {
        json body;
        try {
            body = json::parse(req.body);
        } catch (const std::exception& e) {
            sendError(res, 400, "Invalid JSON: " + std::string(e.what()), "invalid_request_error");
            return;
        }
        if (!body.is_object()) {
            sendError(res, 400, "request body must be a JSON object", "invalid_request_error");
            return;
        }
        if (body.contains("tools") && !body["tools"].is_null() && !body["tools"].empty()) {
            sendError(res, 400, "client-side 'tools' are not supported; the agent runs its "
                                "own tools",
                      "invalid_request_error");
            return;
        }

        Conversation conv;
        try {
            conv = parseConversation(body.contains("messages") ? body["messages"] : json(),
                                     opts.rejectSystemMessages);
        } catch (const RequestError& e) {
            sendError(res, e.status, e.what(), "invalid_request_error");
            return;
        }

        if (stopping.load()) {
            sendError(res, 503, "Server is shutting down", "server_error", "shutting_down");
            return;
        }
        bool expected = false;
        if (!busy.compare_exchange_strong(expected, true)) {
            sendBusy(res);
            return;
        }
        auto slot = std::make_shared<BusySlot>(busy);

        const bool stream = body.value("stream", false);
        const bool includeUsage = body.contains("stream_options") &&
                                  body["stream_options"].is_object() &&
                                  body["stream_options"].value("include_usage", false);
        if (stream) {
            startStream(res, std::move(conv), includeUsage, slot);
        } else {
            runNonStreaming(res, std::move(conv));
        }
    }

    void sendBusy(httplib::Response& res) {
        sendError(res, 409, "Agent is busy with another request; this server runs one "
                            "completion at a time. Retry when it finishes.",
                  "conflict", "agent_busy");
    }

    json runAgent(Conversation& conv) {
        agent.setHistory(std::move(conv.history));
        return agent.processQuery(std::vector<Message>{std::move(conv.finalTurn)});
    }

    static json usageOf(const json& result) {
        if (result.contains("usage") && result["usage"].is_object()) return result["usage"];
        return UsageStats{}.toJson();
    }

    void runNonStreaming(httplib::Response& res, Conversation conv) {
        json result;
        try {
            result = runAgent(conv);
        } catch (const std::invalid_argument& e) {
            sendError(res, 400, e.what(), "invalid_request_error");
            return;
        } catch (const std::runtime_error& e) {
            if (isAgentBusyError(e)) { sendBusy(res); return; }
            sendError(res, 500, std::string("Agent error: ") + e.what(), "server_error");
            return;
        } catch (const std::exception& e) {
            sendError(res, 500, std::string("Internal error: ") + e.what(), "server_error");
            return;
        }

        // A run that failed, ran out of steps or was cancelled is not a completion.
        const std::string status = result.value("status", "success");
        if (status != "success") {
            json err = errorJson(result.value("result", ""), "agent_error", status);
            err["gaia"] = {{"status", status},
                           {"steps_taken", result.value("steps_taken", 0)},
                           {"usage", usageOf(result)}};
            res.status = 500;
            res.set_content(err.dump(-1, ' ', false, json::error_handler_t::replace),
                            "application/json");
            return;
        }

        json response = {
            {"id", generateCompletionId()},
            {"object", "chat.completion"},
            {"created", unixTimestamp()},
            {"model", modelName()},
            {"choices", json::array({{{"index", 0},
                                      {"message", {{"role", "assistant"},
                                                   {"content", result.value("result", "")}}},
                                      {"finish_reason", "stop"}}})},
            {"usage", usageOf(result)},
            {"gaia", {{"status", "success"},
                      {"steps_taken", result.value("steps_taken", 0)},
                      {"steps_limit", result.value("steps_limit", 0)}}}};
        res.set_content(response.dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
    }

    void startStream(httplib::Response& res, Conversation conv, bool includeUsage,
                     std::shared_ptr<BusySlot> slot) {
        auto session = std::make_shared<StreamSession>(generateCompletionId(), unixTimestamp(),
                                                       modelName());
        auto convPtr = std::make_shared<Conversation>(std::move(conv));
        res.set_header("Cache-Control", "no-cache");
        res.set_header("X-Accel-Buffering", "no");
        res.set_chunked_content_provider(
            "text/event-stream",
            [this, session, convPtr, includeUsage, slot](size_t, httplib::DataSink& sink) {
                return streamCompletion(sink, *session, *convPtr, includeUsage, session);
            });
    }

    bool streamCompletion(httplib::DataSink& sink, StreamSession& s, Conversation& conv,
                          bool includeUsage, const std::shared_ptr<StreamSession>& sp) {
        auto write = [&sink](const std::string& frame) {
            return sink.is_writable() && sink.write(frame.data(), frame.size());
        };
        bool connected = write(sseFrame(chunkJson(s.id(), s.created(), s.model(),
                                                  {{"role", "assistant"}, {"content", ""}},
                                                  nullptr)));
        if (!connected) return false;

        handler->attach(sp);
        json result;
        std::string error;
        std::string errorCode;
        std::thread worker([&] {
            try {
                result = runAgent(conv);
            } catch (const std::runtime_error& e) {
                error = e.what();
                if (isAgentBusyError(e)) errorCode = "agent_busy";
            } catch (const std::exception& e) {
                error = e.what();
            }
            s.finish();
        });

        for (;;) {
            bool finished = false;
            auto frames = s.take(std::chrono::milliseconds(100), finished);
            for (const auto& f : frames) {
                if (connected && !write(f)) connected = false;
            }
            if (connected && !sink.is_writable()) connected = false;
            // Re-assert every pass: processQuery clears the flag when it starts.
            if (!connected || stopping.load()) agent.requestCancel();
            if (finished) break;
        }
        worker.join();
        handler->detach();

        if (!connected) return false;

        if (!error.empty()) {
            json err = errorJson("Agent error: " + error, "server_error", errorCode);
            write(sseFrame(err));
            sink.done();
            return true;
        }

        const std::string status = result.value("status", "success");
        if (status != "success") {
            json err = errorJson(result.value("result", ""), "agent_error", status);
            err["gaia"] = {{"status", status}, {"steps_taken", result.value("steps_taken", 0)}};
            write(sseFrame(err));
            sink.done();
            return true;
        }

        const std::string rest = s.reconcile(result.value("result", ""));
        if (!rest.empty()) {
            write(sseFrame(chunkJson(s.id(), s.created(), s.model(), {{"content", rest}}, nullptr)));
        }
        write(sseFrame(chunkJson(s.id(), s.created(), s.model(), json::object(), "stop")));
        if (includeUsage) {
            json u = {{"id", s.id()},
                      {"object", "chat.completion.chunk"},
                      {"created", s.created()},
                      {"model", s.model()},
                      {"choices", json::array()},
                      {"usage", usageOf(result)}};
            write(sseFrame(u));
        }
        write("data: [DONE]\n\n");
        sink.done();
        return true;
    }

    // ---- GET /v1/tools ----

    void handleListTools(httplib::Response& res) {
        json tools = json::array();
        for (const auto& [name, info] : agent.tools().allTools()) {
            if (!info.enabled) continue;
            json params = json::array();
            for (const auto& p : info.parameters) {
                params.push_back({{"name", p.name},
                                  {"type", paramTypeToString(p.type)},
                                  {"required", p.required},
                                  {"description", p.description}});
            }
            tools.push_back({{"name", info.name},
                             {"description", info.description},
                             {"parameters", params}});
        }
        res.set_content(json({{"tools", tools}}).dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
    }

    // ---- POST /v1/tools/:name (opt-in) ----

    void handleExecuteTool(const httplib::Request& req, httplib::Response& res) {
        std::string toolName = req.matches[1].str();
        json args = json::object();
        if (!req.body.empty()) {
            try {
                args = json::parse(req.body);
            } catch (const std::exception& e) {
                sendError(res, 400, "Invalid JSON body: " + std::string(e.what()),
                          "invalid_request_error");
                return;
            }
        }
        if (!agent.tools().hasTool(toolName)) {
            std::string resolved = agent.tools().resolveName(toolName);
            if (resolved.empty()) {
                sendError(res, 404, "Tool not found: " + toolName, "not_found");
                return;
            }
            toolName = resolved;
        }
        try {
            json result = agent.toolRegistry().executeTool(toolName, args);
            res.set_content(json({{"tool", toolName}, {"result", result}}).dump(-1, ' ', false, json::error_handler_t::replace),
                            "application/json");
        } catch (const std::exception& e) {
            sendError(res, 500, "Tool execution error: " + std::string(e.what()), "server_error");
        }
    }

    // ---- Sessions ----

    void handleListSessions(httplib::Response& res) {
        json arr = json::array();
        if (sessionStore) {
            for (const auto& s : sessionStore->list()) {
                arr.push_back({{"id", s.id},
                               {"timestamp", s.timestamp},
                               {"preview", s.preview},
                               {"message_count", s.messageCount}});
            }
        }
        res.set_content(arr.dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
    }

    void handleDeleteSession(const httplib::Request& req, httplib::Response& res) {
        std::string sessionId = req.matches[1].str();
        if (!sessionStore) {
            sendError(res, 404, "Session store not configured", "not_found");
            return;
        }
        bool removed = false;
        try {
            removed = sessionStore->remove(sessionId);
        } catch (const std::invalid_argument& e) {
            sendError(res, 400, e.what(), "invalid_request_error");
            return;
        }
        if (!removed) {
            sendError(res, 404, "Session not found: " + sessionId, "not_found");
            return;
        }
        res.set_content(json({{"deleted", true}, {"id", sessionId}}).dump(-1, ' ', false, json::error_handler_t::replace), "application/json");
    }
};

// ---------------------------------------------------------------------------
// Public interface
// ---------------------------------------------------------------------------

ApiServer::ApiServer(Agent& agent, ServerOptions options)
    : impl_(std::make_unique<Impl>(agent, std::move(options))) {
    impl_->setupRoutes();
}

ApiServer::ApiServer(Agent& agent, int port)
    : ApiServer(agent, [port] {
          ServerOptions o;
          o.port = port;
          return o;
      }()) {}

ApiServer::~ApiServer() {
    stop();
}

void ApiServer::setSessionStore(std::shared_ptr<SessionStore> store) {
    impl_->sessionStore = std::move(store);
}

void ApiServer::run() {
    const auto& o = impl_->opts;
    if (!isLoopbackHost(o.host) && o.apiKey.empty()) {
        throw std::runtime_error(
            "ApiServer refuses to bind non-loopback host '" + o.host +
            "' without an API key: anyone on the network could run the agent's tools. "
            "Set ServerOptions::apiKey or bind 127.0.0.1.");
    }

    int port = o.port;
    if (port == 0) {
        port = impl_->server.bind_to_any_port(o.host);
        if (port <= 0) {
            throw std::runtime_error("ApiServer failed to bind any port on " + o.host);
        }
    } else if (!impl_->server.bind_to_port(o.host, port)) {
        throw std::runtime_error("ApiServer failed to bind " + o.host + ":" +
                                 std::to_string(port) +
                                 ". Check that the port is free or pick another with --port.");
    }
    impl_->boundPort = port;
    if (impl_->stopping.load()) return;

    std::cerr << "[ApiServer] Listening on http://" << o.host << ":" << port
              << (o.apiKey.empty() ? " (no API key)" : " (API key required)")
              << (o.exposeToolRoutes ? ", direct tool routes ON" : "") << std::endl;

    if (!impl_->server.listen_after_bind()) {
        if (impl_->stopping.load()) return;
        throw std::runtime_error("ApiServer stopped unexpectedly on " + o.host + ":" +
                                 std::to_string(port));
    }
}

void ApiServer::stop() {
    impl_->stopping.store(true);
    if (impl_->busy.load()) impl_->agent.requestCancel();
    impl_->server.stop();
}

bool ApiServer::isRunning() const {
    return impl_->server.is_running();
}

int ApiServer::port() const {
    return impl_->boundPort.load();
}

const char* ApiServer::version() {
    return GAIA_CORE_VERSION;
}

} // namespace gaia
