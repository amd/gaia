// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/model_registry.h"

namespace gaia {

const std::vector<ModelCapability>& knownModels() {
    // Mirrors MODELS in src/gaia/llm/lemonade_client.py (see header note).
    // gemma4-it-e2b-FLM is false because the FastFlowLM/NPU server 500s on an
    // OpenAI ``tools`` payload; the embedders are false because they never chat.
    static const std::vector<ModelCapability> kModels = {
        {"Gemma-4-E4B-it-GGUF",        true},
        {"gemma4-it-e2b-FLM",          false},
        {"Qwen3.5-35B-A3B-GGUF",       true},
        {"Qwen3-0.6B-GGUF",            true},
        {"Qwen3-VL-4B-Instruct-GGUF",  true},
        {"Qwen3-8B-GGUF",              true},
        {"user.embeddinggemma-300m-GGUF", false},
        {"embed-gemma-300m-FLM",       false},
    };
    return kModels;
}

bool isToolCallingModel(const std::string& modelId) {
    if (modelId.empty()) return false;
    for (const auto& m : knownModels()) {
        if (m.modelId == modelId) return m.toolCalling;
    }
    return false; // Unknown model — conservative. See header for the rationale.
}

} // namespace gaia
