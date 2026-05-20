// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "bash_tools.h"

#include <algorithm>
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

ToolInfo BashTools::bashExecute() {
    ToolInfo info;
    info.name = "bash_execute";
    info.description =
        "Execute a shell command and return its output. "
        "The command runs in the detected shell (bash preferred, sh fallback). "
        "Output is truncated at 32 KB. Use timeout_ms to control the deadline.";
    info.parameters = {
        {"command", ToolParamType::STRING, /*required=*/true,
         "The shell command to execute"},
        {"timeout_ms", ToolParamType::INTEGER, /*required=*/false,
         "Timeout in milliseconds (default: 30000)"},
    };
    info.callback = doBashExecute;
    info.policy = ToolPolicy::CONFIRM;
    return info;
}

json BashTools::doBashExecute(const json& args) {
    // Extract arguments
    std::string command = args.value("command", "");
    if (command.empty()) {
        return {{"error", "command parameter is required"}};
    }

    int timeoutMs = args.value("timeout_ms", DEFAULT_TIMEOUT_MS);
    if (timeoutMs <= 0) {
        timeoutMs = DEFAULT_TIMEOUT_MS;
    }

    // Detect the shell and build the full command
    std::string shell = detectShell();
    std::string fullCommand;

#ifdef _WIN32
    if (!shell.empty()) {
        // Use detected bash/sh: wrap the command in shell -c "..."
        // Escape double quotes in the command for the outer shell
        std::string escaped = command;
        // Replace \ with \\ and " with \" for the bash -c wrapper
        std::string safeCmd;
        safeCmd.reserve(escaped.size() + 16);
        for (char c : escaped) {
            if (c == '"') {
                safeCmd += "\\\"";
            } else {
                safeCmd += c;
            }
        }
        fullCommand = shell + " -c \"" + safeCmd + "\"";
    } else {
        // No bash/sh available — run via cmd.exe directly
        fullCommand = "cmd.exe /C " + command;
    }
#else
    // POSIX: always use bash -c (or sh -c as fallback)
    if (shell.empty()) {
        shell = "sh";
    }
    // Escape single quotes for POSIX shell: replace ' with '\''
    std::string safeCmd;
    safeCmd.reserve(command.size() + 16);
    for (char c : command) {
        if (c == '\'') {
            safeCmd += "'\\''";
        } else {
            safeCmd += c;
        }
    }
    fullCommand = shell + " -c '" + safeCmd + "'";
#endif

    // Execute via ProcessRunner
    ProcessResult result = ProcessRunner::run(fullCommand, timeoutMs, "", {}, MAX_OUTPUT_BYTES);

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

std::string BashTools::detectShell() {
#ifdef _WIN32
    // On Windows, try these in order:
    // 1. bash (Git Bash, MSYS2, WSL — typically on PATH)
    // 2. sh (fallback)
    if (isToolAvailable("bash")) {
        return "bash";
    }
    if (isToolAvailable("sh")) {
        return "sh";
    }
    // No POSIX shell found
    return "";
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
