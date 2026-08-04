// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/process.h>

#include <string>
#include <stdexcept>

using namespace gaia;

// ---------------------------------------------------------------------------
// Helper: platform-appropriate commands
// ---------------------------------------------------------------------------

#ifdef _WIN32
static const char* ECHO_HELLO     = "cmd /c echo hello";
static const char* FAIL_CMD       = "cmd /c exit 1";
static const char* STDERR_CMD     = "cmd /c echo error_msg 1>&2";
static const char* LARGE_OUTPUT   = "cmd /c \"for /L %i in (1,1,5000) do @echo line_%i\"";
#else
static const char* ECHO_HELLO     = "echo hello";
static const char* FAIL_CMD       = "false";
static const char* STDERR_CMD     = "echo error_msg >&2";
static const char* LARGE_OUTPUT   = "seq 1 5000 | while read i; do echo \"line_$i\"; done";
#endif

// ---------------------------------------------------------------------------
// ProcessRunner::run
// ---------------------------------------------------------------------------

TEST(ProcessRunnerTest, EchoHello) {
    auto result = ProcessRunner::run(ECHO_HELLO, 10000);

    EXPECT_EQ(result.exitCode, 0);
    EXPECT_FALSE(result.timedOut);
    // stdout should contain "hello" (may have trailing newline / \r\n)
    EXPECT_NE(result.stdout_output.find("hello"), std::string::npos);
}

TEST(ProcessRunnerTest, FailingCommand) {
    auto result = ProcessRunner::run(FAIL_CMD, 10000);

    EXPECT_NE(result.exitCode, 0);
    EXPECT_FALSE(result.timedOut);
}

TEST(ProcessRunnerTest, StderrCapture) {
    auto result = ProcessRunner::run(STDERR_CMD, 10000);

    // stderr should contain "error_msg"
    EXPECT_NE(result.stderr_output.find("error_msg"), std::string::npos);
}

TEST(ProcessRunnerTest, OutputCapping) {
    // Run a command that produces many lines, cap at 256 bytes
    const size_t capBytes = 256;
    auto result = ProcessRunner::run(LARGE_OUTPUT, 30000, "", {}, capBytes);

    EXPECT_EQ(result.exitCode, 0);
    EXPECT_FALSE(result.timedOut);
    // stdout should be capped at or near the limit
    EXPECT_LE(result.stdout_output.size(), capBytes);
    // Should have captured at least something
    EXPECT_FALSE(result.stdout_output.empty());
}

TEST(ProcessRunnerTest, EmptyCommand) {
    auto result = ProcessRunner::run("", 10000);

    // Empty command should fail gracefully
    EXPECT_EQ(result.exitCode, -1);
    EXPECT_FALSE(result.stderr_output.empty());
}

// ---------------------------------------------------------------------------
// ProcessRunner::runOrThrow
// ---------------------------------------------------------------------------

TEST(ProcessRunnerTest, RunOrThrowSuccess) {
    std::string output = ProcessRunner::runOrThrow(ECHO_HELLO, 10000);

    EXPECT_NE(output.find("hello"), std::string::npos);
}

TEST(ProcessRunnerTest, RunOrThrowFailure) {
    EXPECT_THROW(
        ProcessRunner::runOrThrow(FAIL_CMD, 10000),
        std::runtime_error
    );
}

// ---------------------------------------------------------------------------
// Timeout behavior
// ---------------------------------------------------------------------------

TEST(ProcessRunnerTest, TimeoutKillsProcess) {
    // Run a command that sleeps forever, with a short timeout
#ifdef _WIN32
    const char* sleepCmd = "cmd /c ping -n 60 127.0.0.1 >nul";
#else
    const char* sleepCmd = "sleep 60";
#endif

    auto result = ProcessRunner::run(sleepCmd, 1000);  // 1 second timeout

    EXPECT_TRUE(result.timedOut);
}

// ---------------------------------------------------------------------------
// Working directory
// ---------------------------------------------------------------------------

TEST(ProcessRunnerTest, WorkingDirectory) {
    // Use temp directory as cwd
#ifdef _WIN32
    const char* pwdCmd = "cmd /c cd";
    const char* testDir = "C:\\";
#else
    const char* pwdCmd = "pwd";
    const char* testDir = "/tmp";
#endif

    auto result = ProcessRunner::run(pwdCmd, 10000, testDir);

    EXPECT_EQ(result.exitCode, 0);
    // Output should contain the directory we specified
    EXPECT_NE(result.stdout_output.find(testDir), std::string::npos);
}

// ---------------------------------------------------------------------------
// Environment variables
// ---------------------------------------------------------------------------

TEST(ProcessRunnerTest, EnvironmentVariables) {
    std::map<std::string, std::string> env = {
        {"GAIA_TEST_VAR", "test_value_12345"}
    };

#ifdef _WIN32
    const char* printEnvCmd = "cmd /c echo %GAIA_TEST_VAR%";
#else
    const char* printEnvCmd = "echo $GAIA_TEST_VAR";
#endif

    auto result = ProcessRunner::run(printEnvCmd, 10000, "", env);

    EXPECT_EQ(result.exitCode, 0);
    EXPECT_NE(result.stdout_output.find("test_value_12345"), std::string::npos);
}

// ---------------------------------------------------------------------------
// No-timeout mode (timeoutMs = 0)
// ---------------------------------------------------------------------------

TEST(ProcessRunnerTest, NoTimeoutMode) {
    auto result = ProcessRunner::run(ECHO_HELLO, 0);

    EXPECT_EQ(result.exitCode, 0);
    EXPECT_FALSE(result.timedOut);
    EXPECT_NE(result.stdout_output.find("hello"), std::string::npos);
}

// ---------------------------------------------------------------------------
// ShellSession — state that survives between commands
// ---------------------------------------------------------------------------

#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <map>
#include <thread>

#ifndef _WIN32
#include <csignal>
#include <sys/types.h>
#endif

namespace fs = std::filesystem;

namespace {

#ifdef _WIN32
const char* PRINT_CWD   = "cd";
const char* SET_VAR     = "set GAIA_SESSION_VAR=persisted_9876";
const char* PRINT_VAR   = "echo %GAIA_SESSION_VAR%";
#else
const char* PRINT_CWD   = "pwd";
const char* SET_VAR     = "export GAIA_SESSION_VAR=persisted_9876";
const char* PRINT_VAR   = "echo $GAIA_SESSION_VAR";
#endif

std::string changeDirTo(const fs::path& dir) {
#ifdef _WIN32
    return "cd /d \"" + dir.string() + "\"";
#else
    return "cd \"" + dir.string() + "\"";
#endif
}

class ShellSessionTest : public ::testing::Test {
protected:
    fs::path dirA_;
    fs::path dirB_;

    void SetUp() override {
        fs::path root = fs::temp_directory_path() / "gaia_shell_session_test";
        std::error_code ec;
        fs::remove_all(root, ec);
        dirA_ = root / "alpha_dir";
        dirB_ = root / "beta_dir";
        fs::create_directories(dirA_);
        fs::create_directories(dirB_);
    }

    void TearDown() override {
        std::error_code ec;
        fs::remove_all(fs::temp_directory_path() / "gaia_shell_session_test", ec);
    }
};

} // namespace

TEST_F(ShellSessionTest, CwdPersistsBetweenCalls) {
    ShellSession session;

    auto moved = session.run(changeDirTo(dirA_), 10000);
    EXPECT_EQ(moved.exitCode, 0);

    auto pwd = session.run(PRINT_CWD, 10000);
    EXPECT_EQ(pwd.exitCode, 0);
    EXPECT_NE(pwd.stdout_output.find("alpha_dir"), std::string::npos)
        << "stdout: " << pwd.stdout_output;
    EXPECT_NE(session.cwd().find("alpha_dir"), std::string::npos);
}

TEST_F(ShellSessionTest, ExportedVariablePersists) {
    ShellSession session;

    EXPECT_EQ(session.run(SET_VAR, 10000).exitCode, 0);

    auto echoed = session.run(PRINT_VAR, 10000);
    EXPECT_EQ(echoed.exitCode, 0);
    EXPECT_NE(echoed.stdout_output.find("persisted_9876"), std::string::npos)
        << "stdout: " << echoed.stdout_output;

    const auto env = session.environment();
    ASSERT_EQ(env.count("GAIA_SESSION_VAR"), 1u);
    EXPECT_EQ(env.at("GAIA_SESSION_VAR"), "persisted_9876");
}

TEST_F(ShellSessionTest, CwdAndVariablesCombineAcrossThreeCalls) {
    ShellSession session;

    session.run(changeDirTo(dirA_) + " && " + SET_VAR, 10000);
    auto result = session.run(std::string(PRINT_CWD) + " && " + PRINT_VAR, 10000);

    EXPECT_EQ(result.exitCode, 0);
    EXPECT_NE(result.stdout_output.find("alpha_dir"), std::string::npos);
    EXPECT_NE(result.stdout_output.find("persisted_9876"), std::string::npos);
}

TEST_F(ShellSessionTest, DoesNotMutateTheCallingProcess) {
    // The bug ProcessRunner::run(cwd, env) has: process-wide chdir/setenv.
    const fs::path before = fs::current_path();

    ShellSession session;
    session.run(changeDirTo(dirA_) + " && " + SET_VAR, 10000);

    EXPECT_EQ(fs::current_path(), before);
    EXPECT_EQ(std::getenv("GAIA_SESSION_VAR"), nullptr);
}

TEST_F(ShellSessionTest, SessionsAreIndependent) {
    ShellSession first;
    ShellSession second;

    first.run(changeDirTo(dirA_), 10000);
    second.run(changeDirTo(dirB_), 10000);

    EXPECT_NE(first.run(PRINT_CWD, 10000).stdout_output.find("alpha_dir"),
              std::string::npos);
    EXPECT_NE(second.run(PRINT_CWD, 10000).stdout_output.find("beta_dir"),
              std::string::npos);
}

TEST_F(ShellSessionTest, SetCwdAndSetEnvSeedTheSession) {
    ShellSession session;

    ASSERT_TRUE(session.setCwd(dirB_.string()));
    session.setEnv("GAIA_SEEDED_VAR", "seed_value_42");

    auto result = session.run(std::string(PRINT_CWD) + " && " +
#ifdef _WIN32
                                  "echo %GAIA_SEEDED_VAR%",
#else
                                  "echo $GAIA_SEEDED_VAR",
#endif
                              10000);

    EXPECT_NE(result.stdout_output.find("beta_dir"), std::string::npos);
    EXPECT_NE(result.stdout_output.find("seed_value_42"), std::string::npos);

    EXPECT_FALSE(session.setCwd((dirB_ / "does_not_exist").string()));
}

TEST_F(ShellSessionTest, ResetRestoresTheStartingState) {
    ShellSession session;
    const std::string start = session.cwd();

    session.run(changeDirTo(dirA_) + " && " + SET_VAR, 10000);
    ASSERT_NE(session.cwd(), start);

    session.reset();
    EXPECT_EQ(session.cwd(), start);
    EXPECT_TRUE(session.environment().empty());

    auto echoed = session.run(PRINT_VAR, 10000);
    EXPECT_EQ(echoed.stdout_output.find("persisted_9876"), std::string::npos);
}

TEST_F(ShellSessionTest, ExitCodeStderrAndOutputCapAreUnchanged) {
    ShellSession session;

    auto failed = session.run(FAIL_CMD, 10000);
    EXPECT_NE(failed.exitCode, 0);

    auto errored = session.run(STDERR_CMD, 10000);
    EXPECT_NE(errored.stderr_output.find("error_msg"), std::string::npos);

    const size_t capBytes = 256;
    auto capped = session.run(LARGE_OUTPUT, 30000, capBytes);
    EXPECT_LE(capped.stdout_output.size(), capBytes);
    EXPECT_FALSE(capped.stdout_output.empty());
}

TEST_F(ShellSessionTest, TimeoutStillKillsTheCommand) {
#ifdef _WIN32
    const char* sleepCmd = "ping -n 60 127.0.0.1 >nul";
#else
    const char* sleepCmd = "sleep 60";
#endif
    ShellSession session;
    auto result = session.run(sleepCmd, 1000);
    EXPECT_TRUE(result.timedOut);
}

TEST_F(ShellSessionTest, EmptyCommandFailsGracefully) {
    ShellSession session;
    auto result = session.run("", 10000);

    EXPECT_EQ(result.exitCode, -1);
    EXPECT_FALSE(result.stderr_output.empty());
}

TEST_F(ShellSessionTest, StateBookkeepingDoesNotLeakIntoOutput) {
    ShellSession session;
    auto result = session.run(ECHO_HELLO, 10000);

    EXPECT_NE(result.stdout_output.find("hello"), std::string::npos);
    // The cwd/env probe writes to a side file, never to the captured streams.
    EXPECT_EQ(result.stdout_output.find("GAIA-ENV"), std::string::npos);
    EXPECT_EQ(result.stdout_output.find("PATH="), std::string::npos);
}

TEST_F(ShellSessionTest, SharedSessionIsProcessWide) {
    ShellSession::shared().reset();
    ShellSession::shared().run(changeDirTo(dirA_), 10000);
    EXPECT_NE(ShellSession::shared().cwd().find("alpha_dir"), std::string::npos);
    ShellSession::shared().reset();
}

// --- Regressions: the session must not be corruptible by command output ----

TEST_F(ShellSessionTest, MultiLineExportedValueDoesNotFabricateVariables) {
#ifndef _WIN32
    // A value containing a line that looks like an assignment must stay part
    // of that value. Parsing it as a second variable would let a command's
    // data become session configuration — `PATH=/tmp/evil` on its own line
    // would hijack PATH for every later command.
    ShellSession session;
    session.run("export MULTI='line1\nINJECTED=pwned\nline3'", 10000);

    const auto env = session.environment();
    EXPECT_EQ(env.count("INJECTED"), 0u);
    ASSERT_EQ(env.count("MULTI"), 1u);
    EXPECT_EQ(env.at("MULTI"), "line1\nINJECTED=pwned\nline3");

    auto echoed = session.run("printf '%s' \"$MULTI\"", 10000);
    EXPECT_NE(echoed.stdout_output.find("line3"), std::string::npos);
#else
    GTEST_SKIP() << "Windows environment values cannot contain newlines";
#endif
}

TEST_F(ShellSessionTest, VariableWithAnUnusableNameDoesNotCorruptItsNeighbour) {
#ifndef _WIN32
    // Stands in for Windows' `ProgramFiles(x86)`, which sorts right next to
    // `ProgramFiles` and used to be glued onto it.
    ShellSession session;
    session.run("export GAIA_NEIGHBOUR=intact", 10000);
    session.run("env 'ZZ-BAD=hello' true", 10000);

    const auto env = session.environment();
    ASSERT_EQ(env.count("GAIA_NEIGHBOUR"), 1u);
    EXPECT_EQ(env.at("GAIA_NEIGHBOUR"), "intact");
    for (const auto& kv : env) {
        EXPECT_EQ(kv.second.find("ZZ-BAD"), std::string::npos)
            << kv.first << " = " << kv.second;
    }
#else
    GTEST_SKIP() << "POSIX-only stand-in for the Windows name case";
#endif
}

TEST_F(ShellSessionTest, TrailingBackslashCannotSpliceIntoBookkeeping) {
    // The command lives in its own file, so a dangling line continuation
    // cannot swallow the framework's own lines.
    ShellSession session;
    auto result = session.run(std::string(ECHO_HELLO) + " \\", 10000);

    EXPECT_NE(result.stdout_output.find("hello"), std::string::npos);
    EXPECT_EQ(result.stdout_output.find("gaia_rc"), std::string::npos)
        << "stdout: " << result.stdout_output;
    EXPECT_EQ(result.stdout_output.find("GAIA_RC"), std::string::npos);
    EXPECT_EQ(result.exitCode, 0);
}

TEST_F(ShellSessionTest, InteractiveCommandDoesNotHangOnStdin) {
#ifdef _WIN32
    const char* readsStdin = "more";
#else
    const char* readsStdin = "cat";
#endif
    // stdin is /dev/null, so a command waiting for input returns immediately
    // instead of burning the whole timeout.
    ShellSession session;
    auto result = session.run(readsStdin, 10000);
    EXPECT_FALSE(result.timedOut);
}

TEST_F(ShellSessionTest, TimeoutKillsTheWholeProcessTree) {
#ifndef _WIN32
    // A build or test command spawns children; killing only the shell leaves
    // them running, which is exactly the case a persistent shell is for.
    //
    // The grandchild reports its own pid and is then checked with kill(pid, 0).
    // A `pgrep -f <pattern>` probe cannot be used: the shell running the probe
    // has the pattern in its own argv and matches itself, which is both
    // platform-dependent and a false pass waiting to happen.
    ShellSession session;
    auto result = session.run("sleep 971 & echo PID=$!; wait", 1000);
    ASSERT_TRUE(result.timedOut);

    const size_t marker = result.stdout_output.find("PID=");
    ASSERT_NE(marker, std::string::npos)
        << "stdout: " << result.stdout_output;
    const pid_t child =
        static_cast<pid_t>(std::atoi(result.stdout_output.c_str() + marker + 4));
    ASSERT_GT(child, 0);

    // Poll briefly: the pid lingers as a zombie until its new parent reaps it.
    // A survivor would still be sleeping 971 seconds, so a short bound
    // separates the two cases cleanly.
    bool alive = true;
    for (int attempt = 0; attempt < 40 && alive; ++attempt) {
        if (kill(child, 0) != 0) {
            alive = false;
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    EXPECT_FALSE(alive) << "orphaned child " << child << " survived the timeout";
#else
    GTEST_SKIP() << "Windows needs a Job Object for tree kill (not implemented)";
#endif
}
