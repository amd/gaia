// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "bash_tools.h"

#include <algorithm>
#include <filesystem>
#include <sstream>
#include <vector>

#include <gaia/process.h>
#include <gaia/security.h>

#ifdef _WIN32
#include <windows.h>
#endif

namespace gaia {

// ---------------------------------------------------------------------------
// Registration
// ---------------------------------------------------------------------------

void BashTools::registerAll(ToolRegistry& registry) {
    registry.registerTool(bashExecute());
    registry.registerTool(envInspect());
}

// ---------------------------------------------------------------------------
// bash_execute
// ---------------------------------------------------------------------------

ShellSession& BashTools::defaultSession() {
    // One session per process, so cwd and exported variables survive between calls.
    static ShellSession session("", detectShell());
    return session;
}

ToolInfo BashTools::bashExecute() { return bashExecute(nullptr); }

std::shared_ptr<ShellSession> BashTools::newSession(const std::string& dir) {
    return std::make_shared<ShellSession>(dir, detectShell());
}

ToolInfo BashTools::bashExecute(std::shared_ptr<ShellSession> session) {
    ToolInfo info;
    info.name = "bash_execute";
    info.description =
        "Execute a shell command and return its output. "
        "The command runs in the detected shell (bash preferred, sh fallback). "
        "The shell is persistent: a 'cd' or an exported variable stays in "
        "effect for later commands, and the current directory is returned as "
        "cwd. On Windows without a POSIX shell, commands run as a cmd.exe "
        "script, so write a for-loop variable as %%i rather than %i. Output is "
        "truncated at 32 KB. Use timeout_ms to control the deadline.";
    info.parameters = {
        {"command", ToolParamType::STRING, /*required=*/true,
         "The shell command to execute"},
        {"timeout_ms", ToolParamType::INTEGER, /*required=*/false,
         "Timeout in milliseconds (default: 30000)"},
    };
    info.callback = [session](const json& args) {
        return doBashExecute(session ? *session : defaultSession(), args);
    };
    info.policy = ToolPolicy::CONFIRM;
    info.grantScope = [](const json& args) { return shellCommandScope(args.value("command", "")); };
    info.needsConfirm = [](const json& args) {
        return !isReadOnlyShellCommand(args.value("command", ""), !detectShell().empty());
    };
    return info;
}

bool BashTools::setWorkingDirectory(const std::string& dir) { return defaultSession().setCwd(dir); }

json BashTools::doBashExecute(ShellSession& session, const json& args) {
    std::string command = args.value("command", "");
    if (command.empty()) {
        return {{"error", "command parameter is required"}};
    }

    int timeoutMs = args.value("timeout_ms", DEFAULT_TIMEOUT_MS);
    if (timeoutMs <= 0) {
        timeoutMs = DEFAULT_TIMEOUT_MS;
    }

    ProcessResult result = session.run(command, timeoutMs, MAX_OUTPUT_BYTES);

    // Truncate stdout/stderr if needed
    std::string stdoutStr = result.stdout_output;
    std::string stderrStr = result.stderr_output;

    static constexpr const char* TRUNCATION_MSG = "\n... [output truncated at 32 KB]";
    static const size_t TRUNC_LEN = std::strlen(TRUNCATION_MSG);
    if (stdoutStr.size() > MAX_OUTPUT_BYTES) {
        stdoutStr.resize(MAX_OUTPUT_BYTES - TRUNC_LEN);
        stdoutStr += TRUNCATION_MSG;
    }
    if (stderrStr.size() > MAX_OUTPUT_BYTES) {
        stderrStr.resize(MAX_OUTPUT_BYTES - TRUNC_LEN);
        stderrStr += TRUNCATION_MSG;
    }

    return {
        {"stdout", stdoutStr},
        {"stderr", stderrStr},
        {"exit_code", result.exitCode},
        {"timed_out", result.timedOut},
        {"cwd", session.cwd()},
    };
}

// ---------------------------------------------------------------------------
// env_inspect
// ---------------------------------------------------------------------------

ToolInfo BashTools::envInspect() {
    ToolInfo info;
    info.name = "env_inspect";
    info.description =
        "Inspect the shell environment: detect shell version, OS info, "
        "PATH entries, and check for common developer tools "
        "(shellcheck, bats, jq, yq, curl, git, docker).";
    info.parameters = {};  // no args
    info.callback = doEnvInspect;
    info.policy = ToolPolicy::ALLOW;
    return info;
}

json BashTools::doEnvInspect(const json& /*args*/) {
    json result;

    // --- Shell version ---
    std::string shellVersion;
    try {
        shellVersion = ProcessRunner::runOrThrow("bash --version", 5000);
        // Take only the first line
        auto nl = shellVersion.find('\n');
        if (nl != std::string::npos) {
            shellVersion = shellVersion.substr(0, nl);
        }
    } catch (...) {
        shellVersion = "bash not available";
    }
    result["shell"] = shellVersion;

    // --- OS info ---
    std::string osInfo;
    try {
#ifdef _WIN32
        osInfo = ProcessRunner::runOrThrow("systeminfo | findstr /B /C:\"OS Name\" /C:\"OS Version\"", 10000);
#else
        osInfo = ProcessRunner::runOrThrow("uname -a", 5000);
#endif
        // Trim trailing whitespace
        while (!osInfo.empty() && (osInfo.back() == '\n' || osInfo.back() == '\r')) {
            osInfo.pop_back();
        }
    } catch (...) {
        osInfo = "unknown";
    }
    result["os"] = osInfo;

    // --- PATH entries ---
    json pathEntries = json::array();
    std::string pathVar;
#ifdef _WIN32
    pathVar = getEnvVar("PATH", "");
    char delimiter = ';';
#else
    pathVar = getEnvVar("PATH", "");
    char delimiter = ':';
#endif
    if (!pathVar.empty()) {
        std::istringstream stream(pathVar);
        std::string entry;
        while (std::getline(stream, entry, delimiter)) {
            if (!entry.empty()) {
                pathEntries.push_back(entry);
            }
        }
    }
    result["path"] = pathEntries;

    // --- Installed tools ---
    json tools = json::object();
    const std::vector<std::string> toolNames = {
        "shellcheck", "bats", "jq", "yq", "curl", "git", "docker"
    };
    for (const auto& name : toolNames) {
        tools[name] = isToolAvailable(name);
    }
    result["tools"] = tools;

    return result;
}

// ---------------------------------------------------------------------------
// Shell detection
// ---------------------------------------------------------------------------

#ifdef _WIN32
namespace {
std::vector<std::string> whereAll(const std::string& name) {
    std::vector<std::string> out;
    ProcessResult r;
    try {
        r = ProcessRunner::run("where " + name + " 2>nul", 3000);
    } catch (const std::exception&) {
        return out;
    }
    if (r.exitCode != 0) return out;
    std::istringstream lines(r.stdout_output);
    for (std::string line; std::getline(lines, line);) {
        while (!line.empty() && (line.back() == '\r' || line.back() == ' ')) line.pop_back();
        if (!line.empty()) out.push_back(line);
    }
    return out;
}

std::string lowered(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

std::string findPosixShell() {
    // System32\bash.exe and the WindowsApps alias launch WSL, whose Linux side
    // can't see C:\ paths as written.
    for (const char* name : {"bash", "sh"}) {
        for (const auto& path : whereAll(name)) {
            const std::string l = lowered(path);
            if (l.find("\\windows\\system32\\") != std::string::npos ||
                l.find("\\windowsapps\\") != std::string::npos) {
                continue;
            }
            return path;
        }
    }
    // Git for Windows usually puts only <git>\cmd on PATH; its bash is beside it.
    for (const auto& git : whereAll("git")) {
        const std::filesystem::path root = std::filesystem::path(git).parent_path().parent_path();
        for (const char* rel : {"bin\\bash.exe", "usr\\bin\\bash.exe"}) {
            std::error_code ec;
            if (std::filesystem::is_regular_file(root / rel, ec)) return (root / rel).string();
        }
    }
    return "";
}
}  // namespace
#endif

std::string BashTools::detectShell() {
#ifdef _WIN32
    // Quoted full path of Git Bash / MSYS2, or "" for cmd.exe.
    static const std::string shell = [] {
        const std::string path = findPosixShell();
        return path.empty() ? std::string() : "\"" + path + "\"";
    }();
    return shell;
#else
    // On POSIX, prefer bash, fall back to sh
    if (isToolAvailable("bash")) {
        return "bash";
    }
    return "sh";
#endif
}

// ---------------------------------------------------------------------------
// Tool availability check
// ---------------------------------------------------------------------------

bool BashTools::isToolAvailable(const std::string& toolName) {
    if (toolName.empty()) {
        return false;
    }

#ifdef _WIN32
    std::string cmd = "where " + toolName + " >nul 2>&1";
#else
    std::string cmd = "which " + toolName + " >/dev/null 2>&1";
#endif

    try {
        ProcessResult result = ProcessRunner::run(cmd, 3000);
        return result.exitCode == 0;
    } catch (...) {
        return false;
    }
}

} // namespace gaia
