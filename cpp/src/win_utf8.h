// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#pragma once

#ifdef _WIN32

#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <string>

namespace gaia::detail {

/// UTF-8 to the UTF-16 Windows takes for commands, folders and file names.
inline std::wstring widen(const std::string& utf8) {
    if (utf8.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
    std::wstring wide(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), &wide[0], n);
    return wide;
}

/// @p len UTF-16 units at @p wide, as UTF-8.
inline std::string narrow(const wchar_t* wide, size_t len) {
    if (len == 0) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, wide, static_cast<int>(len), nullptr, 0, nullptr, nullptr);
    std::string utf8(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, wide, static_cast<int>(len), &utf8[0], n, nullptr, nullptr);
    return utf8;
}

inline std::string narrow(const std::wstring& wide) { return narrow(wide.data(), wide.size()); }

}  // namespace gaia::detail

#endif  // _WIN32
