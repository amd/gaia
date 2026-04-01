// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// HTTP client for the Lemonade inference server.
// Mirrors Python: src/gaia/llm/lemonade_client.py
//
// Key responsibility: context size is set at MODEL LOAD TIME via POST /load,
// not per-request. This class handles load, health check, and chat completions.

#pragma once

#include <functional>
#include <string>
#include <utility>
#include <vector>

#include <nlohmann/json.hpp>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

using json = nlohmann::json;

// ---- Constants (mirrors Python LemonadeClient defaults) ----

inline constexpr const char* LEMONADE_DEFAULT_URL        = "http://localhost:8000/api/v1";
inline constexpr const char* LEMONADE_DEFAULT_MODEL      = "Qwen3-0.6B-GGUF";
inline constexpr int         LEMONADE_REQUEST_TIMEOUT    = 900;   // 15 min  (matches Python)
inline constexpr int         LEMONADE_MODEL_LOAD_TIMEOUT = 12000; // ~200 min (matches Python)

// ---- Config ----

/// Configuration for LemonadeClient.
/// Empty strings trigger environment variable lookup, then fall back to defaults.
struct GAIA_API LemonadeClientConfig {
    std::string baseUrl;       // empty → env LEMONADE_BASE_URL → LEMONADE_DEFAULT_URL
    std::string model;         // empty → env LEMONADE_MODEL → ""
    int         contextSize = 0; // 0 → server default
    bool        debug = false;
};

// ---- Health status ----

/// Parsed result from GET /health.
struct GAIA_API LemonadeHealth {
    bool running = false;
    std::string modelId;   // currently loaded model (empty if none)
    int contextSize = 0;   // active n_ctx (0 if unknown)
    json raw;              // full health response JSON
};

// ---- Composite status ----

/// Composite status from health + models endpoints.
/// Mirrors Python LemonadeStatus dataclass.
struct GAIA_API LemonadeStatus {
    bool running = false;
    std::string url;
    int contextSize = 0;
    std::vector<json> loadedModels;
    json healthData;
    std::string error;
};

// ---- Client ----

/// HTTP client for the Lemonade inference server.
///
/// Mirrors the subset of Python LemonadeClient used by the C++ Agent:
///   - isServerRunning()        → quick reachability check
///   - ready()                  → server running with status "ok"
///   - healthCheck()            → full status including loaded model + ctx size
///   - getStatus()              → composite health + models status
///   - listModels()             → GET /models
///   - getModelDetails()        → GET /models/{id}
///   - unloadModel()            → POST /unload
///   - checkModelLoaded()       → case-insensitive model search in listModels
///   - getStats()               → GET /stats
///   - getSystemInfo()          → GET /system-info
///   - setParams()              → POST /params
///   - loadModel()              → POST /load {"model_name":…, "ctx_size":…}
///   - ensureModelLoaded()      → load only when necessary
///   - chatCompletions()        → POST /chat/completions, returns raw response body
///   - validateContextSize()    → compare token count against server ctx window
class GAIA_API LemonadeClient {
public:
    /// Config-based constructor (preferred).
    /// Resolves env vars and normalizes URL automatically.
    explicit LemonadeClient(const LemonadeClientConfig& config = {});

    /// Legacy constructor for backward compatibility.
    /// Delegates to the config-based constructor.
    ///
    /// @param baseUrl  Server root, e.g. "http://localhost:8000"
    /// @param debug    Emit extra diagnostics to stderr when true
    LemonadeClient(const std::string& baseUrl, bool debug = false);

    // Non-copyable (contains no resources but keep consistent with Agent)
    LemonadeClient(const LemonadeClient&) = delete;
    LemonadeClient& operator=(const LemonadeClient&) = delete;

    // Movable
    LemonadeClient(LemonadeClient&&) = default;
    LemonadeClient& operator=(LemonadeClient&&) = default;

    // ---- Server status ----

    /// Return true if the server responds to GET /health.
    bool isServerRunning();

    /// Return true if the server is running and health status is "ok".
    /// Mirrors Python ready().
    bool ready();

    /// Fetch full health status (model id, context size, …).
    LemonadeHealth healthCheck();

    /// Composite status from health + models endpoints.
    /// Mirrors Python get_status().
    LemonadeStatus getStatus();

    // ---- Model management ----

    /// List models available on the server.
    /// @param showAll  Also include models not currently loaded
    /// Mirrors Python list_models().
    json listModels(bool showAll = false);

    /// Get details for a specific model by ID.
    /// Mirrors Python get_model_details().
    json getModelDetails(const std::string& modelId);

    /// Unload the currently loaded model from the server.
    /// Clears the stored model_ name on success.
    /// Mirrors Python unload_model().
    void unloadModel();

    /// Return true if the given model is currently loaded (case-insensitive match).
    /// Mirrors Python check_model_loaded().
    bool checkModelLoaded(const std::string& modelId);

    /// Get inference statistics from the server.
    /// Mirrors Python get_stats().
    json getStats();

    /// Get system hardware information.
    /// @param verbose  Include detailed hardware info
    /// Mirrors Python get_system_info().
    json getSystemInfo(bool verbose = false);

    /// Set server-side parameters.
    /// Mirrors Python set_params().
    json setParams(const json& params);

    /// Load (or reload) a model on the server.
    ///
    /// Mirrors Python LemonadeClient.load_model():
    ///   POST /load {"model_name": modelName, "ctx_size": ctxSize}
    ///
    /// Stores modelName in model_ on success.
    ///
    /// @param modelName  Lemonade model identifier
    /// @param ctxSize    Context window tokens (0 → server default)
    /// @param timeoutSec Maximum seconds to wait for the server to finish loading
    /// @return           Raw JSON response body
    /// @throws std::runtime_error on HTTP or parse error
    json loadModel(const std::string& modelName, int ctxSize = 0,
                   int timeoutSec = LEMONADE_MODEL_LOAD_TIMEOUT);

    /// Load model only when not already loaded with the requested context size.
    ///
    /// Mirrors Python Agent._ensure_model_loaded():
    ///   1. healthCheck()
    ///   2. If modelId matches AND (ctxSize == 0 OR contextSize matches) → skip
    ///   3. Otherwise call loadModel()
    ///
    /// When called with no args, falls back to stored model_ / contextSize_.
    ///
    /// @param modelName  Override model (empty → use stored model_)
    /// @param ctxSize    Override ctx (-1 → use stored contextSize_; 0 → server default)
    /// @throws std::runtime_error if load fails
    void ensureModelLoaded(const std::string& modelName = "", int ctxSize = -1);

    /// Validate that the server context window is large enough for the given token count.
    /// Returns {true, ""} on success or when server is unreachable (non-blocking).
    /// Mirrors Python validate_context_size().
    std::pair<bool, std::string> validateContextSize(int tokens);

    // ---- Inference ----

    /// POST /chat/completions and return the raw response body string.
    /// @param requestBody  OpenAI-compatible request JSON
    /// @param timeoutSec   Max seconds to wait (default: LEMONADE_REQUEST_TIMEOUT)
    /// @throws std::runtime_error on HTTP or connection error
    std::string chatCompletions(const json& requestBody,
                                int timeoutSec = LEMONADE_REQUEST_TIMEOUT);

    /// POST /chat/completions with "stream": true and invoke onToken for each
    /// delta token as it arrives. Returns the raw HTTP response bytes for
    /// fallback non-streaming parsing (e.g. when the server ignores "stream").
    ///
    /// @param requestBody  OpenAI-compatible request JSON (stream field added automatically)
    /// @param onToken      Callback invoked for each content token
    /// @param timeoutSec   Max seconds to wait (default: LEMONADE_REQUEST_TIMEOUT)
    /// @throws std::runtime_error on HTTP or connection error
    std::string chatCompletionsStreaming(const json& requestBody,
                                         StreamCallback onToken,
                                         int timeoutSec = LEMONADE_REQUEST_TIMEOUT);

    // ---- Configuration ----

    const std::string& baseUrl() const { return baseUrl_; }
    /// Normalize and store the URL (appends /api/v1 if missing).
    void setBaseUrl(const std::string& url);

    const std::string& model() const { return model_; }
    void setModel(const std::string& m) { model_ = m; }

    int contextSize() const { return contextSize_; }
    void setContextSize(int ctx) { contextSize_ = ctx; }

    bool debug() const { return debug_; }
    void setDebug(bool d) { debug_ = d; }

private:
    /// Normalize URL: strip trailing slashes, append /api/v1 if missing.
    static std::string normalizeUrl(const std::string& url);

    /// Parsed URL components.
    struct UrlParts {
        std::string host;
        int port = 80;
        std::string basePath; // everything after host:port (may be "")
        bool useSSL = false;
    };

    UrlParts parseUrl(const std::string& url) const;

    /// GET request; returns response body or throws.
    std::string httpGet(const std::string& path, int timeoutSec = 10);

    /// POST request; returns response body or throws.
    std::string httpPost(const std::string& path, const std::string& body,
                         int timeoutSec = 30);

    /// Streaming POST request using httplib::Client::send().
    /// Calls receiver for each response body chunk. Sets streamDone=true when
    /// receiver returns false (i.e. SseParser got [DONE]) so the caller can
    /// distinguish intentional stream completion from a real cancellation error.
    /// @throws std::runtime_error on connection error or non-2xx status
    void httpPostStreaming(const std::string& path, const std::string& body,
                           std::function<bool(const char*, size_t)> receiver,
                           bool& streamDone, int timeoutSec);

    std::string baseUrl_;
    std::string model_;
    int contextSize_ = 0;
    bool debug_ = false;
};

} // namespace gaia
