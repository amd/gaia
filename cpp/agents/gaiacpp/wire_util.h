// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

// Helpers both gaiacpp transports (the HTTP sidecar and the stdio server) use.

#pragma once

#include <cstddef>
#include <random>
#include <string>

#include <nlohmann/json.hpp>

namespace gaia::wire {

/// One-line JSON for the wire; invalid UTF-8 is replaced rather than thrown on.
inline std::string dumps(const nlohmann::json& j) {
    return j.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
}

/// @p n random hex digits, for ids such as a confirmation's.
inline std::string randomHex(size_t n) {
    static thread_local std::mt19937_64 rng(std::random_device{}());
    static const char* hex = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; ++i) s.push_back(hex[rng() & 0xF]);
    return s;
}

}  // namespace gaia::wire
