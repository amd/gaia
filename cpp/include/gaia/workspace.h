// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Workspace: the folders an agent may touch. Mirrors the Python agent's path
// confinement (PathValidator / search scope): file tools refuse paths outside
// the roots, and shell commands that name an existing location outside them
// (other than system directories programs live in) are refused before running.
//
// The shell check reads the command text, so it is a guard against an agent
// wandering (a drive-wide `find`, a document's planted path), not a sandbox.

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/types.h"

namespace gaia {

class GAIA_API Workspace {
public:
    /// The first root is primary: relative paths resolve against it. Roots and
    /// every path checked against them are UTF-8 (fs::path::u8string()), on
    /// Windows too; a string in the ANSI code page is not accepted.
    explicit Workspace(std::vector<std::string> roots);

    const std::vector<std::string>& roots() const { return roots_; }

    /// True when `path` (relative to the primary root) lies inside a root.
    bool contains(const std::string& path) const;

    /// "" when the path is allowed, else the refusal the model should read.
    std::string checkPath(const std::string& path) const;

    /// "" when the command names no outside location, else the refusal.
    std::string checkCommand(const std::string& command) const;

    /// True when a read-only command, split by splitShellCommand(), reads
    /// nothing outside the roots and the system folders: every argument is
    /// resolved against `cwd` (the shell's folder; "" = the primary root),
    /// following `..`, `~` and links. False when `cwd` itself is outside.
    bool readsStayInside(const std::vector<std::vector<std::string>>& segments,
                         const std::string& cwd) const;

    /// Wrap a tool so each named string argument must pass checkPath().
    static ToolInfo confine(ToolInfo tool, std::shared_ptr<const Workspace> ws,
                            std::vector<std::string> pathArgs);

    /// Wrap a shell tool so its command argument must pass checkCommand().
    static ToolInfo confineShell(ToolInfo tool, std::shared_ptr<const Workspace> ws,
                                 std::string commandArg = "command");

private:
    std::string refusal(const std::string& path) const;

    std::vector<std::string> roots_;
};

} // namespace gaia
