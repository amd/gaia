// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The send-email skill's tools, run the way the agent runs a gated call: the
// preflight, then the approval card, then the tool. Accounts live in a temp-HOME
// vault and mailbox answers come from a fake Transport, so nothing is drafted or
// sent anywhere. The parity cases replay what
// fixtures/mail/make_send_email_parity.py recorded from the Python tools,
// including every request that would change the mailbox and Gmail's MIME text.

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <mutex>
#include <random>
#include <regex>
#include <vector>

#include "gaia/connectors.h"
#include "gaia/email_tools.h"
#include "gaia/send_email_tools.h"
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

std::string pathOf(const std::string& url) {
    const size_t hostEnd = url.find('/', std::string("https://").size());
    const std::string path = url.substr(hostEnd, url.find('?', hostEnd) - hostEnd);
    std::string out;
    for (size_t i = 0; i < path.size(); ++i) {
        if (path[i] == '%' && i + 2 < path.size()) {
            out += static_cast<char>(std::stoi(path.substr(i + 1, 2), nullptr, 16));
            i += 2;
        } else {
            out += path[i];
        }
    }
    return out;
}

std::string base64urlDecode(std::string s) {
    static const std::string alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
    std::string out;
    int bits = 0;
    unsigned value = 0;
    for (char c : s) {
        const size_t at = alphabet.find(c);
        if (at == std::string::npos) continue;
        value = (value << 6) | static_cast<unsigned>(at);
        bits += 6;
        if (bits >= 8) {
            bits -= 8;
            out += static_cast<char>((value >> bits) & 0xFF);
        }
    }
    return out;
}

/// A write's JSON body as the Python script records it: Gmail's raw MIME as text.
json decoded(json body) {
    json* holder = body.is_object() && body.contains("message") ? &body["message"] : &body;
    if (holder->is_object() && holder->contains("raw")) {
        const std::string raw = (*holder)["raw"].get<std::string>();
        holder->erase("raw");
        static const std::regex boundary("={15}\\d{19}==");
        (*holder)["mime"] = std::regex_replace(base64urlDecode(raw), boundary, "BOUNDARY");
        (*holder)["raw_is_base64url"] = raw.find_first_of("=+/") == std::string::npos;
    }
    return body;
}

/// Serves `routes` as the Python script's router does, and records every write.
struct Router {
    json routes;
    std::vector<int> used;
    json writes = json::array();
    std::mutex mu;  // Gmail fetches message details on several threads at once

    explicit Router(json r) : routes(std::move(r)), used(routes.size(), 0) {}

    HttpResponse operator()(const mail::HttpCall& call) {
        std::lock_guard<std::mutex> lock(mu);
        const std::string path = pathOf(call.url);
        if (call.method != "GET") {
            writes.push_back({{"method", call.method},
                              {"path", path},
                              {"json", call.body.empty() ? json(nullptr) : decoded(json::parse(call.body))}});
        }
        for (size_t i = 0; i < routes.size(); ++i) {
            const json& route = routes[i];
            if (route["method"] != call.method || route["path"] != path) continue;
            if (route.contains("times") && used[i] >= route["times"].get<int>()) continue;
            ++used[i];
            if (route.contains("raise")) {
                const std::string kind = route["raise"].get<std::string>();
                throw mail::TransportError(kind, kind == "ConnectError" || kind == "ConnectTimeout", "simulated");
            }
            HttpResponse resp;
            resp.status = route["status"].get<int>();
            if (route.contains("text")) {
                resp.body = route["text"].get<std::string>();
            } else if (route.contains("json")) {
                resp.body = route["json"].dump();
            }
            return resp;
        }
        ADD_FAILURE() << "no route for " << call.method << " " << call.url;
        HttpResponse resp;
        resp.status = 599;
        return resp;
    }
};

class SendEmailToolsTest : public ::testing::Test {
protected:
    fs::path home;
    std::string savedHome;
    std::map<std::string, std::string> keychain;
    std::shared_ptr<Router> router;
    std::string surface = "cli";
    std::unique_ptr<EmailTools> email;
    std::unique_ptr<SendEmailTools> sendEmail;
    ToolRegistry registry;

    void SetUp() override {
        std::random_device rd;
        home = fs::temp_directory_path() / ("gaia-send-email-" + std::to_string(rd()));
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
        connectors::setTokenTransportForTesting([](const mail::HttpCall& call) {
            const std::string provider = call.url.find("googleapis") != std::string::npos ? "google" : "microsoft";
            HttpResponse resp;
            resp.status = 200;
            resp.body = R"({"access_token":"at-)" + provider + R"(","expires_in":3600})";
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

    void connect(const json& connections, const json& grants) {
        connectors::resetForTesting();
        json entries = json::object();
        json index = json::object();
        for (auto it = connections.begin(); it != connections.end(); ++it) {
            const std::string p = it.key();
            const json& spec = it.value();
            json blob{{"account_email", spec.value("account_email", "")},
                      {"account_id", "acct-" + p},
                      {"refresh_token", "rt-" + p},
                      {"scopes", spec["scopes"]},
                      {"client_id_hash", connectors::crc32Hex("client-" + p)},
                      {"connected_at", 1.0}};
            if (p == "microsoft") blob["tenant"] = "consumers";
            entries[p + ":default"] = blob.dump();
            entries["provider:" + p] = json{{"client_id", "client-" + p}, {"client_secret", "sec"}}.dump();
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
        options.turnBodyBudget = [] { return 12000; };
        std::shared_ptr<Router> r = router;
        options.transport = [r](const mail::HttpCall& call) { return (*r)(call); };
        email = std::make_unique<EmailTools>(std::move(options));
        sendEmail = std::make_unique<SendEmailTools>(*email);
        registry.clear();
        email->registerAll(registry);
        sendEmail->registerAll(registry);
    }

    const ToolInfo& tool(const std::string& name) {
        const ToolInfo* found = registry.findTool("send-email/" + name);
        if (!found) throw std::logic_error("send-email/" + name + " is not registered");
        return *found;
    }

    /// One call through the stages `stage` names, recorded as the Python script records it.
    json run(const std::string& name, const json& args, const std::string& stage) {
        const ToolInfo& t = tool(name);
        json out = json::object();
        router->writes = json::array();
        bool refused = false;
        if (t.preflight && (stage == "all" || stage == "preflight")) {
            const std::optional<json> refusal = t.preflight(args);
            out["preflight"] = refusal ? *refusal : json(nullptr);
            refused = refusal.has_value();
            if (!refused) out["prompt"] = t.promptArgs(args);
        }
        if ((stage == "all" || stage == "body") && !refused) out["result"] = t.callback(args);
        out["writes"] = router->writes;
        return out;
    }
};

const json& parity() {
    static const json data = [] {
        std::ifstream in(std::string(GAIA_TEST_FIXTURES_DIR) + "/mail/send_email_parity.json");
        EXPECT_TRUE(in.good()) << "missing fixtures/mail/send_email_parity.json";
        return json::parse(in);
    }();
    return data;
}

const json& caseNamed(const std::string& name) {
    for (const auto& c : parity()["cases"]) {
        if (c["name"] == name) return c;
    }
    throw std::logic_error("no parity case " + name);
}

}  // namespace

TEST_F(SendEmailToolsTest, EveryCaseMatchesThePythonTools) {
    const json& cases = parity()["cases"];
    ASSERT_GE(cases.size(), 16u);
    size_t compared = 0;
    for (const auto& c : cases) {
        SCOPED_TRACE(c["name"].get<std::string>());
        surface = c["surface"].get<std::string>();
        connect(c["connections"], c["grants"]);
        router = std::make_shared<Router>(c["routes"]);
        build();
        for (size_t i = 0; i < c["steps"].size(); ++i) {
            const json& step = c["steps"][i];
            if (step.contains("set_connections")) {
                connect(step["set_connections"], step["set_grants"]);
                continue;
            }
            SCOPED_TRACE("step " + std::to_string(i) + " " + step["tool"].get<std::string>());
            const json got = run(step["tool"], step["args"], step["stage"]);
            const json& want = c["expected"][i];
            EXPECT_EQ(got, want) << "got:  " << got.dump(1) << "\nwant: " << want.dump(1);
            ++compared;
        }
    }
    EXPECT_GE(compared, 100u);
}

TEST_F(SendEmailToolsTest, SchemasMatchThePythonTools) {
    build();
    const json& schemas = parity()["schemas"];
    ASSERT_EQ(schemas.size(), SendEmailTools::toolNames().size());
    for (const auto& full : SendEmailTools::toolNames()) {
        const std::string name = full.substr(std::string("send-email/").size());
        SCOPED_TRACE(name);
        const ToolInfo& t = tool(name);
        const json& want = schemas.at(name);
        EXPECT_EQ(t.description, want["description"].get<std::string>());
        EXPECT_EQ(t.atomic, want["atomic"].get<bool>());
        const bool confirm = want["confirm"].get<bool>();
        EXPECT_EQ(t.policy, confirm ? ToolPolicy::CONFIRM : ToolPolicy::ALLOW);
        EXPECT_EQ(t.personMustApprove, confirm) << "no always-allow for a change or a send";
        EXPECT_EQ(static_cast<bool>(t.preflight), confirm);
        EXPECT_EQ(static_cast<bool>(t.promptArgs), confirm);
        EXPECT_TRUE(t.onlineService);
        EXPECT_FALSE(t.enabled) << "loading the skill enables it";
        ASSERT_EQ(t.parameters.size(), want["parameters"].size());
        for (size_t i = 0; i < t.parameters.size(); ++i) {
            const auto& p = t.parameters[i];
            const json& w = want["parameters"][i];
            EXPECT_EQ(p.name, w["name"].get<std::string>());
            EXPECT_EQ(paramTypeToString(p.type), w["type"].get<std::string>());
            EXPECT_EQ(p.required, w["required"].get<bool>());
            EXPECT_EQ(p.description, w["description"].get<std::string>());
        }
    }
}

TEST_F(SendEmailToolsTest, TheRegistryRefusesTheToolsUntilTheSkillLoads) {
    const json& c = caseNamed("gmail_send_now");
    connect(c["connections"], c["grants"]);
    router = std::make_shared<Router>(c["routes"]);
    build();
    const json args{{"to", "dana@example.com"}, {"subject", "Hi"}, {"body", "x"}};
    const json refused = registry.executeTool("send-email/send_now", args);
    EXPECT_EQ(refused.value("status", ""), "error") << refused.dump(1);
    EXPECT_TRUE(router->writes.empty()) << "a disabled tool must not reach the mailbox";
}

TEST_F(SendEmailToolsTest, ADeclinedCardDropsTheMessageItShowed) {
    const json& c = caseNamed("gmail_send_now");
    connect(c["connections"], c["grants"]);
    router = std::make_shared<Router>(c["routes"]);
    build();
    registry.setEnabled("send-email/send_now", true);
    const json args{{"to", "dana@example.com"}, {"subject", "Hi"}, {"body", "x"}};
    json shown;
    registry.setConfirmCallback([&shown](const std::string&, const json& a) {
        shown = a;
        return ToolConfirmResult::DENY;
    });
    const json declined = registry.executeTool("send-email/send_now", args);
    EXPECT_NE(declined.value("status", ""), "success") << declined.dump(1);
    ASSERT_TRUE(shown.is_object()) << "the card was never raised: " << declined.dump(1);
    EXPECT_NE(shown.value("_exact_text", "").find("dana@example.com"), std::string::npos) << shown.dump(1);
    EXPECT_EQ(shown.value("_needs_person", false), true) << shown.dump(1);
    const json direct = tool("send_now").callback(args);
    EXPECT_EQ(direct.value("sent", true), false) << "a declined message must not be sendable: " << direct.dump(1);
    EXPECT_NE(direct.value("error", "").find("without going through its confirmation"), std::string::npos);
    EXPECT_TRUE(router->writes.empty()) << "nothing reaches the mailbox after a decline";
}

TEST_F(SendEmailToolsTest, WithNobodyToAskASendIsRefusedBeforeTheMailboxIsRead) {
    const json& c = caseNamed("gmail_reply_and_forward");
    connect(c["connections"], c["grants"]);
    router = std::make_shared<Router>(c["routes"]);
    build();
    registry.setEnabled("send-email/reply_to_message", true);
    registry.setNobodyToAsk([] { return true; });
    const json refused =
        registry.executeTool("send-email/reply_to_message", {{"message_id", "msg1"}, {"body", "Yes, confirmed."}});
    EXPECT_EQ(refused.value("status", ""), "error") << refused.dump(1);
    EXPECT_NE(refused.value("error", "").find("Tell the user the mail did not go out."), std::string::npos)
        << refused.dump(1);
    for (int hits : router->used) EXPECT_EQ(hits, 0) << "the mailbox was read before the refusal";
    EXPECT_TRUE(router->writes.empty());
}

TEST_F(SendEmailToolsTest, ACardForACallThatWasNeverPreparedIsAnError) {
    build();
    EXPECT_THROW(tool("send_now").promptArgs({{"to", "dana@example.com"}}), std::logic_error);
}

TEST_F(SendEmailToolsTest, AddressesPythonsMimeRefusesAreRefusedBeforeTheCard) {
    const json& c = caseNamed("gmail_send_now");
    connect(c["connections"], c["grants"]);
    router = std::make_shared<Router>(c["routes"]);
    build();
    const std::vector<std::pair<std::string, std::string>> cases = {
        {"a..b@x.com", "local-part"},       {".a@x.com", "local-part"},  {"a.@x.com", "local-part"},
        {"a\\b@x.com", "local-part"},       {"bob@x.com.", "domain"},    {"bob@.x.com", "domain"},
        {"bob@x..com", "domain"},           {"bob@x\\y.com", "domain"},  {"=?utf-8?q?x?=@y.com", "local-part"},
        {"=?utf-8?q?x?=b@y.com", "local-part"}, {"bob@=?utf-8?q?x?=.com", "domain"},
    };
    for (const auto& [address, part] : cases) {
        SCOPED_TRACE(address);
        const json got = run("send_now", {{"to", address}, {"subject", "s"}, {"body", "b"}}, "all");
        ASSERT_TRUE(got["preflight"].is_object()) << got.dump(1);
        const std::string error = got["preflight"].value("error", "");
        EXPECT_NE(error.find("`to` has an address that can't be sent to"), std::string::npos) << error;
        EXPECT_NE(error.find(part + " is not"), std::string::npos) << error;
        EXPECT_TRUE(got["writes"].empty());
    }
    for (const std::string address : {"=?abc@x.com", "=?a?b?=@x.com", "a.=?utf-8?q?x?=@y.com", "bob@x_y.com",
                                      "x!#$%&'*+/=?^_`{|}~-@y.com", "a@b"}) {
        SCOPED_TRACE(address);
        const json got = run("send_now", {{"to", address}, {"subject", "s"}, {"body", "b"}}, "preflight");
        EXPECT_TRUE(got["preflight"].is_null()) << "Python sends this one: " << got.dump(1);
    }
}
