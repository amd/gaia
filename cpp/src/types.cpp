// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/types.h"

#include <cstring>
#include <fstream>
#include <stdexcept>

namespace gaia {

// ---- MIME Detection ----

std::string detectImageMimeType(const std::uint8_t* data, std::size_t size) {
    // Buffers < 12 bytes cannot be safely probed for WebP (offset 8–11).
    // Returning "image/png" for null/short buffers is the AC-15e contract:
    // test fixtures call this function directly with 1/5/11-byte header stubs
    // and assert the safe fallback. In practice, Image::fromBytes already rejects
    // empty byte vectors before calling this, and Image::fromFile reads the full
    // file first — so neither factory can produce a mislabeled Image via this
    // path. Full-sized buffers (>= 12 bytes) with unrecognized magic return ""
    // so callers throw with a clear message.
    if (data == nullptr || size < 12) {
        return "image/png";
    }
    // PNG: 89 50 4E 47 0D 0A 1A 0A
    if (data[0] == 0x89 && data[1] == 0x50 && data[2] == 0x4E && data[3] == 0x47 &&
        data[4] == 0x0D && data[5] == 0x0A && data[6] == 0x1A && data[7] == 0x0A) {
        return "image/png";
    }
    // JPEG: FF D8 FF
    if (data[0] == 0xFF && data[1] == 0xD8 && data[2] == 0xFF) {
        return "image/jpeg";
    }
    // GIF87a / GIF89a
    if (data[0] == 'G' && data[1] == 'I' && data[2] == 'F' && data[3] == '8' &&
        (data[4] == '7' || data[4] == '9') && data[5] == 'a') {
        return "image/gif";
    }
    // WebP: "RIFF" ???? "WEBP"
    if (data[0] == 'R' && data[1] == 'I' && data[2] == 'F' && data[3] == 'F' &&
        data[8] == 'W' && data[9] == 'E' && data[10] == 'B' && data[11] == 'P') {
        return "image/webp";
    }
    // BMP: "BM"
    // Note: "BM" is a 2-byte signature with false-positive potential (any data starting "BM...").
    // Acceptable here because Image::fromBytes enforces an explicit whitelist and callers supply real image files.
    if (data[0] == 'B' && data[1] == 'M') {
        return "image/bmp";
    }
    // Unrecognized magic bytes on a full-sized buffer — return empty string.
    // Callers must either supply an explicit mimeType or reject the input.
    return {};
}

// ---- ContentPart ----

json ContentPart::toJson() const {
    json j;
    if (kind == Kind::TEXT) {
        j["type"] = "text";
        j["text"] = text;
    } else {
        j["type"] = "image_url";
        j["image_url"] = {{"url", imageUrl}};
    }
    return j;
}

ContentPart ContentPart::makeText(std::string t) {
    ContentPart p;
    p.kind = Kind::TEXT;
    p.text = std::move(t);
    return p;
}

ContentPart ContentPart::makeImageUrl(std::string url) {
    ContentPart p;
    p.kind = Kind::IMAGE_URL;
    p.imageUrl = std::move(url);
    return p;
}

// ---- Message ----

json Message::toJson() const {
    json j;
    j["role"] = roleToString(role);
    if (parts.has_value()) {
        json arr = json::array();
        for (const auto& p : *parts) {
            arr.push_back(p.toJson());
        }
        j["content"] = arr;
    } else {
        j["content"] = content;
    }
    if (name.has_value()) j["name"] = name.value();
    if (toolCallId.has_value()) j["tool_call_id"] = toolCallId.value();
    return j;
}

Message Message::fromUser(const std::string& text, const std::vector<Image>& images) {
    Message m;
    m.role = MessageRole::USER;
    if (images.empty()) {
        m.content = text;
        return m;
    }
    std::vector<ContentPart> ps;
    ps.reserve(images.size() + (text.empty() ? 0 : 1));
    if (!text.empty()) {
        ps.push_back(ContentPart::makeText(text));
    }
    for (const auto& img : images) {
        ps.push_back(img.toContentPart());
    }
    m.content = text; // retained for callers that read .content; JSON uses parts
    m.parts = std::move(ps);
    return m;
}

void AgentConfig::validate() const {
    if (baseUrl.empty())
        throw std::invalid_argument("baseUrl must not be empty");
    if (modelId.empty())
        throw std::invalid_argument("modelId must not be empty");
    if (maxSteps <= 0)
        throw std::invalid_argument("maxSteps must be > 0");
    if (maxTokens <= 0)
        throw std::invalid_argument("maxTokens must be > 0");
    if (contextSize <= 0)
        throw std::invalid_argument("contextSize must be > 0");
    if (maxPlanIterations <= 0)
        throw std::invalid_argument("maxPlanIterations must be > 0");
    if (maxConsecutiveRepeats < 2)
        throw std::invalid_argument("maxConsecutiveRepeats must be >= 2");
    if (maxHistoryMessages < 0)
        throw std::invalid_argument("maxHistoryMessages must be >= 0 (0 = unlimited)");
    if (temperature < 0.0 || temperature > 2.0)
        throw std::invalid_argument("temperature must be in [0.0, 2.0]");
}

AgentConfig AgentConfig::fromJson(const json& j) {
    AgentConfig c;
    c.baseUrl               = j.value("baseUrl",               c.baseUrl);
    c.modelId               = j.value("modelId",               c.modelId);
    c.maxSteps              = j.value("maxSteps",              c.maxSteps);
    c.maxPlanIterations     = j.value("maxPlanIterations",     c.maxPlanIterations);
    c.maxConsecutiveRepeats = j.value("maxConsecutiveRepeats", c.maxConsecutiveRepeats);
    c.maxHistoryMessages    = j.value("maxHistoryMessages",    c.maxHistoryMessages);
    c.contextSize           = j.value("contextSize",           c.contextSize);
    c.maxTokens             = j.value("maxTokens",             c.maxTokens);
    c.debug                 = j.value("debug",                 c.debug);
    c.showPrompts           = j.value("showPrompts",           c.showPrompts);
    c.streaming             = j.value("streaming",             c.streaming);
    c.silentMode            = j.value("silentMode",            c.silentMode);
    c.temperature           = j.value("temperature",           c.temperature);
    c.validate();
    return c;
}

AgentConfig AgentConfig::fromJsonFile(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        throw std::runtime_error("Cannot open config file: " + path);
    }
    json j;
    try {
        file >> j;
    } catch (const json::parse_error& e) {
        throw std::runtime_error(
            std::string("Failed to parse config file '") + path + "': " + e.what());
    }
    return fromJson(j);
}

json AgentConfig::toJson() const {
    return json{
        {"baseUrl",               baseUrl},
        {"modelId",               modelId},
        {"maxSteps",              maxSteps},
        {"maxPlanIterations",     maxPlanIterations},
        {"maxConsecutiveRepeats", maxConsecutiveRepeats},
        {"maxHistoryMessages",    maxHistoryMessages},
        {"contextSize",           contextSize},
        {"maxTokens",             maxTokens},
        {"debug",                 debug},
        {"showPrompts",           showPrompts},
        {"streaming",             streaming},
        {"silentMode",            silentMode},
        {"temperature",           temperature}
    };
}

} // namespace gaia
