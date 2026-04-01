// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/types.h"

#include <fstream>
#include <stdexcept>

namespace gaia {

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
