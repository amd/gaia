// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// FTXUI-based reactive TUI console for agents.
// Drop-in replacement for CleanConsole that provides a fullscreen terminal UI
// with markdown rendering, streaming token display, and structured chat history.
//
// Usage:
//   agent.setOutputHandler(std::make_unique<gaia::TuiConsole>());
//
// Requires GAIA_BUILD_TUI=ON (defines GAIA_HAS_TUI).

#pragma once

#ifdef GAIA_HAS_TUI

#include <mutex>
#include <string>
#include <vector>

#include <ftxui/component/component.hpp>
#include <ftxui/component/screen_interactive.hpp>
#include <ftxui/dom/elements.hpp>

#include "gaia/console.h"
#include "gaia/export.h"

namespace gaia {

// Forward-declare the standalone markdown renderer (defined in tui_markdown.cpp).
ftxui::Element renderMarkdown(const std::string& markdown);

/// FTXUI-based reactive TUI console for agents.
/// Implements the OutputHandler interface with a fullscreen terminal UI.
///
/// Layout:
///   +------------------------------------+
///   |  Chat history (scrollable)          |
///   |  - User messages                    |
///   |  - Agent responses (markdown)       |
///   |  - Tool usage indicators            |
///   +------------------------------------+
///   |  Status: model | tokens | step N/M  |
///   +------------------------------------+
class GAIA_API TuiConsole : public OutputHandler {
public:
    TuiConsole();
    ~TuiConsole() override;

    // --- OutputHandler interface ---
    void printProcessingStart(const std::string& query, int maxSteps,
                              const std::string& modelId) override;
    void printStepHeader(int stepNum, int stepLimit) override;
    void printStateInfo(const std::string& message) override;
    void printThought(const std::string& thought) override;
    void printGoal(const std::string& goal) override;
    void printPlan(const json& plan, int currentStep) override;
    void printToolUsage(const std::string& toolName) override;
    void printToolComplete() override;
    void prettyPrintJson(const json& data, const std::string& title) override;
    void printError(const std::string& message) override;
    void printWarning(const std::string& message) override;
    void printInfo(const std::string& message) override;
    void startProgress(const std::string& message) override;
    void stopProgress() override;
    void printFinalAnswer(const std::string& answer,
                          const UsageStats& usage = {}) override;
    void printCompletion(int stepsTaken, int stepsLimit) override;
    void printDecisionMenu(const std::vector<Decision>& decisions) override;
    void printStreamToken(const std::string& token) override;
    void printStreamEnd() override;

    /// Get the accumulated chat entries as FTXUI Elements (for embedding in a larger TUI).
    std::vector<ftxui::Element> getChatElements();

    /// Get the status bar element.
    ftxui::Element getStatusBar();

private:
    // Chat history entries
    struct ChatEntry {
        enum class Type { USER, ASSISTANT, TOOL, INFO, ERROR, WARNING };
        Type type;
        std::string content;
    };

    /// Append a new entry (mutex must NOT be held by caller).
    void addEntry(ChatEntry::Type type, const std::string& content);

    mutable std::mutex mutex_;
    std::vector<ChatEntry> entries_;
    static constexpr size_t kMaxEntries = 2000;  // evict oldest when exceeded
    std::string currentModel_;
    int currentStep_ = 0;
    int maxSteps_ = 0;
    std::string streamBuffer_;  // accumulates streaming tokens
    bool streaming_ = false;
    std::string progressMessage_;
};

} // namespace gaia

#endif // GAIA_HAS_TUI
