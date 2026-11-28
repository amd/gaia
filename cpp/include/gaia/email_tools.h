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

    // ---- for the send-email tools, which write through the mailbox these read ----

    /// The provider the reads use, resolved now. Throws the reads' MailboxError when none can be.
    std::string activeProvider();
    /// The account a write goes through, as its approval card names it.
    std::string accountLabel();
    /// A token for `scopes` on `provider`. In an app a refusal is a MailboxError naming no command.
    std::string accessToken(const std::string& provider, const std::vector<std::string>& scopes);
    mail::Transport transport();
    std::string surface() const;
    /// The saved drafts, read with the reads' one re-resolve after an auth failure.
    json listDrafts(int limit);

private:
    struct State;
    std::shared_ptr<State> state_;
};

}  // namespace gaia
