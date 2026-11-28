// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// The calendar skill's tools, ported from hub/skills/calendar/tools.py with the
// same names (calendar/<tool>), parameters, descriptions, results and checks.
// They read the connected Google or Outlook calendar and create, change or
// cancel events once a person approves each change on its own card. They
// register disabled; loading the calendar skill turns them on.

#pragma once

#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/mail.h"
#include "gaia/tool_registry.h"

namespace gaia {

class GAIA_API CalendarTools {
public:
    /// The agent the grant ledger records these tools under.
    static constexpr const char* kAgentId = "installed:gaia";
    /// The skill whose load turns these tools on.
    static constexpr const char* kSkillName = "calendar";

    struct Options {
        /// The front-end the current run serves: "agent_ui", "tui", "cli" or "".
        std::function<std::string()> surface;
        /// Empty = mail::httpsTransport().
        mail::Transport transport;
    };

    explicit CalendarTools(Options options);

    /// Registers the five tools, disabled. Reads are allowed; changes ask a person.
    void registerAll(ToolRegistry& registry);

    /// calendar/<tool> for each tool, in the skill's order.
    static const std::vector<std::string>& toolNames();
    /// hub/skills/calendar/SKILL.md, as built into this program.
    static std::string skillMarkdown();
    /// True for any of the five.
    static bool isCalendarTool(const std::string& name);
    /// True for the three tools that change the calendar.
    static bool isWriteTool(const std::string& name);

    /// What a calendar change's approval card says, from the arguments it shows:
    /// sse_translation.render_calendar_summary.
    static std::string approvalSummary(const json& shownArgs);
    /// The same card split up: its rows before "Notifies:", and who it notifies.
    struct ApprovalCard {
        std::vector<std::string> rows;
        std::vector<std::string> notifies;
    };
    static ApprovalCard approvalCard(const json& shownArgs);

    /// Replace the clock (microseconds since 1970, UTC). Empty restores it.
    static void setClockForTesting(std::function<int64_t()> utcMicros);

private:
    struct State;
    std::shared_ptr<State> state_;
};

}  // namespace gaia
