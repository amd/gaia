// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/lemonade_client.h"

#include <algorithm>
#include <iostream>
#include <stdexcept>

#include "gaia/sse_parser.h"
#include "gaia/types.h" // getEnvVar

namespace gaia {

namespace {

const json* findObject(const json& parent, const char* key) {
    if (!parent.is_object()) return nullptr;
    const auto it = parent.find(key);
    return (it != parent.end() && it->is_object()) ? &*it : nullptr;
}

// One decoder for every error shape: Lemonade's nested llama-server error,
// the OpenAI {"error":{"message":..}} object, and a bare {"error":"..."}.
std::string describeServerError(const json& body) {
    const auto it = body.find("error");
    if (it == body.end()) return "LLM server error";
    const json& err = *it;
    if (err.is_string()) {
        const std::string msg = err.get<std::string>();
        return msg.empty() ? "LLM server error" : msg;
    }

    const json* inner = &err;
    if (const json* details = findObject(err, "details")) {
        if (const json* response = findObject(*details, "response")) {
            if (const json* nested = findObject(*response, "error")) inner = nested;
        }
    }

    auto stringField = [](const json& obj, const char* key) -> std::string {
        if (!obj.is_object()) return "";
        const auto f = obj.find(key);
        return (f != obj.end() && f->is_string()) ? f->get<std::string>() : "";
    };
    auto intField = [](const json& obj, const char* key) -> int {
        const auto f = obj.find(key);
        return (f != obj.end() && f->is_number_integer()) ? f->get<int>() : 0;
    };

    std::string msg = stringField(*inner, "message");
    if (msg.empty()) msg = stringField(err, "message");

    if (msg.find("exceeds the available context size") != std::string::npos) {
        return "Server context window too small: prompt is " +
               std::to_string(intField(*inner, "n_prompt_tokens")) +
               " tokens but server n_ctx=" + std::to_string(intField(*inner, "n_ctx")) +
               ".\n"
               "  Restart Lemonade with a larger context:\n"
               "    set LEMONADE_CTX_SIZE=32768 in the environment before starting the server\n"
               "  or via the helper script:\n"
               "    .\\installer\\scripts\\start-lemonade.ps1 -CtxSize 32768";
    }
    return msg.empty() ? "LLM server error" : msg;
}

// Lemonade answers some failures with HTTP 200 and an {"error":..} body.
void throwIfErrorBody(const std::string& body) {
    json parsed;
    try {
        parsed = json::parse(body);
    } catch (const json::parse_error&) {
        return; // not a JSON body -- the caller handles the raw text
    }
    if (parsed.is_object() && parsed.contains("error") && !parsed["error"].is_null()) {
        throw std::runtime_error(describeServerError(parsed));
    }
}

} // namespace

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
    throwIfErrorBody(responseBody);

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
    std::vector<ToolCall> ignored;
    return chatCompletionsStreaming(requestBody, std::move(onToken), ignored, timeoutSec);
}

std::string LemonadeClient::chatCompletionsStreaming(const json& requestBody,
                                                      StreamCallback onToken,
                                                      std::vector<ToolCall>& outToolCalls,
                                                      int timeoutSec) {
    outToolCalls.clear();

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

    parser.finish();

    if (parser.hasError()) {
        throw std::runtime_error(describeServerError(parser.errorEvent()));
    }
    if (parser.malformedEventCount() > 0) {
        throw std::runtime_error(
            std::to_string(parser.malformedEventCount()) +
            " streamed event(s) from " + baseUrl_ +
            "/chat/completions were not valid chat-completion JSON, so part of the "
            "response was lost. First bad event: " + parser.firstMalformedEvent() +
            "\n  Check that the server speaks the OpenAI streaming format, or "
            "disable streaming (AgentConfig::streaming = false).");
    }

    // A server that ignored "stream": true may answer with a plain JSON body.
    if (!parser.hasTokens() && !parser.hasToolCalls() && !rawBytes.empty()) {
        throwIfErrorBody(rawBytes);
    }

    // Only a completed stream yields complete argument strings. A connection
    // dropped mid-tool_call can leave arguments that still parse (a balanced
    // brace lands by luck) but mean something else entirely — executing that
    // is worse than failing. Deliberately fires for the tool-less overload
    // too: a truncated stream is a failed request whoever asked for it.
    if (parser.hasToolCalls() && !parser.done()) {
        throw std::runtime_error(
            "Stream ended before [DONE] while assembling tool_calls — the "
            "arguments are incomplete and must not be executed. Check the "
            "server connection and retry.");
    }
    outToolCalls = parser.toolCalls();
    return rawBytes;
}

} // namespace gaia
