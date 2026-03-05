// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/console.h"

#include <iomanip>
#include <iostream>
#include <sstream>

namespace gaia {

// ---- TerminalConsole ----

void TerminalConsole::printProcessingStart(const std::string& query, int maxSteps,
                                           const std::string& modelId) {
    std::cout << "\n" << BOLD << CYAN << "Processing query" << RESET << ": " << query << "\n";
    std::cout << DIM << "Max steps: " << maxSteps;
    if (!modelId.empty()) {
        std::cout << " | Model: " << modelId;
    }
    std::cout << RESET << "\n\n";
}

void TerminalConsole::printStepHeader(int stepNum, int stepLimit) {
    std::cout << BOLD << BLUE << "--- Step " << stepNum << "/" << stepLimit
              << " ---" << RESET << "\n";
}

void TerminalConsole::printStateInfo(const std::string& message) {
    std::cout << DIM << "[" << message << "]" << RESET << "\n";
}

void TerminalConsole::printThought(const std::string& thought) {
    if (!thought.empty()) {
        std::cout << MAGENTA << "Thought: " << RESET << thought << "\n";
    }
}

void TerminalConsole::printGoal(const std::string& goal) {
    if (!goal.empty()) {
        std::cout << CYAN << "Goal: " << RESET << goal << "\n";
    }
}

void TerminalConsole::printPlan(const json& plan, int currentStep) {
    if (!plan.is_array()) return;

    std::cout << BOLD << "Plan:" << RESET << "\n";
    for (size_t i = 0; i < plan.size(); ++i) {
        bool isCurrent = (static_cast<int>(i) == currentStep);
        const char* marker = isCurrent ? ">>>" : "   ";
        const char* color = isCurrent ? GREEN : DIM;

        std::cout << color << marker << " Step " << (i + 1) << ": ";

        if (plan[i].is_object() && plan[i].contains("tool")) {
            std::cout << plan[i]["tool"].get<std::string>();
        } else {
            std::cout << plan[i].dump();
        }
        std::cout << RESET << "\n";
    }
}

void TerminalConsole::printToolUsage(const std::string& toolName) {
    std::cout << YELLOW << "Tool: " << BOLD << toolName << RESET << "\n";
}

void TerminalConsole::printToolComplete() {
    std::cout << GREEN << "Tool completed." << RESET << "\n";
}

void TerminalConsole::prettyPrintJson(const json& data, const std::string& title) {
    if (!title.empty()) {
        std::cout << DIM << title << ":" << RESET << "\n";
    }

    std::string formatted = data.dump(2);

    // Truncate if very long
    if (formatted.size() > 2000) {
        formatted = formatted.substr(0, 1000) + "\n...[truncated]...\n" +
                    formatted.substr(formatted.size() - 500);
    }

    std::cout << formatted << "\n";
}

void TerminalConsole::printError(const std::string& message) {
    std::cout << RED << "ERROR: " << RESET << message << "\n";
}

void TerminalConsole::printWarning(const std::string& message) {
    std::cout << YELLOW << "WARNING: " << RESET << message << "\n";
}

void TerminalConsole::printInfo(const std::string& message) {
    std::cout << BLUE << "INFO: " << RESET << message << "\n";
}

void TerminalConsole::startProgress(const std::string& message) {
    std::cout << DIM << message << "..." << RESET << std::flush;
}

void TerminalConsole::stopProgress() {
    std::cout << "\n";
}

void TerminalConsole::printFinalAnswer(const std::string& answer) {
    std::cout << "\n" << BOLD << GREEN << "Answer:" << RESET << "\n" << answer << "\n";
}

void TerminalConsole::printCompletion(int stepsTaken, int stepsLimit) {
    std::cout << "\n" << DIM << "Completed in " << stepsTaken << "/" << stepsLimit
              << " steps." << RESET << "\n";
}

void TerminalConsole::printHeader(const std::string& text) {
    std::cout << "\n" << BOLD << text << RESET << "\n";
}

void TerminalConsole::printSeparator(int length) {
    std::cout << std::string(static_cast<size_t>(length), '-') << "\n";
}

void TerminalConsole::printToolInfo(const std::string& name, const std::string& params,
                                    const std::string& description) {
    std::cout << BOLD << name << RESET << "(" << params << ")\n"
              << DIM << "  " << description << RESET << "\n\n";
}

// ---- SilentConsole ----

void SilentConsole::printFinalAnswer(const std::string& answer) {
    if (!silenceFinalAnswer_) {
        std::cout << answer << "\n";
    }
}

} // namespace gaia
