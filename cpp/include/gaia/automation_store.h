// Copyright(C) 2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// AutomationStore: scheduled reminders, summaries, alerts and triggers stored in
// $GAIA_HOME/automations/automations.json. Shares the exact JSON store, locking
// protocol and schema used by Python's gaia.automations.store, the daemon
// scheduler and Settings > Automations.

#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include "gaia/export.h"
#include "gaia/tool_registry.h"

namespace gaia {

struct GAIA_API Automation {
    std::string id;
    std::string name;
    std::string kind;  // reminder | summary | trigger | alert
    std::string prompt;
    std::string say;
    std::int64_t everySeconds = 0;
    double nextAt = 0.0;
    std::string check;
    std::string quiet;
    std::string days;
    bool paused = false;
    double createdAt = 0.0;
    double lastRunAt = 0.0;
    std::int64_t runs = 0;
    std::string lastOutput;
    bool checked = false;
    std::int64_t failures = 0;
    std::string lastAlert;

    bool repeats() const { return everySeconds > 0; }
};

class GAIA_API AutomationStore {
public:
    static constexpr int kMaxAutomations = 25;
    static constexpr std::int64_t kMinIntervalSeconds = 5 * 60;
    static constexpr std::int64_t kMaxDelaySeconds = 366LL * 86400LL;

    struct CreateRequest {
        std::string name;
        std::string prompt;
        std::string say;
        std::string every;
        std::string at;
        std::string check;
        bool alert = false;
        std::string quiet;
        std::string days;
    };

    struct EditRequest {
        std::string ref;
        std::optional<std::string> name;
        std::optional<std::string> prompt;
        std::optional<std::string> say;
        std::optional<std::string> every;
        std::optional<std::string> at;
        std::optional<std::string> check;
        std::optional<std::string> quiet;
        std::optional<std::string> days;
    };

    /// Empty path resolves defaultPath() dynamically on each operation.
    explicit AutomationStore(std::string path = "");

    /// <gaia_home>/automations/automations.json honoring GAIA_HOME, GAIA_CONFIG_DIR, then ~/.gaia.
    static std::string defaultPath();

    // ---- Pure timing and command validation helpers (tested directly) ----

    /// Parses "30m", "every 6h", "1d", "1h30m" into seconds (>= 300).
    static std::int64_t parseInterval(const std::string& text);

    /// Parses relative delays ("10m", "in 2 hours"), clock times ("5pm", "17:30",
    /// "tomorrow 9am"), or ISO-8601 datetimes into Unix epoch seconds.
    static double parseWhen(const std::string& text, double nowEpoch);

    /// Validates "HH:MM-HH:MM" quiet hours and returns the trimmed string.
    static std::string parseQuiet(const std::string& text);

    /// Parses "weekdays", "weekends", "mon-fri", "mon,wed,fri" into canonical "mon,tue,...".
    static std::string parseDays(const std::string& text);

    /// Validates a trigger's read-only check command; throws std::invalid_argument on refusal.
    static void validateCheckCommand(const std::string& command);

    static std::string formatInterval(std::int64_t seconds);
    static std::string formatLocalTime(double epoch);
    static std::string formatShortWhen(double epoch, double nowEpoch);

    /// Plain-language approval summary for sidecar confirmation cards; empty for non-automation tools.
    static std::string confirmSummary(const std::string& tool, const json& args);

    // ---- Store operations (cross-process locked + atomic write) ----

    std::vector<Automation> list() const;
    Automation create(const CreateRequest& req, double nowEpoch = 0.0) const;
    Automation edit(const EditRequest& req, double nowEpoch = 0.0) const;
    Automation setPaused(const std::string& ref, bool paused) const;
    Automation remove(const std::string& ref) const;

    /// Plain-words view matching Python's store.describe(item).
    static json describe(const Automation& item);

    /// Registers list_automations, create_automation, edit_automation,
    /// pause_automation, resume_automation, remove_automation.
    void registerTools(ToolRegistry& registry);

private:
    std::string resolvePath() const;
    bool discordPaired(const std::string& storePath) const;
    static void dropWaitingFor(const std::string& storePath, const std::string& automationId);

    std::string path_;
};

}  // namespace gaia
