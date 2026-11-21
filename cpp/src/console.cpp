// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/console.h"

#include <iomanip>
#include <iostream>
#include <sstream>

namespace gaia {

std::string sanitizeTerminalText(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    const size_t n = text.size();
    size_t i = 0;
    while (i < n) {
        const unsigned char c = static_cast<unsigned char>(text[i]);
        if (c == 0x1B) {
            ++i;
            if (i < n && text[i] == '[') {
                ++i;
                while (i < n && static_cast<unsigned char>(text[i]) >= 0x20 &&
                       static_cast<unsigned char>(text[i]) <= 0x3F) {
                    ++i;
                }
                if (i < n && static_cast<unsigned char>(text[i]) >= 0x40 &&
                    static_cast<unsigned char>(text[i]) <= 0x7E) {
                    ++i;
                }
            } else if (i < n && text[i] == ']') {
                ++i;
                while (i < n && text[i] != 0x07 && text[i] != 0x1B && text[i] != '\n') ++i;
                if (i < n && text[i] == 0x07) {
                    ++i;
                } else if (i + 1 < n && text[i] == 0x1B && text[i + 1] == '\\') {
                    i += 2;
                }
            }
            continue;
        }
        if (c == 0xC2 && i + 1 < n && static_cast<unsigned char>(text[i + 1]) >= 0x80 &&
            static_cast<unsigned char>(text[i + 1]) <= 0x9F) {
            i += 2;
            continue;
        }
        if ((c < 0x20 && c != '\n' && c != '\t') || c == 0x7F) {
            ++i;
            continue;
        }
        out.push_back(text[i]);
        ++i;
    }
    return out;
}

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
        std::cout << MAGENTA << "Thought: " << RESET << sanitizeTerminalText(thought) << "\n";
    }
}

void TerminalConsole::printGoal(const std::string& goal) {
    if (!goal.empty()) {
        std::cout << CYAN << "Goal: " << RESET << sanitizeTerminalText(goal) << "\n";
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

        if (plan[i].is_object() && plan[i].contains("tool") && plan[i]["tool"].is_string()) {
            std::cout << sanitizeTerminalText(plan[i]["tool"].get<std::string>());
        } else {
            std::cout << sanitizeTerminalText(
                plan[i].dump(-1, ' ', false, json::error_handler_t::replace));
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

    std::string formatted =
        sanitizeTerminalText(data.dump(2, ' ', false, json::error_handler_t::replace));

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

void TerminalConsole::printFinalAnswer(const std::string& answer,
                                       const UsageStats& /*usage*/) {
    std::cout << "\n" << BOLD << GREEN << "Answer:" << RESET << "\n"
              << sanitizeTerminalText(answer) << "\n";
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

void TerminalConsole::printStreamToken(const std::string& token) {
    std::cout << sanitizeTerminalText(token) << std::flush;
}

void TerminalConsole::printStreamEnd() {
    std::cout << "\n";
}

// ---- SilentConsole ----

void SilentConsole::printFinalAnswer(const std::string& answer,
                                     const UsageStats& /*usage*/) {
    if (!silenceFinalAnswer_) {
        std::cout << sanitizeTerminalText(answer) << "\n";
    }
}

} // namespace gaia
