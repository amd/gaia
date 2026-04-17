// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// In-process mock HTTP server mimicking the Lemonade Server API.
// Used by benchmarks to avoid requiring a real LLM backend.

#pragma once

#include <atomic>
#include <chrono>
#include <deque>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>

#include <httplib.h>

namespace bench {

// Default chat completion response — agent returns a final answer immediately.
static const std::string kDefaultAnswer = R"({"choices":[{"message":{"content":"{\"thought\":\"done\",\"goal\":\"complete\",\"answer\":\"benchmark result\"}"}}]})";

// Tool-call response — agent calls the echo tool first.
static const std::string kToolCall = R"({"choices":[{"message":{"content":"{\"thought\":\"calling tool\",\"goal\":\"test\",\"tool\":\"echo\",\"tool_args\":{\"message\":\"bench\"}}"}}]})";

// Health response — reports mock-model as already loaded so ensureModelLoaded() skips /load.
static const std::string kHealthOk = R"({"status":"ok","all_models_loaded":[{"model_name":"mock-model","recipe_options":{"ctx_size":16384}}]})";

// Models list response
static const std::string kModelsList = R"({"data":[{"id":"mock-model"}]})";

// Load response
static const std::string kLoadOk = R"({"status":"ok"})";

class MockLlmServer {
public:
    /// Start server on an OS-assigned port.
    /// Constructor blocks until the server is accepting connections.
    MockLlmServer() : server_(std::make_unique<httplib::Server>()) {
        registerHandlers();

        // bind_to_any_port returns the OS-assigned port (avoids CI port conflicts)
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

    // Non-copyable, non-movable
    MockLlmServer(const MockLlmServer&) = delete;
    MockLlmServer& operator=(const MockLlmServer&) = delete;

    /// The port the server is listening on.
    int port() const { return port_; }

    /// Base URL suitable for AgentConfig::baseUrl (without /api/v1 — LemonadeClient adds it).
    std::string baseUrl() const { return "http://127.0.0.1:" + std::to_string(port_); }

    /// Push a response to return for the next POST /chat/completions call.
    /// When the queue is empty the default answer response is returned.
    void pushResponse(const std::string& body) {
        std::lock_guard<std::mutex> lk(mu_);
        responseQueue_.push_back(body);
    }

    /// Push N copies of a response.
    void pushResponses(const std::string& body, int n) {
        std::lock_guard<std::mutex> lk(mu_);
        for (int i = 0; i < n; ++i) {
            responseQueue_.push_back(body);
        }
    }

    /// Clear pending queued responses.
    void clearQueue() {
        std::lock_guard<std::mutex> lk(mu_);
        responseQueue_.clear();
    }

    /// Number of chat completion requests handled so far.
    int requestCount() const { return requestCount_.load(); }

private:
    void registerHandlers() {
        // Health check — always reports mock-model loaded
        server_->Get("/api/v1/health", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(kHealthOk, "application/json");
        });

        // Load model — no-op safety fallback
        server_->Post("/api/v1/load", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(kLoadOk, "application/json");
        });

        // Models list
        server_->Get("/api/v1/models", [](const httplib::Request&, httplib::Response& res) {
            res.set_content(kModelsList, "application/json");
        });

        // Chat completions — dequeue a pre-loaded response or return default answer
        server_->Post("/api/v1/chat/completions",
                      [this](const httplib::Request&, httplib::Response& res) {
                          ++requestCount_;
                          std::string body;
                          {
                              std::lock_guard<std::mutex> lk(mu_);
                              if (!responseQueue_.empty()) {
                                  body = responseQueue_.front();
                                  responseQueue_.pop_front();
                              } else {
                                  body = kDefaultAnswer;
                              }
                          }
                          res.set_content(body, "application/json");
                      });
    }

    void waitUntilReady() {
        // Poll health endpoint until the server responds
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
    std::mutex mu_;
    std::deque<std::string> responseQueue_;
    std::atomic<int> requestCount_{0};
};

} // namespace bench
