// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// General-purpose HTTP client for GAIA C++ tools and agents.
//
// The transport (cpp-httplib) is a PRIVATE dependency compiled into gaia_core:
// this header must never include it, so consumers do not pay for a 10k-line
// header in every translation unit. All transport state lives behind a pimpl.

#pragma once

#include <cstddef>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>

#include "gaia/export.h"

namespace gaia {

/// Request or response header map. Keys are matched case-insensitively when
/// merging and when reading (see HttpResponse::header()), and are sent with the
/// casing you supplied. Repeated response fields are comma-joined, so a
/// multi-value `Set-Cookie` is not round-trippable through this type.
using HttpHeaders = std::map<std::string, std::string>;

/// Streaming body callback. Return false to stop reading — that is treated as
/// normal completion, not an error (e.g. an SSE `[DONE]` sentinel).
using HttpChunkCallback = std::function<bool(const char* data, std::size_t len)>;

// ---- Response ----

struct GAIA_API HttpResponse {
    int         status = 0;
    std::string body;
    HttpHeaders headers;

    bool ok() const { return status >= 200 && status < 300; }

    /// Case-insensitive header lookup. Returns `fallback` when absent.
    std::string header(const std::string& name, const std::string& fallback = "") const;
};

// ---- Error ----

/// Thrown for every HTTP failure: connection refused, timeout, TLS
/// unavailable, or a non-2xx status. There is no silent fallback — the client
/// never returns an empty or default response when a request fails.
///
/// what() names the method, the full URL, and the failure mode.
class GAIA_API HttpError : public std::runtime_error {
public:
    HttpError(const std::string& message, std::string url, int status = 0,
              std::string body = "");

    /// Full URL of the failed request (scheme, host, port, path).
    const std::string& url() const noexcept { return url_; }

    /// HTTP status, or 0 when the request failed before a response arrived.
    int status() const noexcept { return status_; }

    /// Response body for a non-2xx status; empty otherwise. Streamed responses
    /// buffer at most 512 bytes of it.
    const std::string& body() const noexcept { return body_; }

private:
    std::string url_;
    int         status_;
    std::string body_;
};

// ---- Config ----

struct GAIA_API HttpClientConfig {
    /// Base URL prepended to relative request paths, e.g.
    /// "http://localhost:13305/api/v1". Joined to the path with exactly one
    /// '/'; otherwise used verbatim (no /api/v1-style normalization).
    std::string baseUrl;

    /// Read timeout in seconds, must be > 0 (per-request override available).
    int timeoutSec = 30;

    /// Connection timeout in seconds, must be > 0 (per-request override available).
    int connectTimeoutSec = 30;

    /// Sent on every request; per-request headers of the same name win
    /// (matched case-insensitively).
    HttpHeaders defaultHeaders;

    /// https only: verify the server certificate.
    bool verifyTls = true;

    /// https only: custom CA bundle path (empty → system trust store).
    std::string caCertPath;

    /// Log method and URL to stderr.
    bool debug = false;
};

// ---- Client ----

/// Blocking HTTP/HTTPS client.
///
///   HttpClient http({"https://api.example.com"});
///   HttpResponse r = http.get("/v1/models", {{"Authorization", "Bearer …"}});
///
/// A request `path` may also be an absolute URL ("http://…" / "https://…"),
/// in which case the configured base URL is ignored.
///
/// HTTPS requires an OpenSSL-enabled build (auto-detected by CMake); an https
/// URL on an HTTP-only build throws HttpError rather than downgrading.
///
/// Not thread-safe: use one instance per thread.
class GAIA_API HttpClient {
public:
    /// @throws std::invalid_argument if a configured timeout is not positive
    explicit HttpClient(const HttpClientConfig& config = {});

    /// Convenience constructor — base URL with default timeouts.
    explicit HttpClient(const std::string& baseUrl);

    ~HttpClient();

    HttpClient(const HttpClient&)            = delete;
    HttpClient& operator=(const HttpClient&) = delete;
    HttpClient(HttpClient&&) noexcept;
    HttpClient& operator=(HttpClient&&) noexcept;

    /// GET `path`.
    /// @param timeoutSec         Read timeout override (0 → config value)
    /// @param connectTimeoutSec  Connect timeout override (0 → config value)
    /// @throws HttpError on connection failure, timeout, or non-2xx status
    HttpResponse get(const std::string& path, const HttpHeaders& headers = {},
                     int timeoutSec = 0, int connectTimeoutSec = 0);

    /// POST `body` to `path`. Content-Type defaults to "application/json"
    /// unless `headers` supplies one.
    /// @throws HttpError on connection failure, timeout, or non-2xx status
    HttpResponse post(const std::string& path, const std::string& body,
                      const HttpHeaders& headers = {}, int timeoutSec = 0,
                      int connectTimeoutSec = 0);

    /// POST `body` to `path` and hand each response chunk to `onChunk` as it
    /// arrives (SSE and other streamed responses).
    ///
    /// The returned HttpResponse carries the status and headers; its body is
    /// empty because the payload went to the callback. `onChunk` returning
    /// false stops the read and completes normally. On a non-2xx status the
    /// error body is buffered and reported through HttpError instead of being
    /// passed to `onChunk`.
    ///
    /// @throws HttpError on connection failure, timeout, or non-2xx status
    HttpResponse postStreaming(const std::string& path, const std::string& body,
                               HttpChunkCallback onChunk,
                               const HttpHeaders& headers = {}, int timeoutSec = 0,
                               int connectTimeoutSec = 0);

    const std::string& baseUrl() const;
    void setBaseUrl(const std::string& url);

    /// Add or replace a header sent on every request.
    void setDefaultHeader(const std::string& name, const std::string& value);

    bool debug() const;
    void setDebug(bool enabled);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace gaia
