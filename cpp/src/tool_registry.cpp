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
                                bool atomic,
                                std::optional<ToolPolicy> policy) {
    ToolInfo info;
    info.name = name;
    info.description = description;
    info.callback = std::move(callback);
    info.parameters = std::move(params);
    info.atomic = atomic;
    info.policy = policy.value_or(defaultPolicy_);
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

json ToolRegistry::executeTool(const std::string& name, const json& args) {
    const ToolInfo* tool = findTool(name);
    if (!tool) {
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

    const std::string& resolvedName = tool->name;

    // 1. DENY check
    if (tool->policy == ToolPolicy::DENY) {
        return json{{"status", "error"}, {"error", "Tool '" + resolvedName + "' is denied by policy"}};
    }

    // 2. Argument validation (runs before confirmation so user sees clean args)
    json effectiveArgs = args;
    if (tool->validateArgs.has_value()) {
        try {
            effectiveArgs = (*tool->validateArgs)(resolvedName, args);
        } catch (const std::invalid_argument& e) {
            return json{{"status", "error"}, {"error", std::string("Argument validation failed: ") + e.what()}};
        }
    }

    // 3. CONFIRM check
    if (tool->policy == ToolPolicy::CONFIRM) {
        // Fast path: already permanently allowed
        if (allowedStore_ && allowedStore_->isAlwaysAllowed(resolvedName)) {
            // proceed
        } else {
            // Fail-closed: no callback = deny
            if (!confirmCallback_) {
                return json{{"status", "error"}, {"error", "Tool '" + resolvedName + "' requires confirmation but no callback is set"}};
            }

            ToolConfirmResult decision = confirmCallback_(resolvedName, effectiveArgs);
            if (decision == ToolConfirmResult::ALWAYS_ALLOW) {
                if (allowedStore_) {
                    allowedStore_->addAlwaysAllowed(resolvedName);
                }
            } else if (decision == ToolConfirmResult::DENY) {
                return json{{"status", "error"}, {"error", "Tool '" + resolvedName + "' execution denied by user"}};
            }
            // ALLOW_ONCE falls through
        }
    }

    // 4. Execute
    try {
        return tool->callback(effectiveArgs);
    } catch (const std::exception& e) {
        return json{{"status", "error"}, {"error", std::string("Tool execution failed: ") + e.what()}};
    }
}

void ToolRegistry::setConfirmCallback(ToolConfirmCallback cb) {
    confirmCallback_ = std::move(cb);
}

void ToolRegistry::setDefaultPolicy(ToolPolicy policy) {
    defaultPolicy_ = policy;
}

ToolPolicy ToolRegistry::defaultPolicy() const {
    return defaultPolicy_;
}

void ToolRegistry::setAllowedToolsStore(std::shared_ptr<AllowedToolsStore> store) {
    allowedStore_ = std::move(store);
}

std::string ToolRegistry::toLower(const std::string& s) {
    std::string result = s;
    std::transform(result.begin(), result.end(), result.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return result;
}

} // namespace gaia
