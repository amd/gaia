// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Workspace: the folders an agent may touch. Mirrors the Python agent's path
// confinement (PathValidator / search scope): file tools refuse paths outside
// the roots, and shell commands that name a location outside them (other than
// system directories programs live in) are refused before running.
//
// judgeCommand() reads the command's text for absolute paths, ~ paths and
// links; judgeShellCommand() in shell_guard.h expands and resolves every word
// the way the shell will. Both run before the command does: neither is a
// sandbox, and a command they can't read is reported as such, not passed.

#pragma once

#include <functional>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

/// Where a write to `path` (UTF-8, relative to the working directory) lands: every link
/// and junction on the way is followed, including one whose target doesn't exist yet.
/// "" when that can't be worked out (an unreadable link, a loop); treat "" as outside.
GAIA_API std::string resolveWriteTarget(const std::string& path);

class GAIA_API Workspace {
public:
    /// Roots and every path checked against them are UTF-8
    /// (fs::path::u8string()), on Windows too; a string in the ANSI code page
    /// is not accepted. A relative path resolves against the process's working
    /// directory, which is where the tools open it.
    explicit Workspace(std::vector<std::string> roots);

    /// What a check found. `refusal` is "" when the call may run. `askPath` is the
    /// outside location that adding as a root would let through; "" when none
    /// would (a network share, a link, a path that can't be resolved).
    struct Outside {
        std::string refusal;
        std::string askPath;
    };

    std::vector<std::string> roots() const;

    /// Let the tools use one more folder or file from now on. Safe while tools run.
    void addRoot(const std::string& path);

    /// Replace the refusal's closing advice, for a host with no --allow-dir.
    void setRemedy(std::string remedy);

    /// checkPath(), with the location to ask the user about.
    Outside judgePath(const std::string& path) const;

    /// checkCommand(), with the location to ask the user about.
    Outside judgeCommand(const std::string& command, bool posixShell = true) const;

    /// True when `path` (relative to the working directory) lies inside a root.
    bool contains(const std::string& path) const;

    /// Where a path leads, and whether a shell command may use it.
    struct Place {
        bool allowed = false;  // inside a root, or in a system folder
        bool system = false;   // allowed only as a system folder (/usr, /etc, /tmp)
        std::string real;      // every link followed; "" when that can't be worked out
    };

    /// Where `path` leads for a shell standing in `cwd` ("" = the working directory).
    Place locate(const std::string& path, const std::string& cwd) const;

    /// False when the tree under `dir` holds a link that leads outside the roots,
    /// or is too large to tell: a command that follows links would leave through it.
    bool linksStayInside(const std::string& dir) const;

    /// True when `path` is a link, or a folder with a link somewhere in it (or too large to tell).
    static bool holdsLinks(const std::string& path);

    /// Why a file tool must not write to `path`: the file has a second name (a hard
    /// link) that may lie outside the roots. "" when it has one name or isn't there yet.
    std::string sharedFileRefusal(const std::string& path) const;

    /// The refusal for a place a shell command names, with `why` added. Public for the shell guard.
    std::string outsideRefusal(const std::string& word, const std::string& why = "") const;

    /// "" when the path is allowed, else the refusal the model should read.
    std::string checkPath(const std::string& path) const;

    /// "" when the command names no outside location, else the refusal.
    /// `posixShell` false means cmd.exe runs it, where `/s` is a switch, not a path.
    std::string checkCommand(const std::string& command, bool posixShell = true) const;

    /// True for a path Git Bash on Windows serves from outside the file system:
    /// the clipboard, the registry, raw disks and memory.
    static bool isGitBashDevicePath(const std::string& path);

    /// True when a read-only command, split by splitShellCommand(), reads
    /// nothing outside the roots and the system folders: every argument is
    /// resolved against `cwd` (the shell's folder; "" = the primary root),
    /// following `..`, `~` and links. From a folder outside the roots only
    /// granted files may be read; nothing there is listed or matched.
    bool readsStayInside(const std::vector<std::vector<std::string>>& segments,
                         const std::string& cwd) const;

    /// `folder` (spelled as the shell reports it) resolved, when it lies outside
    /// the roots but holds a file root: where an attached file sits. Else "".
    std::string attachedFileFolder(const std::string& folder) const;

    /// Wrap a tool so each named string argument must pass checkPath().
    static ToolInfo confine(ToolInfo tool, std::shared_ptr<const Workspace> ws,
                            std::vector<std::string> pathArgs);

    /// Wrap a shell tool so its command argument must pass checkCommand(), unless
    /// `provenInside` says the call only reads what the roots hold.
    static ToolInfo confineShell(ToolInfo tool, std::shared_ptr<const Workspace> ws,
                                 std::string commandArg = "command", bool posixShell = true,
                                 std::function<bool(const json&)> provenInside = nullptr);

private:
    std::string refusal(const std::string& path) const;

    mutable std::mutex mu_;  // guards roots_ and remedy_
    std::vector<std::string> roots_;
    std::string remedy_;
};

} // namespace gaia
