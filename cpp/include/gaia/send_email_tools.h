// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The send-email skill's tools, ported from hub/skills/send-email/tools.py with
// the same names (send-email/<tool>), parameters, descriptions, results and
// approval cards. They write through the mailbox EmailTools reads. Each change
// or send is built before its approval card, so the card shows exactly what goes
// out, and the approved call sends that same message, never a rebuilt one.

#pragma once

#include <memory>
#include <string>
#include <vector>

#include "gaia/email_tools.h"
#include "gaia/export.h"
#include "gaia/tool_registry.h"

namespace gaia {

class GAIA_API SendEmailTools {
public:
    /// `email` must outlive this object.
    explicit SendEmailTools(EmailTools& email);

    /// Registers the eight tools disabled, as loading the skill enables them.
    void registerAll(ToolRegistry& registry);

    /// The registered names, "send-email/list_drafts" and so on.
    static const std::vector<std::string>& toolNames();

private:
    struct State;
    std::shared_ptr<State> state_;
};

}  // namespace gaia
