// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Media tools over OpenAI-compatible endpoints, which Lemonade serves:
//   transcribe_audio  POST /audio/transcriptions (Whisper)
//   generate_image    POST /images/generations  (Stable Diffusion)
// Each is registered only when its model is configured.

#pragma once

#include <functional>
#include <string>

#include "gaia/export.h"
#include "gaia/tool_registry.h"

namespace gaia {

struct GAIA_API MediaEndpoint {
    std::string baseUrl;  // e.g. http://localhost:13305/api/v1
    std::string apiKey;   // sent as Bearer when set
};

struct GAIA_API MediaToolsOptions {
    std::string transcribeModel;  // empty: no transcribe_audio
    std::string imageModel;       // empty: no generate_image
    /// Where to send each request, resolved per call (so it follows the
    /// agent's endpoint). The audio endpoint may differ (Fireworks hosts
    /// transcription on its own host).
    std::function<MediaEndpoint()> chatEndpoint;
    std::function<MediaEndpoint()> audioEndpoint;  // empty: use chatEndpoint
    /// Returns a refusal for paths the agent may not read or write ("" = ok).
    std::function<std::string(const std::string&)> checkPath;
};

class GAIA_API MediaTools {
public:
    static void registerAll(ToolRegistry& registry, MediaToolsOptions options);

    /// Standard base64 decode; throws std::invalid_argument on bad input.
    static std::string base64Decode(const std::string& in);
};

} // namespace gaia
