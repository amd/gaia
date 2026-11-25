// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#include <algorithm>
#include <cctype>
#include <string>

namespace gaia::detail {

/// @p s with A-Z lowered; every other byte is left as it is.
inline std::string lowerAscii(std::string s) {
    std::transform(s.begin(), s.end(), s.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    return s;
}

}  // namespace gaia::detail
