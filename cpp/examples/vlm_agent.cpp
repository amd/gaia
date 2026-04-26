// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Minimal VLM example: loads an image from disk and asks a vision model
// about it via the OpenAI-compatible /chat/completions endpoint.
//
// Usage:  vlm_agent <image_path> [prompt]
//
// Requires a Lemonade server running with a VLM model loaded.
//   Environment:
//     LEMONADE_BASE_URL   (default: http://localhost:8000/api/v1)
//     GAIA_MODEL_ID       (default: Qwen3-VL-4B-Instruct-GGUF)

#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/types.h>

int main(int argc, char** argv) {
    if (argc < 2) {
        std::cerr << "Usage: " << (argc > 0 ? argv[0] : "vlm_agent")
                  << " <image_path> [prompt]\n";
        return 2;
    }
    std::string imagePath = argv[1];
    std::string prompt = (argc >= 3) ? argv[2] : "Describe this image.";

    try {
        gaia::Image img = gaia::Image::fromFile(imagePath);
        std::cout << "Loaded " << img.size() << " bytes, MIME: "
                  << img.mimeType() << "\n";

        gaia::AgentConfig cfg;
        cfg.modelId = gaia::getEnvVar("GAIA_MODEL_ID", "Qwen3-VL-4B-Instruct-GGUF");
        cfg.contextSize = 32768;   // VLM-recommended
        cfg.maxSteps = 3;
        cfg.silentMode = false;

        gaia::Agent agent(cfg);
        gaia::json result = agent.processQuery(prompt, {img});

        std::cout << "\n== Answer ==\n"
                  << result.value("result", "<no result>") << "\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "Error: " << e.what() << "\n";
        return 1;
    }
}
