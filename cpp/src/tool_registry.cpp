// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/tool_registry.h"

#include <algorithm>
#include <sstream>
#include <stdexcept>

namespace gaia {

void ToolRegistry::registerTool(ToolInfo info) {
    if (tools_.count(info.name)) {
        throw std::runtime_error("Tool already registered: " + info.name);
    }
    std::string name = info.name;
    tools_.emplace(std::move(name), std::move(info));
}

void ToolRegistry::registerTool(const std::string& name,
                                const std::string& description,
                                ToolCallback callback,
                                std::vector<ToolParameter> params,
                                bool atomic) {
    ToolInfo info;
    info.name = name;
    info.description = description;
    info.callback = std::move(callback);
    info.parameters = std::move(params);
    info.atomic = atomic;
    registerTool(std::move(info));
}

const ToolInfo* ToolRegistry::findTool(const std::string& name) const {
    auto it = tools_.find(name);
    if (it != tools_.end()) {
        return &it->second;
    }
    return nullptr;
}

std::string ToolRegistry::resolveName(const std::string& name) const {
    // Try case-insensitive suffix match (handles unprefixed MCP names)
    std::string lower = toLower(name);
    std::string suffix = "_" + lower;

    std::vector<std::string> matches;
    for (const auto& [registeredName, _] : tools_) {
        std::string regLower = toLower(registeredName);
        if (regLower.size() >= suffix.size() &&
            regLower.substr(regLower.size() - suffix.size()) == suffix) {
            matches.push_back(registeredName);
        }
    }
    if (matches.size() == 1) {
        return matches[0];
    }

    // Try exact case-insensitive match
    matches.clear();
    for (const auto& [registeredName, _] : tools_) {
        if (toLower(registeredName) == lower) {
            matches.push_back(registeredName);
        }
    }
    if (matches.size() == 1) {
        return matches[0];
    }

    return "";
}

bool ToolRegistry::hasTool(const std::string& name) const {
    return tools_.count(name) > 0;
}

bool ToolRegistry::removeTool(const std::string& name) {
    return tools_.erase(name) > 0;
}

const std::map<std::string, ToolInfo>& ToolRegistry::allTools() const {
    return tools_;
}

size_t ToolRegistry::size() const {
    return tools_.size();
}

void ToolRegistry::clear() {
    tools_.clear();
}

std::string ToolRegistry::formatForPrompt() const {
    std::ostringstream oss;
    for (const auto& [name, tool] : tools_) {
        oss << "- " << name << "(";

        bool first = true;
        for (const auto& param : tool.parameters) {
            if (!first) oss << ", ";
            first = false;

            oss << param.name;
            if (!param.required) oss << "?";
            oss << ": " << paramTypeToString(param.type);
        }

        oss << "): " << tool.description << "\n";
    }
    return oss.str();
}

json ToolRegistry::executeTool(const std::string& name, const json& args) const {
    const ToolInfo* tool = findTool(name);
    if (!tool) {
        // Try resolving the name
        std::string resolved = resolveName(name);
        if (!resolved.empty()) {
            tool = findTool(resolved);
        }
    }

    if (!tool) {
        return json{{"status", "error"}, {"error", "Tool '" + name + "' not found"}};
    }

    if (!tool->callback) {
        return json{{"status", "error"}, {"error", "Tool '" + name + "' has no callback"}};
    }

    try {
        return tool->callback(args);
    } catch (const std::exception& e) {
        return json{{"status", "error"}, {"error", std::string("Tool execution failed: ") + e.what()}};
    }
}

std::string ToolRegistry::toLower(const std::string& s) {
    std::string result = s;
    std::transform(result.begin(), result.end(), result.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return result;
}

} // namespace gaia
