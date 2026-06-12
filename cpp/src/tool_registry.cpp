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

bool ToolRegistry::setEnabled(const std::string& name, bool enabled) {
    auto it = tools_.find(name);
    if (it == tools_.end()) return false;
    it->second.enabled = enabled;
    return true;
}

bool ToolRegistry::isEnabled(const std::string& name) const {
    const ToolInfo* tool = findTool(name);
    return tool && tool->enabled;
}

std::vector<std::string> ToolRegistry::enabledTools() const {
    std::vector<std::string> result;
    for (const auto& [name, tool] : tools_) {
        if (tool.enabled) result.push_back(name);
    }
    return result;
}

std::string ToolRegistry::formatForPrompt() const {
    std::ostringstream oss;
    for (const auto& [name, tool] : tools_) {
        if (!tool.enabled) continue;
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

    if (!tool->enabled) {
        return json{{"status", "error"}, {"error", "Tool '" + tool->name + "' is disabled"}};
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
    } else if (!tool->parameters.empty()) {
        // Auto-validate against declared parameter schema
        std::string validationError = validateArgsAgainstSchema(tool->parameters, effectiveArgs);
        if (!validationError.empty()) {
            return json{{"status", "error"}, {"error", "Invalid arguments for '" + resolvedName + "': " + validationError}};
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

std::string ToolRegistry::validateArgsAgainstSchema(
    const std::vector<ToolParameter>& params, const json& args) {
    // args should be an object (or null/missing treated as empty object)
    json effectiveArgs = (args.is_null() || args.is_discarded()) ? json::object() : args;
    if (!effectiveArgs.is_object()) {
        return "expected object, got " + std::string(effectiveArgs.type_name());
    }

    // Check required parameters are present
    for (const auto& param : params) {
        if (param.required && !effectiveArgs.contains(param.name)) {
            return "missing required parameter '" + param.name + "'";
        }

        // Type-check if the parameter is present
        if (effectiveArgs.contains(param.name)) {
            const auto& val = effectiveArgs[param.name];
            bool typeOk = false;
            switch (param.type) {
                case ToolParamType::STRING:
                    typeOk = val.is_string();
                    break;
                case ToolParamType::INTEGER:
                    typeOk = val.is_number_integer();
                    break;
                case ToolParamType::NUMBER:
                    typeOk = val.is_number();
                    break;
                case ToolParamType::BOOLEAN:
                    typeOk = val.is_boolean();
                    break;
                case ToolParamType::ARRAY:
                    typeOk = val.is_array();
                    break;
                case ToolParamType::OBJECT:
                    typeOk = val.is_object();
                    break;
                case ToolParamType::UNKNOWN:
                    typeOk = true;  // accept anything
                    break;
            }
            if (!typeOk) {
                return "parameter '" + param.name + "' should be " +
                       paramTypeToString(param.type) + ", got " +
                       std::string(val.type_name());
            }
        }
    }

    return "";  // valid
}

} // namespace gaia
