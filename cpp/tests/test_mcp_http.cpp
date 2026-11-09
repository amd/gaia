// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// MCP Streamable HTTP transport, driven against an in-process httplib server
// that behaves like a real MCP endpoint: one POST URL, JSON or SSE replies,
// a session id handed out on initialize and enforced afterwards, 202 for
// notifications.

#include <gtest/gtest.h>
#include <gaia/mcp_client.h>
#include <gaia/mcp_registry.h>

#include <atomic>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include <httplib.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;
using namespace gaia;
namespace fs = std::filesystem;

namespace {

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

::testing::AssertionResult contains(const std::string& haystack, const std::string& needle) {
    if (haystack.find(needle) != std::string::npos) return ::testing::AssertionSuccess();
    return ::testing::AssertionFailure() << "expected to find \"" << needle << "\" in:\n"
                                         << haystack;
}

/// One request the mock server saw.
struct SeenRequest {
    json body;
    std::string sessionId;
    std::string protocolVersion;
    std::string accept;
    std::string contentType;
    std::string authorization;
};

/// Minimal MCP Streamable HTTP server on a free loopback port.
class MockMcpHttpServer {
public:
    enum class Reply { Json, Sse, SseHeldOpen, InvalidJson, HtmlContentType, SseNoReply };

    static constexpr const char* kSessionId = "sess-7f3a";
    static constexpr const char* kServerVersion = "2025-06-18";

    MockMcpHttpServer() : server_(std::make_unique<httplib::Server>()) {
        server_->Post("/mcp", [this](const httplib::Request& req, httplib::Response& res) {
            handle(req, res);
        });
        port_ = server_->bind_to_any_port("127.0.0.1");
        if (port_ <= 0) throw std::runtime_error("MockMcpHttpServer: bind failed");
        thread_ = std::thread([this] { server_->listen_after_bind(); });
        while (!server_->is_running()) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }

    ~MockMcpHttpServer() {
        stopping_ = true;
        server_->stop();
        if (thread_.joinable()) thread_.join();
    }

    std::string url(const std::string& path = "/mcp") const {
        return "http://127.0.0.1:" + std::to_string(port_) + path;
    }

    void setReply(Reply r) { reply_ = r; }
    /// Requests whose Authorization differs get 401.
    void requireAuthorization(std::string value) { requiredAuth_ = std::move(value); }
    /// tools/call answers HTTP 500.
    void failToolCalls(bool fail) { failToolCalls_ = fail; }
    /// Forget the session, so the next request carrying it gets 404.
    void expireSession() { sessionValid_ = false; }

    std::vector<SeenRequest> seen() const {
        std::lock_guard<std::mutex> lk(mu_);
        return seen_;
    }

private:
    void handle(const httplib::Request& req, httplib::Response& res) {
        SeenRequest s;
        s.sessionId = req.get_header_value("Mcp-Session-Id");
        s.protocolVersion = req.get_header_value("MCP-Protocol-Version");
        s.accept = req.get_header_value("Accept");
        s.contentType = req.get_header_value("Content-Type");
        s.authorization = req.get_header_value("Authorization");
        s.body = json::parse(req.body, nullptr, false);
        {
            std::lock_guard<std::mutex> lk(mu_);
            seen_.push_back(s);
        }

        if (!requiredAuth_.empty() && s.authorization != requiredAuth_) {
            res.status = 401;
            res.set_content(R"({"error":"unauthorized"})", "application/json");
            return;
        }
        if (s.body.is_discarded() || s.body.value("jsonrpc", "") != "2.0" ||
            s.accept.find("application/json") == std::string::npos ||
            s.accept.find("text/event-stream") == std::string::npos) {
            res.status = 400;
            res.set_content("bad request", "text/plain");
            return;
        }

        const std::string method = s.body.value("method", "");
        if (method != "initialize") {
            // Spec: 400 without a session id, 404 for a session the server dropped.
            if (s.sessionId.empty()) {
                res.status = 400;
                res.set_content("missing Mcp-Session-Id", "text/plain");
                return;
            }
            if (s.sessionId != kSessionId || !sessionValid_) {
                res.status = 404;
                res.set_content("session not found", "text/plain");
                return;
            }
        }

        if (!s.body.contains("id")) {  // notification
            res.status = 202;
            return;
        }

        json result;
        if (method == "initialize") {
            sessionValid_ = true;
            res.set_header("Mcp-Session-Id", kSessionId);
            result = {{"protocolVersion", kServerVersion},
                      {"capabilities", {{"tools", json::object()}}},
                      {"serverInfo", {{"name", "mock-mcp"}, {"version", "1.0"}}}};
        } else if (method == "tools/list") {
            result = {{"tools", json::array({
                {{"name", "get_weather"},
                 {"description", "Weather for a city"},
                 {"inputSchema", {{"type", "object"},
                                  {"properties", {{"city", {{"type", "string"}}}}},
                                  {"required", {"city"}}}},
                 {"annotations", {{"readOnlyHint", true}}}},
                {{"name", "delete_file"},
                 {"description", "Delete a file"},
                 {"inputSchema", {{"type", "object"}}}},
            })}};
        } else if (method == "tools/call") {
            if (failToolCalls_) {
                res.status = 500;
                res.set_content("internal error", "text/plain");
                return;
            }
            const std::string city = s.body["params"]["arguments"].value("city", "?");
            result = {{"content", json::array({{{"type", "text"}, {"text", "Sunny in " + city}}})}};
        } else {
            reply(res, {{"jsonrpc", "2.0"}, {"id", s.body["id"]},
                        {"error", {{"code", -32601}, {"message", "Method not found"}}}});
            return;
        }
        reply(res, {{"jsonrpc", "2.0"}, {"id", s.body["id"]}, {"result", result}});
    }

    void reply(httplib::Response& res, const json& message) {
        // Server-initiated traffic that precedes the reply on an SSE stream.
        const std::string preamble =
            ": keep-alive comment\n"
            "event: message\n"
            "data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/message\","
            "\"params\":{\"level\":\"info\",\"data\":\"working\"}}\n\n"
            "data: {\"jsonrpc\":\"2.0\",\"id\":999,\"result\":{}}\n\n";
        switch (reply_.load()) {
            case Reply::Json:
                res.set_content(message.dump(), "application/json");
                break;
            case Reply::Sse: {
                // Payload split over two data: lines, joined with '\n' per SSE.
                std::string dumped = message.dump();
                const size_t mid = dumped.find(',') + 1;
                res.set_content(preamble + "id: 1\r\ndata: " + dumped.substr(0, mid) +
                                    "\r\ndata: " + dumped.substr(mid) + "\r\n\r\n",
                                "text/event-stream");
                break;
            }
            case Reply::SseHeldOpen: {
                const std::string first = preamble + "data: " + message.dump() + "\n\n";
                res.set_chunked_content_provider(
                    "text/event-stream",
                    [this, first, sent = false](size_t, httplib::DataSink& sink) mutable {
                        if (!sent) {
                            sent = true;
                            return sink.write(first.data(), first.size());
                        }
                        // Keep the stream open, as servers may after the reply.
                        for (int i = 0; i < 100 && !stopping_ && sink.is_writable(); ++i) {
                            std::this_thread::sleep_for(std::chrono::milliseconds(50));
                        }
                        sink.done();
                        return true;
                    });
                break;
            }
            case Reply::InvalidJson:
                res.set_content("{\"jsonrpc\": \"2.0\", \"id\": ", "application/json");
                break;
            case Reply::HtmlContentType:
                res.set_content("<html>not mcp</html>", "text/html");
                break;
            case Reply::SseNoReply:
                res.set_content(preamble, "text/event-stream");
                break;
        }
    }

    std::unique_ptr<httplib::Server> server_;
    std::thread thread_;
    int port_ = 0;
    std::atomic<Reply> reply_{Reply::Json};
    std::atomic<bool> sessionValid_{true};
    std::atomic<bool> failToolCalls_{false};
    std::atomic<bool> stopping_{false};
    std::string requiredAuth_;
    mutable std::mutex mu_;
    std::vector<SeenRequest> seen_;
};

json httpConfig(const std::string& url, json headers = json::object()) {
    return {{"type", "http"}, {"url", url}, {"headers", std::move(headers)}};
}

/// Connects, lists tools, and calls one — the golden path every mode must pass.
void expectGoldenPath(MockMcpHttpServer& server) {
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    EXPECT_TRUE(client.isConnected());

    auto tools = client.listTools();
    ASSERT_EQ(tools.size(), 2u);
    EXPECT_EQ(tools[0].name, "get_weather");

    json result = client.callTool("get_weather", {{"city", "Austin"}});
    ASSERT_TRUE(result.contains("content")) << result.dump();
    EXPECT_EQ(result["content"][0]["text"], "Sunny in Austin");
}

} // namespace

// ---------------------------------------------------------------------------
// Handshake and session
// ---------------------------------------------------------------------------

TEST(MCPHttpTransportTest, JsonRepliesGoldenPath) {
    MockMcpHttpServer server;
    expectGoldenPath(server);
}

TEST(MCPHttpTransportTest, SseRepliesSkipServerMessagesAndJoinDataLines) {
    MockMcpHttpServer server;
    server.setReply(MockMcpHttpServer::Reply::Sse);
    expectGoldenPath(server);
}

TEST(MCPHttpTransportTest, SseStreamHeldOpenReturnsAtTheReply) {
    MockMcpHttpServer server;
    server.setReply(MockMcpHttpServer::Reply::SseHeldOpen);
    const auto start = std::chrono::steady_clock::now();
    expectGoldenPath(server);
    // Each reply is followed by a 5s idle stream; waiting for EOF would show here.
    EXPECT_LT(std::chrono::steady_clock::now() - start, std::chrono::seconds(4));
}

TEST(MCPHttpTransportTest, SessionIdAndProtocolVersionRoundTrip) {
    MockMcpHttpServer server;
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    client.listTools();

    auto seen = server.seen();
    ASSERT_EQ(seen.size(), 3u);  // initialize, notifications/initialized, tools/list

    EXPECT_EQ(seen[0].body["method"], "initialize");
    EXPECT_TRUE(seen[0].sessionId.empty());
    EXPECT_TRUE(seen[0].protocolVersion.empty());
    EXPECT_TRUE(seen[0].body["params"]["protocolVersion"].is_string());
    EXPECT_TRUE(contains(seen[0].contentType, "application/json"));

    EXPECT_EQ(seen[1].body["method"], "notifications/initialized");
    EXPECT_FALSE(seen[1].body.contains("id"));
    EXPECT_EQ(seen[1].sessionId, MockMcpHttpServer::kSessionId);
    EXPECT_EQ(seen[1].protocolVersion, MockMcpHttpServer::kServerVersion);

    EXPECT_EQ(seen[2].body["method"], "tools/list");
    EXPECT_EQ(seen[2].sessionId, MockMcpHttpServer::kSessionId);
    EXPECT_EQ(seen[2].protocolVersion, MockMcpHttpServer::kServerVersion);
}

TEST(MCPHttpTransportTest, TransportExposesNegotiatedSession) {
    MockMcpHttpServer server;
    HttpTransport transport(server.url(), {}, 10);
    ASSERT_TRUE(transport.connect());
    json reply = transport.sendRequest("initialize", {{"protocolVersion", "2025-11-25"},
                                                      {"capabilities", json::object()},
                                                      {"clientInfo", {{"name", "t"}}}});
    EXPECT_EQ(reply["result"]["serverInfo"]["name"], "mock-mcp");
    EXPECT_EQ(transport.sessionId(), MockMcpHttpServer::kSessionId);
    EXPECT_EQ(transport.protocolVersion(), MockMcpHttpServer::kServerVersion);

    transport.disconnect();
    EXPECT_FALSE(transport.isConnected());
    EXPECT_TRUE(transport.sessionId().empty());
}

TEST(MCPHttpTransportTest, HttpToolsKeepTheConfirmationGate) {
    MockMcpHttpServer server;
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    auto tools = client.listTools();
    ASSERT_EQ(tools.size(), 2u);
    EXPECT_EQ(tools[0].toToolInfo("weather").policy, ToolPolicy::ALLOW);
    EXPECT_EQ(tools[1].toToolInfo("weather").policy, ToolPolicy::CONFIRM);
    EXPECT_EQ(tools[1].toToolInfo("weather").name, "mcp_weather_delete_file");
}

TEST(MCPHttpTransportTest, UnknownMethodSurfacesTheJsonRpcError) {
    MockMcpHttpServer server;
    HttpTransport transport(server.url(), {}, 10);
    transport.connect();
    transport.sendRequest("initialize", json::object());
    json reply = transport.sendRequest("resources/list");
    ASSERT_TRUE(reply.contains("error"));
    EXPECT_EQ(reply["error"]["message"], "Method not found");
}

// ---------------------------------------------------------------------------
// Header env expansion
// ---------------------------------------------------------------------------

TEST(MCPHttpTransportTest, HeaderEnvVarIsExpandedAndSent) {
    setEnvVar("GAIA_TEST_MCP_HTTP_TOKEN", "s3cret");
    MockMcpHttpServer server;
    server.requireAuthorization("Bearer s3cret");

    MCPClient client = MCPClient::fromConfig(
        "auth", httpConfig(server.url(),
                           {{"Authorization", "Bearer ${GAIA_TEST_MCP_HTTP_TOKEN}"},
                            {"X-Literal", "cost $5 and ${GAIA_TEST_MCP_HTTP_TOKEN}!"}}),
        10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    EXPECT_EQ(client.listTools().size(), 2u);
    for (const auto& s : server.seen()) EXPECT_EQ(s.authorization, "Bearer s3cret");
    setEnvVar("GAIA_TEST_MCP_HTTP_TOKEN", nullptr);
}

TEST(MCPHttpTransportTest, UnsetHeaderEnvVarIsALoudError) {
    setEnvVar("GAIA_TEST_MCP_HTTP_UNSET", nullptr);
    try {
        MCPClient::fromConfig(
            "auth", httpConfig("http://127.0.0.1:1/mcp",
                               {{"Authorization", "Bearer ${GAIA_TEST_MCP_HTTP_UNSET}"}}));
        FAIL() << "expected std::invalid_argument";
    } catch (const std::invalid_argument& e) {
        EXPECT_TRUE(contains(e.what(), "GAIA_TEST_MCP_HTTP_UNSET"));
        EXPECT_TRUE(contains(e.what(), "Authorization"));
        EXPECT_TRUE(contains(e.what(), "not set"));
    }
}

TEST(MCPHttpTransportTest, ExpandEnvVarsEdgeCases) {
    setEnvVar("GAIA_TEST_MCP_A", "x");
    EXPECT_EQ(expandMcpEnvVars("${GAIA_TEST_MCP_A}${GAIA_TEST_MCP_A}", "ctx"), "xx");
    EXPECT_EQ(expandMcpEnvVars("$GAIA_TEST_MCP_A and $", "ctx"), "$GAIA_TEST_MCP_A and $");
    EXPECT_THROW(expandMcpEnvVars("Bearer ${GAIA_TEST_MCP_A", "ctx"), std::invalid_argument);
    EXPECT_THROW(expandMcpEnvVars("${}", "ctx"), std::invalid_argument);
    setEnvVar("GAIA_TEST_MCP_A", nullptr);
}

// ---------------------------------------------------------------------------
// Failures are loud
// ---------------------------------------------------------------------------

TEST(MCPHttpTransportTest, WrongPath404FailsConnectNamingUrlAndStatus) {
    MockMcpHttpServer server;
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url("/nope")), 10);
    EXPECT_FALSE(client.connect());
    EXPECT_TRUE(contains(client.lastError(), server.url("/nope")));
    EXPECT_TRUE(contains(client.lastError(), "404"));
    EXPECT_TRUE(contains(client.lastError(), "\"url\""));
}

TEST(MCPHttpTransportTest, ServerError500OnToolCallThrows) {
    MockMcpHttpServer server;
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    server.failToolCalls(true);
    try {
        client.callTool("get_weather", {{"city", "Austin"}});
        FAIL() << "expected std::runtime_error";
    } catch (const std::runtime_error& e) {
        EXPECT_TRUE(contains(e.what(), "500"));
        EXPECT_TRUE(contains(e.what(), server.url()));
        EXPECT_TRUE(contains(e.what(), "tools/call"));
    }
}

TEST(MCPHttpTransportTest, Unauthorized401NamesTheHeaders) {
    MockMcpHttpServer server;
    server.requireAuthorization("Bearer right");
    MCPClient client = MCPClient::fromConfig(
        "auth", httpConfig(server.url(), {{"Authorization", "Bearer wrong"}}), 10);
    EXPECT_FALSE(client.connect());
    EXPECT_TRUE(contains(client.lastError(), "401"));
    EXPECT_TRUE(contains(client.lastError(), "Authorization"));
}

TEST(MCPHttpTransportTest, ExpiredSession404AsksToReconnect) {
    MockMcpHttpServer server;
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    server.expireSession();
    try {
        client.callTool("get_weather", {{"city", "Austin"}});
        FAIL() << "expected std::runtime_error";
    } catch (const std::runtime_error& e) {
        EXPECT_TRUE(contains(e.what(), "404"));
        EXPECT_TRUE(contains(e.what(), "reconnect"));
    }
    EXPECT_FALSE(client.isConnected());

    // A fresh handshake gets a new session and works again.
    ASSERT_TRUE(client.connect()) << client.lastError();
    EXPECT_EQ(client.callTool("get_weather", {{"city", "Oslo"}})["content"][0]["text"],
              "Sunny in Oslo");
}

TEST(MCPHttpTransportTest, MalformedJsonBodyFailsConnect) {
    MockMcpHttpServer server;
    server.setReply(MockMcpHttpServer::Reply::InvalidJson);
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    EXPECT_FALSE(client.connect());
    EXPECT_TRUE(contains(client.lastError(), "invalid JSON"));
    EXPECT_TRUE(contains(client.lastError(), server.url()));
}

TEST(MCPHttpTransportTest, UnexpectedContentTypeFailsConnect) {
    MockMcpHttpServer server;
    server.setReply(MockMcpHttpServer::Reply::HtmlContentType);
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    EXPECT_FALSE(client.connect());
    EXPECT_TRUE(contains(client.lastError(), "text/html"));
}

TEST(MCPHttpTransportTest, SseStreamWithoutReplyFailsConnect) {
    MockMcpHttpServer server;
    server.setReply(MockMcpHttpServer::Reply::SseNoReply);
    MCPClient client = MCPClient::fromConfig("weather", httpConfig(server.url()), 10);
    EXPECT_FALSE(client.connect());
    EXPECT_TRUE(contains(client.lastError(), "without answering"));
}

TEST(MCPHttpTransportTest, ConnectionRefusedFailsConnect) {
    const std::string url = "http://127.0.0.1:1/mcp";  // nothing listens on port 1
    MCPClient client = MCPClient::fromConfig("gone", httpConfig(url), 2);
    EXPECT_FALSE(client.connect());
    EXPECT_TRUE(contains(client.lastError(), url));
    EXPECT_TRUE(contains(client.lastError(), "running"));
}

// ---------------------------------------------------------------------------
// fromConfig shapes
// ---------------------------------------------------------------------------

TEST(MCPHttpConfigTest, AcceptsStreamableHttpTypeAndUrlOnly) {
    MockMcpHttpServer server;
    for (const json& config : {json{{"type", "streamable-http"}, {"url", server.url()}},
                               json{{"url", server.url()}}}) {
        MCPClient client = MCPClient::fromConfig("s", config, 10);
        ASSERT_TRUE(client.connect()) << config.dump() << ": " << client.lastError();
        EXPECT_EQ(client.listTools().size(), 2u);
    }
}

TEST(MCPHttpConfigTest, RejectsBadShapes) {
    const std::vector<json> bad = {
        {{"type", "http"}},                                              // no url
        {{"type", "http"}, {"url", "ftp://x/mcp"}},                      // not http(s)
        {{"type", "sse"}, {"url", "http://x/sse"}},                      // legacy SSE
        {{"url", "http://x/mcp"}, {"command", "npx"}},                   // ambiguous
        {{"type", "http"}, {"url", "http://x/mcp"}, {"headers", "x"}},   // headers not object
        {{"type", "http"}, {"url", "http://x/mcp"}, {"headers", {{"A", 1}}}},
    };
    for (const auto& config : bad) {
        EXPECT_THROW(MCPClient::fromConfig("s", config), std::invalid_argument) << config.dump();
    }
}

// ---------------------------------------------------------------------------
// Registry: http entries resolve and connect
// ---------------------------------------------------------------------------

class MCPHttpRegistryTest : public ::testing::Test {
protected:
    fs::path dir;

    void SetUp() override {
        dir = fs::temp_directory_path() /
              ("gaia_mcp_http_" + std::to_string(std::chrono::steady_clock::now()
                                                     .time_since_epoch()
                                                     .count()));
        fs::create_directories(dir);
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(dir, ec);
    }

    std::string write(const json& servers) {
        fs::path p = dir / "mcp_servers.json";
        std::ofstream(p) << json{{"mcpServers", servers}}.dump(2);
        return p.string();
    }
};

TEST_F(MCPHttpRegistryTest, HttpEntryResolvesAndConnects) {
    setEnvVar("GAIA_TEST_MCP_REG_TOKEN", "tok");
    MockMcpHttpServer server;
    server.requireAuthorization("Bearer tok");
    MCPRegistry registry({write({{"lemonade",
                                  {{"type", "http"},
                                   {"url", server.url()},
                                   {"headers", {{"Authorization",
                                                 "Bearer ${GAIA_TEST_MCP_REG_TOKEN}"}}}}}})});

    json config = registry.require("lemonade");
    // Expansion happens at connect time, so a reconnect re-reads the env.
    EXPECT_EQ(config["headers"]["Authorization"], "Bearer ${GAIA_TEST_MCP_REG_TOKEN}");
    EXPECT_FALSE(config.contains("command"));

    MCPClient client = MCPClient::fromConfig("lemonade", config, 10);
    ASSERT_TRUE(client.connect()) << client.lastError();
    EXPECT_EQ(client.listTools().size(), 2u);
    setEnvVar("GAIA_TEST_MCP_REG_TOKEN", nullptr);
}

TEST_F(MCPHttpRegistryTest, UrlOnlyEntryIsHttpAndHeadersDefaultToEmpty) {
    MCPRegistry registry({write({{"remote", {{"url", "https://example.invalid/mcp"}}}})});
    json config = registry.require("remote");
    EXPECT_EQ(config["headers"], json::object());
}

TEST_F(MCPHttpRegistryTest, BadHttpEntriesRaise) {
    MCPRegistry registry({write({
        {"nourl", {{"type", "http"}}},
        {"both", {{"url", "http://x/mcp"}, {"command", "npx"}}},
        {"keyring", {{"type", "http"},
                     {"url", "http://x/mcp"},
                     {"headers", {{"Authorization", {{"$keyring", "gaia:lemonade"}}}}}}},
        {"sse", {{"type", "sse"}, {"url", "http://x/sse"}}},
    })});
    auto messageFor = [&](const std::string& id) -> std::string {
        try {
            registry.require(id);
        } catch (const MCPRegistryError& e) {
            return e.what();
        }
        return "";
    };
    EXPECT_TRUE(contains(messageFor("nourl"), "url"));
    EXPECT_TRUE(contains(messageFor("both"), "\"type\""));
    EXPECT_TRUE(contains(messageFor("keyring"), "keyring"));
    EXPECT_TRUE(contains(messageFor("sse"), "'sse'"));
}

// ---------------------------------------------------------------------------
// stdio now completes the handshake too
// ---------------------------------------------------------------------------

TEST(MCPStdioHandshakeTest, ConnectSendsInitializedNotification) {
#ifdef _WIN32
    GTEST_SKIP() << "POSIX-only: uses /bin/sh as the MCP server";
#else
    fs::path dir = fs::temp_directory_path() / "gaia_mcp_stdio_handshake";
    fs::create_directories(dir);
    fs::path out = dir / "second_line.json";
    fs::remove(out);
    fs::path script = dir / "server.sh";
    {
        std::ofstream s(script);
        s << "read _init\n"
          << "printf '{\"jsonrpc\":\"2.0\",\"id\":0,\"result\":{\"serverInfo\":{\"name\":\"sh\"}}}\\n'\n"
          << "read second\n"
          << "printf '%s' \"$second\" > '" << out.string() << "'\n"
          << "read _rest\n";
    }
    MCPClient client = MCPClient::fromConfig("sh", {{"command", "/bin/sh"},
                                                     {"args", {script.string()}}}, 10);
    ASSERT_TRUE(client.connect()) << client.lastError();

    std::string second;
    for (int i = 0; i < 200 && second.empty(); ++i) {
        std::ifstream in(out);
        std::getline(in, second);
        if (second.empty()) std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    client.disconnect();
    json notification = json::parse(second);
    EXPECT_EQ(notification["method"], "notifications/initialized");
    EXPECT_FALSE(notification.contains("id"));
    fs::remove_all(dir);
#endif
}
