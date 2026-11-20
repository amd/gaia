// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/model_registry.h"

#include <algorithm>

namespace gaia {

const std::vector<ModelCapability>& knownModels() {
    // Mirrors MODELS in src/gaia/llm/lemonade_client.py (see header note).
    // The embedders are false because they never chat.
    static const std::vector<ModelCapability> kModels = {
        {"kokoro-v1",                  true},
        {"Gemma-4-E4B-it-GGUF",        true},
        {"user.Qwen3.8-Flash-Next-GGUF", true},
        {"Qwen3.6-35B-A3B-GGUF",       true},
        {"Muse-Glimmer-30B-GGUF",      true},
        {"user.ggml-gpt-oss-20b-MXFP4-GGUF", true},
        {"gpt-oss-20b-GGUF",           true},
        {"gpt-oss-120b-mxfp-GGUF",     true},
        {"gpt-oss-120b-GGUF",          true},
        {"gemma4-it-e2b-FLM",          true},
        {"gemma4-it-e4b-FLM",          true},
        {"Qwen3.5-35B-A3B-GGUF",       true},
        {"Qwen3-0.6B-GGUF",            true},
        {"Qwen3-VL-4B-Instruct-GGUF",  true},
        {"Qwen3-8B-GGUF",              true},
        {"user.embeddinggemma-300m-GGUF", false},
        {"embed-gemma-300m-FLM",       false},
    };
    return kModels;
}

std::optional<bool> knownToolCalling(const std::string& modelId) {
    for (const auto& m : knownModels()) {
        if (m.modelId == modelId) return m.toolCalling;
    }
    return std::nullopt;
}

bool isCompactModel(const std::string& modelId) {
    static const std::vector<std::string> kCompact = {
        // feature_tier="compact" in Python's MODELS
        "Gemma-4-E4B-it-GGUF", "gemma4-it-e2b-FLM", "gemma4-it-e4b-FLM",
        // measured with gaiacpp on the 30-task suite
        "Gemma-4-E2B-it-GGUF", "Qwen3-0.6B-GGUF", "Qwen3-4B-Instruct-2507-GGUF", "qwen3-it-4b-FLM",
        "qwen3.5-2b-FLM", "qwen3.5-4b-FLM", "user.Qwen3.5-2B-GGUF", "user.Qwen3.5-4B-GGUF",
        "LFM2.5-8B-A1B", "user.Nemotron-3-Nano-4B-GGUF", "user.Granite-4.1-3B-GGUF",
        "user.LFM3-1.7B-GGUF",
    };
    return std::find(kCompact.begin(), kCompact.end(), modelId) != kCompact.end();
}

const std::vector<std::string>& compactWithheldTools() {
    static const std::vector<std::string> kWithheld = {
        "git_status", "git_diff", "git_log", "git_show",  // bash runs git
        "index_codebase", "search_code",                  // grep and file_search find code
        "update_memory", "forget",                        // remember and recall stay
        "env_inspect",                                    // the prompt names the shell and Python
    };
    return kWithheld;
}

bool isToolCallingModel(const std::string& modelId) {
    // Unknown model — conservative. See header for the rationale.
    return knownToolCalling(modelId).value_or(false);
}

} // namespace gaia
