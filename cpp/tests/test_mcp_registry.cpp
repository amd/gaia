// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/mcp_client.h>
#include <gaia/mcp_registry.h>

#include <atomic>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <string>
#include <thread>
#include <vector>

#include <nlohmann/json.hpp>

using json = nlohmann::json;
using namespace gaia;
namespace fs = std::filesystem;

namespace {

/// Set (value != nullptr) or clear an environment variable, portably.
void setEnvVar(const char* key, const char* value) {
#ifdef _WIN32
    _putenv_s(key, value ? value : "");
#else
    if (value) {
        setenv(key, value, 1);  // NOLINT(concurrency-mt-unsafe)
    } else {
        unsetenv(key);  // NOLINT(concurrency-mt-unsafe)
    }
#endif
}

/// True when `haystack` contains `needle` — keeps the error-message
/// assertions readable.
::testing::AssertionResult contains(const std::string& haystack, const std::string& needle) {
    if (haystack.find(needle) != std::string::npos) return ::testing::AssertionSuccess();
    return ::testing::AssertionFailure() << "expected to find \"" << needle
                                         << "\" in:\n" << haystack;
}

} // namespace

// ---------------------------------------------------------------------------
// Fixture — every test gets its own temp config directory
// ---------------------------------------------------------------------------

class MCPRegistryTest : public ::testing::Test {
protected:
    fs::path dir;
    std::string savedConfigDir;
    bool hadConfigDir = false;

    void SetUp() override {
        // Per-test directory: ctest runs these as concurrent processes, and a
        // shared temp dir means one test's TearDown deletes another's config.
        const auto* info = ::testing::UnitTest::GetInstance()->current_test_info();
        dir = fs::temp_directory_path() /
              ("gaia_mcp_registry_test_" + std::string(info ? info->name() : "unnamed"));
        fs::remove_all(dir);
        fs::create_directories(dir);

        if (const char* existing = std::getenv("GAIA_CONFIG_DIR")) {
            hadConfigDir = true;
            savedConfigDir = existing;
        }
        // Start from a clean slate so a developer who exports GAIA_CONFIG_DIR
        // does not run a different suite from CI.
        setEnvVar("GAIA_CONFIG_DIR", nullptr);
    }

    void TearDown() override {
        setEnvVar("GAIA_CONFIG_DIR", hadConfigDir ? savedConfigDir.c_str() : nullptr);
        fs::remove_all(dir);
    }

    /// Write raw text to <dir>/<name> and return the path.
    std::string writeFile(const std::string& name, const std::string& contents) const {
        fs::path path = dir / name;
        std::ofstream out(path);
        out << contents;
        out.close();
        EXPECT_TRUE(out.good()) << "failed to write " << path.string();
        return path.string();
    }

    /// A registry over a single explicit file inside the temp dir.
    static MCPRegistry over(const std::string& path) {
        return MCPRegistry(std::vector<std::string>{path});
    }

    /// Two servers, one of them carrying args + env.
    std::string writeSampleConfig(const std::string& name = "mcp_servers.json") const {
        return writeFile(name, R"({
          "mcpServers": {
            "github": {
              "command": "npx",
              "args": ["-y", "@modelcontextprotocol/server-github"],
              "env": {"GITHUB_TOKEN": "ghp_example"},
              "description": "GitHub repository access"
            },
            "time": {
              "command": "uvx",
              "args": ["mcp-server-time"]
            }
          }
        })");
    }

    /// Path to the repo's Python-side descriptor, or empty when this is a
    /// standalone `cpp/` checkout without the Python tree.
    static fs::path packagedMcpJson() {
        fs::path repoRoot = fs::path(GAIA_TEST_FIXTURES_DIR)  // <repo>/cpp/tests/fixtures
                                .parent_path()                // <repo>/cpp/tests
                                .parent_path()                // <repo>/cpp
                                .parent_path();               // <repo>
        fs::path candidate = repoRoot / "src" / "gaia" / "mcp" / "mcp.json";
        std::error_code ec;
        return fs::exists(candidate, ec) ? candidate : fs::path();
    }
};

// ---------------------------------------------------------------------------
// Resolution
// ---------------------------------------------------------------------------

TEST_F(MCPRegistryTest, ResolvesKnownId) {
    MCPRegistry registry = over(writeSampleConfig());

    auto config = registry.resolve("github");
    ASSERT_TRUE(config.has_value());
    EXPECT_EQ((*config)["command"], "npx");
    EXPECT_EQ((*config)["args"], json::array({"-y", "@modelcontextprotocol/server-github"}));
    EXPECT_EQ((*config)["env"]["GITHUB_TOKEN"], "ghp_example");
    EXPECT_EQ((*config)["description"], "GitHub repository access");
}

TEST_F(MCPRegistryTest, ResolvedConfigIsAcceptedByMCPClientFromConfig) {
    MCPRegistry registry = over(writeSampleConfig());

    // The whole point of the registry: what it returns is directly launchable.
    EXPECT_NO_THROW({
        MCPClient client = MCPClient::fromConfig("github", registry.require("github"));
        EXPECT_EQ(client.name(), "github");
    });
}

TEST_F(MCPRegistryTest, NormalizesMissingArgsAndEnvToEmpty) {
    MCPRegistry registry = over(writeFile(
        "mcp_servers.json", R"({"mcpServers": {"bare": {"command": "run-me"}}})"));

    json config = registry.require("bare");
    EXPECT_EQ(config["command"], "run-me");
    EXPECT_TRUE(config["args"].is_array());
    EXPECT_TRUE(config["args"].empty());
    EXPECT_TRUE(config["env"].is_object());
    EXPECT_TRUE(config["env"].empty());
}

TEST_F(MCPRegistryTest, ListServersReturnsSortedIds) {
    MCPRegistry registry = over(writeSampleConfig());
    EXPECT_EQ(registry.listServers(), (std::vector<std::string>{"github", "time"}));
}

TEST_F(MCPRegistryTest, AcceptsServersKeyAlias) {
    MCPRegistry registry = over(writeFile(
        "mcp_servers.json", R"({"servers": {"time": {"command": "uvx"}}})"));
    EXPECT_EQ(registry.require("time")["command"], "uvx");
}

TEST_F(MCPRegistryTest, IgnoresUnknownTopLevelKeys) {
    // The packaged descriptor also carries "clients", "tools", "rateLimits", …
    MCPRegistry registry = over(writeFile("mcp.json", R"({
      "mcpServers": {"time": {"command": "uvx"}},
      "clients": {"custom": {"supported": true}},
      "tools": {"gaia.query": {"description": "…"}},
      "version": "1.0.0"
    })"));
    EXPECT_EQ(registry.require("time")["command"], "uvx");
}

// ---------------------------------------------------------------------------
// Fail-loudly paths
// ---------------------------------------------------------------------------

TEST_F(MCPRegistryTest, UnknownIdRaisesListingAvailableIds) {
    std::string path = writeSampleConfig();
    MCPRegistry registry = over(path);

    EXPECT_FALSE(registry.resolve("slack").has_value());

    try {
        registry.require("slack");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        const std::string msg = e.what();
        EXPECT_TRUE(contains(msg, "slack"));   // the id asked for
        EXPECT_TRUE(contains(msg, path));      // where we looked
        EXPECT_TRUE(contains(msg, "github"));  // what is actually available
        EXPECT_TRUE(contains(msg, "time"));
    }
}

TEST_F(MCPRegistryTest, UnknownIdWithEmptyConfigStillNamesThePath) {
    std::string path = writeFile("mcp_servers.json", R"({"mcpServers": {}})");
    MCPRegistry registry = over(path);

    try {
        registry.require("github");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        const std::string msg = e.what();
        EXPECT_TRUE(contains(msg, "github"));
        EXPECT_TRUE(contains(msg, path));
        EXPECT_TRUE(contains(msg, "No servers are configured"));
    }
}

TEST_F(MCPRegistryTest, MissingConfigFileRaisesNamingEverySearchedPath) {
    std::string a = (dir / "mcp.json").string();
    std::string b = (dir / "mcp_servers.json").string();
    MCPRegistry registry(std::vector<std::string>{a, b});

    EXPECT_FALSE(registry.configExists());

    try {
        registry.resolve("github");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        const std::string msg = e.what();
        EXPECT_TRUE(contains(msg, a));
        EXPECT_TRUE(contains(msg, b));
        EXPECT_TRUE(contains(msg, "GAIA_CONFIG_DIR"));
    }
}

TEST_F(MCPRegistryTest, MalformedJsonRaisesWithParseErrorAndPath) {
    std::string path = writeFile("mcp_servers.json", R"({"mcpServers": {"github": })");
    MCPRegistry registry = over(path);

    try {
        registry.listServers();
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        const std::string msg = e.what();
        EXPECT_TRUE(contains(msg, path));
        EXPECT_TRUE(contains(msg, "parse error"));  // nlohmann's own diagnostic
    }
}

TEST_F(MCPRegistryTest, TrailingContentAfterTheJsonObjectRaises) {
    // A half-overwritten config parses "fine" with operator>>, which discards
    // everything after the first value. Python's json.load errors on it, so
    // this must too — otherwise the two runtimes disagree about corruption.
    std::string path = writeFile("mcp_servers.json",
                                 R"({"mcpServers": {"time": {"command": "uvx"}}} trailing junk)");
    MCPRegistry registry = over(path);

    try {
        registry.listServers();
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), path));
        EXPECT_TRUE(contains(e.what(), "parse error"));
    }
}

TEST_F(MCPRegistryTest, MissingMcpServersKeyRaises) {
    std::string path = writeFile("mcp_servers.json", R"({"version": "1.0.0"})");
    MCPRegistry registry = over(path);

    try {
        registry.listServers();
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "mcpServers"));
        EXPECT_TRUE(contains(e.what(), path));
    }
}

TEST_F(MCPRegistryTest, EntryWithoutCommandRaises) {
    MCPRegistry registry = over(writeFile(
        "mcp_servers.json", R"({"mcpServers": {"broken": {"args": ["x"]}}})"));

    try {
        registry.require("broken");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "broken"));
        EXPECT_TRUE(contains(e.what(), "command"));
    }
}

TEST_F(MCPRegistryTest, DisabledServerRaisesRatherThanVanishing) {
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {"github": {"command": "npx", "disabled": true}}
    })"));

    // Still listed — the id exists, it is just switched off. Resolving it is
    // an error, so a skill declaring mcp:connect:github cannot lose its tools
    // without anyone noticing.
    EXPECT_EQ(registry.listServers(), (std::vector<std::string>{"github"}));

    try {
        registry.require("github");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "github"));
        EXPECT_TRUE(contains(e.what(), "disabled"));
    }
}

TEST_F(MCPRegistryTest, NonStdioTransportRaisesNamingTheTransport) {
    // Same gate as Python's MCPClientManager. Without it the reader gets
    // "has no usable command" and goes off fixing the wrong line.
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {"remote": {"type": "sse", "url": "https://example.invalid/sse"}}
    })"));

    try {
        registry.require("remote");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "sse"));
        EXPECT_TRUE(contains(e.what(), "stdio"));
    }
}

TEST_F(MCPRegistryTest, ResolveThrowsForAPresentButUnlaunchableEntry) {
    // resolve() returns nullopt only for "no such id". An entry that exists
    // but cannot be launched is an error, not an absence.
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {"off": {"command": "npx", "disabled": true},
                     "headless": {"args": ["x"]}}
    })"));

    EXPECT_THROW(registry.resolve("off"), MCPRegistryError);
    EXPECT_THROW(registry.resolve("headless"), MCPRegistryError);
    EXPECT_FALSE(registry.resolve("absent").has_value());
}

TEST_F(MCPRegistryTest, IsDisabledLetsCallersSkipInsteadOfCatching) {
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {"off": {"command": "npx", "disabled": true},
                     "on": {"command": "uvx"}}
    })"));

    EXPECT_TRUE(registry.isDisabled("off"));
    EXPECT_FALSE(registry.isDisabled("on"));
    EXPECT_THROW(registry.isDisabled("absent"), MCPRegistryError);
}

TEST_F(MCPRegistryTest, UnrecognizedEntryKeysArePreservedNotDropped) {
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {"x": {"command": "run", "cwd": "/srv/project", "timeout": 90}}
    })"));

    json config = registry.require("x");
    EXPECT_EQ(config["cwd"], "/srv/project");
    EXPECT_EQ(config["timeout"], 90);
}

TEST_F(MCPRegistryTest, NonBooleanDisabledRaisesInsteadOfThrowingAJsonTypeError) {
    MCPRegistry registry = over(writeFile(
        "mcp_servers.json",
        R"({"mcpServers": {"github": {"command": "npx", "disabled": "yes"}}})"));

    try {
        registry.require("github");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "disabled"));
    }
}

TEST_F(MCPRegistryTest, KeyringEnvReferenceRaisesActionableError) {
    // What `gaia connectors` writes for a secret env value — the C++ runtime
    // has no keychain support, so passing the reference string through as the
    // token would hand the server a bogus credential.
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {
        "github": {
          "command": "npx",
          "env": {"GITHUB_TOKEN": {"$keyring": "gaia.connections:github:GITHUB_TOKEN"}}
        }
      }
    })"));

    try {
        registry.require("github");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "GITHUB_TOKEN"));
        EXPECT_TRUE(contains(e.what(), "keyring"));
    }
}

TEST_F(MCPRegistryTest, NonStringArgRaisesNamingTheIndex) {
    MCPRegistry registry = over(writeFile(
        "mcp_servers.json", R"({"mcpServers": {"x": {"command": "run", "args": ["a", 7]}}})"));

    try {
        registry.require("x");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "args[1]"));
    }
}

TEST_F(MCPRegistryTest, NonStringEnvValuesStringifyExactlyAsPythonDoes) {
    // Python's MCPClient does str(value) — booleans come out "True"/"False",
    // so a server started from either runtime sees identical bytes.
    MCPRegistry registry = over(writeFile(
        "mcp_servers.json",
        R"({"mcpServers": {"x": {"command": "run",
                                 "env": {"PORT": 8765, "DEBUG": true, "QUIET": false}}}})"));

    json config = registry.require("x");
    EXPECT_EQ(config["env"]["PORT"], "8765");
    EXPECT_EQ(config["env"]["DEBUG"], "True");
    EXPECT_EQ(config["env"]["QUIET"], "False");
}

TEST_F(MCPRegistryTest, EmptySearchPathListIsRejected) {
    EXPECT_THROW(MCPRegistry(std::vector<std::string>{}), std::invalid_argument);
}

TEST_F(MCPRegistryTest, ConcurrentReadersShareOneParse) {
    // The lazy parse cache is the reason this class holds a mutex; exercise it.
    MCPRegistry registry = over(writeSampleConfig());

    std::vector<std::thread> threads;
    std::atomic<int> resolved{0};
    for (int i = 0; i < 8; ++i) {
        threads.emplace_back([&registry, &resolved, i] {
            for (int n = 0; n < 50; ++n) {
                if (i % 4 == 0) {
                    registry.reload();
                } else if (registry.resolve("github").has_value() &&
                           registry.listServers().size() == 2) {
                    resolved.fetch_add(1);
                }
            }
        });
    }
    for (auto& t : threads) t.join();
    EXPECT_GT(resolved.load(), 0);
}

// ---------------------------------------------------------------------------
// Config directory resolution
// ---------------------------------------------------------------------------

TEST_F(MCPRegistryTest, GaiaConfigDirOverrideIsHonored) {
    writeSampleConfig();
    setEnvVar("GAIA_CONFIG_DIR", dir.string().c_str());

    EXPECT_EQ(MCPRegistry::configDir(), dir.string());

    MCPRegistry registry;  // default search paths
    EXPECT_EQ(registry.configPath(), (dir / "mcp_servers.json").string());
    EXPECT_EQ(registry.require("github")["command"], "npx");
}

TEST_F(MCPRegistryTest, EmptyGaiaConfigDirFallsBackToHomeDotGaia) {
    setEnvVar("GAIA_CONFIG_DIR", "");
    EXPECT_TRUE(contains(MCPRegistry::configDir(), ".gaia"));
}

TEST_F(MCPRegistryTest, McpServersJsonWinsOverMcpJson) {
    writeFile("mcp.json",
              R"({"mcpServers": {"github": {"command": "from-mcp-json"},
                                 "only-in-mcp-json": {"command": "kept"}}})");
    writeFile("mcp_servers.json",
              R"({"mcpServers": {"github": {"command": "from-mcp-servers-json"}}})");
    setEnvVar("GAIA_CONFIG_DIR", dir.string().c_str());

    MCPRegistry registry;
    // Conflicting ids resolve to the connectors-maintained file …
    EXPECT_EQ(registry.require("github")["command"], "from-mcp-servers-json");
    // … but entries only present in mcp.json are not dropped.
    EXPECT_EQ(registry.require("only-in-mcp-json")["command"], "kept");
}

TEST_F(MCPRegistryTest, EntryErrorNamesTheFileTheEntryCameFrom) {
    std::string lowPrecedence = writeFile(
        "mcp.json", R"({"mcpServers": {"broken": {"args": ["x"]}}})");
    writeFile("mcp_servers.json", R"({"mcpServers": {"time": {"command": "uvx"}}})");
    setEnvVar("GAIA_CONFIG_DIR", dir.string().c_str());

    MCPRegistry registry;
    try {
        registry.require("broken");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        // Not the highest-precedence file that happens to exist — the one the
        // reader actually has to edit.
        EXPECT_TRUE(contains(e.what(), lowPrecedence));
    }
}

TEST_F(MCPRegistryTest, ConfigPathNamesTheFileToCreateWhenNoneExists) {
    setEnvVar("GAIA_CONFIG_DIR", dir.string().c_str());
    MCPRegistry registry;
    EXPECT_FALSE(registry.configExists());
    EXPECT_EQ(registry.configPath(), (dir / "mcp_servers.json").string());
}

TEST_F(MCPRegistryTest, ReloadPicksUpAFileWrittenAfterFirstRead) {
    std::string path = writeFile("mcp_servers.json",
                                 R"({"mcpServers": {"time": {"command": "uvx"}}})");
    MCPRegistry registry = over(path);
    EXPECT_EQ(registry.listServers(), (std::vector<std::string>{"time"}));

    writeFile("mcp_servers.json",
              R"({"mcpServers": {"time": {"command": "uvx"}, "github": {"command": "npx"}}})");
    registry.reload();
    EXPECT_EQ(registry.listServers(), (std::vector<std::string>{"github", "time"}));
}

// ---------------------------------------------------------------------------
// Python interop — one configuration, both runtimes
// ---------------------------------------------------------------------------

TEST_F(MCPRegistryTest, ResolvesEntryInTheShapePythonWrites) {
    // Checked-in fixture mirroring McpServerHandler.configure()'s output:
    // catalog-prefixed ids, command / args / env / disabled, and the $keyring
    // env block it writes for secret values.
    fs::path fixture = fs::path(GAIA_TEST_FIXTURES_DIR) / "mcp_servers_python.json";
    ASSERT_TRUE(fs::exists(fixture)) << fixture.string();

    MCPRegistry registry = over(fixture.string());
    EXPECT_EQ(registry.listServers(),
              (std::vector<std::string>{"gaia-bridge", "mcp-github", "mcp-memory"}));

    json memory = registry.require("mcp-memory");
    EXPECT_EQ(memory["command"], "npx");
    EXPECT_EQ(memory["args"], json::array({"-y", "@modelcontextprotocol/server-memory"}));
    EXPECT_TRUE(memory["env"].empty());
    EXPECT_FALSE(registry.isDisabled("mcp-memory"));

    json bridge = registry.require("gaia-bridge");
    EXPECT_EQ(bridge["command"], "python");
    EXPECT_EQ(bridge["args"], json::array({"-m", "gaia.mcp.mcp_bridge"}));
    EXPECT_EQ(bridge["env"]["GAIA_MCP_PORT"], "8765");
    EXPECT_EQ(bridge["description"],
              "GAIA MCP Server for exposing AI agents to third-party applications");
}

TEST_F(MCPRegistryTest, ConnectorsSecretBackedServerIsRefusedNotSilentlyMisconfigured) {
    // `gaia connectors configure mcp-github` stores GITHUB_TOKEN in the OS
    // keyring. The C++ runtime has no keychain, so this server is Python-only —
    // and says so, instead of launching npx with a literal "$keyring" string
    // as the token and failing somewhere inside GitHub's API.
    fs::path fixture = fs::path(GAIA_TEST_FIXTURES_DIR) / "mcp_servers_python.json";
    MCPRegistry registry = over(fixture.string());

    try {
        registry.require("mcp-github");
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "mcp-github"));
        EXPECT_TRUE(contains(e.what(), "GITHUB_TOKEN"));
        EXPECT_TRUE(contains(e.what(), "keyring"));
    }
}

TEST_F(MCPRegistryTest, ResolvesThePackagedPythonMcpJson) {
    fs::path packaged = packagedMcpJson();
    if (packaged.empty()) {
        GTEST_SKIP() << "src/gaia/mcp/mcp.json not present (standalone cpp/ checkout)";
    }

    // The real file the Python runtime ships, unmodified — extra top-level
    // keys and all.
    MCPRegistry registry = over(packaged.string());
    json bridge = registry.require("gaia-bridge");
    EXPECT_EQ(bridge["command"], "python");
    EXPECT_EQ(bridge["args"], json::array({"-m", "gaia.mcp.mcp_bridge"}));
    EXPECT_EQ(bridge["env"]["LEMONADE_BASE_URL"], "http://localhost:13305/api/v1");
    EXPECT_EQ(bridge["env"]["GAIA_MCP_HOST"], "localhost");
    EXPECT_EQ(bridge["env"]["GAIA_MCP_PORT"], "8765");
}

// ---------------------------------------------------------------------------
// env actually reaches the launched server process
// ---------------------------------------------------------------------------

TEST_F(MCPRegistryTest, EnvValuesReachTheStdioTransportChildProcess) {
#ifdef _WIN32
    GTEST_SKIP() << "POSIX-only: uses /bin/sh to echo the child's environment";
#else
    // A one-shot MCP-ish server: read a request line, answer with the value of
    // the env var the registry config asked for.
    fs::path script = dir / "echo_env_server.sh";
    {
        std::ofstream out(script);
        out << "read _line\n"
            << "printf '{\"jsonrpc\":\"2.0\",\"id\":0,\"result\":{\"seen\":\"%s\"}}\\n'"
               " \"$GAIA_TEST_MCP_ENV\"\n";
    }

    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {
        "echo": {
          "command": "/bin/sh",
          "args": ["SCRIPT"],
          "env": {"GAIA_TEST_MCP_ENV": "from-mcp-json"}
        }
      }
    })"));
    // Patch the script path in (raw string above keeps the JSON readable).
    json config = registry.require("echo");
    config["args"][0] = script.string();

    std::vector<std::string> args;
    for (const auto& arg : config["args"]) args.push_back(arg.get<std::string>());
    std::map<std::string, std::string> env;
    for (const auto& kv : config["env"].items()) env[kv.key()] = kv.value().get<std::string>();

    StdioTransport transport(config["command"].get<std::string>(), args, env, 10, false);
    ASSERT_TRUE(transport.connect());
    json response = transport.sendRequest("ping");
    transport.disconnect();

    EXPECT_EQ(response["result"]["seen"], "from-mcp-json");
#endif
}

// ---------------------------------------------------------------------------
// Agent::connectMcpServerById
// ---------------------------------------------------------------------------

TEST_F(MCPRegistryTest, AgentConnectByIdRaisesOnUnknownId) {
    MCPRegistry registry = over(writeSampleConfig());
    Agent agent;

    try {
        agent.connectMcpServerById("slack", registry);
        FAIL() << "expected MCPRegistryError";
    } catch (const MCPRegistryError& e) {
        EXPECT_TRUE(contains(e.what(), "slack"));
        EXPECT_TRUE(contains(e.what(), "github"));
    }
}

TEST_F(MCPRegistryTest, AgentConnectByIdRaisesWhenNoConfigExists) {
    MCPRegistry registry = over((dir / "does_not_exist.json").string());
    Agent agent;

    EXPECT_THROW(agent.connectMcpServerById("github", registry), MCPRegistryError);
}

TEST_F(MCPRegistryTest, AgentConnectByIdReturnsFalseWhenTheServerFailsToLaunch) {
    // Resolution succeeds; the launch does not. That is a connection failure,
    // not a configuration failure, so it keeps connectMcpServer()'s bool contract.
    MCPRegistry registry = over(writeFile("mcp_servers.json", R"({
      "mcpServers": {"ghost": {"command": "gaia-no-such-mcp-server-binary"}}
    })"));
    Agent agent;

    EXPECT_FALSE(agent.connectMcpServerById("ghost", registry));
}
