// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/lemonade_client.h"

#include <algorithm>
#include <iostream>
#include <stdexcept>

#include "gaia/sse_parser.h"
#include "gaia/types.h" // getEnvVar

namespace gaia {

// ---------------------------------------------------------------------------
// URL normalization
// ---------------------------------------------------------------------------

/*static*/ std::string LemonadeClient::normalizeUrl(const std::string& url) {
    // Strip trailing slashes
    std::string result = url;
    while (!result.empty() && result.back() == '/') {
        result.pop_back();
    }

    // Preserve OpenAI-compatible /v1 endpoints and Lemonade /api/v1 endpoints.
    const std::string lemonadeSuffix = "/api/v1";
    const std::string openaiSuffix = "/v1";
    const bool hasLemonadeSuffix =
        result.size() >= lemonadeSuffix.size() &&
        result.substr(result.size() - lemonadeSuffix.size()) == lemonadeSuffix;
    const bool hasOpenAiSuffix =
        result.size() >= openaiSuffix.size() &&
        result.substr(result.size() - openaiSuffix.size()) == openaiSuffix;

    if (!hasLemonadeSuffix && !hasOpenAiSuffix) {
        result += lemonadeSuffix;
    }

    return result;
}

// ---------------------------------------------------------------------------
// Constructors
// ---------------------------------------------------------------------------

LemonadeClient::LemonadeClient(const LemonadeClientConfig& config)
    : debug_(config.debug) {
    // Resolve base URL: config → env LEMONADE_BASE_URL → default
    std::string url = config.baseUrl;
    if (url.empty()) {
        url = getEnvVar("LEMONADE_BASE_URL");
    }
    if (url.empty()) {
        url = LEMONADE_DEFAULT_URL;
    }
    setBaseUrl(url);
    http_.setDebug(debug_);

    // Resolve model: config → env LEMONADE_MODEL → empty
    model_ = config.model;
    if (model_.empty()) {
        model_ = getEnvVar("LEMONADE_MODEL");
    }

    contextSize_ = config.contextSize;
}

// Legacy constructor — delegates to config-based constructor.
LemonadeClient::LemonadeClient(const std::string& baseUrl, bool debug)
    : LemonadeClient(LemonadeClientConfig{baseUrl, "", 0, debug}) {}

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

void LemonadeClient::setBaseUrl(const std::string& url) {
    baseUrl_ = normalizeUrl(url);
    http_.setBaseUrl(baseUrl_);
}

// ---------------------------------------------------------------------------
// Low-level HTTP helpers — thin forwards to gaia::HttpClient
// ---------------------------------------------------------------------------

std::string LemonadeClient::httpGet(const std::string& path, int timeoutSec) {
    // Lemonade health probes must fail fast, so the connect timeout tracks the
    // read timeout rather than the client default.
    return http_.get(path, {}, timeoutSec, timeoutSec).body;
}

std::string LemonadeClient::httpPost(const std::string& path, const std::string& body,
                                     int timeoutSec) {
    return http_.post(path, body, {}, timeoutSec).body;
}

// ---------------------------------------------------------------------------
// Server status
// ---------------------------------------------------------------------------

bool LemonadeClient::isServerRunning() {
    try {
        httpGet("/health", 5);
        return true;
    } catch (...) {
        return false;
    }
}

bool LemonadeClient::ready() {
    try {
        std::string body = httpGet("/health", 5);
        json j = json::parse(body);
        return j.value("status", "") == "ok";
    } catch (...) {
        return false;
    }
}

LemonadeHealth LemonadeClient::healthCheck() {
    LemonadeHealth h;
    try {
        std::string body = httpGet("/health", 10);
        h.raw = json::parse(body);
        h.running = true;

        // Extract loaded model id and context size from health response.
        // Lemonade health format:
        //   {"all_models_loaded": [{"model_name": "...", "recipe_options": {"ctx_size": N}}]}
        if (h.raw.contains("all_models_loaded") && h.raw["all_models_loaded"].is_array() &&
            !h.raw["all_models_loaded"].empty()) {
            const auto& first = h.raw["all_models_loaded"][0];
            h.modelId = first.value("model_name", "");
            if (first.contains("recipe_options") && first["recipe_options"].is_object()) {
                h.contextSize = first["recipe_options"].value("ctx_size", 0);
            }
        }
    } catch (...) {
        h.running = false;
    }
    return h;
}

LemonadeStatus LemonadeClient::getStatus() {
    LemonadeStatus status;
    status.url = baseUrl_;

    try {
        LemonadeHealth health = healthCheck();
        status.running     = health.running;
        status.healthData  = health.raw;

        if (health.running) {
            status.contextSize = health.contextSize;

            // Collect loaded models (non-fatal if endpoint fails)
            try {
                json models = listModels();
                if (models.contains("data") && models["data"].is_array()) {
                    for (const auto& m : models["data"]) {
                        status.loadedModels.push_back(m);
                    }
                }
            } catch (...) {}
        }
    } catch (const std::exception& e) {
        status.running = false;
        status.error   = e.what();
    }

    return status;
}

// ---------------------------------------------------------------------------
// Model management
// ---------------------------------------------------------------------------

json LemonadeClient::listModels(bool showAll) {
    std::string path = "/models";
    if (showAll) path += "?show_all=true";
    std::string body = httpGet(path, 10);
    return json::parse(body);
}

json LemonadeClient::getModelDetails(const std::string& modelId) {
    std::string body = httpGet("/models/" + modelId, 10);
    return json::parse(body);
}

void LemonadeClient::unloadModel() {
    httpPost("/unload", "{}", 30);
    model_.clear();
}

bool LemonadeClient::checkModelLoaded(const std::string& modelId) {
    try {
        json models = listModels();
        if (models.contains("data") && models["data"].is_array()) {
            std::string lower = modelId;
            std::transform(lower.begin(), lower.end(), lower.begin(), ::tolower);

            for (const auto& m : models["data"]) {
                std::string name = m.value("id", "");
                std::transform(name.begin(), name.end(), name.begin(), ::tolower);
                if (name == lower) return true;
            }
        }
    } catch (...) {}
    return false;
}

json LemonadeClient::getStats() {
    std::string body = httpGet("/stats", 10);
    return json::parse(body);
}

json LemonadeClient::getSystemInfo(bool verbose) {
    std::string path = "/system-info";
    if (verbose) path += "?verbose=true";
    std::string body = httpGet(path, 10);
    return json::parse(body);
}

json LemonadeClient::setParams(const json& params) {
    std::string body = httpPost("/params", params.dump(), 30);
    return json::parse(body);
}

json LemonadeClient::loadModel(const std::string& modelName, int ctxSize, int timeoutSec) {
    json payload = {{"model_name", modelName}};
    if (ctxSize > 0) {
        payload["ctx_size"] = ctxSize;
    }

    if (debug_) {
        std::cerr << "[Lemonade] Loading model: " << modelName
                  << " ctx_size=" << ctxSize << std::endl;
    }

    std::string body = httpPost("/load", payload.dump(), timeoutSec);
    json result = json::parse(body);
    model_ = modelName; // store on success
    return result;
}

void LemonadeClient::ensureModelLoaded(const std::string& modelName, int ctxSize) {
    std::string effectiveModel = modelName.empty() ? model_ : modelName;
    int effectiveCtx = (ctxSize < 0) ? contextSize_ : ctxSize;

    if (effectiveModel.empty()) {
        if (debug_) {
            std::cerr << "[Lemonade] ensureModelLoaded: no model specified, skipping" << std::endl;
        }
        return;
    }

    // Non-Lemonade OpenAI-compatible servers such as Ollama do not expose
    // Lemonade's /health and /load endpoints. In that case, skip model
    // management and let the server handle model selection from the request.
    if (baseUrl_.size() < 7 || baseUrl_.substr(baseUrl_.size() - 7) != "/api/v1") {
        if (debug_) {
            std::cerr << "[Lemonade] ensureModelLoaded: non-Lemonade endpoint detected, skipping" << std::endl;
        }
        return;
    }

    LemonadeHealth h = healthCheck();

    if (h.running && h.modelId == effectiveModel) {
        // Model already loaded — check context size if requested
        if (effectiveCtx == 0 || h.contextSize == effectiveCtx) {
            if (debug_) {
                std::cerr << "[Lemonade] Model already loaded: " << effectiveModel
                          << " ctx=" << h.contextSize << std::endl;
            }
            return;
        }
    }

    loadModel(effectiveModel, effectiveCtx);
}

std::pair<bool, std::string> LemonadeClient::validateContextSize(int tokens) {
    try {
        LemonadeHealth h = healthCheck();
        if (!h.running) {
            return {true, "Server not running — cannot validate"};
        }
        if (h.contextSize > 0 && tokens > h.contextSize) {
            return {false, "Token count " + std::to_string(tokens) +
                               " exceeds server context size " + std::to_string(h.contextSize)};
        }
        return {true, ""};
    } catch (const std::exception& e) {
        return {true, std::string("Validation skipped: ") + e.what()};
    }
}

// ---------------------------------------------------------------------------
// Inference
// ---------------------------------------------------------------------------

std::string LemonadeClient::chatCompletions(const json& requestBody, int timeoutSec) {
    std::string responseBody = httpPost("/chat/completions", requestBody.dump(), timeoutSec);

    // Lemonade returns HTTP 200 even for server-side errors -- check the body
    // before returning so all callers get a proper exception instead of a
    // malformed response.
    try {
        json responseJson = json::parse(responseBody);
        if (responseJson.contains("error")) {
            std::string errMsg = "LLM server error";
            try {
                const auto& inner = responseJson["error"]["details"]["response"]["error"];
                std::string msg = inner.value("message", "");
                if (msg.find("exceeds the available context size") != std::string::npos) {
                    int nCtx    = inner.value("n_ctx", 0);
                    int nPrompt = inner.value("n_prompt_tokens", 0);
                    errMsg =
                        "Server context window too small: prompt is " +
                        std::to_string(nPrompt) + " tokens but server n_ctx=" +
                        std::to_string(nCtx) + ".\n" +
                        "  Restart Lemonade with a larger context:\n" +
                        "    lemonade-server serve --ctx-size 32768\n" +
                        "  or via the helper script:\n" +
                        "    .\\installer\\scripts\\start-lemonade.ps1 -CtxSize 32768";
                } else if (!msg.empty()) {
                    errMsg = msg;
                }
            } catch (...) {}
            throw std::runtime_error(errMsg);
        }
    } catch (const std::runtime_error&) {
        throw;
    } catch (...) {
        // JSON parse failed — return raw body and let the caller handle it
    }

    return responseBody;
}

// ---------------------------------------------------------------------------
// Streaming inference
// ---------------------------------------------------------------------------

void LemonadeClient::httpPostStreaming(const std::string& path, const std::string& body,
                                       std::function<bool(const char*, size_t)> receiver,
                                       int timeoutSec) {
    http_.postStreaming(
        path, body,
        [&receiver](const char* data, size_t len) { return receiver(data, len); },
        {}, timeoutSec);
}

std::string LemonadeClient::chatCompletionsStreaming(const json& requestBody,
                                                      StreamCallback onToken,
                                                      int timeoutSec) {
    json body = requestBody;
    body["stream"] = true;

    SseParser parser(onToken);
    std::string rawBytes;

    httpPostStreaming(
        "/chat/completions",
        body.dump(),
        [&parser, &rawBytes](const char* data, size_t len) -> bool {
            rawBytes.append(data, len);
            return parser.feed(data, len);
        },
        timeoutSec
    );

    // If no tokens were extracted, the server may have returned an error or a
    // non-streaming response. Apply the same embedded-error check as chatCompletions().
    if (!parser.hasTokens() && !rawBytes.empty()) {
        try {
            const json responseJson = json::parse(rawBytes);
            if (responseJson.contains("error")) {
                std::string errMsg = "LLM server error";
                try {
                    const auto& inner =
                        responseJson["error"]["details"]["response"]["error"];
                    std::string msg = inner.value("message", "");
                    if (msg.find("exceeds the available context size") != std::string::npos) {
                        int nCtx    = inner.value("n_ctx", 0);
                        int nPrompt = inner.value("n_prompt_tokens", 0);
                        errMsg =
                            "Server context window too small: prompt is " +
                            std::to_string(nPrompt) + " tokens but server n_ctx=" +
                            std::to_string(nCtx) + ".\n" +
                            "  Restart Lemonade with a larger context:\n" +
                            "    lemonade-server serve --ctx-size 32768\n" +
                            "  or via the helper script:\n" +
                            "    .\\installer\\scripts\\start-lemonade.ps1 -CtxSize 32768";
                    } else if (!msg.empty()) {
                        errMsg = msg;
                    }
                } catch (...) {}
                throw std::runtime_error(errMsg);
            }
        } catch (const std::runtime_error&) {
            throw;
        } catch (...) {
            // Not valid JSON — return raw bytes for caller's fallback handling
        }
    }

    return rawBytes;
}

} // namespace gaia
