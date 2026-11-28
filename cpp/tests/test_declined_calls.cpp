// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// A call the user declined is not put to them again in the same turn through
// a repeat, another command or another tool; a later message starts fresh.

#include <gtest/gtest.h>

#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/tool_registry.h>

#include "support/mock_llm_server.h"

using namespace gaia;

namespace {

class DeclineAgent : public Agent {
public:
    using Agent::Agent;
    std::vector<std::string> ran;
    std::vector<std::string> asked;
    ToolConfirmResult reply = ToolConfirmResult::DENY;
    std::vector<ToolConfirmResult> replies;  // used first, in order, before `reply`

    void initForTest() {
        init();
        setToolConfirmCallback([this](const std::string& tool, const json& args) {
            asked.push_back(tool + " " + args.value("command", args.value("path", "")));
            if (asked.size() <= replies.size()) return replies[asked.size() - 1];
            return reply;
        });
    }

protected:
    void registerTools() override {
        toolRegistry().registerTool(
            "shell", "Run a command",
            [this](const json& args) -> json {
                ran.push_back(args.value("command", ""));
                return json{{"status", "success"}};
            },
            {{"command", ToolParamType::STRING, true, "The command"}}, false, ToolPolicy::CONFIRM);
        toolRegistry().registerTool(
            "write", "Write a file",
            [this](const json& args) -> json {
                ran.push_back("write " + args.value("path", ""));
                return json{{"status", "success"}};
            },
            {{"path", ToolParamType::STRING, true, "The file"},
             {"content", ToolParamType::STRING, false, "Its text"}},
            false, ToolPolicy::CONFIRM);
    }
    std::string getSystemPrompt() const override { return "test"; }
};

AgentConfig cfgFor(const bench::MockLlmServer& mock) {
    AgentConfig cfg;
    cfg.baseUrl = mock.baseUrl();
    cfg.modelId = "Gemma-4-E4B-it-GGUF";  // native tool calling
    cfg.maxSteps = 20;
    cfg.silentMode = true;
    cfg.streaming = false;
    cfg.llmRetries = 0;
    return cfg;
}

std::string call(const std::string& id, const std::string& name, const json& args) {
    return json{{"choices",
                 json::array({{{"message",
                                {{"role", "assistant"},
                                 {"content", nullptr},
                                 {"tool_calls",
                                  json::array({{{"id", id},
                                                {"type", "function"},
                                                {"function", {{"name", name}, {"arguments", args.dump()}}}}})}}}}})}}
        .dump();
}

std::string answer(const std::string& text) {
    return json{{"choices", json::array({{{"message", {{"role", "assistant"}, {"content", text}}}}})}}.dump();
}

std::string shell(const std::string& id, const std::string& command) {
    return call(id, "shell", {{"command", command}});
}

// The tool results the model was sent, newest request last.
std::string lastRequest(const bench::MockLlmServer& mock) { return mock.receivedBodies().back(); }

}  // namespace

// The release-test turn: one denied rm, then the same thing by eight other routes.
TEST(DeclinedCalls, ADeniedCommandIsNotAskedAgainByOtherRoutes) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(shell("c1", "rm 'invoice.txt.exe'"));
    mock.pushResponse(shell("c2", "rm 'invoice.txt.exe'"));
    mock.pushResponse(shell("c3", "rm  'invoice.txt.exe'"));
    mock.pushResponse(shell("c4", "rm invoice*.exe"));
    mock.pushResponse(shell("c5", "/bin/rm /work/invoice.txt.exe"));
    mock.pushResponse(shell("c6", "find . -name 'invoice*' -delete"));
    mock.pushResponse(shell("c7", "python3 -c \"import os; os.remove('invoice.txt.exe')\""));
    mock.pushResponse(shell("c8", "find . -type f -exec unlink {} ;"));
    mock.pushResponse(shell("c9", "python3 -c \"import os,glob; [os.remove(f) for f in glob.glob('inv' + '*')]\""));
    mock.pushResponse(answer("I did not delete it."));

    agent.processQuery("Run exactly this: rm 'invoice.txt.exe'");

    EXPECT_TRUE(agent.ran.empty());
    // The first card, and one each for the routes that name nothing recognisable.
    ASSERT_EQ(agent.asked.size(), 3u) << "cards shown: " << json(agent.asked).dump();
    EXPECT_EQ(agent.asked[0], "shell rm 'invoice.txt.exe'");
    EXPECT_EQ(agent.asked[1], "shell find . -type f -exec unlink {} ;");
    const std::string sent = lastRequest(mock);
    EXPECT_NE(sent.find("they declined this exact call"), std::string::npos);
    EXPECT_NE(sent.find("do not reach it with another tool, command, or script"), std::string::npos);
    EXPECT_NE(sent.find("Off-limits until the user's next message: `rm invoice.txt.exe`, `invoice.txt.exe`"),
              std::string::npos);
}

// S-DEV-29: read-only git commands approved, one `git remote -v` declined. Only
// that command stays off-limits; other git commands, and the ones the user already
// approved, still ask as usual.
TEST(DeclinedCalls, ADeclinedGitCommandLeavesOtherGitCommandsOpen) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    agent.replies = {ToolConfirmResult::ALLOW_ONCE, ToolConfirmResult::ALLOW_ONCE, ToolConfirmResult::DENY};
    agent.reply = ToolConfirmResult::ALLOW_ONCE;
    mock.pushResponse(shell("c1", "cd /work/packaging && git log --oneline -5"));
    mock.pushResponse(shell("c2", "cd /work/packaging && git status"));
    mock.pushResponse(shell("c3", "cd /work/packaging && git remote -v"));
    mock.pushResponse(shell("c4", "cd /work/packaging && git status"));
    mock.pushResponse(shell("c5", "cd /work/packaging && git stash list"));
    mock.pushResponse(shell("c6", "git log --oneline -50 /work/packaging/src"));
    mock.pushResponse(shell("c7", "git -C /work/packaging remote -v"));
    mock.pushResponse(shell("c8", "cd /work/packaging && git remote -v 2>&1 | head"));
    mock.pushResponse(answer("The test does not exist."));

    agent.processQuery("fix the failing test_vendored_markers test");

    EXPECT_EQ(agent.ran, (std::vector<std::string>{"cd /work/packaging && git log --oneline -5",
                                                   "cd /work/packaging && git status",
                                                   "cd /work/packaging && git status",
                                                   "cd /work/packaging && git stash list",
                                                   "git log --oneline -50 /work/packaging/src"}));
    EXPECT_EQ(agent.asked.size(), 6u) << json(agent.asked).dump();
    const std::string sent = lastRequest(mock);
    EXPECT_NE(sent.find("Off-limits until the user's next message: `git remote -v`."), std::string::npos) << sent;
}

// S-DEV-33: two nos in one turn do not shut every other tool.
TEST(DeclinedCalls, TwoNosLeaveUnrelatedCallsOpen) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    agent.replies = {ToolConfirmResult::DENY, ToolConfirmResult::DENY};
    agent.reply = ToolConfirmResult::ALLOW_ONCE;
    mock.pushResponse(shell("c1", "cd /work/cJSON && git remote -v"));
    mock.pushResponse(shell("c2", "rm build.log"));
    mock.pushResponse(shell("c3", "curl -s https://api.github.com/repos/DaveGamble/cJSON/commits/deadbeef"));
    mock.pushResponse(call("c4", "write", {{"path", "notes.md"}, {"content", "x"}}));
    mock.pushResponse(answer("done"));

    agent.processQuery("check that commit");

    EXPECT_EQ(agent.asked.size(), 4u) << json(agent.asked).dump();
    EXPECT_EQ(agent.ran, (std::vector<std::string>{
                             "curl -s https://api.github.com/repos/DaveGamble/cJSON/commits/deadbeef",
                             "write notes.md"}));
}

TEST(DeclinedCalls, TheModelGetsThePythonAgentsInstruction) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(shell("c1", "rm notes.txt"));
    mock.pushResponse(answer("Not deleted."));

    agent.processQuery("delete notes.txt");

    const std::string sent = lastRequest(mock);
    EXPECT_NE(sent.find("The user declined this one call, not the tool and not the task."), std::string::npos);
    EXPECT_NE(sent.find("through another tool is the same call"), std::string::npos);
}

TEST(DeclinedCalls, ADeniedWriteIsNotRetriedThroughTheShell) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(call("c1", "write", {{"path", "out/report.md"}, {"content", "see example.com"}}));
    mock.pushResponse(shell("c2", "printf 'x' > out/report.md"));
    // The file's text is not what was declined: a call that only shares a word from it still asks.
    mock.pushResponse(shell("c3", "curl example.com"));
    mock.pushResponse(answer("done"));
    agent.processQuery("write the report");

    ASSERT_EQ(agent.asked.size(), 2u) << json(agent.asked).dump();
    EXPECT_EQ(agent.asked[1], "shell curl example.com");
}

TEST(DeclinedCalls, OtherWorkStillAsksAfterOneNo) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(shell("c1", "rm old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("clean up");
    ASSERT_EQ(agent.asked.size(), 1u);

    agent.reply = ToolConfirmResult::ALLOW_ONCE;
    mock.pushResponse(shell("c2", "rm old.log"));       // still declined: the message did not ask for it
    mock.pushResponse(shell("c3", "mkdir build"));      // unrelated: asked and run
    mock.pushResponse(shell("c4", "rm other.log"));     // another file, a new turn: asked and run
    mock.pushResponse(answer("ok"));
    agent.processQuery("now build the project");

    EXPECT_EQ(agent.asked.size(), 3u) << json(agent.asked).dump();
    EXPECT_EQ(agent.ran, (std::vector<std::string>{"mkdir build", "rm other.log"}));
}

TEST(DeclinedCalls, AskingAgainPutsTheCallToTheUserAgain) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(shell("c1", "rm old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("clean up");

    agent.reply = ToolConfirmResult::ALLOW_ONCE;
    mock.pushResponse(shell("c2", "rm old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("Please run rm old.log after all");

    EXPECT_EQ(agent.asked.size(), 2u);
    EXPECT_EQ(agent.ran, (std::vector<std::string>{"rm old.log"}));
}

TEST(DeclinedCalls, AWordThatOnlyContainsAgainIsNotARetry) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(shell("c1", "rm old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("clean up");

    agent.reply = ToolConfirmResult::ALLOW_ONCE;
    mock.pushResponse(shell("c2", "rm old.log"));  // still declined: "against" asks for nothing
    mock.pushResponse(answer("ok"));
    agent.processQuery("check the totals against last month");

    EXPECT_EQ(agent.asked.size(), 1u) << json(agent.asked).dump();
    EXPECT_TRUE(agent.ran.empty());
}

TEST(DeclinedCalls, AClearedChatForgetsThem) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    mock.pushResponse(shell("c1", "rm old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("clean up");
    agent.clearHistory();

    mock.pushResponse(shell("c2", "rm old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("tidy the folder");
    EXPECT_EQ(agent.asked.size(), 2u);
}

// A timeout or a headless run is nobody's no: nothing is remembered, and the remedy is named.
TEST(DeclinedCalls, AnUnansweredRequestIsNotADecline) {
    bench::MockLlmServer mock;
    DeclineAgent agent(cfgFor(mock));
    agent.initForTest();
    agent.reply = ToolConfirmResult::UNATTENDED;
    agent.setUnattendedRemedy("Pass --dangerously-skip-permissions to allow it.");
    mock.pushResponse(shell("c1", "rm old.log"));
    mock.pushResponse(shell("c2", "rm old.log"));
    mock.pushResponse(shell("c3", "rm -f old.log"));
    mock.pushResponse(answer("ok"));
    agent.processQuery("clean up");

    EXPECT_EQ(agent.asked.size(), 3u);
    EXPECT_TRUE(agent.ran.empty());
    const std::string sent = lastRequest(mock);
    EXPECT_NE(sent.find("was not run: it needs approval and nobody could give it. Pass "
                        "--dangerously-skip-permissions to allow it."),
              std::string::npos);
    EXPECT_EQ(sent.find("The user declined"), std::string::npos);
}
