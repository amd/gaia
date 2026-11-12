// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <random>

#include "../agents/gaia/gaia_agent.h"
#include "support/mock_llm_server.h"

using namespace gaia;
namespace fs = std::filesystem;

namespace {

std::string answer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})}}
        .dump();
}

struct Workdir {
    fs::path path;
    Workdir() {
        std::mt19937_64 rng(std::random_device{}());
        path = fs::current_path() / ("gaia-agent-test-" + std::to_string(rng()));
        fs::create_directories(path / "out");
    }
    ~Workdir() {
        std::error_code ec;
        fs::remove_all(path, ec);
    }
};

GaiaAgent::Options optionsFor(const Workdir& w) {
    GaiaAgent::Options o;
    o.skills = false;
    o.webTools = false;
    o.workspaceRoots = {w.path.u8string()};
    o.memoryPath = ":memory:";
    return o;
}

AgentConfig cfgFor(const bench::MockLlmServer& mock) {
    AgentConfig c;
    c.baseUrl = mock.baseUrl();
    c.modelId = "Gemma-4-E4B-it-GGUF";
    c.silentMode = true;
    c.streaming = false;
    c.maxSteps = 6;
    return c;
}

}  // namespace

TEST(GaiaAgentTest, ClaimedFilesAreFound) {
    auto f = GaiaAgent::claimedFiles(
        "Done. I saved the table to `totals.md` and wrote out/summary.csv. "
        "Also created C:\\Users\\Me\\deck.pptx. The input data.csv was not changed.");
    ASSERT_EQ(f.size(), 3u);
    EXPECT_EQ(f[0], "totals.md");
    EXPECT_EQ(f[1], "out/summary.csv");
    EXPECT_EQ(f[2], "C:\\Users\\Me\\deck.pptx");
    EXPECT_TRUE(GaiaAgent::claimedFiles("The report covers 2025.").empty());
}

TEST(GaiaAgentTest, MissingClaimedFileSendsTheAnswerBackOnce) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("All done: I saved the summary to report.md."));
    mock.pushResponse(answer("Correction: I could not write report.md; here is the summary inline."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("summarize");
    EXPECT_EQ(r["result"], "Correction: I could not write report.md; here is the summary inline.");
    const auto bodies = mock.receivedBodies();
    ASSERT_EQ(bodies.size(), 2u);
    EXPECT_NE(bodies[1].find("no such file exists"), std::string::npos);
}

TEST(GaiaAgentTest, ExistingFileInASubfolderIsAccepted) {
    Workdir w;
    std::ofstream(w.path / "out" / "report.md") << "# Report\n";
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Saved report.md in the out folder."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("summarize");
    EXPECT_EQ(r["result"], "Saved report.md in the out folder.");
    EXPECT_EQ(mock.receivedBodies().size(), 1u);
}

TEST(GaiaAgentTest, ToolsAreConfinedByDefault) {
    Workdir w;
    bench::MockLlmServer mock;
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    agent.toolRegistry().setConfirmCallback(
        [](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
    json r = agent.toolRegistry().executeTool("file_read", json{{"path", "/etc/hostname"}});
    EXPECT_EQ(r["status"], "error");
    EXPECT_TRUE(agent.tools().hasTool("query_documents"));
}

TEST(GaiaAgentTest, AWriteOutsideTheFoldersIsRefusedWithoutAskingTheUser) {
    Workdir w;
    Workdir elsewhere;
    bench::MockLlmServer mock;
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    int asked = 0;
    agent.toolRegistry().setConfirmCallback([&asked](const std::string&, const json&) {
        ++asked;
        return ToolConfirmResult::ALLOW_ONCE;
    });
    const fs::path outside = elsewhere.path / "leak.txt";
    for (const json& call : {json{{"tool", "file_write"}, {"args", {{"path", outside.string()}, {"content", "x"}}}},
                             json{{"tool", "file_edit"},
                                  {"args", {{"path", outside.string()}, {"old_string", "a"}, {"new_string", "b"}}}}}) {
        json r = agent.toolRegistry().executeTool(call["tool"].get<std::string>(), call["args"]);
        EXPECT_EQ(r["status"], "error") << r.dump();
        EXPECT_EQ(r.value("executed", true), false) << r.dump();
    }
    EXPECT_EQ(asked, 0);
    EXPECT_FALSE(fs::exists(outside));

    // Inside the folders the user is still asked, and the write happens.
    json ok = agent.toolRegistry().executeTool(
        "file_write", json{{"path", (w.path / "out" / "kept.txt").string()}, {"content", "x"}});
    EXPECT_NE(ok.value("status", ""), "error") << ok.dump();
    EXPECT_EQ(asked, 1);
    EXPECT_TRUE(fs::exists(w.path / "out" / "kept.txt"));
}

TEST(GaiaAgentTest, AFolderWithANonAsciiNameStillMakesAValidRequest) {
    Workdir parent;
    const fs::path folder = parent.path / fs::u8path("caf\xC3\xA9 notes");
    fs::create_directories(folder);
    const fs::path before = fs::current_path();
    fs::current_path(folder);
    bench::MockLlmServer mock;
    mock.pushResponse(answer("ok"));
    json result;
    {
        GaiaAgent::Options o;
        o.skills = false;
        o.webTools = false;
        o.workspaceRoots = {folder.u8string()};
        o.memoryPath = ":memory:";
        GaiaAgent agent(cfgFor(mock), o);
        result = agent.processQuery("hello");
    }
    fs::current_path(before);

    EXPECT_EQ(result["result"], "ok") << result.dump(-1, ' ', false, json::error_handler_t::replace);
    ASSERT_EQ(mock.receivedBodies().size(), 1u);
    const std::string system = json::parse(mock.receivedBodies()[0])["messages"][0]["content"];
    const std::string name = "caf\xC3\xA9 notes";
    EXPECT_NE(system.find("Working directory: " + folder.u8string()), std::string::npos);
    EXPECT_NE(system.find(name), std::string::npos);
}

TEST(GaiaAgentTest, ThePromptListsTheWorkingFolderTwoLevelsDeep) {
    Workdir w;
    std::ofstream(w.path / "budget_2025.md") << "x";
    std::ofstream(w.path / ".secret") << "x";
    fs::create_directories(w.path / "exports" / "deep");
    std::ofstream(w.path / "exports" / "orders.csv") << "x";
    std::ofstream(w.path / "exports" / "deep" / "hidden-by-depth.txt") << "x";
    const fs::path before = fs::current_path();
    fs::current_path(w.path);
    bench::MockLlmServer mock;
    mock.pushResponse(answer("ok"));
    {
        GaiaAgent agent(cfgFor(mock), optionsFor(w));
        agent.processQuery("hello");
    }
    fs::current_path(before);

    ASSERT_EQ(mock.receivedBodies().size(), 1u);
    const std::string system = json::parse(mock.receivedBodies()[0])["messages"][0]["content"];
    EXPECT_NE(system.find("In it when this turn began: budget_2025.md, exports/, exports/deep/, exports/orders.csv, out/"),
              std::string::npos)
        << system.substr(system.find("## Environment"));
    EXPECT_EQ(system.find(".secret"), std::string::npos);
    // Which command starts Python is stated, so the model does not go looking.
    EXPECT_NE(system.find("- Python: "), std::string::npos);
    EXPECT_EQ(system.find("python3 -c"), std::string::npos);
    EXPECT_EQ(system.find("hidden-by-depth"), std::string::npos);
}

TEST(GaiaAgentTest, ALargeFolderIsListedUpToALimit) {
    Workdir w;
    for (int i = 0; i < 80; ++i) std::ofstream(w.path / ("file" + std::to_string(100 + i) + ".txt")) << "x";
    const fs::path before = fs::current_path();
    fs::current_path(w.path);
    bench::MockLlmServer mock;
    mock.pushResponse(answer("ok"));
    {
        GaiaAgent agent(cfgFor(mock), optionsFor(w));
        agent.processQuery("hello");
    }
    fs::current_path(before);

    const std::string system = json::parse(mock.receivedBodies().at(0))["messages"][0]["content"];
    EXPECT_NE(system.find("file159.txt, and 21 more"), std::string::npos) << system.substr(system.find("## Environment"));
    EXPECT_EQ(system.find("file160.txt"), std::string::npos);
}

TEST(GaiaAgentTest, AnalyzeImageSendsTheImageToTheModel) {
    Workdir w;
    // Smallest valid PNG: 1x1 transparent pixel.
    const unsigned char png[] = {
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x06, 0x00, 0x00, 0x00, 0x1F, 0x15, 0xC4,
        0x89, 0x00, 0x00, 0x00, 0x0A, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9C, 0x63, 0x00, 0x01, 0x00, 0x00,
        0x05, 0x00, 0x01, 0x0D, 0x0A, 0x2D, 0xB4, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE,
        0x42, 0x60, 0x82};
    std::ofstream(w.path / "dot.png", std::ios::binary).write(reinterpret_cast<const char*>(png), sizeof png);
    bench::MockLlmServer mock;
    mock.pushResponse(answer("A single transparent pixel."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.toolRegistry().executeTool(
        "analyze_image", json{{"path", (w.path / "dot.png").string()}, {"question", "What is this?"}});
    ASSERT_EQ(r["status"], "success") << r.dump();
    EXPECT_EQ(r["answer"], "A single transparent pixel.");
    const json body = json::parse(mock.receivedBodies().back());
    EXPECT_FALSE(body.contains("tools"));
    const auto& parts = body["messages"][0]["content"];
    bool hasImage = false;
    for (const auto& p : parts) {
        hasImage |= p["type"] == "image_url" &&
                    p["image_url"]["url"].get<std::string>().rfind("data:image/png;base64,", 0) == 0;
    }
    EXPECT_TRUE(hasImage);

    json outside = agent.toolRegistry().executeTool(
        "analyze_image", json{{"path", "/etc/hostname"}, {"question", "?"}});
    EXPECT_EQ(outside["status"], "error");
}

TEST(GaiaAgentTest, ClaimingTestsRanWithoutRunningThemIsSentBack) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Fixed the bug, and all 12 tests pass."));
    mock.pushResponse(answer("Fixed the bug; I did not run the tests."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("fix it");
    EXPECT_EQ(r["result"], "Fixed the bug; I did not run the tests.");
    EXPECT_NE(mock.receivedBodies()[1].find("no command ran"), std::string::npos);
}

TEST(GaiaAgentTest, SayingAFileIsMissingWithoutLookingIsSentBack) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("budget.xlsx doesn't exist in your folder."));
    mock.pushResponse(answer("I checked: budget.xlsx is not in the folder."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("total budget.xlsx");
    EXPECT_EQ(r["result"], "I checked: budget.xlsx is not in the folder.");
    EXPECT_NE(mock.receivedBodies()[1].find("you didn't look"), std::string::npos);
    EXPECT_EQ(mock.receivedBodies().size(), 2u) << "sent back once, then accepted";
}

TEST(GaiaAgentTest, PlainAnswersAreNotSentBack) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("Paris is the capital of France."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    agent.processQuery("capital of France?");
    EXPECT_EQ(mock.receivedBodies().size(), 1u);
}

TEST(GaiaAgentTest, ADenialIsNotAClaimThatAFileWasSaved) {
    for (const char* denial : {"Nothing was written to `notes.md`.",
                               "Nothing saved to out.md.",
                               "Not saved to out.md.",
                               "The report was never written to out.md.",
                               "No file was written this turn, so report.md is unchanged.",
                               "No files were saved to out/report.md.",
                               "None of the files were written, not even totals.csv.",
                               "I have created no files; notes.md is as it was.",
                               "I saved nothing to notes.md.",
                               "To be clear: I wrote no file this turn, and `pwned.txt` is not there."}) {
        EXPECT_TRUE(GaiaAgent::claimedFiles(denial).empty()) << denial;
    }
}

TEST(GaiaAgentTest, AWriteThatWouldHaveHappenedNamesNoFile) {
    for (const char* counterfactual :
         {"Had it been allowed, I would have written the totals to totals.csv.",
          "Here's what I would have written, read from `pivot_q_region.xlsx`:",
          "This is what I'd have saved, taken from `pivot_q_region.xlsx`.",
          "Here\xE2\x80\x99s what I\xE2\x80\x99""d have saved, taken from `pivot_q_region.xlsx`.",
          "I could not have written notes.txt without approval."}) {
        EXPECT_TRUE(GaiaAgent::claimedFiles(counterfactual).empty()) << counterfactual;
    }
}

TEST(GaiaAgentTest, ASaveBesideADenialOrACounterfactualIsStillAClaim) {
    using Files = std::vector<std::string>;
    EXPECT_EQ(GaiaAgent::claimedFiles("I wrote no files except `summary.md`."), Files{"summary.md"});
    EXPECT_EQ(GaiaAgent::claimedFiles("Nothing was saved besides notes.md."), Files{"notes.md"});
    EXPECT_EQ(GaiaAgent::claimedFiles("I would have written it to a.txt, but I saved it to `b.txt`."),
              Files{"b.txt"});
    EXPECT_EQ(GaiaAgent::claimedFiles("I would have written a.txt and then saved b.txt instead."),
              Files{"b.txt"});
    EXPECT_EQ(GaiaAgent::claimedFiles("I wrote no draft file, but saved the notes to x.md."), Files{"x.md"});
    EXPECT_EQ(GaiaAgent::claimedFiles("Nothing was written to a.md. I saved the rest to `b.md`."), Files{"b.md"});
}

TEST(GaiaAgentTest, ADeclinedWriteReportedHonestlyIsNotSentBack) {
    Workdir w;
    for (const char* honest : {"The write was declined. Nothing was written to `notes.md`.",
                               "Had it been allowed, I would have written the totals to totals.csv."}) {
        bench::MockLlmServer mock;
        mock.pushResponse(answer(honest));
        GaiaAgent agent(cfgFor(mock), optionsFor(w));
        json r = agent.processQuery("write the totals to a file");
        EXPECT_EQ(r["result"], honest);
        EXPECT_EQ(mock.receivedBodies().size(), 1u) << "told to 'Create it now' after: " << honest;
    }
}

TEST(GaiaAgentTest, RequestsNotToActAreRecognised) {
    for (const char* request :
         {"I want net revenue by region for a sales CSV I will attach later. Do NOT run anything in this "
          "turn. Answer only with a five-step numbered plan in the first person, each step of the form 'I "
          "will use the `run_python` tool to ...', and close with the sentence: This should give you the "
          "exact breakdown you asked for.",
          "Don't use any tools, just tell me how you'd do it.",
          "Without using any tools, explain it.",
          "do not call any tools to answer this",
          "Don\xE2\x80\x99t run any tools yet.",
          "Don't open any files.",
          "Don't do anything yet.",
          "Just give me the plan for migrating the DB.",
          "Answer only with a short plan.",
          "Plan only, please.",
          "Use no tools for this one.",
          "Don't touch anything until I say so.",
          "Don't do anything before I confirm.",
          "Don't use any tools, but explain how you would do it.",
          "Explain the bug without opening any files.",
          "Give me only a plan, then wait for my go-ahead.",
          "Just give me a plan. I'll decide later whether to run it.",
          "Answer only with a short plan and close with a one-line summary.",
          "I want only a plan.",
          "Give me the plan only.",
          "Show the sales trend, but don't use any tools yet."}) {
        EXPECT_TRUE(GaiaAgent::asksForNoAction(request)) << request;
    }
}

TEST(GaiaAgentTest, RequestsThatLimitTheWorkAreNotRequestsNotToAct) {
    for (const char* request :
         {"What is the net revenue excluding returned orders, broken down by region?",
          "In ~/gaia-eval/resilience/ create totals.py and test_totals.py. Don't run anything yet.",
          "For orders.csv I want net revenue by region. Do NOT run anything in this turn.",
          "Without running anything, how would you clean this CSV?",
          "Fix foo.py and don't do anything else.",
          "Clean up the folder but don't touch anything else in it.",
          "Don't run anything destructive.",
          "Don't use pandas.",
          "Write a script but don't run it.",
          "Does config.yaml set debug? Answer only yes or no.",
          "Run the tests and don't open any other files.",
          "Don't read anything into it, just summarize report.md.",
          "Last time you answered without reading any files and got it wrong. What is the total gross in "
          "C:/x/orders.csv?",
          "Don't touch anything until you've read C:/x/orders.csv, then tell me the total.",
          "Don't do anything before you check the README.",
          "What is in C:/x/notes.txt? Don't open any files but that one.",
          "Don't open any files except config.yaml.",
          "Don't touch anything other than todo.txt.",
          "Give me only a plan for the migration, then do step 1.",
          "Just give me a plan and then carry it out.",
          "Plan only at first, and then run step 1 once it reads well.",
          "Answer only with a short plan. After that, execute it.",
          "My migration plan only covers Postgres; extend it to MySQL.",
          "Write not only a plan but the code too.",
          "Update the plan only for step 3 and save it to plan.md",
          "Don't touch anything \xE2\x80\x94 just read config.py and tell me what it sets",
          "Don't touch anything, just read config.py and tell me what it sets",
          "Don't touch anything - open config.py and tell me what it sets",
          "Fix the bug in foo.py but don't touch anything.",
          ""}) {
        EXPECT_FALSE(GaiaAgent::asksForNoAction(request)) << request;
    }
}

TEST(GaiaAgentTest, ARequestToUseNoToolsSwitchesOffTheNudgeToLook) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("config.yaml was not found."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("Use no tools for this one. Does config.yaml set debug?");
    EXPECT_EQ(r["result"], "config.yaml was not found.");
    EXPECT_EQ(mock.receivedBodies().size(), 1u) << "the user said not to look";
}

TEST(GaiaAgentTest, ForbiddingOnlyARunStillChecksTheAnswer) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("config.yaml was not found."));
    mock.pushResponse(answer("I looked: there is no config.yaml here."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("In this folder create totals.py and test_totals.py from config.yaml. "
                                "Don't run anything yet.");
    EXPECT_EQ(r["result"], "I looked: there is no config.yaml here.");
    ASSERT_EQ(mock.receivedBodies().size(), 2u);
    EXPECT_NE(mock.receivedBodies()[1].find("you didn't look"), std::string::npos);
}

TEST(GaiaAgentTest, AFalseSaveClaimIsStillSentBackWhenToolsWereForbidden) {
    Workdir w;
    bench::MockLlmServer mock;
    mock.pushResponse(answer("I saved the plan to plan.md."));
    mock.pushResponse(answer("Here is the plan; nothing was saved."));
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json r = agent.processQuery("Use no tools. Give me a plan.");
    EXPECT_EQ(r["result"], "Here is the plan; nothing was saved.");
    EXPECT_EQ(mock.receivedBodies().size(), 2u);
}

namespace {

std::string shellCall(const std::string& command) {
    json args = json{{"command", command}}.dump();
    return json{{"choices",
                 json::array({{{"message",
                                {{"role", "assistant"},
                                 {"content", nullptr},
                                 {"tool_calls",
                                  json::array({{{"id", "c1"},
                                                {"type", "function"},
                                                {"function", {{"name", "bash_execute"}, {"arguments", args}}}}})}}}}})}}
        .dump();
}

// An agent that may write anywhere, so a script can put its output beside an input outside the folder.
struct UnconfinedRun {
    Workdir work, elsewhere;
    fs::path kx;
    bench::MockLlmServer mock;
    UnconfinedRun() : kx(elsewhere.path / "kx") {
        fs::create_directories(kx);
        std::ofstream(kx / "reconciliation.md") << "# Recon\n";
    }
    json run(const std::string& request) {
        GaiaAgent::Options o = optionsFor(work);
        o.fullAccess = true;
        GaiaAgent agent(cfgFor(mock), o);
        agent.toolRegistry().setConfirmCallback(
            [](const std::string&, const json&) { return ToolConfirmResult::ALLOW_ONCE; });
        return agent.processQuery(request);
    }
};

}  // namespace

TEST(GaiaAgentTest, ABareOutputAScriptWroteBesideTheNamedInputIsFound) {
    UnconfinedRun t;
    const fs::path xlsx = t.kx / "recon_tables.xlsx";
    t.mock.pushResponse(shellCall("echo PK > \"" + xlsx.generic_string() + "\""));
    t.mock.pushResponse(answer("Both files are in kx: I created recon_tables.xlsx."));
    json r = t.run("Turn " + (t.kx / "reconciliation.md").string() +
                   " into a Word document. Also make recon_tables.xlsx with one sheet per category.");
    ASSERT_TRUE(fs::exists(xlsx)) << "the shell did not write the file: " << r.dump();
    EXPECT_EQ(r["result"], "Both files are in kx: I created recon_tables.xlsx.");
    EXPECT_EQ(t.mock.receivedBodies().size(), 2u) << "a file on disk was reported missing";
}

TEST(GaiaAgentTest, ABareOutputNoRunWroteIsStillMissing) {
    UnconfinedRun t;
    const fs::path old = t.kx / "recon_tables.xlsx";
    std::ofstream(old) << "PK";
    fs::last_write_time(old, fs::last_write_time(old) - std::chrono::hours(1));
    t.mock.pushResponse(shellCall("echo done"));
    t.mock.pushResponse(answer("Done: I created recon_tables.xlsx."));
    t.mock.pushResponse(answer("I did not create recon_tables.xlsx."));
    json r = t.run("Read " + (t.kx / "reconciliation.md").string() + " and make recon_tables.xlsx from it.");
    EXPECT_EQ(r["result"], "I did not create recon_tables.xlsx.");
    ASSERT_EQ(t.mock.receivedBodies().size(), 3u);
    EXPECT_NE(t.mock.receivedBodies()[2].find("no such file exists"), std::string::npos);
}

TEST(GaiaAgentTest, ABareOutputIsNotLookedForInAFolderNobodyNamed) {
    UnconfinedRun t;
    const fs::path stray = t.kx / "recon_tables.xlsx";
    t.mock.pushResponse(shellCall("echo PK > \"" + stray.generic_string() + "\""));
    t.mock.pushResponse(answer("Done: I created recon_tables.xlsx."));
    t.mock.pushResponse(answer("It went to another folder."));
    json r = t.run("Make recon_tables.xlsx.");
    ASSERT_TRUE(fs::exists(stray)) << r.dump();
    EXPECT_EQ(t.mock.receivedBodies().size(), 3u);
}

TEST(GaiaAgentTest, CodeSearchHonorsGitignoreAndFindsCode) {
    Workdir w;
    fs::create_directories(w.path / "src");
    fs::create_directories(w.path / "node_modules" / "dep");
    std::ofstream(w.path / ".gitignore") << "generated/\n";
    fs::create_directories(w.path / "generated");
    std::ofstream(w.path / "src" / "net.py") << "def fetch_with_retries(url, max_retries=3):\n    pass\n";
    std::ofstream(w.path / "node_modules" / "dep" / "x.js") << "function fetch_with_retries() {}\n";
    std::ofstream(w.path / "generated" / "gen.py") << "fetch_with_retries = None\n";
    bench::MockLlmServer mock;
    GaiaAgent agent(cfgFor(mock), optionsFor(w));
    json idx = agent.toolRegistry().executeTool("index_codebase", json{{"path", w.path.string()}});
    ASSERT_EQ(idx["status"], "success") << idx.dump();
    EXPECT_EQ(idx["files"], 1) << "node_modules and .gitignore'd folders are skipped";
    json r = agent.toolRegistry().executeTool("search_code", json{{"query", "fetch_with_retries max_retries"}});
    ASSERT_EQ(r["status"], "success");
    ASSERT_FALSE(r["results"].empty());
    EXPECT_NE(r["results"][0]["file"].get<std::string>().find("net.py"), std::string::npos);
}
