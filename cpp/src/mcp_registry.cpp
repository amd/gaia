// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/mcp_registry.h"

#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <sstream>

namespace fs = std::filesystem;

namespace gaia {

namespace {

/// Join a list of strings with ", " (empty list -> `empty`).
std::string joinOr(const std::vector<std::string>& items, const char* empty) {
    if (items.empty()) return empty;
    std::ostringstream oss;
    for (size_t i = 0; i < items.size(); ++i) {
        if (i > 0) oss << ", ";
        oss << items[i];
    }
    return oss.str();
}

/// Human-readable JSON type name, for shape errors.
const char* typeName(const json& v) {
    if (v.is_object()) return "object";
    if (v.is_array())  return "array";
    if (v.is_string()) return "string";
    if (v.is_number()) return "number";
    if (v.is_boolean()) return "boolean";
    if (v.is_null())   return "null";
    return "value";
}

/// Stringify an env value the way Python's MCPClient does (`str(value)`), so a
/// server started from either runtime sees byte-identical values.
std::string pythonStr(const json& value) {
    if (value.is_boolean()) return value.get<bool>() ? "True" : "False";
    return value.dump();
}

} // anonymous namespace

// ---------------------------------------------------------------------------
// Construction / search paths
// ---------------------------------------------------------------------------

std::string MCPRegistry::configDir() {
    const char* configured = std::getenv("GAIA_CONFIG_DIR");  // NOLINT(concurrency-mt-unsafe)
    if (configured && *configured) return configured;
#ifdef _WIN32
    const char* profile = std::getenv("USERPROFILE");  // NOLINT(concurrency-mt-unsafe)
    std::string home = profile ? profile : "C:\\Users\\Default";
    return home + "\\.gaia";
#else
    const char* home = std::getenv("HOME");  // NOLINT(concurrency-mt-unsafe)
    std::string h = home ? home : "/tmp";
    return h + "/.gaia";
#endif
}

std::vector<std::string> MCPRegistry::defaultSearchPaths() {
    fs::path dir(configDir());
    // Lowest precedence first: `mcp_servers.json` is the file `gaia connectors`
    // maintains and the Python runtime reads, so it wins over `mcp.json`.
    return {(dir / "mcp.json").string(), (dir / "mcp_servers.json").string()};
}

MCPRegistry::MCPRegistry() : MCPRegistry(defaultSearchPaths()) {}

MCPRegistry::MCPRegistry(std::vector<std::string> searchPaths)
    : searchPaths_(std::move(searchPaths)) {
    if (searchPaths_.empty()) {
        throw std::invalid_argument(
            "MCPRegistry requires at least one search path; pass "
            "MCPRegistry::defaultSearchPaths() for the standard locations.");
    }
}

std::string MCPRegistry::searchedPathsSuffix() const {
    return "searched: " + joinOr(searchPaths_, "(none)");
}

std::string MCPRegistry::writeTargetPath() const {
    return searchPaths_.back();
}

/// fs::exists() clears `ec` when the file simply is not there, so a set `ec`
/// means we could not tell — which is not the same as "not configured".
bool MCPRegistry::pathExists(const std::string& path) {
    std::error_code ec;
    bool present = fs::exists(path, ec);
    if (ec) {
        throw MCPRegistryError(
            "Could not check for an MCP server config at " + path + ": " + ec.message() +
            ". Fix the permissions on that path, or point GAIA_CONFIG_DIR somewhere readable.");
    }
    return present;
}

std::string MCPRegistry::configPath() const {
    for (auto it = searchPaths_.rbegin(); it != searchPaths_.rend(); ++it) {
        if (pathExists(*it)) return *it;
    }
    return writeTargetPath();
}

bool MCPRegistry::configExists() const {
    for (const auto& path : searchPaths_) {
        if (pathExists(path)) return true;
    }
    return false;
}

void MCPRegistry::reload() {
    std::lock_guard<std::mutex> lock(mutex_);
    loaded_ = false;
    servers_.clear();
    entrySource_.clear();
}

// ---------------------------------------------------------------------------
// Loading
// ---------------------------------------------------------------------------

void MCPRegistry::ensureLoadedLocked() const {
    if (loaded_) return;

    std::map<std::string, json> merged;
    std::map<std::string, std::string> sources;
    bool foundAnyFile = false;

    for (const auto& path : searchPaths_) {
        if (!pathExists(path)) continue;

        std::ifstream in(path);
        if (!in) {
            throw MCPRegistryError(
                "MCP server config at " + path + " exists but could not be opened. "
                "Check the file's permissions.");
        }

        json data;
        try {
            // Strict parse: json::parse() rejects trailing content, which
            // operator>> silently discards. A half-written config must be a
            // loud error here, exactly as it is on the Python side.
            data = json::parse(in);
        } catch (const json::parse_error& e) {
            throw MCPRegistryError(
                "MCP server config at " + path + " is not valid JSON: " + e.what() +
                ". Fix the file, or delete it and re-add the server with "
                "`gaia connectors`.");
        }

        if (!data.is_object()) {
            throw MCPRegistryError(
                "MCP server config at " + path + " must be a JSON object with an "
                "\"mcpServers\" key, got " + typeName(data) + ".");
        }

        // `servers` is accepted as an alias, matching Python's MCPConfig.
        const char* key = data.contains("mcpServers") ? "mcpServers"
                        : data.contains("servers")    ? "servers"
                                                      : nullptr;
        if (key == nullptr) {
            throw MCPRegistryError(
                "MCP server config at " + path + " has no \"mcpServers\" key. "
                "Expected {\"mcpServers\": {\"<id>\": {\"command\": ..., \"args\": [...]}}}.");
        }

        const json& servers = data[key];
        if (!servers.is_object()) {
            throw MCPRegistryError(
                "MCP server config at " + path + ": \"" + key + "\" must be an object "
                "mapping server ids to configs, got " + typeName(servers) + ".");
        }

        for (const auto& item : servers.items()) {
            merged[item.key()] = item.value();   // later file wins
            sources[item.key()] = path;          // so errors name the right file
        }
        foundAnyFile = true;
    }

    if (!foundAnyFile) {
        throw MCPRegistryError(
            "No MCP server configuration found (" + searchedPathsSuffix() + "). "
            "Add a server with `gaia connectors`, create " + writeTargetPath() +
            " with {\"mcpServers\": {\"<id>\": {\"command\": \"...\", \"args\": [...]}}}, "
            "or point GAIA_CONFIG_DIR at the directory that holds it.");
    }

    servers_ = std::move(merged);
    entrySource_ = std::move(sources);
    loaded_ = true;
}

// ---------------------------------------------------------------------------
// Entry validation
// ---------------------------------------------------------------------------

json MCPRegistry::normalizeEntry(const std::string& id, const json& entry) const {
    auto sourceIt = entrySource_.find(id);
    const std::string where =
        sourceIt == entrySource_.end() ? std::string() : " (from " + sourceIt->second + ")";

    if (!entry.is_object()) {
        throw MCPRegistryError(
            "MCP server '" + id + "' must be a JSON object with a \"command\" key, got " +
            typeName(entry) + where + ".");
    }

    if (entry.contains("disabled")) {
        if (!entry["disabled"].is_boolean()) {
            throw MCPRegistryError(
                "MCP server '" + id + "': \"disabled\" must be true or false, got " +
                typeName(entry["disabled"]) + where + ".");
        }
        if (entry["disabled"].get<bool>()) {
            throw MCPRegistryError(
                "MCP server '" + id + "' is disabled" + where +
                ". Set \"disabled\": false to use it, or drop the key entirely.");
        }
    }

    // Same gate as Python's MCPClientManager: stdio is the only transport
    // either runtime speaks. Saying so beats "has no usable command", which
    // sends the reader off fixing the wrong line.
    if (entry.contains("type")) {
        if (!entry["type"].is_string()) {
            throw MCPRegistryError(
                "MCP server '" + id + "': \"type\" must be a string, got " +
                typeName(entry["type"]) + where + ".");
        }
        const std::string transport = entry["type"].get<std::string>();
        if (transport != "stdio") {
            throw MCPRegistryError(
                "MCP server '" + id + "' uses the '" + transport + "' transport" + where +
                ", and GAIA's MCP client only speaks stdio. Configure a stdio command for "
                "this server, or drop it from the config.");
        }
    }

    if (!entry.contains("command") || !entry["command"].is_string() ||
        entry["command"].get<std::string>().empty()) {
        throw MCPRegistryError(
            "MCP server '" + id + "' has no usable \"command\"" + where +
            ". Every entry needs a non-empty command string, e.g. "
            "{\"command\": \"npx\", \"args\": [\"-y\", \"server-github\"]}.");
    }

    // Keep every key the entry carried and normalize the ones we launch with,
    // so nothing a caller (or a future GAIA version) put in the file is
    // dropped on the floor here.
    json out = entry;

    json args = json::array();
    if (entry.contains("args")) {
        if (!entry["args"].is_array()) {
            throw MCPRegistryError(
                "MCP server '" + id + "': \"args\" must be an array of strings, got " +
                typeName(entry["args"]) + where + ".");
        }
        size_t index = 0;
        for (const auto& arg : entry["args"]) {
            if (!arg.is_string()) {
                throw MCPRegistryError(
                    "MCP server '" + id + "': args[" + std::to_string(index) +
                    "] must be a string, got " + typeName(arg) + where + ".");
            }
            args.push_back(arg);
            ++index;
        }
    }
    out["args"] = std::move(args);

    json env = json::object();
    if (entry.contains("env")) {
        if (!entry["env"].is_object()) {
            throw MCPRegistryError(
                "MCP server '" + id + "': \"env\" must be an object of string values, got " +
                typeName(entry["env"]) + where + ".");
        }
        for (const auto& kv : entry["env"].items()) {
            const json& value = kv.value();
            if (value.is_string()) {
                env[kv.key()] = value;
            } else if (value.is_number() || value.is_boolean()) {
                // Env values are strings to the OS; accept both spellings.
                env[kv.key()] = pythonStr(value);
            } else if (value.is_object() && value.contains("$keyring")) {
                throw MCPRegistryError(
                    "MCP server '" + id + "' keeps its \"" + kv.key() +
                    "\" value in the OS keyring (reference " + value["$keyring"].dump() +
                    ")" + where + ", and this runtime has no keychain support — passing the "
                    "reference through would hand the server a bogus credential. Launch this "
                    "server from the Python runtime, which resolves keyring references.");
            } else {
                throw MCPRegistryError(
                    "MCP server '" + id + "': env[\"" + kv.key() +
                    "\"] must be a string, got " + typeName(value) + where + ".");
            }
        }
    }
    out["env"] = std::move(env);

    return out;
}

// ---------------------------------------------------------------------------
// Lookup
// ---------------------------------------------------------------------------

MCPRegistryError MCPRegistry::unknownIdError(const std::string& id) const {
    std::vector<std::string> available;
    available.reserve(servers_.size());
    for (const auto& kv : servers_) available.push_back(kv.first);  // std::map: sorted

    std::string message =
        "MCP server '" + id + "' is not configured (" + searchedPathsSuffix() + "). ";
    if (available.empty()) {
        message += "No servers are configured at all. Add one with `gaia connectors`, or "
                   "write it into " + writeTargetPath() + ".";
    } else {
        message += "Available ids: " + joinOr(available, "(none)") + ". Add '" + id +
                   "' with `gaia connectors`, or write it into " + writeTargetPath() + ".";
    }
    return MCPRegistryError(message);
}

std::optional<json> MCPRegistry::resolve(const std::string& id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    ensureLoadedLocked();
    auto it = servers_.find(id);
    if (it == servers_.end()) return std::nullopt;
    return normalizeEntry(id, it->second);
}

json MCPRegistry::require(const std::string& id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    ensureLoadedLocked();

    auto it = servers_.find(id);
    if (it == servers_.end()) throw unknownIdError(id);
    return normalizeEntry(id, it->second);
}

bool MCPRegistry::isDisabled(const std::string& id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    ensureLoadedLocked();

    auto it = servers_.find(id);
    if (it == servers_.end()) throw unknownIdError(id);
    const json& entry = it->second;
    return entry.is_object() && entry.contains("disabled") &&
           entry["disabled"].is_boolean() && entry["disabled"].get<bool>();
}

std::vector<std::string> MCPRegistry::listServers() const {
    std::lock_guard<std::mutex> lock(mutex_);
    ensureLoadedLocked();
    std::vector<std::string> ids;
    ids.reserve(servers_.size());
    for (const auto& kv : servers_) ids.push_back(kv.first);  // std::map: already sorted
    return ids;
}

} // namespace gaia
