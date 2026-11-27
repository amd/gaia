// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include <string>

namespace gaia::detail {

/// @p s as one argument on a ProcessRunner command line: double quotes for
/// cmd.exe (a Windows file name cannot hold one), single quotes for sh.
/// cmd.exe still expands %NAME% inside the quotes; see ShellSafePath in
/// document_index.cpp for a file name that holds a %.
inline std::string shellQuote(const std::string& s) {
#ifdef _WIN32
    return "\"" + s + "\"";
#else
    std::string out = "'";
    for (char c : s) out += (c == '\'') ? std::string("'\\''") : std::string(1, c);
    return out + "'";
#endif
}

}  // namespace gaia::detail
