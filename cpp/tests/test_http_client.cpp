// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for gaia::HttpClient against the in-process mock server.

// Included first, and alone: cpp-httplib is a private dependency of gaia_core,
// so this header must be usable without dragging it into a consumer's TU.
#include <gaia/http_client.h>
#ifdef CPPHTTPLIB_HTTPLIB_H
#error "gaia/http_client.h must not include cpp-httplib (it is a PRIVATE dependency)"
#endif

#include <string>
#include <utility>
#include <vector>

#include <gtest/gtest.h>

#include <gaia/lemonade_client.h>

#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

// A port nothing listens on in the test environment.
constexpr const char* kUnreachableUrl = "http://127.0.0.1:19753";

} // namespace

// ---------------------------------------------------------------------------
// GET
// ---------------------------------------------------------------------------

TEST(HttpClientTest, GetReturnsStatusAndBody) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    HttpResponse res = http.get("/api/v1/health");

    EXPECT_TRUE(res.ok());
    EXPECT_EQ(res.status, 200);
    EXPECT_NE(res.body.find("\"status\":\"ok\""), std::string::npos);
}

TEST(HttpClientTest, GetSendsRequestHeaders) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    HttpResponse res = http.get("/test/echo", {{"X-Gaia-Test", "hello-header"}});

    EXPECT_EQ(res.body, "hello-header");
}

TEST(HttpClientTest, DefaultHeadersAreSentAndOverridable) {
    bench::MockLlmServer server;
    HttpClientConfig cfg;
    cfg.baseUrl        = server.baseUrl();
    cfg.defaultHeaders = {{"X-Gaia-Test", "from-default"}};
    HttpClient http(cfg);

    EXPECT_EQ(http.get("/test/echo").body, "from-default");
    EXPECT_EQ(http.get("/test/echo", {{"X-Gaia-Test", "from-request"}}).body,
              "from-request");

    http.setDefaultHeader("X-Gaia-Test", "replaced");
    EXPECT_EQ(http.get("/test/echo").body, "replaced");
}

TEST(HttpClientTest, RequestHeaderWinsOverDifferentlyCasedDefault) {
    bench::MockLlmServer server;
    HttpClientConfig cfg;
    cfg.baseUrl        = server.baseUrl();
    cfg.defaultHeaders = {{"x-gaia-test", "from-default"}};
    HttpClient http(cfg);

    // Header names are case-insensitive: the request value must replace the
    // default rather than both going on the wire.
    EXPECT_EQ(http.get("/test/echo", {{"X-Gaia-Test", "from-request"}}).body,
              "from-request");
    EXPECT_EQ(http.post("/test/echo", "body",
                        {{"content-type", "text/plain"}})
                  .header("X-Gaia-Content-Type"),
              "text/plain");
}

TEST(HttpClientTest, ResponseHeadersAreExposedCaseInsensitively) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    HttpResponse res = http.get("/test/echo");

    EXPECT_EQ(res.header("X-Gaia-Echo"), "pong");
    EXPECT_EQ(res.header("x-gaia-echo"), "pong");
    EXPECT_EQ(res.header("X-Missing", "fallback"), "fallback");
}

TEST(HttpClientTest, AbsoluteUrlPathOverridesBaseUrl) {
    bench::MockLlmServer server;
    HttpClient http(kUnreachableUrl);

    HttpResponse res = http.get(server.baseUrl() + "/test/echo",
                                {{"X-Gaia-Test", "absolute"}});

    EXPECT_EQ(res.body, "absolute");
}

TEST(HttpClientTest, SetBaseUrlRetargetsSubsequentRequests) {
    bench::MockLlmServer server;
    HttpClient http(kUnreachableUrl);
    EXPECT_EQ(http.baseUrl(), kUnreachableUrl);

    http.setBaseUrl(server.baseUrl());

    EXPECT_EQ(http.baseUrl(), server.baseUrl());
    EXPECT_EQ(http.get("/test/echo", {{"X-Gaia-Test", "retargeted"}}).body, "retargeted");
}

// ---------------------------------------------------------------------------
// POST
// ---------------------------------------------------------------------------

TEST(HttpClientTest, PostSendsBodyAndDefaultsToJsonContentType) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    HttpResponse res = http.post("/test/echo", R"({"k":"v"})");

    EXPECT_EQ(res.status, 200);
    EXPECT_EQ(res.body, R"({"k":"v"})");
    EXPECT_EQ(res.header("X-Gaia-Content-Type"), "application/json");
}

TEST(HttpClientTest, PostHonoursExplicitContentTypeAndHeaders) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    HttpResponse res = http.post("/test/echo", "plain body",
                                 {{"Content-Type", "text/plain"},
                                  {"X-Gaia-Test", "post-header"}});

    EXPECT_EQ(res.header("X-Gaia-Content-Type"), "text/plain");
    EXPECT_EQ(res.header("X-Gaia-Test-Seen"), "post-header");
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

TEST(HttpClientTest, PostStreamingDeliversChunksInOrder) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    std::string received;
    HttpResponse res = http.postStreaming(
        "/test/stream", "{}", [&received](const char* data, std::size_t len) {
            received.append(data, len);
            return true;
        });

    EXPECT_EQ(res.status, 200);
    EXPECT_TRUE(res.body.empty()); // payload went to the callback
    EXPECT_NE(received.find("data: alpha"), std::string::npos);
    EXPECT_NE(received.find("data: beta"), std::string::npos);
    EXPECT_NE(received.find("data: [DONE]"), std::string::npos);
    EXPECT_LT(received.find("data: alpha"), received.find("data: beta"));
}

TEST(HttpClientTest, PostStreamingCallbackStopIsNormalCompletion) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    std::vector<std::string> chunks;
    // Returning false mirrors SseParser hitting the [DONE] sentinel.
    EXPECT_NO_THROW({
        HttpResponse res = http.postStreaming(
            "/test/stream", "{}", [&chunks](const char* data, std::size_t len) {
                chunks.emplace_back(data, len);
                return false;
            });
        EXPECT_EQ(res.status, 200);
    });
    EXPECT_EQ(chunks.size(), 1u);
}

TEST(HttpClientTest, PostStreamingThrowsOnErrorStatusWithoutCallingCallback) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    bool callbackCalled = false;
    try {
        http.postStreaming("/test/stream-error", "{}",
                           [&callbackCalled](const char*, std::size_t) {
                               callbackCalled = true;
                               return true;
                           });
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_EQ(e.status(), 503);
        EXPECT_EQ(e.body(), "stream unavailable");
        EXPECT_NE(std::string(e.what()).find("stream unavailable"), std::string::npos);
        EXPECT_NE(std::string(e.what()).find("/test/stream-error"), std::string::npos);
    }
    EXPECT_FALSE(callbackCalled);
}

TEST(HttpClientTest, PostStreamingRejectsEmptyCallback) {
    HttpClient http(kUnreachableUrl);
    EXPECT_THROW(http.postStreaming("/test/stream", "{}", nullptr), HttpError);
}

// ---------------------------------------------------------------------------
// Failure modes — every one raises HttpError, none returns a default response
// ---------------------------------------------------------------------------

TEST(HttpClientTest, ConnectionFailureThrowsHttpErrorNamingTheUrl) {
    HttpClient http(kUnreachableUrl);

    try {
        http.get("/api/v1/health");
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_EQ(e.status(), 0); // failed before any response
        EXPECT_NE(e.url().find("127.0.0.1:19753"), std::string::npos);
        const std::string what = e.what();
        EXPECT_NE(what.find("http://127.0.0.1:19753/api/v1/health"), std::string::npos);
        EXPECT_NE(what.find("could not connect"), std::string::npos);
    }
}

TEST(HttpClientTest, ErrorStatusThrowsHttpErrorCarryingStatusAndBody) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    try {
        http.get("/test/boom");
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_EQ(e.status(), 500);
        EXPECT_EQ(e.body(), "boom: upstream exploded");
        const std::string what = e.what();
        EXPECT_NE(what.find("returned HTTP 500"), std::string::npos);
        EXPECT_NE(what.find("boom: upstream exploded"), std::string::npos);
    }
}

TEST(HttpClientTest, UnknownPathThrowsNotFound) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    try {
        http.get("/test/does-not-exist");
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_EQ(e.status(), 404);
    }
}

TEST(HttpClientTest, ReadTimeoutThrowsHttpError) {
    bench::MockLlmServer server;
    HttpClient http(server.baseUrl());

    try {
        http.get("/test/slow", {}, /*timeoutSec=*/1);
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_EQ(e.status(), 0);
        EXPECT_NE(std::string(e.what()).find("1s read timeout"), std::string::npos);
    }
}

TEST(HttpClientTest, EmptyBaseUrlThrowsActionableError) {
    HttpClient http{HttpClientConfig{}};

    try {
        http.get("/health");
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_NE(std::string(e.what()).find("baseUrl"), std::string::npos);
    }
}

TEST(HttpClientTest, HttpErrorIsAStdException) {
    // Callers that catch std::exception (e.g. LemonadeClient::getStatus) keep working.
    HttpClient http(kUnreachableUrl);
    EXPECT_THROW(http.get("/health"), std::runtime_error);
}

TEST(HttpClientTest, MalformedPortIsRejected) {
    HttpClient http("http://127.0.0.1:8080abc");

    try {
        http.get("/health");
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_NE(std::string(e.what()).find("Invalid port"), std::string::npos);
    }
    EXPECT_THROW(HttpClient("http://127.0.0.1:0").get("/health"), HttpError);
    EXPECT_THROW(HttpClient("http://127.0.0.1:99999").get("/health"), HttpError);
}

TEST(HttpClientTest, HeaderInjectionIsRejected) {
    HttpClient http(kUnreachableUrl);

    EXPECT_THROW(http.get("/health", {{"X-Evil", "v\r\nX-Injected: 1"}}), HttpError);
    EXPECT_THROW(http.get("/health", {{"X-Evil\r\nX-Injected", "v"}}), HttpError);
    EXPECT_THROW(http.get("/hea der"), HttpError);
    EXPECT_THROW(http.post("/health", "{}",
                           {{"Content-Type", "application/json\r\nX-Injected: 1"}}),
                 HttpError);
}

TEST(HttpClientTest, NonPositiveConfiguredTimeoutIsRejected) {
    HttpClientConfig cfg;
    cfg.baseUrl    = kUnreachableUrl;
    cfg.timeoutSec = 0;
    EXPECT_THROW(HttpClient{cfg}, std::invalid_argument);
}

// ---------------------------------------------------------------------------
// Path joining and moves
// ---------------------------------------------------------------------------

TEST(HttpClientTest, BasePathAndRequestPathJoinWithOneSlash) {
    bench::MockLlmServer server;

    HttpClient trailing(server.baseUrl() + "/");
    EXPECT_EQ(trailing.get("/test/echo", {{"X-Gaia-Test", "a"}}).body, "a");

    HttpClient noLeadingSlash(server.baseUrl());
    EXPECT_EQ(noLeadingSlash.get("test/echo", {{"X-Gaia-Test", "b"}}).body, "b");

    HttpClient nested(server.baseUrl() + "/test");
    EXPECT_EQ(nested.get("/echo", {{"X-Gaia-Test", "c"}}).body, "c");
}

TEST(HttpClientTest, MovedFromClientStaysUsable) {
    bench::MockLlmServer server;
    HttpClient source(server.baseUrl());
    HttpClient moved(std::move(source));

    EXPECT_EQ(moved.get("/test/echo", {{"X-Gaia-Test", "moved"}}).body, "moved");

    // A moved-from client must not be a live null — LemonadeClient's defaulted
    // move relies on this.
    EXPECT_NO_THROW(source.setBaseUrl(server.baseUrl()));
    EXPECT_EQ(source.get("/test/echo", {{"X-Gaia-Test", "revived"}}).body, "revived");
}

// ---------------------------------------------------------------------------
// LemonadeClient over HttpClient — the refactored transport path
// ---------------------------------------------------------------------------

TEST(LemonadeOverHttpClientTest, HealthAndModelsRoundTrip) {
    bench::MockLlmServer server;
    LemonadeClient client(server.baseUrl());

    EXPECT_TRUE(client.isServerRunning());
    EXPECT_TRUE(client.ready());

    LemonadeHealth health = client.healthCheck();
    EXPECT_TRUE(health.running);
    EXPECT_EQ(health.modelId, "mock-model");
    EXPECT_EQ(health.contextSize, 16384);

    json models = client.listModels();
    EXPECT_EQ(models["data"][0]["id"], "mock-model");
}

TEST(LemonadeOverHttpClientTest, ChatCompletionsPostsBody) {
    bench::MockLlmServer server;
    LemonadeClient client(server.baseUrl());

    const std::string body =
        client.chatCompletions({{"model", "mock-model"}, {"messages", json::array()}});

    EXPECT_NE(body.find("benchmark result"), std::string::npos);
    ASSERT_EQ(server.receivedBodies().size(), 1u);
    EXPECT_NE(server.receivedBodies()[0].find("mock-model"), std::string::npos);
}

TEST(LemonadeOverHttpClientTest, StreamingChatCompletionsStopsOnDoneSentinel) {
    bench::MockLlmServer server;
    server.enableSseStreaming(true);
    LemonadeClient client(server.baseUrl());

    // The [DONE] sentinel makes SseParser stop the read; that must complete
    // normally rather than surfacing as a cancellation error.
    std::string streamed;
    std::string raw;
    EXPECT_NO_THROW({
        raw = client.chatCompletionsStreaming(
            {{"model", "mock-model"}, {"messages", json::array()}},
            [&streamed](const std::string& token) { streamed += token; });
    });

    EXPECT_EQ(streamed, "Hello world");
    EXPECT_NE(raw.find("data: "), std::string::npos);
}

TEST(LemonadeOverHttpClientTest, ServerErrorRaisesWithStatusAndBody) {
    bench::MockLlmServer server;
    server.pushResponse(R"({"detail":"model not loaded"})", 500);
    LemonadeClient client(server.baseUrl());

    try {
        client.chatCompletions({{"model", "mock-model"}});
        FAIL() << "expected HttpError";
    } catch (const HttpError& e) {
        EXPECT_EQ(e.status(), 500);
        EXPECT_NE(std::string(e.what()).find("model not loaded"), std::string::npos);
        EXPECT_NE(std::string(e.what()).find("/api/v1/chat/completions"),
                  std::string::npos);
    }
}
