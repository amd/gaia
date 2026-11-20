// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Read-only helper agents for delegate_task: what a helper is, and how many run at once.

#pragma once

#include <memory>
#include <string>
#include <vector>

#include <gaia/agent.h>
#include <gaia/lemonade_client.h>
#include <gaia/workspace.h>

namespace gaia {

/// How many helpers run at once, and what decided it.
struct HelperWidth {
    int width = 1;
    std::string source;  // "override", "cloud", "npu", "slots", "unknown"
    std::string note;    // why the slot count is unknown
};

inline constexpr int kCloudHelperWidth = 8;
inline constexpr int kLocalHelperWidth = 4;
inline constexpr int kMaxHelperTasks = 16;
/// The most helpers one call runs once the tool has shared a many-file task out.
inline constexpr int kMaxSharedHelpers = 64;

/// True when modelId at baseUrl is served by a hosted provider, read off the names alone.
bool isHostedModel(const std::string& modelId, const std::string& baseUrl);

/// The width for a model: one on the NPU, whatever was asked; else `fixed` above 0; else
/// kCloudHelperWidth for a hosted model; else a local model's slot count, at most
/// kLocalHelperWidth; one when the server does not say.
HelperWidth helperWidth(const std::string& modelId, const std::string& baseUrl,
                        const ModelCapacity& capacity, int fixed);

/// An agent that can only read: file_read, file_search and read_tool_output, inside the
/// folders `workspace` allows (null = anywhere the user can read). It has no
/// delegate_task, so a helper cannot start another.
class HelperAgent : public Agent {
public:
    HelperAgent(const AgentConfig& config, std::shared_ptr<const Workspace> workspace);

    /// The tools a helper is built with, in registration order.
    static const std::vector<std::string>& toolNames();

protected:
    void registerTools() override;
    std::string getSystemPrompt() const override;
    std::optional<std::string> announcedStepNote(const std::string& answer) override;

private:
    std::shared_ptr<const Workspace> workspace_;
};

}  // namespace gaia
