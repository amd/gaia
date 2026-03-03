// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/lemonade_client.h"

#include <algorithm>
#include <iostream>
#include <stdexcept>

#include <httplib.h>

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

    // Append /api/v1 if not already present
    const std::string suffix = "/api/v1";
    if (result.size() < suffix.size() ||
        result.substr(result.size() - suffix.size()) != suffix) {
        result += suffix;
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
    baseUrl_ = normalizeUrl(url);

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
}

// ---------------------------------------------------------------------------
// URL parsing
// ---------------------------------------------------------------------------

LemonadeClient::UrlParts LemonadeClient::parseUrl(const std::string& url) const {
    UrlParts parts;
    std::string rest = url;

    if (rest.substr(0, 8) == "https://") {
        parts.useSSL = true;
        parts.port   = 443;
        rest         = rest.substr(8);
    } else if (rest.substr(0, 7) == "http://") {
        rest = rest.substr(7);
    }

    // Split authority from path
    auto slashPos = rest.find('/');
    std::string authority;
    if (slashPos != std::string::npos) {
        authority       = rest.substr(0, slashPos);
        parts.basePath  = rest.substr(slashPos);
    } else {
        authority = rest;
    }

    // Split host:port
    auto colonPos = authority.find(':');
    if (colonPos != std::string::npos) {
        parts.host = authority.substr(0, colonPos);
        try {
            parts.port = std::stoi(authority.substr(colonPos + 1));
        } catch (const std::exception&) {
            throw std::runtime_error("Invalid port in URL: " + url);
        }
    } else {
        parts.host = authority;
    }

    return parts;
}

// ---------------------------------------------------------------------------
// Low-level HTTP helpers
// ---------------------------------------------------------------------------

std::string LemonadeClient::httpGet(const std::string& path, int timeoutSec) {
    UrlParts p = parseUrl(baseUrl_);
    std::string fullPath = p.basePath + path;
    if (fullPath.empty()) fullPath = "/";

    if (debug_) {
        std::cerr << "[Lemonade] GET " << p.host << ":" << p.port << fullPath << std::endl;
    }

    if (p.useSSL) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
        httplib::SSLClient cli(p.host, p.port);
        cli.set_connection_timeout(timeoutSec);
        cli.set_read_timeout(timeoutSec);
        auto res = cli.Get(fullPath);
        if (!res) {
            throw std::runtime_error("GET " + fullPath + " failed: connection error");
        }
        if (res->status < 200 || res->status >= 300) {
            throw std::runtime_error("GET " + fullPath + " returned HTTP " +
                                     std::to_string(res->status));
        }
        return res->body;
#else
        throw std::runtime_error("SSL not supported. Use http:// base URL.");
#endif
    }

    httplib::Client cli(p.host, p.port);
    cli.set_connection_timeout(timeoutSec);
    cli.set_read_timeout(timeoutSec);
    auto res = cli.Get(fullPath);
    if (!res) {
        throw std::runtime_error("GET " + fullPath + " failed: connection error to " +
                                 p.host + ":" + std::to_string(p.port));
    }
    if (res->status < 200 || res->status >= 300) {
        throw std::runtime_error("GET " + fullPath + " returned HTTP " +
                                 std::to_string(res->status));
    }
    return res->body;
}

std::string LemonadeClient::httpPost(const std::string& path, const std::string& body,
                                     int timeoutSec) {
    UrlParts p = parseUrl(baseUrl_);
    std::string fullPath = p.basePath + path;
    if (fullPath.empty()) fullPath = "/";

    if (debug_) {
        std::cerr << "[Lemonade] POST " << p.host << ":" << p.port << fullPath << std::endl;
    }

    if (p.useSSL) {
#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
        httplib::SSLClient cli(p.host, p.port);
        cli.set_connection_timeout(30);
        cli.set_read_timeout(timeoutSec);
        auto res = cli.Post(fullPath, body, "application/json");
        if (!res) {
            throw std::runtime_error("POST " + fullPath + " failed: connection error");
        }
        if (res->status < 200 || res->status >= 300) {
            throw std::runtime_error("POST " + fullPath + " returned HTTP " +
                                     std::to_string(res->status) + ": " + res->body);
        }
        return res->body;
#else
        throw std::runtime_error("SSL not supported. Use http:// base URL.");
#endif
    }

    httplib::Client cli(p.host, p.port);
    cli.set_connection_timeout(30);
    cli.set_read_timeout(timeoutSec);
    auto res = cli.Post(fullPath, body, "application/json");
    if (!res) {
        throw std::runtime_error("POST " + fullPath + " failed: connection error to " +
                                 p.host + ":" + std::to_string(p.port));
    }
    if (res->status < 200 || res->status >= 300) {
        throw std::runtime_error("POST " + fullPath + " returned HTTP " +
                                 std::to_string(res->status) + ": " + res->body);
    }
    return res->body;
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
    return httpPost("/chat/completions", requestBody.dump(), timeoutSec);
}

} // namespace gaia
