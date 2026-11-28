// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Media tools over OpenAI-compatible endpoints, which Lemonade serves:
//   transcribe_audio  POST /audio/transcriptions (Whisper)
//   generate_image    POST /images/generations   (Stable Diffusion / TheNoise)
//   list_sd_models    GET  /models               (downloaded image models)

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

/// Saved image creation defaults from GAIA's config.json.
struct GAIA_API ImageSettings {
    std::string model;  // empty = default ("SDXL-Turbo")
    std::string size;   // optional "WIDTHxHEIGHT", empty = model default
    int steps = 0;      // optional 1..100, 0 = model default
};

struct GAIA_API MediaToolsOptions {
    std::string transcribeModel;  // empty: no transcribe_audio
    std::string imageModel;       // explicit override (e.g. --image-model); also enables image tools
    bool enableImageTools = false;  // register generate_image and list_sd_models
    /// Read saved image settings fresh per call (default: GAIA's config.json).
    std::function<ImageSettings()> imageSettings;
    /// Default folder for generated images when output_path is omitted
    /// (default: <GAIA_CONFIG_DIR>/cache/sd/images).
    std::function<std::string()> imageOutputDir;
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

    /// Read image_model, image_size, image_steps from GAIA's config.json
    /// (GAIA_CONFIG_FILE -> GAIA_CONFIG_DIR -> GAIA_HOME -> ~/.gaia).
    /// Throws std::runtime_error if config.json exists but is malformed.
    static ImageSettings loadImageSettingsFromConfig();

    /// Default generated images folder (<GAIA_CONFIG_DIR>/cache/sd/images).
    static std::string defaultImageOutputDir();

    /// take_screenshot: capture the screen to a PNG with the OS's own tool
    /// (PowerShell + System.Drawing, screencapture, grim / gnome-screenshot /
    /// scrot / ImageMagick). CONFIRM: a screenshot can show anything.
    static ToolInfo screenshotTool(std::function<std::string(const std::string&)> checkPath = {});

    /// Standard base64 decode; throws std::invalid_argument on bad input.
    static std::string base64Decode(const std::string& in);
};

} // namespace gaia
