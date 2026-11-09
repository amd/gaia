// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// gaiacpp as the GAIA daemon's "gaia" sidecar: the HTTP + SSE contract the
// Agent UI speaks to the Python flagship (hub/agents/gaia/python/gaia_agent/
// server.py), at API 2.14 — chat with streaming, tool activity, approvals,
// Stop and bypass. Started by the daemon as `gaiacpp --host H --port P`.

#pragma once

#include <string>

namespace gaia {

struct SidecarOptions {
    std::string host = "127.0.0.1";
    int port = 0;
    std::string defaultModel;  // used when a /query names none
};

/// Serve until stopped. Throws std::runtime_error when the token is missing
/// or the port can't be bound.
int runSidecar(const SidecarOptions& options);

} // namespace gaia
