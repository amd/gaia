// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Checks on a shell command or a file write that every agent with a shell shares:
// what would run part of a file name, blank a script's own $name, or overwrite a file
// another program holds.

#pragma once

#include <filesystem>
#include <string>
#include <vector>

#include "gaia/export.h"

namespace gaia::shellguards {

/// Why a one-line `python -c` script cannot run (a block statement after a semicolon); "" if it can.
GAIA_API std::string oneLineBlockRefusal(const std::string& command);

/// command with a backslash before each ${name} or $name a POSIX shell would replace, usually with
/// nothing, inside an inline script it expands (python -c "...", or an unquoted heredoc). Unchanged
/// when there is none, or when command or earlier (previous commands in the session) sets it.
GAIA_API std::string keepScriptDollars(const std::string& command, const std::string& earlier = "");

/// Names under roots (two levels, dependency and hidden folders skipped) holding shell syntax.
GAIA_API std::vector<std::string> hostileNames(const std::vector<std::string>& roots);

/// Why command must not run: it uses one of names outside any quotes, where the shell would run
/// part of the name. "" when it is safe.
GAIA_API std::string unquotedHostileName(const std::string& command, const std::vector<std::string>& names);

/// Why a write to path must not go ahead unasked: a lock file beside it, or a header saying another
/// program owns it. "" when nothing guards it.
GAIA_API std::string guardedFileReason(const std::filesystem::path& path);

}  // namespace gaia::shellguards
