// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/model_registry.h"

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

bool isToolCallingModel(const std::string& modelId) {
    // Unknown model — conservative. See header for the rationale.
    return knownToolCalling(modelId).value_or(false);
}

} // namespace gaia
