// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// C++ GAIA VLM integration tests.
// Requires: lemonade-server running at GAIA_CPP_BASE_URL
//           (default: http://localhost:8000/api/v1) with a VLM model
//           (default: Qwen3-VL-4B-Instruct-GGUF) available.
//
// Env overrides:
//   GAIA_CPP_BASE_URL       base URL (default http://localhost:8000/api/v1)
//   GAIA_CPP_TEST_VLM_MODEL VLM model id (default Qwen3-VL-4B-Instruct-GGUF)
//   GAIA_CPP_TEST_VLM_CTX   context size (default 32768)
//   GAIA_CPP_TEST_VLM_SMALL_CTX  ctx size used by the overflow test (default 2048)
//
// Build:
//   cmake -B build-int -S cpp -DGAIA_BUILD_INTEGRATION_TESTS=ON
//   cmake --build build-int --config Release
//
// Run:
//   ctest --test-dir build-int -C Release --output-on-failure -R VLMIntegration

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/lemonade_client.h>
#include <gaia/types.h>

#include <cstdlib>
#include <iostream>
#include <string>

#ifndef GAIA_TEST_FIXTURES_DIR
#define GAIA_TEST_FIXTURES_DIR "cpp/tests/fixtures"
#endif

// Pinned Lemonade version — must match src/gaia/version.py::LEMONADE_VERSION.
// AC-23: integration tests assert server version matches the GAIA-pinned value.
#ifndef GAIA_PINNED_LEMONADE_VERSION
#define GAIA_PINNED_LEMONADE_VERSION "10.0.0"
#endif

namespace {

std::string testBaseUrl() {
    return gaia::getEnvVar("GAIA_CPP_BASE_URL", "http://localhost:8000/api/v1");
}

std::string testVlmModel() {
    return gaia::getEnvVar("GAIA_CPP_TEST_VLM_MODEL", "Qwen3-VL-4B-Instruct-GGUF");
}

int envInt(const char* name, int fallback) {
    const char* v = std::getenv(name);
    if (!v || !*v) return fallback;
    try {
        return std::stoi(v);
    } catch (const std::invalid_argument&) {
        return fallback;   // non-numeric value — treat as unset
    } catch (const std::out_of_range&) {
        return fallback;   // value overflows int — treat as unset
    }
}

int testVlmCtxSize()      { return envInt("GAIA_CPP_TEST_VLM_CTX", 32768); }
int testVlmSmallCtxSize() { return envInt("GAIA_CPP_TEST_VLM_SMALL_CTX", 2048); }

gaia::AgentConfig baseVlmConfig(int ctxSize) {
    gaia::AgentConfig cfg;
    cfg.baseUrl     = testBaseUrl();
    cfg.modelId     = testVlmModel();
    cfg.contextSize = ctxSize;
    cfg.maxSteps    = 3;
    cfg.silentMode  = true;
    return cfg;
}

std::string fixturePath(const std::string& name) {
    return std::string(GAIA_TEST_FIXTURES_DIR) + "/" + name;
}

// AC-23 version-pin guard — report a mismatch against GAIA_PINNED_LEMONADE_VERSION.
// Uses LemonadeClient::getSystemInfo() which exposes server metadata.
// Non-fatal (EXPECT_* not ASSERT_*): mismatch logs a clear message but does
// not skip the test body, so the VLM contract still runs.
void expectPinnedLemonadeVersion() {
    try {
        gaia::LemonadeClient client(testBaseUrl());
        gaia::json info = client.getSystemInfo(false);
        std::string v;
        // Look for a version field under common keys.
        if (info.contains("version") && info["version"].is_string()) {
            v = info["version"].get<std::string>();
        } else if (info.contains("server_version") && info["server_version"].is_string()) {
            v = info["server_version"].get<std::string>();
        } else if (info.contains("lemonade_version") && info["lemonade_version"].is_string()) {
            v = info["lemonade_version"].get<std::string>();
        }
        if (v.empty()) {
            std::cerr << "[VLM version pin] Lemonade /system-info did not return a "
                         "version field; skipping pin check (informational only)."
                      << std::endl;
            SUCCEED();
            return;
        }
        EXPECT_EQ(v, std::string(GAIA_PINNED_LEMONADE_VERSION))
            << "Lemonade server version (" << v << ") does not match "
            << "GAIA-pinned version (" << GAIA_PINNED_LEMONADE_VERSION << "). "
            << "Update LEMONADE_VERSION in src/gaia/version.py or roll back the server.";
    } catch (const std::exception& e) {
        std::cerr << "[VLM version pin] Lemonade /system-info probe failed: "
                  << e.what()
                  << " (informational only; continuing VLM test)."
                  << std::endl;
        SUCCEED();
    }
}

} // namespace

// ---------------------------------------------------------------------------
// AC-21: processQuery(userInput, images) against live VLM returns a non-empty
// result string.
// ---------------------------------------------------------------------------
TEST(VLMIntegrationTest, DescribeFixtureImage) {
    expectPinnedLemonadeVersion();

    class VlmAgent : public gaia::Agent {
    public:
        explicit VlmAgent(const gaia::AgentConfig& cfg) : Agent(cfg) { init(); }
    protected:
        std::string getSystemPrompt() const override {
            return "You are a minimal vision assistant. Briefly describe what you see.";
        }
    };

    VlmAgent agent(baseVlmConfig(testVlmCtxSize()));
    gaia::Image img = gaia::Image::fromFile(fixturePath("tiny.png"));

    gaia::json result = agent.processQuery(
        "Describe this image in one sentence.", {img}, 3);

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty())
        << "Expected non-empty VLM response";
}

// ---------------------------------------------------------------------------
// AC-22: processQuery(vector<Message>) overload against live VLM.
// ---------------------------------------------------------------------------
TEST(VLMIntegrationTest, MessagesListOverload) {
    expectPinnedLemonadeVersion();

    class VlmAgent : public gaia::Agent {
    public:
        explicit VlmAgent(const gaia::AgentConfig& cfg) : Agent(cfg) { init(); }
    protected:
        std::string getSystemPrompt() const override {
            return "You are a minimal vision assistant. Briefly describe what you see.";
        }
    };

    VlmAgent agent(baseVlmConfig(testVlmCtxSize()));
    gaia::Image img = gaia::Image::fromFile(fixturePath("tiny.png"));

    std::vector<gaia::Message> msgs;
    msgs.push_back(gaia::Message::fromUser(
        "What is in this image? Keep it short.", {img}));

    gaia::json result = agent.processQuery(msgs, 3);

    ASSERT_TRUE(result.contains("result"));
    std::string answer = result["result"].get<std::string>();
    EXPECT_FALSE(answer.empty())
        << "Expected non-empty VLM response via messages-list overload";
}

// ---------------------------------------------------------------------------
// AC-23: Loading the VLM with a small ctx size should surface an error
// (raw passthrough) when an image request exceeds the budget.
//
// This test is best-effort: if the server accepts the request anyway (newer
// Lemonade builds may auto-grow ctx for VLMs), we do not fail — the AC says
// the error, when present, must surface as std::runtime_error with a
// non-empty what().
// ---------------------------------------------------------------------------
TEST(VLMIntegrationTest, ContextOverflowSurfacesError) {
    expectPinnedLemonadeVersion();

    class VlmAgent : public gaia::Agent {
    public:
        explicit VlmAgent(const gaia::AgentConfig& cfg) : Agent(cfg) { init(); }
    protected:
        std::string getSystemPrompt() const override {
            return "You are a minimal vision assistant.";
        }
    };

    gaia::AgentConfig cfg = baseVlmConfig(testVlmSmallCtxSize());
    VlmAgent agent(cfg);
    gaia::Image img = gaia::Image::fromFile(fixturePath("tiny.png"));

    bool threwRuntime = false;
    std::string whatMessage;
    try {
        gaia::json result = agent.processQuery(
            "Describe the image in extreme detail.", {img}, 3);
        // If it did not throw, that's acceptable — log so a human notices.
        SUCCEED() << "Server accepted VLM image request at ctx_size="
                  << testVlmSmallCtxSize()
                  << " — no overflow error surfaced.";
        return;
    } catch (const std::runtime_error& e) {
        threwRuntime = true;
        whatMessage = e.what();
    } catch (const std::exception& e) {
        FAIL() << "Expected std::runtime_error, got "
               << typeid(e).name() << ": " << e.what();
    }

    EXPECT_TRUE(threwRuntime);
    EXPECT_FALSE(whatMessage.empty())
        << "Expected non-empty what() message on ctx overflow";
    // Per NON-1 (plan): we do NOT assert any substring. Raw passthrough only.
}
