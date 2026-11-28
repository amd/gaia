// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The email read tools, called through a ToolRegistry the way the agent calls
// them. Accounts live in a temp-HOME vault, mailbox answers come from a fake
// Transport, and nothing reaches the network. The parity cases replay what
// fixtures/mail/make_email_tools_parity.py recorded from the Python tools, so a
// drift in either runtime's results, wording or schemas fails here.

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <random>

#include "gaia/connect_card.h"
#include "gaia/connectors.h"
#include "gaia/email_tools.h"
#include "gaia/tool_registry.h"

namespace fs = std::filesystem;
using namespace gaia;

namespace {

const std::string kKey(32, '\x07');
const std::string kKeyHex = [] {
    std::string h;
    for (int i = 0; i < 32; ++i) h += "07";
    return h;
}();

void setEnv(const char* name, const std::string& value) {
#ifdef _WIN32
    _putenv_s(name, value.c_str());
#else
    if (value.empty()) {
        unsetenv(name);
    } else {
        setenv(name, value.c_str(), 1);
    }
#endif
}

std::string pctDecode(const std::string& s) {
    std::string out;
    for (size_t i = 0; i < s.size(); ++i) {
        if (s[i] == '%' && i + 2 < s.size()) {
            out += static_cast<char>(std::stoi(s.substr(i + 1, 2), nullptr, 16));
            i += 2;
        } else if (s[i] == '+') {
            out += ' ';
        } else {
            out += s[i];
        }
    }
    return out;
}

struct Request {
    std::string method;
    std::string path;
    std::vector<std::pair<std::string, std::string>> params;
    std::string auth;

    std::string first(const std::string& key) const {
        for (const auto& [k, v] : params) {
            if (k == key) return v;
        }
        return "\x01";  // absent
    }
};

Request parse(const mail::HttpCall& call) {
    Request r;
    r.method = call.method;
    const size_t hostEnd = call.url.find('/', std::string("https://").size());
    const std::string rest = call.url.substr(hostEnd);
    const size_t q = rest.find('?');
    r.path = pctDecode(rest.substr(0, q));
    if (q != std::string::npos) {
        std::string query = rest.substr(q + 1);
        size_t at = 0;
        while (at <= query.size()) {
            size_t amp = query.find('&', at);
            if (amp == std::string::npos) amp = query.size();
            const std::string pair = query.substr(at, amp - at);
            const size_t eq = pair.find('=');
            if (!pair.empty()) r.params.emplace_back(pctDecode(pair.substr(0, eq)), pctDecode(pair.substr(eq + 1)));
            at = amp + 1;
        }
    }
    auto it = call.headers.find("Authorization");
    if (it != call.headers.end()) r.auth = it->second;
    return r;
}

/// Serves `routes` as the Python script's router does: the first route whose
/// method, path and listed query values match, and whose `times` is not used up.
struct Router {
    json routes;
    std::vector<int> used;
    std::vector<Request> seen;

    explicit Router(json r) : routes(std::move(r)), used(routes.size(), 0) {}

    HttpResponse operator()(const mail::HttpCall& call) {
        const Request req = parse(call);
        seen.push_back(req);
        for (size_t i = 0; i < routes.size(); ++i) {
            const json& route = routes[i];
            if (route["method"] != req.method || route["path"] != req.path) continue;
            bool match = true;
            const json query = route.value("query", json::object());
            for (auto it = query.begin(); it != query.end(); ++it) {
                match = match && req.first(it.key()) == it.value().get<std::string>();
            }
            if (!match) continue;
            if (route.contains("times") && used[i] >= route["times"].get<int>()) continue;
            ++used[i];
            HttpResponse resp;
            resp.status = route["status"].get<int>();
            resp.body = route["json"].dump();
            return resp;
        }
        ADD_FAILURE() << "no route for " << call.method << " " << call.url;
        HttpResponse resp;
        resp.status = 599;
        return resp;
    }
};

json stripKeys(json value, const std::vector<std::string>& keys) {
    if (value.is_object()) {
        for (const auto& k : keys) value.erase(k);
        for (auto& [k, v] : value.items()) v = stripKeys(v, keys);
    } else if (value.is_array()) {
        for (auto& v : value) v = stripKeys(v, keys);
    }
    return value;
}

class EmailToolsTest : public ::testing::Test {
protected:
    fs::path home;
    std::string savedHome;
    std::map<std::string, std::string> keychain;
    std::set<std::string> revoked;
    std::shared_ptr<Router> router;
    std::string surface = "cli";
    int budget = 12000;
    std::unique_ptr<EmailTools> tools;
    ToolRegistry registry;

    void SetUp() override {
        std::random_device rd;
        home = fs::temp_directory_path() / ("gaia-email-tools-" + std::to_string(rd()));
        fs::create_directories(home / ".gaia" / "connectors");
#ifdef _WIN32
        savedHome = getEnvVar("USERPROFILE");
        setEnv("USERPROFILE", home.string());
#else
        savedHome = getEnvVar("HOME");
        setEnv("HOME", home.string());
#endif
        setEnv("GAIA_GOOGLE_CLIENT_ID", "");
        setEnv("GAIA_GOOGLE_CLIENT_SECRET", "");
        connectors::resetForTesting();
        keychain["vault-master-key"] = kKeyHex;
        connectors::setSecretReaderForTesting([this](const std::string&, const std::string& user) {
            auto it = keychain.find(user);
            return it == keychain.end() ? std::optional<std::string>() : std::optional<std::string>(it->second);
        });
        connectors::setTokenTransportForTesting([this](const mail::HttpCall& call) {
            const std::string provider = call.url.find("googleapis") != std::string::npos ? "google" : "microsoft";
            HttpResponse resp;
            if (revoked.count(provider)) {
                resp.status = 400;
                resp.body = R"({"error":"invalid_grant"})";
            } else {
                resp.status = 200;
                resp.body = R"({"access_token":"at-)" + provider + R"(","expires_in":3600})";
            }
            return resp;
        });
        router = std::make_shared<Router>(json::array());
    }

    void TearDown() override {
        connectors::setSecretReaderForTesting({});
        connectors::setTokenTransportForTesting({});
        connectors::resetForTesting();
#ifdef _WIN32
        setEnv("USERPROFILE", savedHome);
#else
        setEnv("HOME", savedHome);
#endif
        std::error_code ec;
        fs::remove_all(home, ec);
    }

    void writeFile(const std::string& name, const std::string& text) {
        std::ofstream(home / ".gaia" / "connectors" / name, std::ios::binary) << text;
    }

    /// Accounts as the Python script describes them: {provider: {scopes, account_email, error?}}.
    void connect(const json& connections, const json& grants) {
        json entries = json::object();
        json index = json::object();
        for (auto it = connections.begin(); it != connections.end(); ++it) {
            const std::string p = it.key();
            const json& spec = it.value();
            const std::string error = spec.value("error", "");
            json blob{{"account_email", spec.value("account_email", "")},
                      {"account_id", "acct-" + p},
                      {"refresh_token", "rt-" + p},
                      {"scopes", spec["scopes"]},
                      {"client_id_hash", connectors::crc32Hex("client-" + p)},
                      {"connected_at", 1.0}};
            if (p == "microsoft") blob["tenant"] = "consumers";
            entries[p + ":default"] = blob.dump();
            if (error != "configuration") {
                entries["provider:" + p] = json{{"client_id", "client-" + p}, {"client_secret", "sec"}}.dump();
            }
            if (error == "busy") entries["recovery:forward:" + p] = R"({"account_slot":"default"})";
            index[p] = {{"default_slot", "default"},
                        {"accounts", {{{"slot", "default"}, {"account_id", "acct-" + p}, {"state", "ok"}}}}};
        }
        writeFile("vault.enc",
                  connectors::encryptVaultForTesting(kKey, json{{"v", 1}, {"entries", entries}}.dump()));
        writeFile("accounts.json", json{{"version", 1}, {"connectors", index}}.dump());
        json ledger = json::object();
        for (auto it = grants.begin(); it != grants.end(); ++it) ledger[it.key()] = {{EmailTools::kAgentId, it.value()}};
        writeFile("grants.json", ledger.dump());
    }

    void build() {
        EmailTools::Options options;
        options.surface = [this] { return surface; };
        options.turnBodyBudget = [this] { return budget; };
        std::shared_ptr<Router> r = router;
        options.transport = [r](const mail::HttpCall& call) { return (*r)(call); };
        tools = std::make_unique<EmailTools>(std::move(options));
        registry.clear();
        tools->registerAll(registry);
    }

    json callTool(const std::string& name, const json& args = json::object()) { return registry.executeTool(name, args); }
};

const json& parity() {
    static const json data = [] {
        std::ifstream in(std::string(GAIA_TEST_FIXTURES_DIR) + "/mail/email_tools_parity.json");
        EXPECT_TRUE(in.good()) << "missing fixtures/mail/email_tools_parity.json";
        return json::parse(in);
    }();
    return data;
}

const json& gmailRoutes() {
    static const json routes = [] {
        for (const auto& c : parity()["cases"]) {
            if (c["name"] == "gmail_reads") return c["routes"];
        }
        return json::array();
    }();
    return routes;
}

}  // namespace

// ---- parity with the Python tools ----

TEST_F(EmailToolsTest, EveryCaseMatchesThePythonTools) {
    const json& cases = parity()["cases"];
    ASSERT_GE(cases.size(), 25u);
    size_t compared = 0;
    for (const auto& c : cases) {
        SCOPED_TRACE(c["name"].get<std::string>());
        connectors::resetForTesting();
        surface = c["surface"].get<std::string>();
        budget = c["budget"].get<int>();
        revoked.clear();
        for (auto it = c["token_errors"].begin(); it != c["token_errors"].end(); ++it) {
            ASSERT_EQ(it.value(), "ConnectionRevokedError");
            revoked.insert(it.key());
        }
        connect(c["connections"], c["grants"]);
        router = std::make_shared<Router>(c["routes"]);
        build();
        const std::vector<std::string> ignore = c["ignore_keys"].get<std::vector<std::string>>();
        for (size_t i = 0; i < c["steps"].size(); ++i) {
            const json& step = c["steps"][i];
            if (step.value("new_turn", false)) {
                tools->startTurn();
                continue;
            }
            SCOPED_TRACE("step " + std::to_string(i) + " " + step["tool"].get<std::string>());
            const json got = stripKeys(callTool(step["tool"], step["args"]), ignore);
            const json want = stripKeys(c["expected"][i], ignore);
            EXPECT_EQ(got, want) << "got:  " << got.dump(1) << "\nwant: " << want.dump(1);
            ++compared;
        }
    }
    EXPECT_GE(compared, 60u);
}

TEST_F(EmailToolsTest, SchemasMatchThePythonTools) {
    build();
    const json& schemas = parity()["schemas"];
    ASSERT_EQ(schemas.size(), EmailTools::toolNames().size());
    for (const auto& name : EmailTools::toolNames()) {
        SCOPED_TRACE(name);
        const ToolInfo* tool = registry.findTool(name);
        ASSERT_NE(tool, nullptr);
        const json& want = schemas.at(name);
        EXPECT_EQ(tool->description, want["description"].get<std::string>());
        EXPECT_EQ(tool->atomic, want["atomic"].get<bool>());
        // As src/gaia/agents/base/offline.py's ONLINE_SERVICE_TOOLS: only the account list stays on this PC.
        EXPECT_EQ(tool->onlineService, name != "list_connected_mailboxes") << "refused while the internet is off";
        ASSERT_EQ(tool->parameters.size(), want["parameters"].size());
        for (size_t i = 0; i < tool->parameters.size(); ++i) {
            const auto& p = tool->parameters[i];
            const json& w = want["parameters"][i];
            EXPECT_EQ(p.name, w["name"].get<std::string>());
            EXPECT_EQ(paramTypeToString(p.type), w["type"].get<std::string>());
            EXPECT_EQ(p.required, w["required"].get<bool>());
            EXPECT_EQ(p.description, w["description"].get<std::string>());
        }
    }
}

// ---- behaviour the parity cases do not spell out ----

TEST_F(EmailToolsTest, GmailListInboxSendsTheRequestTheMailboxExpects) {
    connect({{"google", {{"scopes", {"https://www.googleapis.com/auth/gmail.readonly"}}, {"account_email", "me@example.com"}}}},
            {{"google", {"https://www.googleapis.com/auth/gmail.readonly"}}});
    router = std::make_shared<Router>(gmailRoutes());
    build();
    const json result = callTool("list_inbox", {{"limit", 2}, {"unread_only", true}});
    ASSERT_EQ(result["success"], true) << result.dump(1);
    EXPECT_EQ(result["count"], 2);
    EXPECT_EQ(result["suspicious_count"], 1) << "the PayPal lookalike is flagged";
    EXPECT_EQ(result["messages"][1]["suspicious"], true);
    EXPECT_TRUE(result.contains("suspicious_guidance"));
    const Request& listing = router->seen.at(0);
    EXPECT_EQ(listing.path, "/gmail/v1/users/me/messages");
    EXPECT_EQ(listing.first("maxResults"), "2");
    std::vector<std::string> labels;
    for (const auto& [k, v] : listing.params) {
        if (k == "labelIds") labels.push_back(v);
    }
    EXPECT_EQ(labels, (std::vector<std::string>{"INBOX", "UNREAD"}));
    for (const auto& r : router->seen) EXPECT_EQ(r.auth, "Bearer at-google") << r.path;
}

TEST_F(EmailToolsTest, AStringLimitFromASmallModelIsAccepted) {
    connect({{"google", {{"scopes", {"https://www.googleapis.com/auth/gmail.readonly"}}, {"account_email", "me@example.com"}}}},
            {{"google", {"https://www.googleapis.com/auth/gmail.readonly"}}});
    router = std::make_shared<Router>(gmailRoutes());
    build();
    const json result = callTool("list_inbox", {{"limit", "1"}});
    ASSERT_EQ(result["success"], true) << result.dump(1);
    EXPECT_EQ(router->seen.at(0).first("maxResults"), "1");
}

TEST_F(EmailToolsTest, AConnectMadeMidSessionIsSeenOnTheNextCall) {
    surface = "agent_ui";
    build();
    json first = callTool("list_inbox");
    EXPECT_EQ(first["success"], false);
    EXPECT_EQ(first["executed"], false) << "account setup stopped it, so memory must not learn it";
    EXPECT_EQ(first["connect_card"]["providers"], json({"google", "microsoft"}));
    connect({{"google", {{"scopes", {"https://www.googleapis.com/auth/gmail.readonly"}}, {"account_email", "me@example.com"}}}},
            {{"google", {"https://www.googleapis.com/auth/gmail.readonly"}}});
    router->routes = gmailRoutes();
    router->used.assign(router->routes.size(), 0);
    const json second = callTool("list_inbox");
    EXPECT_EQ(second["success"], true) << second.dump(1);
}

TEST_F(EmailToolsTest, NeedsATurnBudget) {
    EmailTools::Options options;
    EXPECT_THROW(EmailTools{options}, std::invalid_argument);
}

TEST(ConnectCard, OffersOnlyKnownAccountsAndAsksForTheAccessNamed) {
    const json card = connect::connectCard({"google", "google", "microsoft"}, "installed:gaia", {"microsoft"},
                                           {{"google", {"s1", "s1"}}, {"microsoft", {}}, {"other", {"x"}}},
                                           {{"google", "me@example.com"}, {"other", "x"}});
    EXPECT_EQ(card, json({{"kind", "connect_account"},
                          {"providers", {"google", "microsoft"}},
                          {"agent_id", "installed:gaia"},
                          {"reconnect", {"microsoft"}},
                          {"scopes", {{"google", {"s1"}}}},
                          {"account", {{"google", "me@example.com"}}}}));
    EXPECT_THROW(connect::connectCard({}, "installed:gaia"), std::invalid_argument);
    EXPECT_THROW(connect::connectCard({"dropbox"}, "installed:gaia"), std::invalid_argument);
}

TEST(ConnectCard, EachSurfaceGetsItsOwnRemedy) {
    json ui{{"error", "No mailbox."}};
    connect::withConnectRemedy(ui, {"google"}, "installed:gaia", "agent_ui");
    EXPECT_EQ(ui["error"], std::string("No mailbox. ") + connect::kConnectButtonNote);
    EXPECT_TRUE(ui.contains("connect_card"));

    json tui{{"error", ""}};
    connect::withConnectRemedy(tui, {"google"}, "installed:gaia", "tui");
    EXPECT_EQ(tui["error"], connect::kConnectInAppNote);
    EXPECT_FALSE(tui.contains("connect_card"));

    json cli = json::object();
    connect::withConnectRemedy(cli, {"google", "microsoft"}, "installed:gaia", "cli");
    EXPECT_EQ(cli["error"],
              "Connect it with `gaia connectors connect google --grant-agent installed:gaia` or `gaia connectors "
              "connect microsoft --grant-agent installed:gaia`, or in the GAIA desktop app under Settings > "
              "Connectors, then ask again.");

    json untouched{{"error", "x"}};
    connect::withConnectRemedy(untouched, {}, "installed:gaia", "agent_ui");
    EXPECT_EQ(untouched, json({{"error", "x"}}));
}

TEST(ConnectCard, ABusyTokenIsRetriedNotReconnected) {
    const auto [busy, busyReconnect] =
        connect::tokenFault("Google", connectors::ConnectorsError("busy", "ConnectionBusyError"));
    EXPECT_FALSE(busyReconnect);
    EXPECT_EQ(busy, "The Google connection is being saved by another GAIA operation right now. Retry in a moment.");
    const auto [revoked, revokedReconnect] =
        connect::tokenFault("Microsoft", connectors::ConnectorsError("gone", "ConnectionRevokedError"));
    EXPECT_TRUE(revokedReconnect);
    EXPECT_EQ(revoked, "GAIA could not use the Microsoft connection (ConnectionRevokedError), so it needs reconnecting.");
}
