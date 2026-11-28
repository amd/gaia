// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <ctime>
#include <filesystem>
#include <fstream>
#include <random>
#include <set>
#include <string>

#include "../agents/gaiacpp/gaia_agent.h"
#include <gaia/automation_store.h>
#include "support/mock_llm_server.h"

using namespace gaia;
namespace fs = std::filesystem;

namespace {

struct TempDir {
    fs::path path;
    TempDir() {
        std::mt19937_64 rng(std::random_device{}());
        path = fs::temp_directory_path() / ("gaia-auto-test-" + std::to_string(rng()));
        fs::create_directories(path);
    }
    ~TempDir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
};

double localEpoch(int year, int month, int day, int hour, int minute) {
    std::tm tm{};
    tm.tm_year = year - 1900;
    tm.tm_mon = month - 1;
    tm.tm_mday = day;
    tm.tm_hour = hour;
    tm.tm_min = minute;
    tm.tm_sec = 0;
    tm.tm_isdst = -1;
    return static_cast<double>(std::mktime(&tm));
}

}  // namespace

TEST(AutomationTimingTest, ParseIntervalsValidAndFloor) {
    EXPECT_EQ(AutomationStore::parseInterval("30m"), 1800);
    EXPECT_EQ(AutomationStore::parseInterval("every 6h"), 6 * 3600);
    EXPECT_EQ(AutomationStore::parseInterval("1d"), 86400);
    EXPECT_EQ(AutomationStore::parseInterval("1h30m"), 5400);
    EXPECT_EQ(AutomationStore::parseInterval("2 weeks"), 14 * 86400);
    EXPECT_EQ(AutomationStore::parseInterval("5 minutes"), 300);

    EXPECT_THROW(AutomationStore::parseInterval("1m"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseInterval("4m"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseInterval("0m"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseInterval("nonsense"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseInterval(""), std::invalid_argument);
}

TEST(AutomationTimingTest, ParseWhenRelativeClockAndIso) {
    const double now = localEpoch(2026, 11, 6, 14, 0);

    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("10m", now), now + 600);
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("in 2 hours", now), now + 7200);
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("+1h30m", now), now + 5400);
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("3 days", now), now + 3 * 86400);

    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("17:30", now), localEpoch(2026, 11, 6, 17, 30));
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("5pm", now), localEpoch(2026, 11, 6, 17, 0));
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("9am", now), localEpoch(2026, 11, 7, 9, 0));
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("tomorrow 9am", now), localEpoch(2026, 11, 7, 9, 0));
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("at 12:15am", now), localEpoch(2026, 11, 7, 0, 15));
    EXPECT_DOUBLE_EQ(AutomationStore::parseWhen("2026-12-01T09:00", now), localEpoch(2026, 12, 1, 9, 0));

    EXPECT_THROW(AutomationStore::parseWhen("today 9am", now), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseWhen("yesterday", now), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseWhen("25:00", now), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseWhen("13pm", now), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseWhen("0m", now), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseWhen("2020-01-01T00:00", now), std::invalid_argument);
}

TEST(AutomationTimingTest, ParseQuietAndDays) {
    EXPECT_EQ(AutomationStore::parseQuiet("22:00-07:00"), "22:00-07:00");
    EXPECT_THROW(AutomationStore::parseQuiet("25:00-07:00"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseQuiet("overnight"), std::invalid_argument);

    EXPECT_EQ(AutomationStore::parseDays("weekdays"), "mon,tue,wed,thu,fri");
    EXPECT_EQ(AutomationStore::parseDays("weekends"), "sat,sun");
    EXPECT_EQ(AutomationStore::parseDays("mon,wed,fri"), "mon,wed,fri");
    EXPECT_EQ(AutomationStore::parseDays("fri-mon"), "mon,fri,sat,sun");
    EXPECT_THROW(AutomationStore::parseDays("someday"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::parseDays(""), std::invalid_argument);
}

TEST(AutomationStoreTest, ValidateCheckCommands) {
    EXPECT_NO_THROW(AutomationStore::validateCheckCommand("gaia automation ci-status --repo amd/gaia --branch main"));
    EXPECT_NO_THROW(AutomationStore::validateCheckCommand("gh pr view 12 --repo amd/gaia --json reviewDecision"));
    EXPECT_NO_THROW(AutomationStore::validateCheckCommand("gh api repos/amd/gaia/pulls"));
    EXPECT_NO_THROW(AutomationStore::validateCheckCommand("gh search prs --repo amd/gaia is:open"));

    EXPECT_THROW(AutomationStore::validateCheckCommand(""), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("rm -rf /"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("/usr/bin/gh pr view 12 --repo amd/gaia"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh pr view 12"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh pr list --repo amd/gaia --paginate"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh pr merge 12 --repo amd/gaia"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh repo diff --repo amd/gaia"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh workflow view 1 --repo amd/gaia"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh pr view 12 --repo amd/gaia --web"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh api graphql -R amd/gaia"), std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh auth status --show-token -R amd/gaia"),
                 std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh pr view 12 --repo amd/gaia; echo pwn"),
                 std::invalid_argument);
    EXPECT_THROW(AutomationStore::validateCheckCommand("gh pr view 12 --repo amd/gaia | cat"), std::invalid_argument);
}

TEST(AutomationStoreTest, TriggerTrialCheckRecordsBaselineAndFailsLoudly) {
    TempDir dir;
    const fs::path storePath = dir.path / "automations" / "automations.json";
    AutomationStore store(storePath.u8string());
    const double now = localEpoch(2026, 11, 6, 14, 0);

    // Failed trial check must fail loudly and must NOT write the trigger to disk.
    store.setCheckRunner([](const std::string&, const std::vector<std::string>&) -> AutomationStore::CheckResult {
        return {false, "gh: not logged in"};
    });
    AutomationStore::CreateRequest req;
    req.name = "Main CI";
    req.every = "15m";
    req.prompt = "Summarize failures";
    req.check = "gaia automation ci-status --repo amd/gaia --branch main";
    EXPECT_THROW(store.create(req, now), std::invalid_argument);
    EXPECT_TRUE(store.list().empty());

    // Passing trial check records checked=true and saves baseline output.
    std::vector<std::string> seenArgv;
    store.setCheckRunner([&](const std::string&, const std::vector<std::string>& argv) -> AutomationStore::CheckResult {
        seenArgv = argv;
        return {true, "amd/gaia@main: all workflows passing"};
    });
    const Automation created = store.create(req, now);
    EXPECT_EQ(created.kind, "trigger");
    EXPECT_TRUE(created.checked);
    EXPECT_EQ(created.lastOutput, "amd/gaia@main: all workflows passing");
    ASSERT_FALSE(seenArgv.empty());
    EXPECT_EQ(seenArgv[0], "gaia");

    // Editing check with a failing check leaves the existing trigger untouched.
    store.setCheckRunner([](const std::string&, const std::vector<std::string>&) -> AutomationStore::CheckResult {
        return {false, "bad repo"};
    });
    AutomationStore::EditRequest badEdit;
    badEdit.ref = created.id;
    badEdit.check = "gh pr view 1 --repo amd/gaia";
    EXPECT_THROW(store.edit(badEdit, now), std::invalid_argument);
    const auto unchanged = store.list();
    ASSERT_EQ(unchanged.size(), 1u);
    EXPECT_EQ(unchanged[0].check, req.check);
    EXPECT_TRUE(unchanged[0].checked);
    EXPECT_EQ(unchanged[0].lastOutput, "amd/gaia@main: all workflows passing");

    // Editing check with a passing check updates check + baseline + checked=true.
    store.setCheckRunner([](const std::string&, const std::vector<std::string>&) -> AutomationStore::CheckResult {
        return {true, "APPROVED"};
    });
    const Automation edited = store.edit(badEdit, now);
    EXPECT_EQ(edited.check, "gh pr view 1 --repo amd/gaia");
    EXPECT_TRUE(edited.checked);
    EXPECT_EQ(edited.lastOutput, "APPROVED");
    EXPECT_EQ(edited.failures, 0);
}

TEST(AutomationStoreTest, RemoveDropsAlreadyFiredOutboxEntries) {
    TempDir dir;
    const fs::path storePath = dir.path / "automations" / "automations.json";
    const fs::path outboxPath = dir.path / "automations" / "outbox.json";
    fs::create_directories(storePath.parent_path());
    {
        std::ofstream out(outboxPath);
        out << json{{"deliveries",
                     json::array({
                         {{"id", "d1"}, {"automation_id", "a1"}, {"name", "Call Sam"}},
                         {{"id", "d2"}, {"automation_id", "a2"}, {"name", "Water plants"}},
                     })}}
                   .dump(2);
    }

    AutomationStore store(storePath.u8string());
    const Automation byName = store.remove("call sam");
    EXPECT_EQ(byName.kind, "waiting");
    EXPECT_EQ(byName.name, "Call Sam");

    const Automation byWaitingId = store.remove("waiting:d2");
    EXPECT_EQ(byWaitingId.kind, "waiting");
    EXPECT_EQ(byWaitingId.name, "Water plants");

    std::ifstream in(outboxPath);
    const json obJson = json::parse(in);
    EXPECT_TRUE(obJson["deliveries"].empty());
    EXPECT_THROW(store.remove("waiting:d2"), std::invalid_argument);
}

TEST(AutomationStoreTest, CrudLifecycleSchemaAndOutboxDrop) {
    TempDir dir;
    const fs::path storePath = dir.path / "automations" / "automations.json";
    const fs::path outboxPath = dir.path / "automations" / "outbox.json";
    AutomationStore store(storePath.u8string());
    const double now = localEpoch(2026, 11, 6, 14, 0);

    AutomationStore::CreateRequest req;
    req.name = "Call Sam";
    req.at = "5pm";
    req.say = "Call Sam about the venue";
    const Automation created = store.create(req, now);
    EXPECT_EQ(created.id.size(), 8u);
    EXPECT_EQ(created.name, "Call Sam");
    EXPECT_EQ(created.kind, "reminder");
    EXPECT_EQ(created.say, "Call Sam about the venue");
    EXPECT_EQ(created.everySeconds, 0);
    EXPECT_DOUBLE_EQ(created.nextAt, localEpoch(2026, 11, 6, 17, 0));
    EXPECT_FALSE(created.paused);

    // Verify exact Python AutomationStore JSON schema on disk.
    std::ifstream in(storePath);
    ASSERT_TRUE(in);
    const json raw = json::parse(in);
    ASSERT_TRUE(raw.is_object() && raw["automations"].is_array());
    ASSERT_EQ(raw["automations"].size(), 1u);
    const json& row = raw["automations"][0];
    for (const char* key : {"id", "name", "kind", "prompt", "say", "every_seconds", "next_at", "check", "quiet",
                            "days", "paused", "created_at", "last_run_at", "runs", "last_output", "checked",
                            "failures", "last_alert"}) {
        EXPECT_TRUE(row.contains(key)) << "missing key: " << key;
    }

    // Duplicate name (case-insensitive) fails loudly.
    AutomationStore::CreateRequest dup;
    dup.name = "call sam";
    dup.at = "10m";
    dup.say = "again";
    EXPECT_THROW(store.create(dup, now), std::invalid_argument);

    // Edit by name.
    AutomationStore::EditRequest edit;
    edit.ref = "call sam";
    edit.say = "Call Sam at 6pm";
    edit.at = "6pm";
    const Automation edited = store.edit(edit, now);
    EXPECT_EQ(edited.say, "Call Sam at 6pm");
    EXPECT_DOUBLE_EQ(edited.nextAt, localEpoch(2026, 11, 6, 18, 0));

    // Seed an outbox entry and confirm pause drops it.
    {
        std::ofstream out(outboxPath);
        out << json{{"deliveries", json::array({{{"id", "d1"}, {"automation_id", created.id}, {"name", "Call Sam"}}})}}
                   .dump(2);
    }
    const Automation paused = store.setPaused(created.id, true);
    EXPECT_TRUE(paused.paused);
    {
        std::ifstream ob(outboxPath);
        const json obJson = json::parse(ob);
        EXPECT_TRUE(obJson["deliveries"].empty());
    }

    const Automation resumed = store.setPaused("Call Sam", false);
    EXPECT_FALSE(resumed.paused);

    // Remove by id.
    const Automation removed = store.remove(created.id);
    EXPECT_EQ(removed.id, created.id);
    EXPECT_TRUE(store.list().empty());
}

TEST(AutomationStoreTest, InvalidInputsAndCorruptFileFailLoudly) {
    TempDir dir;
    const fs::path storePath = dir.path / "automations" / "automations.json";
    AutomationStore store(storePath.u8string());
    const double now = localEpoch(2026, 11, 6, 14, 0);

    AutomationStore::CreateRequest bad;
    EXPECT_THROW(store.create(bad, now), std::invalid_argument);

    bad.name = "No schedule";
    bad.say = "hi";
    EXPECT_THROW(store.create(bad, now), std::invalid_argument);

    bad.every = "1d";
    EXPECT_THROW(store.create(bad, now), std::invalid_argument);  // say not allowed on repeating

    bad.say.clear();
    EXPECT_THROW(store.create(bad, now), std::invalid_argument);  // empty prompt on repeating

    // Corrupt file on disk must fail loudly and NOT be silently wiped.
    fs::create_directories(storePath.parent_path());
    {
        std::ofstream out(storePath);
        out << "{not valid json";
    }
    EXPECT_THROW(store.list(), std::runtime_error);
}

TEST(AutomationStoreTest, GaiaAgentRegistersAndRunsAutomationTools) {
    TempDir dir;
    bench::MockLlmServer mock;
    AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.modelId = "Gemma-4-E4B-it-GGUF";
    cfg.silentMode = true;
    cfg.streaming = false;

    GaiaAgent::Options opts;
    opts.skills = false;
    opts.webTools = false;
    opts.workspaceRoots = {dir.path.u8string()};
    opts.memoryPath = ":memory:";
    opts.automationsPath = (dir.path / "automations" / "automations.json").u8string();

    GaiaAgent agent(cfg, opts);
    ASSERT_TRUE(agent.tools().hasTool("list_automations"));
    ASSERT_TRUE(agent.tools().hasTool("create_automation"));
    ASSERT_TRUE(agent.tools().hasTool("edit_automation"));
    ASSERT_TRUE(agent.tools().hasTool("pause_automation"));
    ASSERT_TRUE(agent.tools().hasTool("resume_automation"));
    ASSERT_TRUE(agent.tools().hasTool("remove_automation"));

    EXPECT_EQ(agent.tools().findTool("list_automations")->policy, ToolPolicy::ALLOW);
    for (const char* mut :
         {"create_automation", "edit_automation", "pause_automation", "resume_automation", "remove_automation"}) {
        const ToolInfo* info = agent.tools().findTool(mut);
        ASSERT_NE(info, nullptr);
        EXPECT_EQ(info->policy, ToolPolicy::CONFIRM) << mut;
        EXPECT_FALSE(info->grantScope) << mut << " must not offer always-allow";
        EXPECT_FALSE(info->allowAlways) << mut << " must disallow standing grants";
    }

    // Without approval callback, create_automation is denied fail-closed.
    json denied = agent.toolRegistry().executeTool(
        "create_automation", json{{"name", "Call Sam"}, {"at", "10m"}, {"say", "Call Sam"}});
    EXPECT_NE(denied.value("status", ""), "success");

    // Even if AllowedToolsStore contains create_automation or a callback returns ALWAYS_ALLOW,
    // the registry must never skip confirmation or persist a standing grant.
    auto store = std::make_shared<AllowedToolsStore>("");
    store->addAlwaysAllowed("create_automation");
    agent.toolRegistry().setAllowedToolsStore(store);
    int confirmCalls = 0;
    agent.toolRegistry().setConfirmCallback([&](const std::string&, const json&) {
        ++confirmCalls;
        return ToolConfirmResult::ALWAYS_ALLOW;
    });
    json created = agent.toolRegistry().executeTool(
        "create_automation", json{{"name", "Call Sam"}, {"at", "10m"}, {"say", "Call Sam"}});
    ASSERT_EQ(created.value("status", ""), "success") << created.dump();
    EXPECT_EQ(confirmCalls, 1);

    // Second call must ask again even after ALWAYS_ALLOW was returned above.
    json second = agent.toolRegistry().executeTool(
        "create_automation", json{{"name", "Call Pat"}, {"at", "20m"}, {"say", "Call Pat"}});
    ASSERT_EQ(second.value("status", ""), "success") << second.dump();
    EXPECT_EQ(confirmCalls, 2);

    json listed = agent.toolRegistry().executeTool("list_automations", json::object());
    ASSERT_EQ(listed.value("status", ""), "success");
    EXPECT_EQ(listed.value("count", 0), 2);
}
