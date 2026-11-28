// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The email read tools, ported from src/gaia/agents/tools/email_tools.py with
// the same names, parameters, descriptions and results, so the model sees the
// same thing whichever harness runs. A mailbox is used only when it is
// connected, carries a read scope and this agent holds a grant for it; with two,
// Gmail wins. The tools only read.

#pragma once

#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/mail.h"
#include "gaia/tool_registry.h"

namespace gaia {

class GAIA_API EmailTools {
public:
    /// The agent the grant ledger records these tools under.
    static constexpr const char* kAgentId = "installed:gaia";

    struct Options {
        /// The front-end the current run serves: "agent_ui", "tui", "cli" or "".
        /// Empty = "".
        std::function<std::string()> surface;
        /// Characters all mail bodies read in one turn may take together. Required.
        std::function<int()> turnBodyBudget;
        /// Empty = mail::httpsTransport().
        mail::Transport transport;
    };

    /// Throws std::invalid_argument when turnBodyBudget is missing.
    explicit EmailTools(Options options);

    /// Registers the eight tools, each marked as calling an online service.
    void registerAll(ToolRegistry& registry);

    /// A new turn: the turn's body budget starts over.
    void startTurn();

    static const std::vector<std::string>& toolNames();

private:
    struct State;
    std::shared_ptr<State> state_;
};

}  // namespace gaia
