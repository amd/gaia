// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/clean_console.h"

#include <iostream>
#include <sstream>

namespace gaia {

void CleanConsole::printProcessingStart(const std::string& /*query*/, int /*maxSteps*/,
                                        const std::string& /*modelId*/) {
    std::cout << std::endl;
    planShown_ = false;
    toolsRun_ = 0;
    lastGoal_.clear();
}

void CleanConsole::printStepHeader(int stepNum, int stepLimit) {
    stepNum_ = stepNum;
    stepLimit_ = stepLimit;
}

void CleanConsole::printStateInfo(const std::string& /*message*/) {}

void CleanConsole::printThought(const std::string& thought) {
    if (thought.empty()) return;

    // Look for structured FINDING:/DECISION: reasoning format
    auto findingPos = thought.find("FINDING:");
    if (findingPos == std::string::npos) findingPos = thought.find("Finding:");
    auto decisionPos = thought.find("DECISION:");
    if (decisionPos == std::string::npos) decisionPos = thought.find("Decision:");

    if (findingPos != std::string::npos || decisionPos != std::string::npos) {
        // Structured reasoning: parse and color-code
        if (findingPos != std::string::npos) {
            size_t start = findingPos + 8; // skip "FINDING:"
            size_t end = (decisionPos != std::string::npos) ? decisionPos : thought.size();
            std::string text = thought.substr(start, end - start);
            size_t f = text.find_first_not_of(" \t\n\r");
            size_t l = text.find_last_not_of(" \t\n\r");
            if (f != std::string::npos) text = text.substr(f, l - f + 1);

            std::cout << color::GREEN << color::BOLD << "  Finding: "
                      << color::RESET;
            printWrapped(text, 79, 11);
        }
        if (decisionPos != std::string::npos) {
            size_t start = decisionPos + 9; // skip "DECISION:"
            std::string text = thought.substr(start);
            size_t f = text.find_first_not_of(" \t\n\r");
            size_t l = text.find_last_not_of(" \t\n\r");
            if (f != std::string::npos) text = text.substr(f, l - f + 1);

            std::cout << color::YELLOW << color::BOLD << "  Decision: "
                      << color::RESET;
            printWrapped(text, 78, 12);
        }
    } else {
        // Fallback: Analysis/Thinking display
        if (toolsRun_ > 0) {
            std::cout << color::BLUE << color::BOLD << "  Analysis: "
                      << color::RESET;
        } else {
            std::cout << color::MAGENTA << "  Thinking: " << color::RESET;
        }
        printWrapped(thought, 78, 12);
    }
}

void CleanConsole::printGoal(const std::string& goal) {
    if (goal.empty() || goal == lastGoal_) return;
    lastGoal_ = goal;
    std::cout << std::endl;
    std::cout << color::CYAN << color::ITALIC
              << "  Goal: " << color::RESET;
    printWrapped(goal, 82, 8);
}

void CleanConsole::printPlan(const json& plan, int /*currentStep*/) {
    if (planShown_ || !plan.is_array()) return;
    planShown_ = true;
    std::cout << color::BOLD << color::CYAN << "  Plan: " << color::RESET;
    for (size_t i = 0; i < plan.size(); ++i) {
        if (i > 0) std::cout << color::GRAY << " -> " << color::RESET;
        if (plan[i].is_object() && plan[i].contains("tool")) {
            std::cout << color::CYAN
                      << plan[i]["tool"].get<std::string>()
                      << color::RESET;
        }
    }
    std::cout << std::endl;
}

void CleanConsole::printToolUsage(const std::string& toolName) {
    lastToolName_ = toolName;
    std::cout << std::endl;
    std::cout << color::YELLOW << color::BOLD
              << "  [" << stepNum_ << "/" << stepLimit_ << "] "
              << toolName << color::RESET << std::endl;
}

void CleanConsole::printToolComplete() {
    ++toolsRun_;
}

void CleanConsole::prettyPrintJson(const json& data,
                                   const std::string& title) {
    // Show tool arguments (the command being sent)
    if (title == "Tool Args" && data.is_object() && !data.empty()) {
        std::string argsStr;
        bool first = true;
        for (auto& [key, val] : data.items()) {
            if (!first) argsStr += ", ";
            argsStr += key + "=";
            if (val.is_string()) argsStr += val.get<std::string>();
            else argsStr += val.dump();
            first = false;
        }
        std::cout << color::GRAY << "      Args: ";
        printWrapped(argsStr, 78, 12);
        std::cout << color::RESET;
        return;
    }

    if (title != "Tool Result" || !data.is_object()) return;

    // Show the command that was executed
    if (data.contains("command")) {
        std::string cmd = data["command"].get<std::string>();
        std::cout << color::CYAN << "      Cmd: " << color::RESET
                  << color::GRAY;
        printWrapped(cmd, 79, 11);
        std::cout << color::RESET;
    }

    // Show error if present
    if (data.contains("error")) {
        std::cout << color::RED << color::BOLD << "      Error: "
                  << color::RESET << color::RED
                  << data["error"].get<std::string>()
                  << color::RESET << std::endl;
        return;
    }

    // Show tool output preview
    if (data.contains("output")) {
        std::string output = data["output"].get<std::string>();
        if (output.empty() || output.find("(no output)") != std::string::npos) {
            std::cout << color::GREEN << "      Result: "
                      << color::RESET << color::GRAY << "(no output)"
                      << color::RESET << std::endl;
            return;
        }
        std::cout << color::GREEN << "      Output:" << color::RESET
                  << std::endl;
        printOutputPreview(output);
    }

    // Show status
    if (data.contains("status")) {
        auto status = data["status"].get<std::string>();
        const char* statusColor = (status == "completed")
            ? color::GREEN : color::YELLOW;
        std::cout << statusColor << "      Status: " << status
                  << color::RESET << std::endl;
    }
}

void CleanConsole::printError(const std::string& message) {
    std::cout << color::RED << color::BOLD << "  ERROR: " << color::RESET
              << color::RED;
    printWrapped(message, 81, 9);
    std::cout << color::RESET;
}

void CleanConsole::printWarning(const std::string& message) {
    std::cout << color::YELLOW << "  WARNING: " << color::RESET
              << message << std::endl;
}

void CleanConsole::printInfo(const std::string& /*message*/) {}

void CleanConsole::startProgress(const std::string& /*message*/) {}

void CleanConsole::stopProgress() {}

void CleanConsole::printFinalAnswer(const std::string& answer) {
    if (answer.empty()) return;

    // Extract clean text — the LLM sometimes returns raw JSON
    std::string cleanAnswer = answer;
    if (!answer.empty() && answer.front() == '{') {
        try {
            auto j = json::parse(answer);
            if (j.is_object()) {
                if (j.contains("answer") && j["answer"].is_string()) {
                    cleanAnswer = j["answer"].get<std::string>();
                } else if (j.contains("thought") && j["thought"].is_string()) {
                    cleanAnswer = j["thought"].get<std::string>();
                }
            }
        } catch (...) {
            // Not valid JSON — use as-is
        }
    }

    std::cout << std::endl;
    std::cout << color::GREEN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    std::cout << color::GREEN << color::BOLD
              << "  Conclusion" << color::RESET << std::endl;
    std::cout << color::GREEN
              << "  ========================================================================================"
              << color::RESET << std::endl;
    // Print each line of the answer word-wrapped
    std::string line;
    std::istringstream stream(cleanAnswer);
    while (std::getline(stream, line)) {
        if (line.empty()) {
            std::cout << std::endl;
        } else {
            std::cout << "  ";
            printWrapped(line, 88, 2);
        }
    }
    std::cout << color::GREEN
              << "  ========================================================================================"
              << color::RESET << std::endl;
}

void CleanConsole::printCompletion(int stepsTaken, int /*stepsLimit*/) {
    std::cout << color::GRAY << "  Completed in " << stepsTaken
              << " steps" << color::RESET << std::endl;
}

// ---------------------------------------------------------------------------
// Protected helpers
// ---------------------------------------------------------------------------

void CleanConsole::printStyledWord(const std::string& word, const char* prevColor) {
    size_t pos = 0;
    while (pos < word.size()) {
        auto boldStart = word.find("**", pos);
        if (boldStart == std::string::npos) {
            std::cout << word.substr(pos);
            break;
        }
        // Print text before **
        std::cout << word.substr(pos, boldStart - pos);
        auto boldEnd = word.find("**", boldStart + 2);
        if (boldEnd == std::string::npos) {
            // Unmatched ** — print literally
            std::cout << word.substr(boldStart);
            break;
        }
        // Print bold content
        std::cout << color::BOLD << color::WHITE
                  << word.substr(boldStart + 2, boldEnd - boldStart - 2)
                  << color::RESET << prevColor;
        pos = boldEnd + 2;
    }
}

void CleanConsole::printWrapped(const std::string& text, size_t width, size_t indent,
                                const char* prevColor) {
    std::string indentStr(indent, ' ');
    std::istringstream words(text);
    std::string word;
    size_t col = 0;
    bool firstWord = true;
    while (words >> word) {
        // Strip ** for length calculation
        std::string plain = word;
        size_t p;
        while ((p = plain.find("**")) != std::string::npos)
            plain.erase(p, 2);

        if (!firstWord && col + 1 + plain.size() > width) {
            std::cout << std::endl << indentStr;
            col = 0;
        } else if (!firstWord) {
            std::cout << ' ';
            ++col;
        }
        printStyledWord(word, prevColor);
        col += plain.size();
        firstWord = false;
    }
    std::cout << color::RESET << std::endl;
}

void CleanConsole::printOutputPreview(const std::string& output) {
    constexpr int kMaxPreviewLines = 10;
    std::istringstream stream(output);
    std::string line;
    int lineCount = 0;
    int totalLines = 0;

    // Count total non-empty lines
    {
        std::istringstream counter(output);
        std::string tmp;
        while (std::getline(counter, tmp)) {
            if (!tmp.empty() && tmp.find_first_not_of(" \t\r\n") != std::string::npos)
                ++totalLines;
        }
    }

    std::cout << color::GRAY << "      .------------------------------------------------------------------------------------"
              << color::RESET << std::endl;
    while (std::getline(stream, line) && lineCount < kMaxPreviewLines) {
        // Skip empty lines
        if (line.empty() || line.find_first_not_of(" \t\r\n") == std::string::npos)
            continue;
        // Trim trailing \r
        if (!line.empty() && line.back() == '\r') line.pop_back();
        // Truncate long lines
        if (line.size() > 82) line = line.substr(0, 79) + "...";
        std::cout << color::GRAY << "      | " << line << color::RESET
                  << std::endl;
        ++lineCount;
    }
    if (totalLines > kMaxPreviewLines) {
        std::cout << color::GRAY << "      | ... ("
                  << (totalLines - kMaxPreviewLines)
                  << " more lines)" << color::RESET << std::endl;
    }
    std::cout << color::GRAY << "      '------------------------------------------------------------------------------------"
              << color::RESET << std::endl;
}

} // namespace gaia
