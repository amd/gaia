// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Unit tests for LemonadeClient — no running server required.

#include <gtest/gtest.h>
#include <gaia/lemonade_client.h>

using namespace gaia;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, ConstantsValues) {
    EXPECT_STREQ(LEMONADE_DEFAULT_URL,   "http://localhost:8000/api/v1");
    EXPECT_STREQ(LEMONADE_DEFAULT_MODEL, "Qwen3-0.6B-GGUF");
    EXPECT_EQ(LEMONADE_REQUEST_TIMEOUT,    900);
    EXPECT_EQ(LEMONADE_MODEL_LOAD_TIMEOUT, 12000);
}

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, DefaultConstruction) {
    LemonadeClient client;
    // Default URL now includes /api/v1 (normalized)
    EXPECT_EQ(client.baseUrl(), "http://localhost:8000/api/v1");
    EXPECT_FALSE(client.debug());
    EXPECT_EQ(client.contextSize(), 0);
    EXPECT_TRUE(client.model().empty());
}

TEST(LemonadeClientTest, ConfigConstruction) {
    LemonadeClientConfig cfg;
    cfg.baseUrl     = "http://myhost:9000";
    cfg.model       = "Qwen3-4B-GGUF";
    cfg.contextSize = 8192;
    cfg.debug       = true;

    LemonadeClient client(cfg);
    EXPECT_EQ(client.baseUrl(),     "http://myhost:9000/api/v1");
    EXPECT_EQ(client.model(),       "Qwen3-4B-GGUF");
    EXPECT_EQ(client.contextSize(), 8192);
    EXPECT_TRUE(client.debug());
}

TEST(LemonadeClientTest, LegacyConstructorCustomUrl) {
    LemonadeClient client("http://192.168.1.100:9000", true);
    // URL is normalized — /api/v1 appended
    EXPECT_EQ(client.baseUrl(), "http://192.168.1.100:9000/api/v1");
    EXPECT_TRUE(client.debug());
}

TEST(LemonadeClientTest, LegacyConstructorUrlAlreadyHasApiV1) {
    LemonadeClient client("http://192.168.1.100:9000/api/v1");
    // No double-append
    EXPECT_EQ(client.baseUrl(), "http://192.168.1.100:9000/api/v1");
}

// ---------------------------------------------------------------------------
// URL normalization
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, UrlNormalizationAddsApiV1) {
    LemonadeClient client("http://localhost:8000");
    EXPECT_EQ(client.baseUrl(), "http://localhost:8000/api/v1");
}

TEST(LemonadeClientTest, UrlNormalizationNoopWhenPresent) {
    LemonadeClient client("http://localhost:8000/api/v1");
    EXPECT_EQ(client.baseUrl(), "http://localhost:8000/api/v1");
}

TEST(LemonadeClientTest, UrlNormalizationStripsTrailingSlash) {
    LemonadeClient client("http://localhost:8000/");
    EXPECT_EQ(client.baseUrl(), "http://localhost:8000/api/v1");
}

TEST(LemonadeClientTest, UrlNormalizationStripsMultipleTrailingSlashes) {
    LemonadeClient client("http://localhost:8000///");
    EXPECT_EQ(client.baseUrl(), "http://localhost:8000/api/v1");
}

TEST(LemonadeClientTest, SetBaseUrlNormalizes) {
    LemonadeClient client("http://localhost:8000");
    client.setBaseUrl("http://remotehost:1234");
    EXPECT_EQ(client.baseUrl(), "http://remotehost:1234/api/v1");
}

TEST(LemonadeClientTest, SetBaseUrlNoopWhenApiV1Present) {
    LemonadeClient client("http://localhost:8000");
    client.setBaseUrl("http://remotehost:1234/api/v1");
    EXPECT_EQ(client.baseUrl(), "http://remotehost:1234/api/v1");
}

// ---------------------------------------------------------------------------
// Getters and setters
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, ModelGetterSetter) {
    LemonadeClient client;
    EXPECT_TRUE(client.model().empty());
    client.setModel("Qwen3-30B-GGUF");
    EXPECT_EQ(client.model(), "Qwen3-30B-GGUF");
    client.setModel("");
    EXPECT_TRUE(client.model().empty());
}

TEST(LemonadeClientTest, ContextSizeGetterSetter) {
    LemonadeClient client;
    EXPECT_EQ(client.contextSize(), 0);
    client.setContextSize(32768);
    EXPECT_EQ(client.contextSize(), 32768);
    client.setContextSize(0);
    EXPECT_EQ(client.contextSize(), 0);
}

TEST(LemonadeClientTest, DebugGetterSetter) {
    LemonadeClient client;
    EXPECT_FALSE(client.debug());
    client.setDebug(true);
    EXPECT_TRUE(client.debug());
}

// ---------------------------------------------------------------------------
// isServerRunning — offline server returns false (no exception)
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, IsServerRunningReturnsFalseWhenOffline) {
    // Use a port that should never be open in the test environment
    LemonadeClient client("http://127.0.0.1:19753");
    EXPECT_FALSE(client.isServerRunning());
}

// ---------------------------------------------------------------------------
// ready() — offline returns false, no exception
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, ReadyReturnsFalseWhenOffline) {
    LemonadeClient client("http://127.0.0.1:19753");
    EXPECT_FALSE(client.ready());
}

// ---------------------------------------------------------------------------
// healthCheck — offline server returns health.running == false
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, HealthCheckOfflineReturnsNotRunning) {
    LemonadeClient client("http://127.0.0.1:19753");
    LemonadeHealth h = client.healthCheck();
    EXPECT_FALSE(h.running);
    EXPECT_TRUE(h.modelId.empty());
    EXPECT_EQ(h.contextSize, 0);
}

// ---------------------------------------------------------------------------
// getStatus() — offline returns not-running with error message
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, GetStatusOfflineReturnsNotRunning) {
    LemonadeClient client("http://127.0.0.1:19753");
    LemonadeStatus s = client.getStatus();
    EXPECT_FALSE(s.running);
    EXPECT_EQ(s.url, "http://127.0.0.1:19753/api/v1");
    // When offline, error may be set or running is simply false — either is acceptable
    EXPECT_TRUE(s.loadedModels.empty());
}

// ---------------------------------------------------------------------------
// validateContextSize() — offline server should NOT block (returns {true, msg})
// ---------------------------------------------------------------------------

TEST(LemonadeClientTest, ValidateContextSizeOfflineDoesNotBlock) {
    LemonadeClient client("http://127.0.0.1:19753");
    auto [ok, msg] = client.validateContextSize(4096);
    // Non-fatal: either {true, "Server not running…"} or {true, "Validation skipped…"}
    EXPECT_TRUE(ok);
}

// ---------------------------------------------------------------------------
// LemonadeStatus struct defaults
// ---------------------------------------------------------------------------

TEST(LemonadeStatusTest, DefaultValues) {
    LemonadeStatus s;
    EXPECT_FALSE(s.running);
    EXPECT_TRUE(s.url.empty());
    EXPECT_EQ(s.contextSize, 0);
    EXPECT_TRUE(s.loadedModels.empty());
    EXPECT_TRUE(s.error.empty());
}

// ---------------------------------------------------------------------------
// LemonadeHealth struct population from sample JSON
// ---------------------------------------------------------------------------

TEST(LemonadeHealthTest, PopulateFromHealthJson) {
    // Sample health response as produced by a real Lemonade server
    json healthJson = {
        {"status", "ok"},
        {"all_models_loaded", json::array({
            json::object({
                {"model_name", "Qwen3-4B-Instruct-2507-GGUF"},
                {"recipe_options", {{"ctx_size", 32768}}}
            })
        })}
    };

    LemonadeHealth h;
    h.running = true;
    h.raw = healthJson;

    if (healthJson.contains("all_models_loaded") &&
        healthJson["all_models_loaded"].is_array() &&
        !healthJson["all_models_loaded"].empty()) {
        const auto& first = healthJson["all_models_loaded"][0];
        h.modelId = first.value("model_name", "");
        if (first.contains("recipe_options") && first["recipe_options"].is_object()) {
            h.contextSize = first["recipe_options"].value("ctx_size", 0);
        }
    }

    EXPECT_TRUE(h.running);
    EXPECT_EQ(h.modelId, "Qwen3-4B-Instruct-2507-GGUF");
    EXPECT_EQ(h.contextSize, 32768);
}

TEST(LemonadeHealthTest, EmptyAllModelsLoaded) {
    json healthJson = {
        {"status", "ok"},
        {"all_models_loaded", json::array()}
    };

    LemonadeHealth h;
    h.running = true;
    h.raw = healthJson;
    // No models: modelId and contextSize stay at defaults
    EXPECT_TRUE(h.modelId.empty());
    EXPECT_EQ(h.contextSize, 0);
}

TEST(LemonadeHealthTest, MissingRecipeOptions) {
    json healthJson = {
        {"all_models_loaded", json::array({
            json::object({{"model_name", "SomeModel"}})
        })}
    };

    LemonadeHealth h;
    h.running = true;
    h.raw = healthJson;

    const auto& first = healthJson["all_models_loaded"][0];
    h.modelId = first.value("model_name", "");
    if (first.contains("recipe_options") && first["recipe_options"].is_object()) {
        h.contextSize = first["recipe_options"].value("ctx_size", 0);
    }

    EXPECT_EQ(h.modelId, "SomeModel");
    EXPECT_EQ(h.contextSize, 0);  // no recipe_options → default 0
}
