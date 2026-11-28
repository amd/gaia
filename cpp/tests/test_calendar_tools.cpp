// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The calendar tools, called through a ToolRegistry the way the agent calls
// them. Accounts live in a temp-HOME vault, calendar answers come from a fake
// Transport, and nothing reaches the network. The parity cases replay what
// fixtures/calendar/make_calendar_tools_parity.py recorded from the Python skill:
// each call's result, the approval prompt it raised, the card it showed and the
// requests it sent. This computer's zone is EST5EDT throughout.

#include <gtest/gtest.h>

#include <ctime>
#include <filesystem>
#include <fstream>
#include <random>

#include "../src/calendar_internal.h"
#include "gaia/calendar_tools.h"
#include "gaia/connectors.h"
#include "gaia/person_approval.h"
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
const std::string kGoogleScope = "https://www.googleapis.com/auth/calendar.events";

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

void setZone(const std::string& zone) {
    setEnv("TZ", zone);
#ifdef _WIN32
    _tzset();
#else
    tzset();
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

/// One request as the Python script records it.
json recorded(const mail::HttpCall& call) {
    const size_t hostEnd = call.url.find('/', std::string("https://").size());
    const std::string rest = call.url.substr(hostEnd);
    const size_t q = rest.find('?');
    json params = json::array();
    if (q != std::string::npos) {
        const std::string query = rest.substr(q + 1);
        size_t at = 0;
        while (at <= query.size()) {
            size_t amp = query.find('&', at);
            if (amp == std::string::npos) amp = query.size();
            const std::string pair = query.substr(at, amp - at);
            const size_t eq = pair.find('=');
            if (!pair.empty()) params.push_back({pctDecode(pair.substr(0, eq)), pctDecode(pair.substr(eq + 1))});
            at = amp + 1;
        }
    }
    auto prefer = call.headers.find("Prefer");
    return {{"method", call.method},
            {"path", pctDecode(rest.substr(0, q))},
            {"params", params},
            {"body", call.body.empty() ? json(nullptr) : json::parse(call.body)},
            {"prefer", prefer == call.headers.end() ? json(nullptr) : json(prefer->second)}};
}

std::string firstParam(const json& request, const std::string& key) {
    for (const auto& p : request["params"]) {
        if (p[0] == key) return p[1];
    }
    return "\x01";  // absent
}

/// Serves `routes` as the Python script's router does: the first route whose
/// method, path and listed query values match, and whose `times` is not used up.
struct Router {
    json routes;
    std::vector<int> used;
    json seen = json::array();

    explicit Router(json r) : routes(std::move(r)), used(routes.size(), 0) {}

    HttpResponse operator()(const mail::HttpCall& call) {
        const json req = recorded(call);
        seen.push_back(req);
        for (size_t i = 0; i < routes.size(); ++i) {
            const json& route = routes[i];
            if (route["method"] != req["method"] || route["path"] != req["path"]) continue;
            bool match = true;
            const json query = route.value("query", json::object());
            for (auto it = query.begin(); it != query.end(); ++it) {
                match = match && firstParam(req, it.key()) == it.value().get<std::string>();
            }
            if (!match) continue;
            if (route.contains("times") && used[i] >= route["times"].get<int>()) continue;
            ++used[i];
            if (route["status"] == -1) throw mail::TransportError("ConnectError", true, "refused");
            HttpResponse resp;
            resp.status = route["status"].get<int>();
            if (!route["json"].is_null()) resp.body = route["json"].dump();
            const json headers = route.value("headers", json::object());
            for (auto it = headers.begin(); it != headers.end(); ++it) resp.headers[it.key()] = it.value();
            return resp;
        }
        ADD_FAILURE() << "no route for " << call.method << " " << call.url;
        HttpResponse resp;
        resp.status = 599;
        return resp;
    }
};

int64_t utcMicros(const std::string& iso) {
    return calendar::parseInstant(iso).utc * 1000000;
}

class CalendarToolsTest : public ::testing::Test {
protected:
    fs::path home;
    std::string savedHome, savedZone;
    std::map<std::string, std::string> keychain;
    std::set<std::string> revoked;
    std::shared_ptr<Router> router;
    std::string surface = "cli";
    std::unique_ptr<CalendarTools> tools;
    ToolRegistry registry;
    json prompts = json::array();
    ToolConfirmResult answer = ToolConfirmResult::ALLOW_ONCE;

    void SetUp() override {
        std::random_device rd;
        home = fs::temp_directory_path() / ("gaia-calendar-tools-" + std::to_string(rd()));
        fs::create_directories(home / ".gaia" / "connectors");
#ifdef _WIN32
        savedHome = getEnvVar("USERPROFILE");
        setEnv("USERPROFILE", home.string());
#else
        savedHome = getEnvVar("HOME");
        setEnv("HOME", home.string());
#endif
        savedZone = getEnvVar("TZ");
        setZone("EST5EDT");
        setEnv("GAIA_EVAL_MAILBOX", "");
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
        CalendarTools::setClockForTesting([] { return utcMicros("2030-01-10T12:00:00Z"); });
        router = std::make_shared<Router>(json::array());
    }

    void TearDown() override {
        CalendarTools::setClockForTesting({});
        connectors::setSecretReaderForTesting({});
        connectors::setTokenTransportForTesting({});
        connectors::resetForTesting();
        setEnv("GAIA_EVAL_MAILBOX", "");
        setZone(savedZone);
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
        for (auto it = grants.begin(); it != grants.end(); ++it) ledger[it.key()] = {{CalendarTools::kAgentId, it.value()}};
        writeFile("grants.json", ledger.dump());
    }

    void connectGoogle() {
        connect({{"google", {{"scopes", {kGoogleScope}}, {"account_email", "me@example.com"}}}},
                {{"google", {kGoogleScope}}});
    }

    void build(bool enable = true) {
        CalendarTools::Options options;
        options.surface = [this] { return surface; };
        std::shared_ptr<Router> r = router;
        options.transport = [r](const mail::HttpCall& call) { return (*r)(call); };
        tools = std::make_unique<CalendarTools>(std::move(options));
        registry.clear();
        tools->registerAll(registry);
        registry.setConfirmCallback([this](const std::string&, const json& args) {
            prompts.push_back(args);
            return answer;
        });
        if (enable) {
            for (const auto& name : CalendarTools::toolNames()) registry.setEnabled(name, true);
        }
    }

    json callTool(const std::string& name, const json& args = json::object()) { return registry.executeTool(name, args); }
};

const json& parity() {
    static const json data = [] {
        std::ifstream in(std::string(GAIA_TEST_FIXTURES_DIR) + "/calendar/calendar_tools_parity.json");
        EXPECT_TRUE(in.good()) << "missing fixtures/calendar/calendar_tools_parity.json";
        return json::parse(in);
    }();
    return data;
}

const json& parityCase(const std::string& name) {
    for (const auto& c : parity()["cases"]) {
        if (c["name"] == name) return c;
    }
    static const json none;
    ADD_FAILURE() << "no parity case " << name;
    return none;
}

const json kBudget{{"id", "g1"},
                   {"summary", "Budget review"},
                   {"start", {{"dateTime", "2030-01-10T09:00:00-05:00"}}},
                   {"end", {{"dateTime", "2030-01-10T10:00:00-05:00"}}},
                   {"organizer", {{"self", true}}},
                   {"attendees", {{{"email", "me@example.com"}, {"self", true}}, {{"email", "dana@example.com"}}}}};

const json kCancelArgs{{"event_id", "g1"},
                       {"expected_title", "Budget review"},
                       {"expected_start", "2030-01-10T09:00:00-05:00"},
                       {"notify_attendees", {"dana@example.com"}}};

}  // namespace

// ---- parity with the Python skill ----

TEST_F(CalendarToolsTest, EveryCaseMatchesThePythonSkill) {
    const json& cases = parity()["cases"];
    ASSERT_GE(cases.size(), 40u);
    size_t compared = 0;
    for (const auto& c : cases) {
        const std::string name = c["name"];
        SCOPED_TRACE(name);
        connectors::resetForTesting();
        surface = c["surface"].get<std::string>();
        const int64_t now = utcMicros(c["now"]);
        CalendarTools::setClockForTesting([now] { return now; });
        setEnv("GAIA_EVAL_MAILBOX", c["eval_mailbox"].get<bool>() ? "/nonexistent/eval.mbox" : "");
        revoked.clear();
        for (auto it = c["token_errors"].begin(); it != c["token_errors"].end(); ++it) {
            ASSERT_EQ(it.value(), "ConnectionRevokedError");
            revoked.insert(it.key());
        }
        connect(c["connections"], c["grants"]);
        router = std::make_shared<Router>(c["routes"]);
        build();
        const bool nobody = c["nobody_to_ask"].get<bool>();
        registry.setNobodyToAsk([nobody] { return nobody; });
        for (size_t i = 0; i < c["steps"].size(); ++i) {
            const json& step = c["steps"][i];
            SCOPED_TRACE("step " + std::to_string(i) + " " + step["tool"].get<std::string>());
            const size_t asked = prompts.size();
            const size_t sent = router->seen.size();
            const json got = callTool(step["tool"], step["args"]);
            const json& want = c["expected"][i];
            EXPECT_TRUE(got == want) << "got:  " << got.dump(1) << "\nwant: " << want.dump(1);

            const json& wantPrompt = c["prompts"][i];
            if (wantPrompt.is_null()) {
                EXPECT_EQ(prompts.size(), asked) << "asked a person when Python did not";
            } else {
                ASSERT_EQ(prompts.size(), asked + 1) << "Python asked a person here";
                json shown = prompts.back();
                EXPECT_EQ(shown.value(approval::kNeedsPersonArg, false), true) << "only a person may answer";
                shown.erase(approval::kNeedsPersonArg);
                EXPECT_TRUE(shown == wantPrompt) << "got:  " << shown.dump(1) << "\nwant: " << wantPrompt.dump(1);
                EXPECT_EQ(CalendarTools::approvalSummary(shown), c["summaries"][i].get<std::string>());
            }

            json requests = json::array();
            for (size_t k = sent; k < router->seen.size(); ++k) requests.push_back(router->seen[k]);
            EXPECT_TRUE(requests == c["requests"][i]) << "got:  " << requests.dump(1)
                                                  << "\nwant: " << c["requests"][i].dump(1);
            ++compared;
        }
    }
    EXPECT_GE(compared, 93u);
}

TEST_F(CalendarToolsTest, TimesParseAsThePythonSkillParsesThem) {
    const json& data = parity()["time"];
    const int64_t winter = utcMicros(data["now"]);
    CalendarTools::setClockForTesting([winter] { return winter; });
    auto outcome = [](const std::function<json()>& fn) {
        try {
            return json{{"ok", fn()}};
        } catch (const calendar::CalendarError& e) {
            return json{{"error", e.what()}};
        }
    };
    for (const auto& c : data["instants"]) {
        const std::string value = c["value"];
        json want = c;
        want.erase("value");
        EXPECT_EQ(outcome([&] { return json(calendar::isoformat(calendar::parseInstant(value, "start"))); }), want)
            << value;
    }
    for (const auto& c : data["dates"]) {
        const std::string value = c["value"];
        json want = c;
        want.erase("value");
        EXPECT_EQ(outcome([&] { return json(calendar::isoformat(calendar::parseDate(value, "end"))); }), want) << value;
    }
    for (const auto& c : data["slots"]) {
        json want = c;
        want.erase("start");
        want.erase("end");
        EXPECT_EQ(outcome([&] {
                      const auto slot = calendar::parseSlot(c["start"], c["end"]);
                      return json{slot.start, slot.end, slot.allDay};
                  }),
                  want)
            << c.dump();
    }
    for (const auto& c : data["daylight"]) {
        json want = c;
        want.erase("value");
        want.erase("now");
        const calendar::Moment now = calendar::parseInstant(c["now"]);
        EXPECT_EQ(outcome([&] {
                      calendar::checkDaylightOffset(c["value"], "start", now);
                      return json(nullptr);
                  }),
                  want)
            << c.dump();
    }
}

TEST_F(CalendarToolsTest, SchemasMatchThePythonSkill) {
    build();
    const json& schemas = parity()["schemas"];
    ASSERT_EQ(schemas.size(), CalendarTools::toolNames().size());
    for (const auto& name : CalendarTools::toolNames()) {
        SCOPED_TRACE(name);
        const ToolInfo* tool = registry.findTool(name);
        ASSERT_NE(tool, nullptr);
        const json& want = schemas.at(name);
        EXPECT_EQ(tool->description, want["description"].get<std::string>());
        EXPECT_EQ(tool->atomic, want["atomic"].get<bool>());
        EXPECT_TRUE(tool->onlineService) << "refused while the internet is off";
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

// ---- how the registry treats them ----

TEST_F(CalendarToolsTest, TheToolsStartOffAndOnlyChangesAskAPerson) {
    build(false);
    for (const auto& name : CalendarTools::toolNames()) {
        SCOPED_TRACE(name);
        const ToolInfo* tool = registry.findTool(name);
        ASSERT_NE(tool, nullptr);
        EXPECT_FALSE(tool->enabled) << "loading the calendar skill turns it on";
        EXPECT_EQ(tool->policy == ToolPolicy::CONFIRM, CalendarTools::isWriteTool(name));
        EXPECT_EQ(tool->personMustApprove, CalendarTools::isWriteTool(name));
        EXPECT_EQ(static_cast<bool>(tool->preflight), CalendarTools::isWriteTool(name));
    }
    const json off = callTool("calendar/list_calendar_events");
    EXPECT_EQ(off["error"], "Tool 'calendar/list_calendar_events' is disabled");
}

TEST_F(CalendarToolsTest, AnAlwaysAnswerApprovesOnlyThisChange) {
    connectGoogle();
    router = std::make_shared<Router>(json::array({
        {{"method", "GET"}, {"path", "/calendar/v3/calendars/primary/events/g1"}, {"status", 200}, {"json", kBudget}},
        {{"method", "DELETE"}, {"path", "/calendar/v3/calendars/primary/events/g1"}, {"status", 204}, {"json", nullptr}},
    }));
    build();
    auto store = std::make_shared<AllowedToolsStore>("");
    registry.setAllowedToolsStore(store);
    answer = ToolConfirmResult::ALWAYS_ALLOW;
    EXPECT_EQ(callTool("calendar/cancel_calendar_event", kCancelArgs)["success"], true);
    EXPECT_FALSE(store->isAlwaysAllowed("calendar/cancel_calendar_event"));
    answer = ToolConfirmResult::ALLOW_ONCE;
    callTool("calendar/cancel_calendar_event", kCancelArgs);
    EXPECT_EQ(prompts.size(), 2u) << "the second cancel asked again";
}

TEST_F(CalendarToolsTest, NothingChangesWhenNobodyCanApprove) {
    connectGoogle();
    router = std::make_shared<Router>(json::array({
        {{"method", "GET"}, {"path", "/calendar/v3/calendars/primary/events/g1"}, {"status", 200}, {"json", kBudget}},
    }));
    build();
    answer = ToolConfirmResult::UNATTENDED;
    const json got = callTool("calendar/cancel_calendar_event", kCancelArgs);
    EXPECT_EQ(got["executed"], false);
    EXPECT_EQ(got["error"],
              "'calendar/cancel_calendar_event' needs your approval and nobody can answer on this run. Start the "
              "request from the GAIA terminal UI or Agent UI to approve it there.");
    for (const auto& r : router->seen) EXPECT_NE(r["method"], "DELETE");
}

TEST_F(CalendarToolsTest, AChangeIsRefusedWhenTheAgentWouldNotAsk) {
    connectGoogle();
    build();
    ToolInfo copy = *registry.findTool("calendar/cancel_calendar_event");
    registry.removeTool(copy.name);
    copy.policy = ToolPolicy::ALLOW;
    registry.registerTool(copy);
    const json got = callTool("calendar/cancel_calendar_event", kCancelArgs);
    EXPECT_EQ(got["executed"], false);
    EXPECT_EQ(got["error"],
              "Not changed: this agent does not ask a person before a calendar change, and calendar changes always "
              "need one. Use the GAIA agent, which asks, and tell the user nothing on the calendar changed.");
    EXPECT_TRUE(router->seen.empty());
}

TEST_F(CalendarToolsTest, ACardSaysNobodyIsToldWhenNobodyIs) {
    EXPECT_EQ(CalendarTools::approvalSummary({{"expected_title", "Lunch"},
                                              {"expected_start", "2030-01-13T12:00:00-05:00"},
                                              {"notify_attendees", json::array()}}),
              "Event: Lunch\nWhen: 2030-01-13T12:00:00-05:00\nNotifies: nobody");
}
