// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/process.h>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <string>
#include <stdexcept>
#include <thread>
#ifndef _WIN32
#include <csignal>
#endif

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

// A build or test command starts other programs; a timeout has to end those too.
TEST(ProcessRunnerTest, TimeoutKillsWhatTheCommandStarted) {
    namespace tfs = std::filesystem;
    const tfs::path dir = tfs::temp_directory_path() /
                          ("gaia-timeout-tree-" + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()));
    tfs::create_directories(dir);
    const auto started = std::chrono::steady_clock::now();
#ifdef _WIN32
    // The inner cmd and its ping both hold held.txt open for as long as they live.
    const auto result = ProcessRunner::run("cmd /c ping -n 60 127.0.0.1 >held.txt", 1000, dir.string());
#else
    const auto result = ProcessRunner::run("sleep 60 & echo $! > pid.txt; wait", 1000, dir.string());
#endif
    EXPECT_TRUE(result.timedOut);
    EXPECT_LT(std::chrono::steady_clock::now() - started, std::chrono::seconds(20))
        << "the call waited for what the command had started";

    bool gone = false;
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
#ifdef _WIN32
    while (!gone && std::chrono::steady_clock::now() < deadline) {
        std::error_code ec;
        tfs::remove(dir / "held.txt", ec);
        gone = !ec;  // a sharing violation while a process still has it open
        if (!gone) std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
#else
    long pid = 0;
    std::ifstream(dir / "pid.txt") >> pid;
    ASSERT_GT(pid, 0);
    while (!gone && std::chrono::steady_clock::now() < deadline) {
        gone = kill(static_cast<pid_t>(pid), 0) != 0;
        if (!gone) std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
#endif
    EXPECT_TRUE(gone) << "the process the command started outlived the timeout";
    std::error_code ec;
    tfs::remove_all(dir, ec);
}

// ---------------------------------------------------------------------------
// stdin
// ---------------------------------------------------------------------------

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <sys/ioctl.h>
#include <unistd.h>
#endif

// This process's stdin can be a host's control pipe (`gaiacpp --stdio`): a
// command that reads stdin must get end of input, never the host's lines.
TEST(ProcessRunnerTest, CommandDoesNotReadThisProcessStdin) {
    const std::string line = "for the parent\n";
#ifdef _WIN32
    const char* readsStdin = "findstr parent";
    SECURITY_ATTRIBUTES sa{sizeof(sa), nullptr, TRUE};
    HANDLE readEnd = nullptr, writeEnd = nullptr;
    ASSERT_TRUE(CreatePipe(&readEnd, &writeEnd, &sa, 0));
    SetHandleInformation(writeEnd, HANDLE_FLAG_INHERIT, 0);
    DWORD written = 0;
    ASSERT_TRUE(WriteFile(writeEnd, line.data(), static_cast<DWORD>(line.size()), &written, nullptr));
    const HANDLE saved = GetStdHandle(STD_INPUT_HANDLE);
    SetStdHandle(STD_INPUT_HANDLE, readEnd);
    auto unread = [&] {
        DWORD n = 0;
        PeekNamedPipe(readEnd, nullptr, 0, nullptr, &n, nullptr);
        return static_cast<size_t>(n);
    };
#else
    const char* readsStdin = "cat";
    int fds[2];
    ASSERT_EQ(pipe(fds), 0);
    ASSERT_EQ(write(fds[1], line.data(), line.size()), static_cast<ssize_t>(line.size()));
    const int saved = dup(STDIN_FILENO);
    dup2(fds[0], STDIN_FILENO);
    auto unread = [&] {
        int n = 0;
        ioctl(fds[0], FIONREAD, &n);
        return static_cast<size_t>(n);
    };
#endif

    // With the writer still open, a command that got the pipe would wait for more.
    const auto limited = ProcessRunner::run(readsStdin, 10000);
    const size_t afterLimited = unread();
#ifdef _WIN32
    CloseHandle(writeEnd);
#else
    close(fds[1]);
#endif
    const auto unlimited = ProcessRunner::run(readsStdin, 0);
    const size_t afterUnlimited = unread();

#ifdef _WIN32
    SetStdHandle(STD_INPUT_HANDLE, saved);
    CloseHandle(readEnd);
#else
    dup2(saved, STDIN_FILENO);
    close(saved);
    close(fds[0]);
#endif

    EXPECT_FALSE(limited.timedOut);
    EXPECT_EQ(limited.stdout_output.find("for the parent"), std::string::npos) << limited.stdout_output;
    EXPECT_EQ(afterLimited, line.size()) << "the line is still there for this process to read";
    EXPECT_EQ(unlimited.stdout_output.find("for the parent"), std::string::npos) << unlimited.stdout_output;
    EXPECT_EQ(afterUnlimited, line.size());
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
// The session runs commands as a batch script, not as a prompt command line,
// so a for-loop variable is written %%i here where ProcessRunner's
// LARGE_OUTPUT (handed straight to `cmd /c`) writes %i. See ShellSession's
// header: %VAR% expansion is identical, %%-loop variables and %1 are not.
const char* SESSION_LARGE_OUTPUT = "for /L %%i in (1,1,5000) do @echo line_%%i";
const char* PRINT_CWD   = "cd";
const char* SET_VAR     = "set GAIA_SESSION_VAR=persisted_9876";
const char* PRINT_VAR   = "echo %GAIA_SESSION_VAR%";
#else
const char* SESSION_LARGE_OUTPUT = LARGE_OUTPUT;
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
    auto capped = session.run(SESSION_LARGE_OUTPUT, 30000, capBytes);
    EXPECT_LE(capped.stdout_output.size(), capBytes);
    EXPECT_FALSE(capped.stdout_output.empty());
}

TEST_F(ShellSessionTest, VariableReferencesInACommandStillExpand) {
    // The session's own variables must be usable by the command that follows.
    // This is the expansion syntax that behaves identically on both platforms
    // and is by far the common case.
    ShellSession session;
    session.setEnv("GAIA_EXPAND_ME", "expanded_value_77");

#ifdef _WIN32
    auto result = session.run("echo %GAIA_EXPAND_ME%", 10000);
#else
    auto result = session.run("echo $GAIA_EXPAND_ME", 10000);
#endif

    EXPECT_EQ(result.exitCode, 0);
    EXPECT_NE(result.stdout_output.find("expanded_value_77"), std::string::npos)
        << "stdout: " << result.stdout_output;
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
    GTEST_SKIP() << "POSIX form of the check; ProcessRunnerTest.TimeoutKillsWhatTheCommandStarted covers Windows";
#endif
}

// ---------------------------------------------------------------------------
// Cancel, non-ASCII folders, the shell's own environment, temp files
// ---------------------------------------------------------------------------

#include <atomic>
#include <fstream>
#include <functional>
#include <nlohmann/json.hpp>

#include "../agents/bash/bash_tools.h"

namespace {

fs::path freshDir(const std::string& stem) {
    static std::atomic<int> n{0};
    const fs::path dir = fs::temp_directory_path() /
                         fs::u8path(stem + std::to_string(std::chrono::steady_clock::now().time_since_epoch().count()) +
                                    "-" + std::to_string(n++));
    fs::create_directories(dir);
    return dir;
}

// The shells a session can run on this machine: the default one, and on
// Windows the POSIX shell bash_execute prefers when one is installed.
std::vector<std::string> sessionShells() {
    std::vector<std::string> shells = {""};
#ifdef _WIN32
    const std::string bash = BashTools::detectShell();
    if (!bash.empty()) shells.push_back(bash);
#endif
    return shells;
}

bool posixSyntax(const std::string& shell) {
#ifdef _WIN32
    return !shell.empty();
#else
    (void)shell;
    return true;
#endif
}

// Files this process's sessions have in the temp folder right now.
size_t ownSessionTempFiles() {
#ifdef _WIN32
    const std::string prefix = "gaia_shell_" + std::to_string(GetCurrentProcessId()) + "_";
#else
    const std::string prefix = "gaia_shell_" + std::to_string(getpid()) + "_";
#endif
    size_t count = 0;
    for (const auto& e : fs::directory_iterator(fs::temp_directory_path())) {
        count += e.path().filename().u8string().rfind(prefix, 0) == 0 ? 1 : 0;
    }
    return count;
}

// True once nothing holds `held` open any more (Windows), or `pidFile`'s process is gone (POSIX).
bool treeIsGone(const fs::path& dir) {
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    while (std::chrono::steady_clock::now() < deadline) {
#ifdef _WIN32
        std::error_code ec;
        fs::remove(dir / "held.txt", ec);
        if (!ec) return true;
#else
        long pid = 0;
        std::ifstream(dir / "pid.txt") >> pid;
        if (pid > 0 && kill(static_cast<pid_t>(pid), 0) != 0) return true;
#endif
        std::this_thread::sleep_for(std::chrono::milliseconds(50));
    }
    return false;
}

#ifdef _WIN32
const char* kStartThenSleep = "echo x>started.txt & cmd /c ping -n 60 127.0.0.1 >held.txt";
#else
const char* kStartThenSleep = "echo x > started.txt; sleep 60 & echo $! > pid.txt; wait";
#endif

}  // namespace

TEST(ProcessRunnerTest, ACancelEndsTheCommandAndWhatItStarted) {
    const fs::path dir = freshDir("gaia-cancel-");
    const auto started = std::chrono::steady_clock::now();
    ProcessResult result;
    {
        // Cancelled as soon as the command has demonstrably started.
        CommandCancelScope scope([&] { return fs::exists(dir / "started.txt"); });
        result = ProcessRunner::run(kStartThenSleep, 60000, dir.u8string());
    }
    EXPECT_TRUE(result.cancelled);
    EXPECT_FALSE(result.timedOut);
    EXPECT_LT(std::chrono::steady_clock::now() - started, std::chrono::seconds(20));
    EXPECT_TRUE(treeIsGone(dir)) << "what the command started outlived the cancel";

    // Outside the scope nothing is cancelled.
    const auto after = ProcessRunner::run(ECHO_HELLO, 10000, dir.u8string());
    EXPECT_FALSE(after.cancelled);
    EXPECT_EQ(after.exitCode, 0);
    std::error_code ec;
    fs::remove_all(dir, ec);
}

TEST_F(ShellSessionTest, ACancelEndsTheSessionCommandAndLeavesNoTempFiles) {
    for (const std::string& shell : sessionShells()) {
        const fs::path dir = freshDir("gaia-session-cancel-");
        ShellSession session(dir.u8string(), shell);
        ASSERT_EQ(session.run(posixSyntax(shell) ? "true" : "rem", 30000).exitCode, 0) << shell;
        const size_t before = ownSessionTempFiles();
        const std::string command = posixSyntax(shell) ? "echo x > started.txt; sleep 60"
                                                        : "echo x>started.txt & ping -n 60 127.0.0.1 >nul";
        const auto started = std::chrono::steady_clock::now();
        ProcessResult result;
        {
            CommandCancelScope scope([&] { return fs::exists(dir / "started.txt"); });
            result = session.run(command, 60000);
        }
        EXPECT_TRUE(result.cancelled) << shell;
        EXPECT_LT(std::chrono::steady_clock::now() - started, std::chrono::seconds(20)) << shell;
        EXPECT_EQ(ownSessionTempFiles(), before) << "a cancelled command left its script files: " << shell;

        const auto timedOut = session.run(posixSyntax(shell) ? "sleep 60" : "ping -n 60 127.0.0.1 >nul", 500);
        EXPECT_TRUE(timedOut.timedOut) << shell;
        EXPECT_EQ(ownSessionTempFiles(), before) << "a timed-out command left its script files: " << shell;

        // The session still works afterwards.
        EXPECT_EQ(session.run(posixSyntax(shell) ? "true" : "rem", 30000).exitCode, 0) << shell;
        std::error_code ec;
        fs::remove_all(dir, ec);
    }
}

TEST_F(ShellSessionTest, CommandsRunInFoldersWithNonAsciiNames) {
    for (const std::string& shell : sessionShells()) {
        const fs::path base = freshDir("gaia-utf8-");
        const std::string cafe = "caf\xc3\xa9 notes", nihon = "\xe6\x97\xa5\xe6\x9c\xac data";
        fs::create_directories(base / fs::u8path(cafe));
        fs::create_directories(base / fs::u8path(nihon));
        std::ofstream(base / fs::u8path(cafe) / "note.txt") << "remember the milk";
        const bool posix = posixSyntax(shell);

        ShellSession session((base / fs::u8path(cafe)).u8string(), shell);
        const auto where = session.run(posix ? "pwd" : "cd", 30000);
        ASSERT_EQ(where.exitCode, 0) << shell << ": " << where.stderr_output;
        EXPECT_NE(where.stdout_output.find(cafe), std::string::npos) << shell << ": " << where.stdout_output;
        EXPECT_NO_THROW(nlohmann::json(where.stdout_output).dump()) << "the folder is reported in UTF-8";
        EXPECT_NE(session.cwd().find(cafe), std::string::npos) << session.cwd();

        const auto read = session.run(posix ? "cat note.txt" : "type note.txt", 30000);
        EXPECT_EQ(read.exitCode, 0) << shell << ": " << read.stderr_output;
        EXPECT_NE(read.stdout_output.find("remember the milk"), std::string::npos) << shell;

        const auto moved = session.run("cd \"../" + nihon + "\"", 30000);
        EXPECT_EQ(moved.exitCode, 0) << shell << ": " << moved.stderr_output;
        EXPECT_NE(session.cwd().find(nihon), std::string::npos) << shell << ": " << session.cwd();
        const auto there = session.run(posix ? "pwd" : "cd", 30000);
        EXPECT_NE(there.stdout_output.find(nihon), std::string::npos) << shell << ": " << there.stdout_output;

        const auto back = session.run("cd \"../" + cafe + "\"", 30000);
        EXPECT_EQ(back.exitCode, 0) << shell << ": " << back.stderr_output;
        EXPECT_NE(session.cwd().find(cafe), std::string::npos) << shell << ": " << session.cwd();
        const auto again = session.run(posix ? "cat note.txt" : "type note.txt", 30000);
        EXPECT_NE(again.stdout_output.find("remember the milk"), std::string::npos) << shell;

        EXPECT_TRUE(session.setCwd((base / fs::u8path(nihon)).u8string()));
        EXPECT_NE(session.cwd().find(nihon), std::string::npos);
        std::error_code ec;
        fs::remove_all(base, ec);
    }
}

// A shell rewrites the environment it inherits (Git Bash turns PATH into its
// own form). Only what a command changed counts as the session's.
TEST_F(ShellSessionTest, EnvironmentHoldsOnlyWhatACommandChanged) {
    for (const std::string& shell : sessionShells()) {
        const bool posix = posixSyntax(shell);
        ShellSession session("", shell);
        ASSERT_EQ(session.run("echo hello", 30000).exitCode, 0) << shell;
        std::string names;
        for (const auto& [name, value] : session.environment()) names += name + " ";
        EXPECT_TRUE(session.environment().empty()) << shell << " reports: " << names;

        ASSERT_EQ(session.run(posix ? "export GAIA_ONE=1" : "set GAIA_ONE=1", 30000).exitCode, 0);
        const auto env = session.environment();
        EXPECT_EQ(env.size(), 1u) << shell;
        EXPECT_EQ(env.count("GAIA_ONE"), 1u) << shell;
        const auto echoed = session.run(posix ? "echo $GAIA_ONE" : "echo %GAIA_ONE%", 30000);
        EXPECT_NE(echoed.stdout_output.find("1"), std::string::npos);
    }
}

TEST(ShellSessionTempFiles, LeftoversOfDeadProcessesAreRemovedAndNothingElse) {
    const fs::path tmp = fs::temp_directory_path();
#ifdef _WIN32
    const std::string mine = std::to_string(GetCurrentProcessId());
#else
    const std::string mine = std::to_string(getpid());
#endif
    // 2147483646 is not a running process on any system this runs on.
    const std::vector<std::string> stale = {"gaia_shell_2147483646_7.sh", "gaia_shell_2147483646_8.cmd",
                                            "gaia_shell_2147483646_9.state", "gaia_shell_2147483646_aB3xYz"};
    const std::vector<std::string> kept = {"gaia_shell_" + mine + "_999999.state",  // this process is alive
                                           "gaia_shell_2147483646_7.txt",           // not a session file type
                                           "gaia_shell_notes.txt",                  // no process id
                                           "gaia_shell_99999999999999_1.sh",        // not a process id
                                           "my_gaia_shell_2147483646_1.sh"};        // another program's name
    for (const auto& name : stale) std::ofstream(tmp / name) << "x";
    for (const auto& name : kept) std::ofstream(tmp / name) << "x";

    const size_t removed = ShellSession::removeStaleTempFiles();
    EXPECT_GE(removed, stale.size());
    for (const auto& name : stale) EXPECT_FALSE(fs::exists(tmp / name)) << name;
    for (const auto& name : kept) {
        EXPECT_TRUE(fs::exists(tmp / name)) << name;
        std::error_code ec;
        fs::remove(tmp / name, ec);
    }
}
