// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Helpers for asserting on ftxui::Screen::ToString() output, which carries
// ANSI style sequences inline — a raw std::string::size() therefore counts
// escape bytes, not columns.

#pragma once

#include <string>
#include <vector>

namespace gaia_test {

/// Remove ANSI CSI/OSC escape sequences so the remaining text is what the
/// user actually sees.
inline std::string stripAnsi(const std::string& s) {
    std::string out;
    out.reserve(s.size());
    for (std::size_t i = 0; i < s.size(); ++i) {
        if (s[i] != '\033') {
            out += s[i];
            continue;
        }
        // CSI: ESC [ ... final byte in 0x40-0x7E
        if (i + 1 < s.size() && s[i + 1] == '[') {
            i += 2;
            while (i < s.size() && (s[i] < 0x40 || s[i] > 0x7E)) ++i;
            continue;
        }
        // Anything else: skip the escape and the byte after it.
        ++i;
    }
    return out;
}

/// Split rendered screen text into visible lines (styles removed).
inline std::vector<std::string> visibleLines(const std::string& rendered) {
    const std::string plain = stripAnsi(rendered);
    std::vector<std::string> lines;
    std::size_t start = 0;
    while (start <= plain.size()) {
        const std::size_t end = plain.find('\n', start);
        std::string line = plain.substr(start, end == std::string::npos ? end : end - start);
        if (!line.empty() && line.back() == '\r') line.pop_back();  // ToString ends rows with CRLF
        lines.push_back(std::move(line));
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return lines;
}

} // namespace gaia_test
