// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MCP server registry — resolves a server *id* to a launchable server config.
// Reads the same on-disk format the Python runtime uses:
//   - src/gaia/mcp/client/config.py       (MCPConfig, the read path)
//   - src/gaia/connectors/mcp_server.py   (McpServerHandler, the write path)
//
// A server whose config carries literal values is reachable from both runtimes
// after being configured once. See the keyring note on resolve() for the one
// case that is Python-only today.

#pragma once

#include <map>
#include <mutex>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "gaia/export.h"

namespace gaia {

using json = nlohmann::json;

#ifdef _MSC_VER
#pragma warning(push)
#pragma warning(disable : 4275)  // exported type derives from non-exported std::runtime_error
#endif

/// Raised when the registry cannot hand back a launchable server config:
/// no config file, malformed JSON, unknown id, disabled entry, or an entry
/// whose credentials this runtime cannot resolve.
///
/// Every message names the id (when there is one), the paths searched, and
/// what the caller can do about it. A skill that silently loses its MCP tools
/// produces an agent confidently claiming it cannot do something it was
/// configured to do, so nothing here degrades quietly.
class GAIA_API MCPRegistryError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

#ifdef _MSC_VER
#pragma warning(pop)
#endif

/// Read-only registry over the `mcpServers` map on disk.
///
/// File shape (identical to Python's, and to the Anthropic/Claude convention):
/// @code
///   {
///     "mcpServers": {
///       "mcp-github": {
///         "command": "npx",
///         "args": ["-y", "@modelcontextprotocol/server-github"],
///         "env": {"GITHUB_TOKEN": "ghp_..."},
///         "description": "GitHub repository access"
///       }
///     }
///   }
/// @endcode
/// Ids are whatever the file uses; servers added by `gaia connectors` are keyed
/// by their catalog id (`mcp-github`, `mcp-tavily`, `mcp-memory`, `mcp-git`).
/// Unknown top-level keys are ignored, so the package-shipped descriptor at
/// `src/gaia/mcp/mcp.json` (which also carries `clients`, `tools`, …) loads
/// unchanged. `servers` is accepted as an alias for `mcpServers`, matching
/// Python's `MCPConfig._read_servers()`.
///
/// Search paths, lowest precedence first, under the config directory
/// (`$GAIA_CONFIG_DIR`, else `~/.gaia`):
///   1. `mcp.json`         — accepted for hand-written configs
///   2. `mcp_servers.json` — what `gaia connectors` writes and the Python
///                           runtime reads; wins on conflicting ids
///
/// Two deliberate deviations from Python, both of which split the two runtimes
/// if you rely on them:
///   - Python's MCP path always reads `~/.gaia/mcp_servers.json` and does not
///     honor `GAIA_CONFIG_DIR`; pointing it elsewhere makes this registry read
///     a file Python will not.
///   - Python does not read `mcp.json` from the config directory, so an id that
///     lives only there resolves in C++ and is invisible to Python. Prefer
///     `mcp_servers.json` for anything both runtimes must see.
///
/// Unlike Python, the C++ registry deliberately does **not** pick up an
/// `mcp_servers.json` from the current working directory: this registry
/// launches subprocesses, and a native binary that spawns commands named by a
/// file in whatever directory it happens to be started from is an attack
/// surface an OEM-shipped agent should not have.
///
/// Usage:
/// @code
///   MCPRegistry registry;
///   json config = registry.require("mcp-github");  // throws with the available ids
///   agent.connectMcpServer("mcp-github", config);
/// @endcode
class GAIA_API MCPRegistry {
public:
    /// Construct over the default search paths (see class docs).
    MCPRegistry();

    /// Construct over explicit files, lowest precedence first (tests, embedders).
    /// @throws std::invalid_argument if `searchPaths` is empty.
    explicit MCPRegistry(std::vector<std::string> searchPaths);

    /// The config directory: `$GAIA_CONFIG_DIR` when set and non-empty,
    /// otherwise `~/.gaia` (`%USERPROFILE%\.gaia` on Windows).
    static std::string configDir();

    /// Files the default-constructed registry reads, lowest precedence first.
    static std::vector<std::string> defaultSearchPaths();

    /// Look up a server id.
    /// @return The entry with `command`, `args` and `env` normalized (`args`
    ///         an array of strings, `env` an object of strings, both present
    ///         even when the file omits them) and every other key it carried
    ///         left intact — ready for `MCPClient::fromConfig()` /
    ///         `Agent::connectMcpServer()`. `std::nullopt` when no entry with
    ///         that id exists.
    /// @throws MCPRegistryError if no config file exists, a config file is
    ///         malformed, or the entry exists but is not launchable: missing
    ///         `command`, `disabled: true`, a non-stdio `type`, or a `$keyring`
    ///         credential reference. That last one is how `gaia connectors`
    ///         stores secrets, so servers configured with one (`mcp-github`,
    ///         `mcp-tavily`) are Python-only until the C++ runtime grows
    ///         keychain support.
    std::optional<json> resolve(const std::string& id) const;

    /// Like resolve(), but an unknown id is an error naming the id, the paths
    /// searched, and the ids that *are* available.
    /// @throws MCPRegistryError always, when the id cannot be resolved.
    json require(const std::string& id) const;

    /// True when the entry is marked `"disabled": true`. Lets a caller walk
    /// listServers() and skip switched-off servers instead of taking the throw
    /// from resolve().
    /// @throws MCPRegistryError if the id is unknown, or on any load failure.
    bool isDisabled(const std::string& id) const;

    /// All configured server ids, sorted, including entries marked `disabled`.
    /// @throws MCPRegistryError if no config file exists or one is malformed.
    std::vector<std::string> listServers() const;

    /// The file entries are read from — the highest-precedence file that
    /// exists. When none exists, the path the user should create
    /// (`<configDir>/mcp_servers.json` for a default-constructed registry).
    std::string configPath() const;

    /// Every path this registry searches, lowest precedence first.
    const std::vector<std::string>& searchPaths() const { return searchPaths_; }

    /// True when at least one searched file exists. Use this to probe before
    /// calling resolve() on a machine with no MCP config at all.
    /// @throws MCPRegistryError if a path cannot be stat'd (e.g. permissions) —
    ///         "could not tell" is not the same answer as "not configured".
    bool configExists() const;

    /// Drop the cached parse so the next call re-reads from disk.
    void reload();

    // Holds a mutex guarding the parse cache, so instances are pinned.
    MCPRegistry(const MCPRegistry&) = delete;
    MCPRegistry& operator=(const MCPRegistry&) = delete;

private:
    /// Parse the search paths into servers_. Caller must hold mutex_.
    void ensureLoadedLocked() const;

    /// Validate and normalize one raw entry into a launchable config.
    /// Caller must hold mutex_.
    /// @throws MCPRegistryError with an actionable message.
    json normalizeEntry(const std::string& id, const json& entry) const;

    /// Build the "not configured, here is what is" error. Caller must hold mutex_.
    MCPRegistryError unknownIdError(const std::string& id) const;

    /// fs::exists() that turns an un-stat-able path into a loud error.
    static bool pathExists(const std::string& path);

    /// "searched: a, b" — reused by every error message.
    std::string searchedPathsSuffix() const;

    /// The file a user should write to: the highest-precedence search path.
    std::string writeTargetPath() const;

    std::vector<std::string> searchPaths_;

    mutable std::mutex mutex_;
    mutable bool loaded_ = false;
    mutable std::map<std::string, json> servers_;
    /// id -> the file it came from, so errors name the file the reader must edit.
    mutable std::map<std::string, std::string> entrySource_;
};

} // namespace gaia
