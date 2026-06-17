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
