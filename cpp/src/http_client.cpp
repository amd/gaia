// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/http_client.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <iostream>
#include <utility>

#include <httplib.h>

namespace gaia {
namespace {

constexpr std::size_t kMaxErrorBodyChars = 512;

std::string toLower(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

/// Cut at a UTF-8 codepoint boundary — error text ends up in JSON payloads,
/// and nlohmann::json::dump() throws on invalid UTF-8.
std::size_t utf8SafeCut(const std::string& s, std::size_t limit) {
    std::size_t cut = std::min(limit, s.size());
    while (cut > 0 && (static_cast<unsigned char>(s[cut]) & 0xC0) == 0x80) --cut;
    return cut;
}

std::string truncateForError(const std::string& body) {
    if (body.size() <= kMaxErrorBodyChars) return body;
    return body.substr(0, utf8SafeCut(body, kMaxErrorBodyChars)) + "… (truncated)";
}

/// Reject header fields that would let a caller inject extra request lines.
void validateHeaderField(const std::string& name, const std::string& value,
                         const std::string& url) {
    const bool badName =
        name.empty() || name.find_first_of("\r\n:\0 \t", 0, 6) != std::string::npos;
    const bool badValue = value.find_first_of("\r\n\0", 0, 3) != std::string::npos;
    if (badName || badValue) {
        throw HttpError("Illegal HTTP header '" + name +
                            "' for " + url +
                            ": header names must be non-empty and free of ':', "
                            "whitespace and control characters, and values must not "
                            "contain CR, LF or NUL.",
                        url);
    }
}

/// Parse a port, rejecting anything that is not a full number in range —
/// std::stoi alone would silently accept "8080abc" as 8080.
int parsePort(const std::string& token, const std::string& url) {
    const bool allDigits =
        !token.empty() && token.find_first_not_of("0123456789") == std::string::npos;
    long port = 0;
    if (allDigits) {
        try {
            port = std::stol(token);
        } catch (const std::exception&) {
            port = 0;
        }
    }
    if (!allDigits || port < 1 || port > 65535) {
        throw HttpError("Invalid port '" + token + "' in URL: " + url +
                            ". Expected a number in 1-65535.",
                        url);
    }
    return static_cast<int>(port);
}

/// Human-readable failure mode for a transport-level error.
std::string describeError(httplib::Error err, int connectTimeoutSec, int readTimeoutSec) {
    switch (err) {
        case httplib::Error::Connection:
        case httplib::Error::ConnectionTimeout:
            return "could not connect (connection refused, host unreachable, or no "
                   "response within the " +
                   std::to_string(connectTimeoutSec) + "s connect timeout)";
        case httplib::Error::Read:
            return "no complete response within the " + std::to_string(readTimeoutSec) +
                   "s read timeout (server too slow or connection dropped)";
        case httplib::Error::Write:
            return "the request could not be sent (connection closed while writing)";
        case httplib::Error::SSLConnection:
            return "TLS handshake failed";
        case httplib::Error::SSLLoadingCerts:
            return "TLS certificates could not be loaded (set HttpClientConfig::caCertPath)";
        case httplib::Error::SSLServerVerification:
            return "TLS server certificate verification failed (set "
                   "HttpClientConfig::caCertPath, or verifyTls=false for a "
                   "self-signed test server)";
        case httplib::Error::Canceled:
            return "the transfer was canceled";
        default:
            return std::string("transport error: ") + httplib::to_string(err);
    }
}

template <typename ClientT>
void applyTimeouts(ClientT& cli, int connectTimeoutSec, int readTimeoutSec) {
    cli.set_connection_timeout(connectTimeoutSec);
    cli.set_read_timeout(readTimeoutSec);
}

/// Case-insensitive key lookup in an HttpHeaders map.
HttpHeaders::iterator findHeader(HttpHeaders& headers, const std::string& name) {
    const std::string wanted = toLower(name);
    for (auto it = headers.begin(); it != headers.end(); ++it) {
        if (toLower(it->first) == wanted) return it;
    }
    return headers.end();
}

HttpHeaders convertHeaders(const httplib::Headers& in) {
    HttpHeaders out;
    for (const auto& kv : in) {
        auto it = findHeader(out, kv.first);
        if (it == out.end()) {
            out.emplace(kv.first, kv.second);
        } else {
            // Repeated field lines collapse to a comma-separated list (RFC 9110 §5.3).
            it->second += ", " + kv.second;
        }
    }
    return out;
}

} // namespace

// ---------------------------------------------------------------------------
// HttpResponse / HttpError
// ---------------------------------------------------------------------------

std::string HttpResponse::header(const std::string& name, const std::string& fallback) const {
    const std::string wanted = toLower(name);
    for (const auto& kv : headers) {
        if (toLower(kv.first) == wanted) return kv.second;
    }
    return fallback;
}

HttpError::HttpError(const std::string& message, std::string url, int status, std::string body)
    : std::runtime_error(message),
      url_(std::move(url)),
      status_(status),
      body_(std::move(body)) {}

// ---------------------------------------------------------------------------
// Impl
// ---------------------------------------------------------------------------

struct HttpClient::Impl {
    HttpClientConfig config;

    /// A resolved request target: which server to open and what to ask for.
    struct Target {
        std::string host;
        int         port = 80;
        bool        useSSL = false;
        std::string path;   // path sent on the wire
        std::string url;    // full URL, for error messages and logging
    };

    explicit Impl(HttpClientConfig cfg) : config(std::move(cfg)) {}

    Target resolve(const std::string& path) const {
        const bool absolute = path.compare(0, 7, "http://") == 0 ||
                              path.compare(0, 8, "https://") == 0;
        const std::string source = absolute ? path : config.baseUrl;

        Target t;
        std::string rest = source;
        std::string scheme = "http://";
        if (rest.compare(0, 8, "https://") == 0) {
            t.useSSL = true;
            t.port   = 443;
            scheme   = "https://";
            rest     = rest.substr(8);
        } else if (rest.compare(0, 7, "http://") == 0) {
            rest = rest.substr(7);
        }

        std::string basePath;
        const auto slashPos = rest.find('/');
        std::string authority;
        if (slashPos != std::string::npos) {
            authority = rest.substr(0, slashPos);
            basePath  = rest.substr(slashPos);
        } else {
            authority = rest;
        }

        // An IPv6 literal is bracketed, so only a colon after ']' is the port.
        const auto hostEnd  = authority.rfind(']');
        const auto colonPos = authority.find(':', hostEnd == std::string::npos ? 0 : hostEnd);
        if (colonPos != std::string::npos) {
            t.host = authority.substr(0, colonPos);
            t.port = parsePort(authority.substr(colonPos + 1), source);
        } else {
            t.host = authority;
        }
        if (t.host.size() >= 2 && t.host.front() == '[' && t.host.back() == ']') {
            t.host = t.host.substr(1, t.host.size() - 2);
        }

        if (t.host.empty()) {
            throw HttpError(
                "No host in URL: '" + source +
                    "'. Set a base URL such as \"http://localhost:8000\" "
                    "(HttpClientConfig::baseUrl, or LEMONADE_BASE_URL / "
                    "LemonadeClientConfig::baseUrl for LemonadeClient), or pass an "
                    "absolute URL as the request path.",
                source);
        }

        if (absolute) {
            t.path = basePath;
        } else {
            // Join base path and request path with exactly one separator.
            while (!basePath.empty() && basePath.back() == '/') basePath.pop_back();
            std::string suffix = path;
            if (!suffix.empty() && suffix.front() != '/') suffix.insert(suffix.begin(), '/');
            t.path = basePath + suffix;
        }
        if (t.path.empty()) t.path = "/";

        // A control character or space in the target would split the request line.
        for (const char c : t.path) {
            if (static_cast<unsigned char>(c) <= 0x20 || static_cast<unsigned char>(c) == 0x7F) {
                throw HttpError("Illegal character in request path '" + t.path +
                                    "': spaces and control characters must be "
                                    "percent-encoded.",
                                source);
            }
        }

        const std::string displayHost =
            t.host.find(':') != std::string::npos ? "[" + t.host + "]" : t.host;
        t.url = scheme + displayHost + ":" + std::to_string(t.port) + t.path;
        return t;
    }

    /// Merge default headers with per-request headers (request wins) and pull
    /// out Content-Type, which httplib takes as a separate argument.
    httplib::Headers mergeHeaders(const HttpHeaders& requestHeaders, const std::string& url,
                                  std::string* contentTypeOut) const {
        // Header names are case-insensitive, so a request "Authorization" must
        // replace a default "authorization" instead of joining it on the wire.
        HttpHeaders merged = config.defaultHeaders;
        for (const auto& kv : requestHeaders) {
            auto existing = findHeader(merged, kv.first);
            if (existing != merged.end()) merged.erase(existing);
            merged.emplace(kv.first, kv.second);
        }

        if (contentTypeOut != nullptr) {
            auto ct = findHeader(merged, "content-type");
            if (ct != merged.end()) {
                validateHeaderField(ct->first, ct->second, url);
                *contentTypeOut = ct->second;
                merged.erase(ct); // httplib takes Content-Type as its own argument
            }
        }

        httplib::Headers out;
        for (const auto& kv : merged) {
            validateHeaderField(kv.first, kv.second, url);
            out.emplace(kv.first, kv.second);
        }
        return out;
    }

    int readTimeout(int override) const {
        return override > 0 ? override : config.timeoutSec;
    }
    int connectTimeout(int override) const {
        return override > 0 ? override : config.connectTimeoutSec;
    }

    void logRequest(const char* method, const Target& t) const {
        if (config.debug) {
            std::cerr << "[HttpClient] " << method << " " << t.url << std::endl;
        }
    }

    /// Turn an httplib Result into an HttpResponse, or throw HttpError.
    HttpResponse finish(const char* method, const Target& t, httplib::Result& result,
                        int connectTimeoutSec, int readTimeoutSec) const {
        if (!result) {
            throw HttpError(std::string(method) + " " + t.url + " failed: " +
                                describeError(result.error(), connectTimeoutSec,
                                              readTimeoutSec) +
                                ". Check that the service is running and that the URL "
                                "is correct.",
                            t.url);
        }

        HttpResponse response;
        response.status  = result->status;
        response.body    = result->body;
        response.headers = convertHeaders(result->headers);

        if (!response.ok()) {
            throw HttpError(std::string(method) + " " + t.url + " returned HTTP " +
                                std::to_string(response.status) +
                                (response.body.empty()
                                     ? std::string(" with an empty body")
                                     : ": " + truncateForError(response.body)),
                            t.url, response.status, response.body);
        }
        return response;
    }

    [[noreturn]] void throwTlsUnavailable(const Target& t) const {
        throw HttpError("Cannot request " + t.url +
                            ": this build has no TLS support. Rebuild gaia_core with "
                            "OpenSSL available (CMake auto-detects it) or use an "
                            "http:// URL.",
                        t.url);
    }
};

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

HttpClient::HttpClient(const HttpClientConfig& config)
    : impl_(std::make_unique<Impl>(config)) {
    if (config.timeoutSec <= 0 || config.connectTimeoutSec <= 0) {
        throw std::invalid_argument(
            "HttpClientConfig timeouts must be > 0 seconds (got timeoutSec=" +
            std::to_string(config.timeoutSec) + ", connectTimeoutSec=" +
            std::to_string(config.connectTimeoutSec) + ")");
    }
}

HttpClient::HttpClient(const std::string& baseUrl)
    : HttpClient([&] {
          HttpClientConfig cfg;
          cfg.baseUrl = baseUrl;
          return cfg;
      }()) {}

HttpClient::~HttpClient() = default;
// Re-arm the moved-from client: every method dereferences impl_, and
// LemonadeClient's defaulted move would otherwise leave a live null.
HttpClient::HttpClient(HttpClient&& other) noexcept
    : impl_(std::move(other.impl_)) {
    other.impl_ = std::unique_ptr<Impl>(new Impl(HttpClientConfig{}));
}

HttpClient& HttpClient::operator=(HttpClient&& other) noexcept {
    if (this != &other) {
        impl_       = std::move(other.impl_);
        other.impl_ = std::unique_ptr<Impl>(new Impl(HttpClientConfig{}));
    }
    return *this;
}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const std::string& HttpClient::baseUrl() const { return impl_->config.baseUrl; }

void HttpClient::setBaseUrl(const std::string& url) { impl_->config.baseUrl = url; }

void HttpClient::setDefaultHeader(const std::string& name, const std::string& value) {
    auto& headers = impl_->config.defaultHeaders;
    auto existing = findHeader(headers, name);
    if (existing != headers.end()) headers.erase(existing);
    headers.emplace(name, value);
}

bool HttpClient::debug() const { return impl_->config.debug; }

void HttpClient::setDebug(bool enabled) { impl_->config.debug = enabled; }

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

HttpResponse HttpClient::get(const std::string& path, const HttpHeaders& headers,
                             int timeoutSec, int connectTimeoutSec) {
    const Impl::Target t = impl_->resolve(path);
    const int readSec    = impl_->readTimeout(timeoutSec);
    const int connectSec = impl_->connectTimeout(connectTimeoutSec);
    const httplib::Headers hdrs = impl_->mergeHeaders(headers, t.url, nullptr);
    impl_->logRequest("GET", t);

    httplib::Result result;
    if (t.useSSL) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
        httplib::SSLClient cli(t.host, t.port);
        cli.enable_server_certificate_verification(impl_->config.verifyTls);
        if (!impl_->config.caCertPath.empty()) {
            cli.set_ca_cert_path(impl_->config.caCertPath);
        }
        applyTimeouts(cli, connectSec, readSec);
        result = cli.Get(t.path, hdrs);
#else
        impl_->throwTlsUnavailable(t);
#endif
    } else {
        httplib::Client cli(t.host, t.port);
        applyTimeouts(cli, connectSec, readSec);
        result = cli.Get(t.path, hdrs);
    }
    return impl_->finish("GET", t, result, connectSec, readSec);
}

HttpResponse HttpClient::post(const std::string& path, const std::string& body,
                              const HttpHeaders& headers, int timeoutSec,
                              int connectTimeoutSec) {
    const Impl::Target t = impl_->resolve(path);
    const int readSec    = impl_->readTimeout(timeoutSec);
    const int connectSec = impl_->connectTimeout(connectTimeoutSec);
    std::string contentType = "application/json";
    const httplib::Headers hdrs = impl_->mergeHeaders(headers, t.url, &contentType);
    impl_->logRequest("POST", t);

    httplib::Result result;
    if (t.useSSL) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
        httplib::SSLClient cli(t.host, t.port);
        cli.enable_server_certificate_verification(impl_->config.verifyTls);
        if (!impl_->config.caCertPath.empty()) {
            cli.set_ca_cert_path(impl_->config.caCertPath);
        }
        applyTimeouts(cli, connectSec, readSec);
        result = cli.Post(t.path, hdrs, body, contentType);
#else
        impl_->throwTlsUnavailable(t);
#endif
    } else {
        httplib::Client cli(t.host, t.port);
        applyTimeouts(cli, connectSec, readSec);
        result = cli.Post(t.path, hdrs, body, contentType);
    }
    return impl_->finish("POST", t, result, connectSec, readSec);
}

HttpResponse HttpClient::postStreaming(const std::string& path, const std::string& body,
                                       HttpChunkCallback onChunk,
                                       const HttpHeaders& headers, int timeoutSec,
                                       int connectTimeoutSec) {
    const Impl::Target t = impl_->resolve(path);
    if (!onChunk) {
        throw HttpError("postStreaming to " + t.url +
                            " requires a chunk callback (got an empty std::function).",
                        t.url);
    }

    const int readSec    = impl_->readTimeout(timeoutSec);
    const int connectSec = impl_->connectTimeout(connectTimeoutSec);
    std::string contentType = "application/json";
    const httplib::Headers hdrs = impl_->mergeHeaders(headers, t.url, &contentType);
    impl_->logRequest("POST (stream)", t);

    int         status       = 0;
    bool        stoppedByCb  = false;
    std::string errorBody;                 // buffered only for non-2xx responses
    HttpHeaders responseHeaders;

    // Fires once headers are in, before any body byte reaches the receiver.
    auto responseHandler = [&](const httplib::Response& res) -> bool {
        status          = res.status;
        responseHeaders = convertHeaders(res.headers);
        return true; // always read the body — needed for error messages
    };

    auto contentReceiver = [&](const char* data, std::size_t len, uint64_t /*offset*/,
                               uint64_t /*total*/) -> bool {
        if (status < 200 || status >= 300) {
            if (errorBody.size() < kMaxErrorBodyChars) {
                errorBody.append(data, std::min(len, kMaxErrorBodyChars - errorBody.size()));
            }
            return true; // drain the error body rather than hand it to the caller
        }
        const bool keepGoing = onChunk(data, len);
        if (!keepGoing) stoppedByCb = true;
        return keepGoing;
    };

    auto buildAndSend = [&](auto& cli) {
        applyTimeouts(cli, connectSec, readSec);

        httplib::Request req;
        req.method  = "POST";
        req.path    = t.path;
        req.headers = hdrs;
        req.set_header("Content-Type", contentType);
        req.body             = body;
        req.response_handler = responseHandler;
        req.content_receiver = contentReceiver;

        return cli.send(req);
    };

    httplib::Result result;
    if (t.useSSL) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
        httplib::SSLClient cli(t.host, t.port);
        cli.enable_server_certificate_verification(impl_->config.verifyTls);
        if (!impl_->config.caCertPath.empty()) {
            cli.set_ca_cert_path(impl_->config.caCertPath);
        }
        result = buildAndSend(cli);
#else
        impl_->throwTlsUnavailable(t);
#endif
    } else {
        httplib::Client cli(t.host, t.port);
        result = buildAndSend(cli);
    }

    // A callback-requested stop surfaces as Error::Canceled — that is normal
    // completion (e.g. an SSE [DONE] sentinel), not a transport failure.
    const bool canceledByUs =
        !result && result.error() == httplib::Error::Canceled && stoppedByCb;

    if (!result && !canceledByUs) {
        throw HttpError("POST " + t.url + " streaming failed: " +
                            describeError(result.error(), connectSec, readSec) +
                            ". Check that the service is running and that the URL is "
                            "correct.",
                        t.url);
    }

    if (status < 200 || status >= 300) {
        throw HttpError("POST " + t.url + " returned HTTP " + std::to_string(status) +
                            (errorBody.empty() ? std::string(" with an empty body")
                                               : ": " + truncateForError(errorBody)),
                        t.url, status, errorBody);
    }

    HttpResponse response;
    response.status  = status;
    response.headers = std::move(responseHeaders);
    return response; // body stayed with the callback
}

} // namespace gaia
