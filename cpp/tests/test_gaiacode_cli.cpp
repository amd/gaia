// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Runs the built gaiacode program end to end against the mock model server.

#include "support/unique_temp.h"
#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>

#include "gaia/process.h"
#include "gaia/types.h"
#include "support/mock_llm_server.h"
#include "../agents/gaiacpp/bash_tools.h"

using gaia::json;
namespace fs = std::filesystem;

namespace {

std::string reply(const json& message) {
    return json{{"choices", json::array({{{"message", message}}})},
                {"usage", {{"prompt_tokens", 100}, {"completion_tokens", 10}, {"total_tokens", 110}}}}
        .dump();
}

json callTool(const std::string& name, const json& args) {
    return {{"role", "assistant"},
            {"content", nullptr},
            {"tool_calls", json::array({{{"id", "c1"},
                                         {"type", "function"},
                                         {"function", {{"name", name}, {"arguments", args.dump()}}}}})}};
}

json say(const std::string& text) { return {{"role", "assistant"}, {"content", text}}; }

std::string bin() {
    std::string b = GAIACODE_BIN;
    if (b.find(' ') != std::string::npos) b = "\"" + b + "\"";
    return b;
}

class GaiacodeCli : public ::testing::Test {
protected:
    void SetUp() override {
        work_ = fs::temp_directory_path() / testsupport::perProcess("gaiacode-cli");
        fs::remove_all(work_);
        fs::create_directories(work_);
        std::ofstream(work_ / "stats.py") << "def mean(xs):\n    return sum(xs)\n";
        // A stand-in pytest the agent's run_checks can call, on either shell.
        std::ofstream(work_ / "pytest", std::ios::binary) << "#!/bin/sh\necho '4 passed in 0.02s'\n";
        fs::permissions(work_ / "pytest", fs::perms::owner_all, fs::perm_options::add);
        std::ofstream(work_ / "pytest.bat", std::ios::binary) << "@echo 4 passed in 0.02s\r\n";
        pytest_ = gaia::BashTools::detectShell().empty() ? "pytest.bat" : "./pytest";
    }
    void TearDown() override {
        std::error_code ec;
        fs::remove_all(work_, ec);
    }

    gaia::ProcessResult run(const bench::MockLlmServer& mock) {
        const std::string cmd = bin() + " -p \"fix it and run the tests\" -m mock-model --base-url " +
                                mock.baseUrl() +
                                " --native-tools always --output-format json --dangerously-skip-permissions";
        return gaia::ProcessRunner::run(cmd, 60000, work_.u8string());
    }

    std::string stats() const {
        std::ifstream in(work_ / "stats.py");
        return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
    }

    fs::path work_;
    std::string pytest_;
};

const json kFix = {{"path", "stats.py"}, {"old_string", "sum(xs)"}, {"new_string", "sum(xs) / len(xs)"}};

}  // namespace

// #8682: the model edits, never runs a test, and keeps saying the tests pass. The loop
// sends the answer back, pushes once more, then tells the user what it couldn't confirm.
TEST_F(GaiacodeCli, AClaimedTestRunThatNeverHappenedIsSentBackAndFlagged) {
    bench::MockLlmServer mock;
    mock.pushResponse(reply(callTool("file_edit", kFix)));
    for (int i = 0; i < 3; ++i) mock.pushResponse(reply(say("Fixed mean(). The tests now pass.")));

    const gaia::ProcessResult r = run(mock);
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    const std::string answer = out.value("result", "");
    EXPECT_NE(answer.find("couldn't confirm the tests pass"), std::string::npos) << answer;
    EXPECT_EQ(out.value("num_llm_calls", 0), 4) << "sent back once, pushed once, then final";
}

// A model that runs the tests after the send-back clears the claim.
TEST_F(GaiacodeCli, ASendBackThatLeadsToARealRunEndsClean) {
    bench::MockLlmServer mock;
    mock.pushResponse(reply(callTool("file_edit", kFix)));
    mock.pushResponse(reply(say("Fixed mean(). The tests now pass.")));
    mock.pushResponse(reply(callTool("run_checks", {{"command", pytest_}})));
    mock.pushResponse(reply(say("Fixed mean(). The tests pass (4 passed).")));

    const gaia::ProcessResult r = run(mock);
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out.value("result", ""), "Fixed mean(). The tests pass (4 passed).");
}

TEST_F(GaiacodeCli, ARealGreenRunLetsTheClaimStand) {
    bench::MockLlmServer mock;
    mock.pushResponse(reply(callTool("file_edit", kFix)));
    mock.pushResponse(reply(callTool("run_checks", {{"command", pytest_}})));
    mock.pushResponse(reply(say("Fixed mean(). The tests pass (4 passed).")));

    const gaia::ProcessResult r = run(mock);
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const json out = json::parse(r.stdout_output);
    EXPECT_EQ(out.value("result", ""), "Fixed mean(). The tests pass (4 passed).");
    EXPECT_EQ(out.value("num_llm_calls", 0), 3);
    EXPECT_NE(stats().find("sum(xs) / len(xs)"), std::string::npos) << "the edit landed";
}

TEST_F(GaiacodeCli, RefusesToGuessAModel) {
    const gaia::ProcessResult r = gaia::ProcessRunner::run(bin() + " -p hi", 30000, work_.u8string(),
                                                           {{"GAIA_MODEL_ID", ""}});
    EXPECT_EQ(r.exitCode, 2);
    EXPECT_NE(r.stderr_output.find("no model is proven"), std::string::npos) << r.stderr_output;
}

// The bundled skills load from beside the binary, and the GitHub tools are offered.
TEST_F(GaiacodeCli, OffersItsSkillsAndGitHubTools) {
    bench::MockLlmServer mock;
    mock.pushResponse(reply(say("Nothing to do.")));
    const gaia::ProcessResult r = run(mock);
    ASSERT_EQ(r.exitCode, 0) << r.stderr_output;
    const std::vector<std::string> bodies = mock.receivedBodies();
    ASSERT_FALSE(bodies.empty());
    const std::string request = bodies.front();
    for (const char* skill : {"fix-ci", "issue-to-pr", "pr-review"}) {
        EXPECT_NE(request.find(skill), std::string::npos) << skill << " is not in the request";
    }
    for (const char* tool : {"gh_issue_view", "gh_pr_view", "gh_pr_checks", "gh_run_log", "gh_issue_comment",
                             "gh_pr_create", "gh_pr_review", "gh_issue_create", "gh_run_rerun", "run_checks"}) {
        EXPECT_NE(request.find(tool), std::string::npos) << tool << " is not offered";
    }
}
