// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// In-process mock HTTP server mimicking the Lemonade Server API.
// Used by unit tests (VLM / agent) AND benchmarks. Lifted from
// cpp/benchmarks/mock_llm_server.h — the benchmarks now include from here.

#pragma once

#include <atomic>
#include <chrono>
#include <deque>
#include <future>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include <httplib.h>

namespace bench {

// Default chat completion response — agent returns a final answer immediately.
static const std::string kDefaultAnswer = R"({"choices":[{"message":{"content":"{\"thought\":\"done\",\"goal\":\"complete\",\"answer\":\"benchmark result\"}"}}]})";

// Tool-call response — agent calls the echo tool first.
static const std::string kToolCall = R"({"choices":[{"message":{"content":"{\"thought\":\"calling tool\",\"goal\":\"test\",\"tool\":\"echo\",\"tool_args\":{\"message\":\"bench\"}}"}}]})";

// Health response — reports mock-model as already loaded so ensureModelLoaded() skips /load.
static const std::string kHealthOk = R"({"status":"ok","all_models_loaded":[{"model_name":"mock-model","recipe_options":{"ctx_size":16384}}]})";

// Health response reporting *no* model loaded — forces ensureModelLoaded() to POST /load.
static const std::string kHealthNoModel = R"({"status":"ok","all_models_loaded":[]})";

// Models list response
static const std::string kModelsList = R"({"data":[{"id":"mock-model"}]})";

// Load response
static const std::string kLoadOk = R"({"status":"ok"})";

class MockLlmServer {
public:
    /// Start server on an OS-assigned port.
    /// Constructor blocks until the server is accepting connections.
    /// @param reportModelLoaded When true, /health claims the model is
    ///   already loaded (skipping /load); when false, forces a /load POST.
    explicit MockLlmServer(bool reportModelLoaded = true)
        : server_(std::make_unique<httplib::Server>()),
          reportModelLoaded_(reportModelLoaded) {
        registerHandlers();

        port_ = server_->bind_to_any_port("127.0.0.1");
        if (port_ <= 0) {
            throw std::runtime_error("MockLlmServer: failed to bind to any port");
        }

        thread_ = std::thread([this]() { server_->listen_after_bind(); });

        waitUntilReady();
    }

    ~MockLlmServer() {
        server_->stop();
        if (thread_.joinable()) {
            thread_.join();
        }
    }

    MockLlmServer(const MockLlmServer&) = delete;
    MockLlmServer& operator=(const MockLlmServer&) = delete;

    int port() const { return port_; }
    std::string baseUrl() const { return "http://127.0.0.1:" + std::to_string(port_); }

    /// Push a chat-completion response body for the next request.
    /// Overload 1: legacy string body, status 200.
    void pushResponse(const std::string& body) {
        std::lock_guard<std::mutex> lk(mu_);
        QueuedResponse qr; qr.body = body; qr.status = 200; responseQueue_.push_back(std::move(qr));
    }
    /// Overload 2: explicit status code.
    void pushResponse(const std::string& body, int status) {
        std::lock_guard<std::mutex> lk(mu_);
        QueuedResponse qr; qr.body = body; qr.status = status; responseQueue_.push_back(std::move(qr));
    }

    void pushResponses(const std::string& body, int n) {
        std::lock_guard<std::mutex> lk(mu_);
        for (int i = 0; i < n; ++i) {
            QueuedResponse qr; qr.body = body; qr.status = 200; responseQueue_.push_back(std::move(qr));
        }
    }

    /// Hold the next /chat/completions response until `release` is ready.
    /// Used to deterministically test concurrent entry: the first call
    /// blocks inside the handler while the second thread attempts entry.
    /// The handler consumes this hold once and returns the default answer.
    void holdNextResponse(std::shared_future<void> release) {
        std::lock_guard<std::mutex> lk(mu_);
        QueuedResponse qr;
        qr.body = kDefaultAnswer;
        qr.status = 200;
        qr.releaseHold = std::move(release);
        responseQueue_.push_back(std::move(qr));
    }

    /// Serve /api/v1/chat/completions as a real SSE stream when the request
    /// body asks for one. Off by default so non-streaming tests are unaffected.
    void enableSseStreaming(bool enabled) { sseStreaming_ = enabled; }

    void clearQueue() {
        std::lock_guard<std::mutex> lk(mu_);
        responseQueue_.clear();
    }

    int requestCount() const { return requestCount_.load(); }

    /// Number of POSTs to /api/v1/load seen so far.
    int loadRequestCount() const { return loadCount_.load(); }

    /// All captured request bodies posted to /api/v1/chat/completions, in order.
    std::vector<std::string> receivedBodies() const {
        std::lock_guard<std::mutex> lk(mu_);
        return receivedBodies_;
    }

private:
    struct QueuedResponse {
        std::string body;
        int status = 200;
        std::shared_future<void> releaseHold; // when valid(), handler waits
    };

    void registerHandlers() {
        server_->Get("/api/v1/health", [this](const httplib::Request&, httplib::Response& res) {
            res.set_content(reportModelLoaded_ ? kHealthOk : kHealthNoModel, "application/json");
        });

        server_->Post("/api/v1/load", [this](const httplib::Request&, httplib::Response& res) {
            ++loadCount_;
            res.set_content(kLoadOk, "application/json");
        });

        server_->Get("/api/v1/models", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(kModelsList, "application/json");
        });

        server_->Post("/api/v1/chat/completions",
                      [this](const httplib::Request& req, httplib::Response& res) {
                          // Store the body BEFORE incrementing requestCount_ so
                          // that any observer polling requestCount() >= N is
                          // guaranteed to find the corresponding body already
                          // present in receivedBodies().
                          {
                              std::lock_guard<std::mutex> lk(mu_);
                              receivedBodies_.push_back(req.body);
                          }
                          ++requestCount_;

                          if (sseStreaming_ &&
                              req.body.find("\"stream\":true") != std::string::npos) {
                              sendSseChatStream(res);
                              return;
                          }

                          QueuedResponse qr;
                          {
                              std::lock_guard<std::mutex> lk(mu_);
                              if (!responseQueue_.empty()) {
                                  qr = std::move(responseQueue_.front());
                                  responseQueue_.pop_front();
                              } else {
                                  qr.body = kDefaultAnswer;
                                  qr.status = 200;
                              }
                          }
                          if (qr.releaseHold.valid()) {
                              qr.releaseHold.wait();
                          }
                          res.status = qr.status;
                          res.set_content(qr.body, "application/json");
                      });

        registerGenericHttpRoutes();
    }

    /// OpenAI-style token stream terminated by the [DONE] sentinel.
    static void sendSseChatStream(httplib::Response& res) {
        auto index = std::make_shared<size_t>(0);
        res.set_chunked_content_provider(
            "text/event-stream", [index](size_t /*offset*/, httplib::DataSink& sink) {
                static const std::vector<std::string> events = {
                    R"(data: {"choices":[{"delta":{"content":"Hello"}}]})"
                    "\n\n",
                    R"(data: {"choices":[{"delta":{"content":" world"}}]})"
                    "\n\n",
                    "data: [DONE]\n\n"};
                if (*index < events.size()) {
                    const std::string& event = events[(*index)++];
                    sink.write(event.data(), event.size());
                } else {
                    sink.done();
                }
                return true;
            });
    }

    /// Transport-level routes under /test/… used by the gaia::HttpClient tests.
    /// They carry no Lemonade semantics — they exercise headers, bodies,
    /// streaming, slow responses, and error statuses.
    void registerGenericHttpRoutes() {
        // Echoes the X-Gaia-Test request header back as the body.
        server_->Get("/test/echo", [](const httplib::Request& req, httplib::Response& res) {
            res.set_header("X-Gaia-Echo", "pong");
            res.set_content(req.get_header_value("X-Gaia-Test"), "text/plain");
        });

        // Echoes the request body back; reports the received Content-Type.
        server_->Post("/test/echo", [](const httplib::Request& req, httplib::Response& res) {
            res.set_header("X-Gaia-Content-Type", req.get_header_value("Content-Type"));
            res.set_header("X-Gaia-Test-Seen", req.get_header_value("X-Gaia-Test"));
            res.set_content(req.body, "text/plain");
        });

        // Responds after a delay — drives read-timeout tests.
        server_->Get("/test/slow", [](const httplib::Request&, httplib::Response& res) {
            std::this_thread::sleep_for(std::chrono::seconds(2));
            res.set_content("late", "text/plain");
        });

        // Non-2xx with a body — drives error-status tests.
        server_->Get("/test/boom", [](const httplib::Request&, httplib::Response& res) {
            res.status = 500;
            res.set_content("boom: upstream exploded", "text/plain");
        });

        // Chunked SSE-shaped stream ending in a [DONE] sentinel.
        server_->Post("/test/stream", [](const httplib::Request&, httplib::Response& res) {
            auto index = std::make_shared<size_t>(0);
            res.set_chunked_content_provider(
                "text/event-stream",
                [index](size_t /*offset*/, httplib::DataSink& sink) {
                    static const std::vector<std::string> chunks = {
                        "data: alpha\n\n", "data: beta\n\n", "data: [DONE]\n\n"};
                    if (*index < chunks.size()) {
                        const std::string& chunk = chunks[(*index)++];
                        sink.write(chunk.data(), chunk.size());
                    } else {
                        sink.done();
                    }
                    return true;
                });
        });

        // Streaming request that fails before any stream body is produced.
        server_->Post("/test/stream-error",
                      [](const httplib::Request&, httplib::Response& res) {
                          res.status = 503;
                          res.set_content("stream unavailable", "text/plain");
                      });
    }

    void waitUntilReady() {
        httplib::Client cli("127.0.0.1", port_);
        cli.set_connection_timeout(1);
        cli.set_read_timeout(1);

        for (int attempt = 0; attempt < 50; ++attempt) {
            auto res = cli.Get("/api/v1/health");
            if (res && res->status == 200) {
                return;
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(20));
        }
        throw std::runtime_error("MockLlmServer: server did not become ready");
    }

    std::unique_ptr<httplib::Server> server_;
    std::thread thread_;
    int port_ = 0;
    bool reportModelLoaded_ = true;
    mutable std::mutex mu_;
    std::deque<QueuedResponse> responseQueue_;
    std::vector<std::string> receivedBodies_;
    std::atomic<int> requestCount_{0};
    std::atomic<int> loadCount_{0};
    std::atomic<bool> sseStreaming_{false};
};

} // namespace bench
