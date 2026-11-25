// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// `gaiacpp --engine`: the agent loop as an engine for another program.
//
// The host owns everything a desktop agent would own itself: the system
// prompt, the tools, the conversation history and the limits on a turn. The
// engine runs the model loop and nothing else. It has no file, shell or web
// tools, reads no settings file, keeps no memory and asks no approval.
//
// The wire is JSON Lines on stdin and stdout, described in
// docs/sdk/host-protocol.mdx and pinned by
// tests/fixtures/stdio/gaia_host_wire.json.
//
//   host -> engine   init, turn, tool_result, shutdown
//   engine -> host   ready, tool_request, model_call, final, error

#pragma once

#include <iosfwd>
#include <string>

namespace gaia {

/// The protocol version `init` must ask for and `ready` reports.
constexpr int kHostProtocol = 1;

struct HostOptions {
    std::string engineVersion;
};

/// Serve one host on `in` / `out` until `shutdown` or end of input.
/// Returns the process exit code: 0 after a clean end, 2 when `init` was
/// refused. Never throws; a failure is written to `out` as an `error` event.
int runHostServer(std::istream& in, std::ostream& out, const HostOptions& options);

}  // namespace gaia
