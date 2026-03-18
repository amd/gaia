// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Clean console output handler for agent examples.
// Provides polished terminal UI with ANSI colors, word-wrapping, tool output
// previews, and bordered final-answer sections.
//
// Usage:
//   agent.setOutputHandler(std::make_unique<gaia::CleanConsole>());
//
// Extend for domain-specific formatting:
//   class MyConsole : public gaia::CleanConsole {
//       void printThought(const std::string& t) override { ... }
//   };

#pragma once

#include <string>

#include "gaia/console.h"
#include "gaia/export.h"

namespace gaia {

// ---------------------------------------------------------------------------
// ANSI color constants — shared by CleanConsole and TUI helpers
// ---------------------------------------------------------------------------
namespace color {
    constexpr const char* RESET   = "\033[0m";
    constexpr const char* BOLD    = "\033[1m";
    constexpr const char* DIM     = "\033[2m";
    constexpr const char* ITALIC  = "\033[3m";
    constexpr const char* UNDERLN = "\033[4m";
    constexpr const char* GRAY    = "\033[90m";
    constexpr const char* RED     = "\033[91m";
    constexpr const char* GREEN   = "\033[92m";
    constexpr const char* YELLOW  = "\033[93m";
    constexpr const char* BLUE    = "\033[94m";
    constexpr const char* MAGENTA = "\033[95m";
    constexpr const char* CYAN    = "\033[96m";
    constexpr const char* WHITE   = "\033[97m";
    // Background
    constexpr const char* BG_BLUE = "\033[44m";
} // namespace color

// ---------------------------------------------------------------------------
// CleanConsole — nicely formatted progress with tool output summaries
// ---------------------------------------------------------------------------
class GAIA_API CleanConsole : public OutputHandler {
public:
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
    void printFinalAnswer(const std::string& answer) override;
    void printCompletion(int stepsTaken, int stepsLimit) override;
    void printDecisionMenu(const std::vector<Decision>& decisions) override;

protected:
    /// Render **bold** markers as ANSI bold+white, then restore prevColor.
    static void printStyledWord(const std::string& word, const char* prevColor);

    /// Print text with word-wrapping at the given width, indented by indent spaces.
    static void printWrapped(const std::string& text, size_t width, size_t indent,
                             const char* prevColor = color::RESET);

    /// Print a compact preview of command output (up to 10 lines).
    void printOutputPreview(const std::string& output);

    int stepNum_ = 0;
    int stepLimit_ = 0;
    int toolsRun_ = 0;
    bool planShown_ = false;
    std::string lastToolName_;
    std::string lastGoal_;
};

} // namespace gaia
