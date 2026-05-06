// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// FTXUI-based reactive TUI console implementation.
// Each print*() method appends a ChatEntry to the internal history.
// getChatElements() and getStatusBar() convert the history to FTXUI Elements
// for embedding in a larger TUI layout.

#ifdef GAIA_HAS_TUI

#include "gaia/tui_console.h"

#include <sstream>

namespace gaia {

using namespace ftxui;

// ---------------------------------------------------------------------------
// Construction / destruction
// ---------------------------------------------------------------------------

TuiConsole::TuiConsole() = default;
TuiConsole::~TuiConsole() = default;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

void TuiConsole::addEntry(ChatEntry::Type type, const std::string& content) {
    std::lock_guard<std::mutex> lock(mutex_);
    entries_.push_back({type, content});
    // Evict oldest entries to prevent unbounded memory growth
    if (entries_.size() > kMaxEntries) {
        entries_.erase(entries_.begin(),
                       entries_.begin() + static_cast<long>(entries_.size() - kMaxEntries));
    }
}

// ---------------------------------------------------------------------------
// OutputHandler: core progress / state
// ---------------------------------------------------------------------------

void TuiConsole::printProcessingStart(const std::string& query, int maxSteps,
                                      const std::string& modelId) {
    std::lock_guard<std::mutex> lock(mutex_);
    currentModel_ = modelId;
    currentStep_ = 0;
    maxSteps_ = maxSteps;
    streamBuffer_.clear();
    streaming_ = false;
    progressMessage_.clear();
    entries_.push_back({ChatEntry::Type::USER, query});
}

void TuiConsole::printStepHeader(int stepNum, int stepLimit) {
    std::lock_guard<std::mutex> lock(mutex_);
    currentStep_ = stepNum;
    maxSteps_ = stepLimit;
}

void TuiConsole::printStateInfo(const std::string& message) {
    if (message.empty()) return;
    addEntry(ChatEntry::Type::INFO, message);
}

void TuiConsole::printThought(const std::string& thought) {
    if (thought.empty()) return;
    addEntry(ChatEntry::Type::INFO, "Thinking: " + thought);
}

void TuiConsole::printGoal(const std::string& goal) {
    if (goal.empty()) return;
    addEntry(ChatEntry::Type::INFO, "Goal: " + goal);
}

void TuiConsole::printPlan(const json& plan, int currentStep) {
    if (!plan.is_array() || plan.empty()) return;

    std::ostringstream oss;
    oss << "Plan (" << plan.size() << " steps):";
    int idx = 0;
    for (const auto& step : plan) {
        std::string marker = (idx == currentStep) ? " >> " : "    ";
        std::string toolName = step.value("tool", "???");
        oss << "\n" << marker << (idx + 1) << ". " << toolName;
        ++idx;
    }
    addEntry(ChatEntry::Type::INFO, oss.str());
}

// ---------------------------------------------------------------------------
// OutputHandler: tool execution
// ---------------------------------------------------------------------------

void TuiConsole::printToolUsage(const std::string& toolName) {
    addEntry(ChatEntry::Type::TOOL, "Using tool: " + toolName + "...");
}

void TuiConsole::printToolComplete() {
    addEntry(ChatEntry::Type::TOOL, "Tool completed");
}

void TuiConsole::prettyPrintJson(const json& data, const std::string& title) {
    if (data.empty()) return;
    std::ostringstream oss;
    if (!title.empty()) {
        oss << title << ": ";
    }
    oss << data.dump(2);
    addEntry(ChatEntry::Type::INFO, oss.str());
}

// ---------------------------------------------------------------------------
// OutputHandler: status messages
// ---------------------------------------------------------------------------

void TuiConsole::printError(const std::string& message) {
    if (message.empty()) return;
    addEntry(ChatEntry::Type::ERROR, message);
}

void TuiConsole::printWarning(const std::string& message) {
    if (message.empty()) return;
    addEntry(ChatEntry::Type::WARNING, message);
}

void TuiConsole::printInfo(const std::string& message) {
    if (message.empty()) return;
    addEntry(ChatEntry::Type::INFO, message);
}

// ---------------------------------------------------------------------------
// OutputHandler: progress indicators
// ---------------------------------------------------------------------------

void TuiConsole::startProgress(const std::string& message) {
    std::lock_guard<std::mutex> lock(mutex_);
    progressMessage_ = message;
}

void TuiConsole::stopProgress() {
    std::lock_guard<std::mutex> lock(mutex_);
    progressMessage_.clear();
}

// ---------------------------------------------------------------------------
// OutputHandler: completion
// ---------------------------------------------------------------------------

void TuiConsole::printFinalAnswer(const std::string& answer) {
    if (answer.empty()) return;
    addEntry(ChatEntry::Type::ASSISTANT, answer);
}

void TuiConsole::printCompletion(int stepsTaken, int stepsLimit) {
    std::ostringstream oss;
    oss << "Completed in " << stepsTaken << "/" << stepsLimit << " steps";
    addEntry(ChatEntry::Type::INFO, oss.str());
}

void TuiConsole::printDecisionMenu(const std::vector<Decision>& decisions) {
    if (decisions.empty()) return;

    std::ostringstream oss;
    oss << "Choose an option:";
    for (size_t i = 0; i < decisions.size(); ++i) {
        oss << "\n  [" << (i + 1) << "] " << decisions[i].label;
        if (!decisions[i].description.empty()) {
            oss << " - " << decisions[i].description;
        }
    }
    addEntry(ChatEntry::Type::INFO, oss.str());
}

// ---------------------------------------------------------------------------
// OutputHandler: streaming
// ---------------------------------------------------------------------------

void TuiConsole::printStreamToken(const std::string& token) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!streaming_) {
        // Start a new assistant entry for streaming
        entries_.push_back({ChatEntry::Type::ASSISTANT, ""});
        streaming_ = true;
        streamBuffer_.clear();
    }
    streamBuffer_ += token;
    // Update the last entry's content with accumulated tokens
    if (!entries_.empty()) {
        entries_.back().content = streamBuffer_;
    }
}

void TuiConsole::printStreamEnd() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (streaming_ && !entries_.empty()) {
        entries_.back().content = streamBuffer_;
    }
    streaming_ = false;
    streamBuffer_.clear();
}

// ---------------------------------------------------------------------------
// FTXUI element accessors
// ---------------------------------------------------------------------------

std::vector<Element> TuiConsole::getChatElements() {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<Element> elements;
    elements.reserve(entries_.size());

    for (const auto& entry : entries_) {
        switch (entry.type) {
            case ChatEntry::Type::USER:
                elements.push_back(
                    hbox(text("> ") | bold, paragraph(entry.content))
                );
                break;

            case ChatEntry::Type::ASSISTANT:
                elements.push_back(renderMarkdown(entry.content));
                break;

            case ChatEntry::Type::TOOL:
                elements.push_back(text(entry.content) | dim);
                break;

            case ChatEntry::Type::INFO:
                elements.push_back(
                    text(entry.content) | color(Color::Blue)
                );
                break;

            case ChatEntry::Type::ERROR:
                elements.push_back(
                    text("Error: " + entry.content) | color(Color::Red) | bold
                );
                break;

            case ChatEntry::Type::WARNING:
                elements.push_back(
                    text("Warning: " + entry.content) | color(Color::Yellow)
                );
                break;
        }
    }

    // Append progress indicator if active
    if (!progressMessage_.empty()) {
        elements.push_back(
            text(progressMessage_ + "...") | dim | blink
        );
    }

    return elements;
}

Element TuiConsole::getStatusBar() {
    std::lock_guard<std::mutex> lock(mutex_);
    return hbox(
        text(currentModel_.empty() ? "model" : currentModel_) | bold,
        separator(),
        text("step " + std::to_string(currentStep_) + "/" + std::to_string(maxSteps_))
    );
}

} // namespace gaia

#endif // GAIA_HAS_TUI
