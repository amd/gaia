// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/git_tools.h"

#include "gaia/process.h"
#include "gaia/secrets.h"
#include "gaia/security.h"

#include <algorithm>
#include <cstring>
#include <sstream>
#include <string>

namespace {

/// Maximum output size returned to the LLM (32 KiB).
constexpr std::size_t kMaxOutputBytes = 32u * 1024u;

/// Run `git <command>` via ProcessRunner, capturing stdout+stderr. `failure`
/// receives why git did not succeed, or "" when it exited 0.
std::string runGit(const std::string& command, std::string& failure) {
#ifdef _WIN32
    // By full path: cmd.exe would run a git.bat or git.exe in the repository before the real one.
    const std::string gitPath = gaia::findProgramOnPath("git");
    if (gitPath.empty()) {
        failure = "git isn't installed, or isn't on PATH. Install Git for Windows, then try again.";
        return "";
    }
    // cmd /c drops the first and last quote of a line that starts with one, so the line gets an extra pair.
    const std::string git = "\"\"" + gitPath + "\"";
    const std::string close = "\"";
#else
    const std::string git = "git";
    const std::string close;
#endif
    // These tools run without asking, and a repo's own config can name a program for git to start:
    // a file-system monitor, an external diff, a textconv filter.
    std::string guarded = git + " -c core.fsmonitor=false " + command + close;
    for (const char* sub : {"diff", "show", "log"}) {
        const size_t n = std::strlen(sub);
        if (command.compare(0, n, sub) == 0 && (command.size() == n || command[n] == ' ')) {
            guarded = git + " -c core.fsmonitor=false " + sub + " --no-ext-diff --no-textconv" +
                      command.substr(n) + close;
        }
    }
    auto result = gaia::ProcessRunner::run(
        guarded,
        /*timeoutMs=*/30000,
        /*cwd=*/"",
        /*env=*/{},
        /*maxOutputBytes=*/kMaxOutputBytes
    );
    // Merge stderr into stdout for backwards compatibility
    if (!result.stderr_output.empty()) {
        if (!result.stdout_output.empty()) {
            result.stdout_output += "\n";
        }
        result.stdout_output += result.stderr_output;
    }
    failure.clear();
    if (result.timedOut) {
        failure = "git " + command + " did not finish within 30 seconds and was stopped.";
    } else if (result.exitCode != 0) {
        failure = result.stdout_output.empty()
                      ? "git " + command + " failed with exit code " + std::to_string(result.exitCode) +
                            ". Check that git is installed and that this folder is a repository."
                      : result.stdout_output;
    }
    return result.stdout_output;
}

/// Truncate output to kMaxOutputBytes, appending a notice if truncated.
std::string truncateOutput(const std::string& output) {
    if (output.size() <= kMaxOutputBytes) {
        return output;
    }
    return output.substr(0, kMaxOutputBytes) + "\n... [output truncated at 32KB]";
}

/// Count non-empty lines in a string.
int countLines(const std::string& text) {
    if (text.empty()) {
        return 0;
    }
    int count = 0;
    std::istringstream stream(text);
    std::string line;
    while (std::getline(stream, line)) {
        if (!line.empty()) {
            ++count;
        }
    }
    return count;
}

/// Helper to create a ToolParameter (C++17 compatible, no designated initializers).
gaia::ToolParameter makeParam(const std::string& name, gaia::ToolParamType type,
                              bool required, const std::string& desc) {
    gaia::ToolParameter p;
    p.name = name;
    p.type = type;
    p.required = required;
    p.description = desc;
    return p;
}

} // anonymous namespace

namespace gaia {

// ---------------------------------------------------------------------------
// registerAll
// ---------------------------------------------------------------------------

void GitTools::registerAll(ToolRegistry& registry) {
    registry.registerTool(gitStatus());
    registry.registerTool(gitDiff());
    registry.registerTool(gitLog());
    registry.registerTool(gitShow());
}

// ---------------------------------------------------------------------------
// gitStatus
// ---------------------------------------------------------------------------

ToolInfo GitTools::gitStatus() {
    ToolInfo info;
    info.name = "git_status";
    info.description = "Get working tree status. Returns porcelain status output "
                       "and whether the tree is clean.";
    info.callback = doGitStatus;
    info.policy = ToolPolicy::ALLOW;
    // No parameters
    return info;
}

json GitTools::doGitStatus(const json& /*args*/) {
    std::string failure;
    std::string output = runGit("status --porcelain", failure);

    // Check for git errors (e.g. not a git repo)
    if (!failure.empty()) {
        return json{{"error", failure}};
    }

    // Trim trailing whitespace
    while (!output.empty() && (output.back() == '\n' || output.back() == '\r')) {
        output.pop_back();
    }

    bool clean = output.empty();
    return json{{"status", truncateOutput(output)}, {"clean", clean}};
}

// ---------------------------------------------------------------------------
// gitDiff
// ---------------------------------------------------------------------------

ToolInfo GitTools::gitDiff() {
    ToolInfo info;
    info.name = "git_diff";
    info.description = "Show changes in working tree or between refs. "
                       "Optionally filter by path or show staged changes.";
    info.callback = doGitDiff;
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        makeParam("path", ToolParamType::STRING, false,
                  "File or directory path to limit the diff to."),
        makeParam("staged", ToolParamType::BOOLEAN, false,
                  "If true, show staged (cached) changes instead of unstaged."),
        makeParam("ref", ToolParamType::STRING, false,
                  "Git ref to diff against (e.g. a branch name or commit hash)."),
    };
    return info;
}

json GitTools::doGitDiff(const json& args) {
    std::string cmd = "diff";

    // --staged flag
    bool staged = args.value("staged", false);
    if (staged) {
        cmd += " --staged";
    }

    // Optional ref
    if (args.contains("ref") && args["ref"].is_string()) {
        std::string ref = args["ref"].get<std::string>();
        if (!isSafeShellArg(ref)) {
            return json{{"error", "Invalid ref argument: contains unsafe characters."}};
        }
        cmd += " " + ref;
    }

    // Optional path
    if (args.contains("path") && args["path"].is_string()) {
        std::string path = args["path"].get<std::string>();
        if (!isSafeShellArg(path)) {
            return json{{"error", "Invalid path argument: contains unsafe characters."}};
        }
        cmd += " -- " + path;
    }

    std::string failure;
    std::string diffOutput = runGit(cmd, failure);
    if (!failure.empty()) {
        return json{{"error", failure}};
    }

    // Count files changed via --stat
    std::string statCmd = "diff --stat";
    if (staged) {
        statCmd += " --staged";
    }
    if (args.contains("ref") && args["ref"].is_string()) {
        statCmd += " " + args["ref"].get<std::string>();
    }
    if (args.contains("path") && args["path"].is_string()) {
        statCmd += " -- " + args["path"].get<std::string>();
    }

    std::string statOutput = runGit(statCmd, failure);
    if (!failure.empty()) {
        return json{{"error", failure}};
    }
    int filesChanged = 0;
    if (!statOutput.empty()) {
        // Each changed file has its own line; the last line is the summary.
        // Count lines that are not the summary line (which contains "changed").
        int totalLines = countLines(statOutput);
        filesChanged = (totalLines > 1) ? totalLines - 1 : totalLines;
    }

    return json{{"diff", truncateOutput(diffOutput)}, {"files_changed", filesChanged}};
}

// ---------------------------------------------------------------------------
// gitLog
// ---------------------------------------------------------------------------

ToolInfo GitTools::gitLog() {
    ToolInfo info;
    info.name = "git_log";
    info.description = "Show recent commit history. Returns up to N commits "
                       "(default 10) in oneline or full format.";
    info.callback = doGitLog;
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        makeParam("count", ToolParamType::INTEGER, false,
                  "Number of commits to show (default 10, max 100)."),
        makeParam("oneline", ToolParamType::BOOLEAN, false,
                  "If true (default), show compact one-line format."),
        makeParam("path", ToolParamType::STRING, false,
                  "File or directory path to filter commit history."),
    };
    return info;
}

json GitTools::doGitLog(const json& args) {
    int count = args.value("count", 10);
    // Clamp to [1, 100]
    count = std::max(1, std::min(count, 100));

    bool oneline = args.value("oneline", true);

    std::string cmd = "log -n " + std::to_string(count);
    if (oneline) {
        cmd += " --oneline";
    }

    // Optional path filter
    if (args.contains("path") && args["path"].is_string()) {
        std::string path = args["path"].get<std::string>();
        if (!isSafeShellArg(path)) {
            return json{{"error", "Invalid path argument: contains unsafe characters."}};
        }
        cmd += " -- " + path;
    }

    std::string failure;
    std::string output = runGit(cmd, failure);
    if (!failure.empty()) {
        return json{{"error", failure}};
    }

    int commits = countLines(output);
    return json{{"log", truncateOutput(output)}, {"commits", commits}};
}

// ---------------------------------------------------------------------------
// gitShow
// ---------------------------------------------------------------------------

ToolInfo GitTools::gitShow() {
    ToolInfo info;
    info.name = "git_show";
    info.description = "Show a specific commit or object. Defaults to HEAD.";
    info.callback = doGitShow;
    info.policy = ToolPolicy::ALLOW;
    info.parameters = {
        makeParam("ref", ToolParamType::STRING, false,
                  "Git ref to show (commit hash, tag, branch). Defaults to HEAD."),
    };
    return info;
}

json GitTools::doGitShow(const json& args) {
    std::string ref = args.value("ref", std::string("HEAD"));

    if (!isSafeShellArg(ref)) {
        return json{{"error", "Invalid ref argument: contains unsafe characters."}};
    }
    // HEAD:.env would hand over a committed file that file_read refuses to open.
    const size_t colon = ref.find(':');
    if (colon != std::string::npos) {
        const std::string name = ref.substr(ref.find_last_of(":/\\") + 1);
        if (isCredentialFileName(name)) {
            return json{{"error", "'" + name + "' holds credentials, keys or secrets, so the agent may not show it. "
                                  "Open it yourself if you need its contents."}};
        }
    }

    std::string failure;
    std::string output = runGit("show " + ref, failure);
    if (!failure.empty()) {
        return json{{"error", failure}};
    }

    return json{{"content", truncateOutput(output)}, {"ref", ref}};
}

} // namespace gaia
