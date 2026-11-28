// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// What a shell command will touch, worked out before it runs.
//
// The command line is parsed the way bash reads it (quotes, variables, globs,
// redirections, `cd`, wrappers such as env, xargs, find -exec and sh -c), and
// every file it names is resolved against the folder it will run in. The answer
// has three parts, because a check that runs before the command can promise
// only so much:
//
//   outside  a place the command names lies outside the allowed folders
//   opaque   the command runs code or takes its words from somewhere this check
//            cannot read (an interpreter, eval, a program it has no rule for),
//            so nothing about where it reads or writes is known
//   network  a program that reaches another computer
//
// This is a check of the command's text, not a sandbox: an opaque command that
// is approved can reach anything the user can.

#pragma once

#include <map>
#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia {

class Workspace;

struct ShellJudgement {
    /// A place outside the folders, as the command wrote it. "" = none found.
    std::string outsideWord;
    /// Where that leads, for asking the user. "" when it is no one place (a wildcard).
    std::string outsidePath;
    /// The whole refusal, when the standard wording would mislead (a hard link). Usually "".
    std::string refusal;
    /// Extra words for the refusal ("cd would leave the folder"). May be "".
    std::string outsideWhy;

    /// Why the check cannot tell where the command reads or writes. "" = it can.
    std::string opaque;
    /// Set when what runs is decided while the command runs (a program name from a
    /// variable, eval, text piped into a shell): nothing can be said about it at all.
    std::string hidden;

    /// The program that reaches another computer ("git fetch", "nc"). "" = none found.
    std::string network;
    /// Script files handed to an interpreter or a shell: {language, path}, with language
    /// "file" for a program run by path. The caller may read them.
    std::vector<std::pair<std::string, std::string>> scripts;
    /// Fetch programs and interpreters as the shell will run them, words quoted, wherever
    /// on the line they sit: inside find -exec, eval, a variable, an AppleScript.
    std::vector<std::string> resolvedLines;

    /// Files the command writes by name (redirection, tee, cp), spelled from the folder it runs in.
    std::vector<std::string> written;

    /// More than one command on the line: a chain, a pipe, a subshell or a substitution.
    bool compound = false;

    bool inside() const { return outsideWord.empty() && opaque.empty(); }
};

/// Judge `command` as bash would run it standing in `cwd`, with `env` laid over
/// this process's environment. With no workspace, paths are not judged: only
/// `opaque`, `hidden`, `network` and `compound` are filled.
GAIA_API ShellJudgement judgeShellCommand(const std::string& command, const std::string& cwd,
                                          const std::map<std::string, std::string>& env, const Workspace* ws);

}  // namespace gaia
