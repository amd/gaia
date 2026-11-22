// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "check_runner.h"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <regex>
#include <set>
#include <sstream>

#include <gaia/security.h>

#include "../gaiacpp/bash_tools.h"

namespace gaia::code {

namespace fs = std::filesystem;

namespace {

bool present(const fs::path& p) {
    std::error_code ec;
    return fs::exists(p, ec);
}

std::string readText(const fs::path& p) {
    std::ifstream in(p, std::ios::binary);
    return std::string(std::istreambuf_iterator<char>(in), std::istreambuf_iterator<char>());
}

bool hasPythonTests(const fs::path& dir) {
    for (const char* marker : {"pytest.ini", "conftest.py", "tox.ini"}) {
        if (present(dir / marker)) return true;
    }
    if (present(dir / "pyproject.toml") && readText(dir / "pyproject.toml").find("pytest") != std::string::npos) {
        return true;
    }
    std::error_code ec;
    for (const fs::path& sub : {dir, dir / "tests", dir / "test"}) {
        if (!fs::is_directory(sub, ec)) continue;
        for (fs::directory_iterator it(sub, fs::directory_options::skip_permission_denied, ec), end;
             !ec && it != end; it.increment(ec)) {
            const std::string name = it->path().filename().u8string();
            if (name.rfind("test_", 0) == 0 && it->path().extension() == ".py") return true;
        }
    }
    return false;
}

std::string python() {
#ifdef _WIN32
    return "python";
#else
    // Many distros ship only python3.
    const char* path = std::getenv("PATH");
    std::istringstream dirs(path ? path : "");
    for (std::string dir; std::getline(dirs, dir, ':');) {
        if (!dir.empty() && present(fs::path(dir) / "python3")) return "python3";
    }
    return "python";
#endif
}

// A count from a runner's output; a run of digits too long for an int is clamped, never thrown.
int count(const std::string& digits) {
    if (digits.size() > 9) return 999999999;
    return std::stoi(digits);
}

int sumMatches(const std::string& text, const std::regex& re, int group) {
    int total = -1;
    for (auto it = std::sregex_iterator(text.begin(), text.end(), re); it != std::sregex_iterator(); ++it) {
        total = (total < 0 ? 0 : total) + count((*it)[group].str());
    }
    return total;
}

void add(int& field, int n) {
    if (n < 0) return;
    field = (field < 0 ? 0 : field) + n;
}

}  // namespace

std::string detectCheckCommand(const fs::path& dir) {
    if (hasPythonTests(dir)) return python() + " -m pytest -q";
    if (present(dir / "go.mod")) return "go test ./...";
    if (present(dir / "Cargo.toml")) return "cargo test";
    if (present(dir / "package.json")) {
        try {
            const json pkg = json::parse(readText(dir / "package.json"));
            const std::string test = pkg.value("scripts", json::object()).value("test", "");
            // npm init's placeholder only prints an error and exits 1.
            if (!test.empty() && test.find("no test specified") == std::string::npos) return "npm test";
        } catch (const json::exception&) {
            // An unreadable package.json is not a runner; the caller says none was found.
        }
    }
    if (present(dir / "CMakeLists.txt")) {
        for (const char* build : {"build", "out/build", "cmake-build-debug"}) {
            if (present(dir / build / "CTestTestfile.cmake")) {
                return std::string("ctest --test-dir ") + build + " --output-on-failure";
            }
        }
    }
    return "";
}

void parseCheckOutput(const std::string& output, CheckResult& r) {
    std::smatch m;
    // pytest: "3 passed, 1 failed, 2 skipped in 0.12s"; errors are failures too.
    static const std::regex pyPassed(R"((\d+) passed)"), pyFailed(R"((\d+) failed)"), pySkipped(R"((\d+) skipped)"),
        pyErrors(R"((\d+) errors?\b)"), pyFailLine(R"(^(?:FAILED|ERROR) (\S+))");
    // cargo: "test result: ok. 4 passed; 0 failed; 1 ignored"
    static const std::regex cargo(R"(test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored)");
    // jest / vitest: "Tests:  1 failed, 4 passed, 5 total" / "Tests  1 failed | 4 passed (5)"
    static const std::regex jestFailed(R"(Tests:?\s+(?:[^\n]*?)(\d+) failed)"),
        jestPassed(R"(Tests:?\s+(?:[^\n]*?)(\d+) passed)"), jestSkipped(R"(Tests:?\s+(?:[^\n]*?)(\d+) skipped)");
    // ctest: "75% tests passed, 1 tests failed out of 4"
    static const std::regex ctest(R"((\d+)% tests passed, (\d+) tests failed out of (\d+))"),
        ctestFail(R"(^\s*\d+ - (\S+) \((?:Failed|Timeout|SEGFAULT|Exception))");
    // go: "ok  	pkg	0.01s" / "FAIL	pkg	0.02s" / "--- FAIL: TestName"
    static const std::regex goOk(R"(^ok\s+\S+)"), goFail(R"(^FAIL\s+\S+)"), goFailName(R"(^\s*--- FAIL: (\S+))");
    // unittest: "Ran 5 tests in 0.001s" then "OK" or "FAILED (failures=1, errors=1)"
    static const std::regex utRan(R"(Ran (\d+) tests? in)"), utFailures(R"(FAILED \((?:[^)]*?)failures=(\d+))"),
        utErrors(R"(FAILED \((?:[^)]*?)errors=(\d+))"), utSkipped(R"(\((?:[^)]*?)skipped=(\d+))");

    // mocha: "5 passing" / "1 failing" / "2 pending"
    static const std::regex mochaPassing(R"(^\s*(\d+) passing\b)"), mochaFailing(R"(^\s*(\d+) failing\b)"),
        mochaPending(R"(^\s*(\d+) pending\b)");
    // rspec: "5 examples, 1 failure, 2 pending"
    static const std::regex rspec(R"((\d+) examples?, (\d+) failures?(?:, (\d+) pending)?)");
    // phpunit: "OK (5 tests, 9 assertions)" / "Tests: 5, Assertions: 9, Failures: 1, Errors: 1, Skipped: 1."
    static const std::regex phpOk(R"(OK \((\d+) tests?,)"), phpTests(R"(Tests: (\d+), Assertions: \d+)"),
        phpFailures(R"(Failures: (\d+))"), phpErrors(R"(Errors: (\d+))"), phpSkipped(R"(Skipped: (\d+))");
    // maven / gradle (JUnit): "Tests run: 5, Failures: 1, Errors: 0, Skipped: 1"
    static const std::regex junit(R"(Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+))");
    // dotnet: "Passed!  - Failed:     0, Passed:     5, Skipped:     0, Total:     5"
    static const std::regex dotnet(R"(Failed:\s+(\d+), Passed:\s+(\d+), Skipped:\s+(\d+), Total:\s+\d+)");
    static const std::regex dotnet9(
        R"(Test summary: total: (\d+), failed: (\d+), succeeded: (\d+), skipped: (\d+))", std::regex::icase);
    // swift: "Executed 5 tests, with 1 failure"
    static const std::regex swiftRan(R"(Executed (\d+) tests?, with (\d+) failures?)");

    if (std::regex_search(output, m, dotnet9)) {
        r.failed = count(m[2].str());
        r.passed = count(m[3].str());
        r.skipped = count(m[4].str());
        return;
    }
    if (std::regex_search(output, dotnet)) {
        r.failed = sumMatches(output, dotnet, 1);
        r.passed = sumMatches(output, dotnet, 2);
        r.skipped = sumMatches(output, dotnet, 3);
        return;
    }
    if (std::regex_search(output, junit)) {
        // Maven prints one line per class, then the totals last.
        std::smatch last;
        for (auto it = std::sregex_iterator(output.begin(), output.end(), junit); it != std::sregex_iterator(); ++it) {
            last = *it;
        }
        const int ran = count(last[1].str()), bad = count(last[2].str()) + count(last[3].str()),
                  skipped = count(last[4].str());
        r.passed = std::max(0, ran - bad - skipped);
        r.failed = bad;
        r.skipped = skipped;
        return;
    }
    if (std::regex_search(output, swiftRan)) {
        // XCTest prints one line per suite, innermost first: the last is the whole run.
        std::smatch outer;
        for (auto it = std::sregex_iterator(output.begin(), output.end(), swiftRan); it != std::sregex_iterator();
             ++it) {
            outer = *it;
        }
        const int ran = count(outer[1].str()), bad = count(outer[2].str());
        r.passed = std::max(0, ran - bad);
        r.failed = bad;
        return;
    }
    if (std::regex_search(output, m, rspec)) {
        const int ran = count(m[1].str()), bad = count(m[2].str());
        const int pending = m[3].matched ? count(m[3].str()) : 0;
        r.passed = std::max(0, ran - bad - pending);
        r.failed = bad;
        r.skipped = pending;
        return;
    }
    if (std::regex_search(output, m, phpOk)) {
        r.passed = count(m[1].str());
        r.failed = 0;
        return;
    }
    if (std::regex_search(output, m, phpTests)) {
        const int ran = count(m[1].str());
        int bad = 0, skipped = 0;
        if (std::regex_search(output, m, phpFailures)) bad += count(m[1].str());
        if (std::regex_search(output, m, phpErrors)) bad += count(m[1].str());
        if (std::regex_search(output, m, phpSkipped)) skipped = count(m[1].str());
        r.passed = std::max(0, ran - bad - skipped);
        r.failed = bad;
        r.skipped = skipped;
        return;
    }
    {
        std::istringstream mochaLines(output);
        bool mocha = false;
        int passing = 0, failing = 0, pending = 0;
        for (std::string line; std::getline(mochaLines, line);) {
            if (std::regex_search(line, m, mochaPassing)) passing = count(m[1].str()), mocha = true;
            else if (std::regex_search(line, m, mochaFailing)) failing = count(m[1].str()), mocha = true;
            else if (std::regex_search(line, m, mochaPending)) pending = count(m[1].str());
        }
        if (mocha) {
            r.passed = passing;
            r.failed = failing;
            r.skipped = pending;
            return;
        }
    }
    if (std::regex_search(output, m, ctest)) {
        const int failed = count(m[2].str()), total = count(m[3].str());
        r.failed = failed;
        r.passed = total - failed;
        std::istringstream ctestLines(output);
        for (std::string line; std::getline(ctestLines, line);) {
            if (std::regex_search(line, m, ctestFail)) r.failures.push_back(m[1].str());
        }
        return;
    }
    if (std::regex_search(output, cargo)) {
        add(r.passed, sumMatches(output, cargo, 1));
        add(r.failed, sumMatches(output, cargo, 2));
        add(r.skipped, sumMatches(output, cargo, 3));
        return;
    }
    if (std::regex_search(output, m, utRan)) {
        const int ran = count(m[1].str());
        int bad = 0;
        if (std::regex_search(output, m, utFailures)) bad += count(m[1].str());
        if (std::regex_search(output, m, utErrors)) bad += count(m[1].str());
        const int skipped = std::regex_search(output, m, utSkipped) ? count(m[1].str()) : 0;
        r.passed = std::max(0, ran - bad - skipped);
        r.failed = bad;
        r.skipped = skipped;
        return;
    }
    if (std::regex_search(output, jestPassed) || std::regex_search(output, jestFailed)) {
        if (std::regex_search(output, m, jestPassed)) r.passed = count(m[1].str());
        r.failed = std::regex_search(output, m, jestFailed) ? count(m[1].str()) : 0;
        if (std::regex_search(output, m, jestSkipped)) r.skipped = count(m[1].str());
        if (r.passed < 0) r.passed = 0;
        return;
    }
    std::istringstream lines(output);
    int goOkCount = 0, goFailCount = 0;
    for (std::string line; std::getline(lines, line);) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (std::regex_search(line, m, goFailName)) r.failures.push_back(m[1].str());
        else if (std::regex_search(line, m, pyFailLine)) r.failures.push_back(m[1].str());
        if (std::regex_search(line, goOk)) ++goOkCount;
        else if (std::regex_search(line, goFail)) ++goFailCount;
    }
    if (goOkCount + goFailCount > 0 && !std::regex_search(output, pyPassed)) {
        r.passed = goOkCount;  // go reports packages, not tests
        r.failed = goFailCount;
        return;
    }
    if (std::regex_search(output, m, pyPassed)) r.passed = count(m[1].str());
    if (std::regex_search(output, m, pyFailed)) r.failed = count(m[1].str());
    if (std::regex_search(output, m, pyErrors)) add(r.failed, count(m[1].str()));
    if (std::regex_search(output, m, pySkipped)) r.skipped = count(m[1].str());
    if (r.passed >= 0 && r.failed < 0) r.failed = 0;
    if (r.failed > 0 && r.passed < 0) r.passed = 0;
    if (output.find("no tests ran") != std::string::npos && r.passed < 0) {
        r.passed = 0;
        r.failed = 0;
    }
}

namespace {

// The program a command word names: its file name, lower case, without a Windows extension.
std::string programName(const std::string& word) {
    std::string name = fs::u8path(word).filename().u8string();
    std::transform(name.begin(), name.end(), name.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    for (const char* ext : {".exe", ".cmd", ".bat", ".ps1"}) {
        const size_t n = std::strlen(ext);
        if (name.size() > n && name.compare(name.size() - n, n, ext) == 0) return name.substr(0, name.size() - n);
    }
    return name;
}

// Whether one command segment runs a test suite: judged by the program it runs, so
// `grep passed pytest.log` or `pip install pytest` is not a test run.
bool runsTests(const std::vector<std::string>& argv) {
    static const std::set<std::string> runners = {"pytest", "py.test", "ctest", "jest", "vitest", "mocha",
                                                  "phpunit", "rspec", "tox", "nox"};
    size_t i = 0;
    while (i < argv.size() && argv[i].find('=') != std::string::npos && argv[i][0] != '-') ++i;  // FOO=1 cmd
    if (i >= argv.size()) return false;
    const std::string p = programName(argv[i]);
    auto arg = [&](size_t k) { return i + k < argv.size() ? argv[i + k] : std::string(); };
    if (runners.count(p)) return true;
    if (p == "python" || p == "python3" || p == "py") {
        for (size_t k = 1; i + k + 1 < argv.size(); ++k) {
            if (arg(k) == "-m") return arg(k + 1) == "pytest" || arg(k + 1) == "unittest";
        }
        return false;
    }
    if (p == "npx" || p == "pnpx" || p == "bunx") return runners.count(programName(arg(1))) > 0;
    if (p == "go" || p == "cargo" || p == "dotnet" || p == "swift" || p == "mix" || p == "mvn" || p == "gradle" ||
        p == "gradlew" || p == "deno") {
        return arg(1) == "test";
    }
    if (p == "npm" || p == "pnpm" || p == "yarn" || p == "bun") {
        return arg(1) == "test" || (arg(1) == "run" && arg(2).rfind("test", 0) == 0);
    }
    if (p == "make") {
        for (size_t k = 1; i + k < argv.size(); ++k) {
            if (arg(k) == "test" || arg(k) == "check") return true;
        }
    }
    return false;
}

std::string nativeFolderOf(const std::string& cwd) {
#ifdef _WIN32
    if (cwd.size() >= 3 && cwd[0] == '/' && std::isalpha(static_cast<unsigned char>(cwd[1])) && cwd[2] == '/') {
        return std::string(1, cwd[1]) + ":" + cwd.substr(2);
    }
#endif
    return cwd;
}

// Puts the shell back in the folder it was in, however the run ends.
struct FolderRestore {
    ShellSession& shell;
    std::string folder;
    bool armed = false;
    ~FolderRestore() {
        if (armed) shell.setCwd(folder);
    }
};

}  // namespace

std::string nativeFolder(const std::string& cwd) { return nativeFolderOf(cwd); }

bool looksLikeTestCommand(const std::string& command) {
    std::vector<std::vector<std::string>> segments;
    if (!splitShellCommand(command, !BashTools::detectShell().empty(), segments)) return false;
    return std::any_of(segments.begin(), segments.end(), runsTests);
}

ToolInfo runChecksTool(std::shared_ptr<ShellSession> session, std::shared_ptr<const Workspace> ws) {
    ToolInfo info;
    info.name = "run_checks";
    info.description =
        "Run the project's tests (or another check command) and report what happened: exit code, "
        "how many passed, failed and were skipped, and the failing tests' names. Finds the runner "
        "itself (pytest, go test, cargo test, npm test, ctest) when no command is given. Use it "
        "after every change before saying tests pass; an answer that says they pass is checked "
        "against its result.";
    info.parameters = {
        {"command", ToolParamType::STRING, false, "Check command to run; default: the project's test runner."},
        {"path", ToolParamType::STRING, false, "Folder to run in; default: the shell's current folder."},
        {"timeout_ms", ToolParamType::INTEGER, false, "Deadline in milliseconds (default 600000)."},
    };
    // It runs the repository's own code.
    info.policy = ToolPolicy::CONFIRM;
    // "Always allow" covers the command it was given, never every command run_checks may later take.
    info.grantScope = [](const json& args) {
        const std::string command = args.is_object() ? args.value("command", "") : "";
        return command.empty() ? std::string("the project's test runner") : shellCommandScope(command);
    };
    info.callback = [session, ws](const json& args) -> json {
        ShellSession& shell = session ? *session : BashTools::defaultSession();
        const bool posix = !BashTools::detectShell().empty();
        const std::string before = nativeFolder(shell.cwd());
        fs::path dir = fs::u8path(before);
        const std::string path = args.value("path", "");
        if (!path.empty()) {
            const fs::path given = fs::u8path(path);
            dir = given.is_absolute() ? given : dir / given;  // relative to the shell, as described
            if (ws) {
                const std::string why = ws->checkPath(dir.u8string());
                if (!why.empty()) return {{"status", "error"}, {"error", why}};
            }
        }
        std::error_code ec;
        if (!fs::is_directory(dir, ec)) {
            return {{"status", "error"}, {"error", "Not a folder: " + dir.u8string()}};
        }
        CheckResult r;
        r.command = args.value("command", "");
        if (r.command.empty()) {
            r.command = detectCheckCommand(dir);
            r.testRun = !r.command.empty();
        } else {
            // A command the model wrote gets the same workspace check as bash_execute.
            if (ws) {
                const std::string why = ws->checkCommand(r.command, posix);
                if (!why.empty()) return {{"status", "error"}, {"error", why}};
            }
            r.testRun = looksLikeTestCommand(r.command);
        }
        if (r.command.empty()) {
            return {{"status", "error"},
                    {"error", "No test runner found in " + dir.u8string() +
                                  " (looked for pytest, go.mod, Cargo.toml, a package.json test script, "
                                  "a CMake build with tests). Pass the command to run."}};
        }
        const int timeout = std::clamp<int>(args.value("timeout_ms", 600000), 1000, 3600000);
        // In the agent's own shell, so its environment (a venv, PATH) applies.
        FolderRestore restore{shell, before};
        if (!path.empty()) {
            if (!shell.setCwd(dir.u8string())) {
                return {{"status", "error"}, {"error", "Cannot run in " + dir.u8string()}};
            }
            restore.armed = true;
        }
        constexpr size_t kCapture = 4 * 1024 * 1024;
        // Twice the limit: CRLF folding can halve a capped capture, which must still read as too long.
        const ProcessResult p = shell.run(r.command, timeout, 2 * kCapture + 2);
        r.exitCode = p.exitCode;
        r.timedOut = p.timedOut;
        const std::string output = p.stdout_output + (p.stderr_output.empty() ? "" : "\n" + p.stderr_output);
        parseCheckOutput(output, r);
        // The capture keeps the start; past its limit the summary at the end is gone.
        const bool clipped = p.stdout_output.size() > kCapture || p.stderr_output.size() > kCapture;
        if (clipped) r.passed = -1;
        constexpr size_t kTail = 8000;  // the summary and failures are at the end
        json out = {{"status", "success"},
                    {"command", r.command},
                    {"exit_code", r.exitCode},
                    {"timed_out", r.timedOut},
                    {"passed", r.passed},
                    {"failed", r.failed},
                    {"skipped", r.skipped},
                    {"failures", r.failures},
                    {"test_run", r.testRun},
                    {"green", r.testRun && r.green()},
                    {"output", output.size() > kTail ? "…" + output.substr(output.size() - kTail) : output}};
        if (!r.testRun) {
            out["note"] = "This command doesn't read as a test run, so its result can't show that tests pass.";
        } else if (clipped) {
            out["note"] = "The output was too long to keep whole, so its summary is missing: run fewer tests, or "
                          "quieter, to see the result.";
        } else if (r.passed < 0) {
            out["note"] = "The output didn't say how many tests ran; read it before reporting a result.";
        }
        return out;
    };
    return info;
}

}  // namespace gaia::code
