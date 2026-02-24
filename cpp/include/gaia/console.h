// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// Console output system for agent display.
// Ported from Python: src/gaia/agents/base/console.py
//
// Python uses an abstract OutputHandler with AgentConsole (Rich) and SilentConsole.
// C++ uses an abstract OutputHandler with TerminalConsole (ANSI) and SilentConsole.

#pragma once

#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace gaia {

using json = nlohmann::json;

/// Abstract output handler interface.
/// Mirrors Python OutputHandler ABC with 15+ required methods.
class OutputHandler {
public:
    virtual ~OutputHandler() = default;

    // === Core Progress/State Methods ===
    virtual void printProcessingStart(const std::string& query, int maxSteps,
                                      const std::string& modelId = "") = 0;
    virtual void printStepHeader(int stepNum, int stepLimit) = 0;
    virtual void printStateInfo(const std::string& message) = 0;
    virtual void printThought(const std::string& thought) = 0;
    virtual void printGoal(const std::string& goal) = 0;
    virtual void printPlan(const json& plan, int currentStep = -1) = 0;

    // === Tool Execution Methods ===
    virtual void printToolUsage(const std::string& toolName) = 0;
    virtual void printToolComplete() = 0;
    virtual void prettyPrintJson(const json& data, const std::string& title = "") = 0;

    // === Status Messages ===
    virtual void printError(const std::string& message) = 0;
    virtual void printWarning(const std::string& message) = 0;
    virtual void printInfo(const std::string& message) = 0;

    // === Progress Indicators ===
    virtual void startProgress(const std::string& message) = 0;
    virtual void stopProgress() = 0;

    // === Completion Methods ===
    virtual void printFinalAnswer(const std::string& answer) = 0;
    virtual void printCompletion(int stepsTaken, int stepsLimit) = 0;

    // === Optional Methods (default no-op) ===
    virtual void printPrompt(const std::string& /*prompt*/, const std::string& /*title*/ = "Prompt") {}
    virtual void printResponse(const std::string& /*response*/, const std::string& /*title*/ = "Response") {}
    virtual void printHeader(const std::string& /*text*/) {}
    virtual void printSeparator(int /*length*/ = 50) {}
    virtual void printToolInfo(const std::string& /*name*/, const std::string& /*params*/,
                               const std::string& /*description*/) {}
};

/// Terminal console with ANSI color output.
/// Simplified version of Python's AgentConsole (without Rich dependency).
class TerminalConsole : public OutputHandler {
public:
    void printProcessingStart(const std::string& query, int maxSteps,
                              const std::string& modelId = "") override;
    void printStepHeader(int stepNum, int stepLimit) override;
    void printStateInfo(const std::string& message) override;
    void printThought(const std::string& thought) override;
    void printGoal(const std::string& goal) override;
    void printPlan(const json& plan, int currentStep = -1) override;
    void printToolUsage(const std::string& toolName) override;
    void printToolComplete() override;
    void prettyPrintJson(const json& data, const std::string& title = "") override;
    void printError(const std::string& message) override;
    void printWarning(const std::string& message) override;
    void printInfo(const std::string& message) override;
    void startProgress(const std::string& message) override;
    void stopProgress() override;
    void printFinalAnswer(const std::string& answer) override;
    void printCompletion(int stepsTaken, int stepsLimit) override;
    void printHeader(const std::string& text) override;
    void printSeparator(int length = 50) override;
    void printToolInfo(const std::string& name, const std::string& params,
                       const std::string& description) override;

private:
    // ANSI color codes
    static constexpr const char* RESET   = "\033[0m";
    static constexpr const char* BOLD    = "\033[1m";
    static constexpr const char* DIM     = "\033[90m";
    static constexpr const char* RED     = "\033[91m";
    static constexpr const char* GREEN   = "\033[92m";
    static constexpr const char* YELLOW  = "\033[93m";
    static constexpr const char* BLUE    = "\033[94m";
    static constexpr const char* MAGENTA = "\033[95m";
    static constexpr const char* CYAN    = "\033[96m";
};

/// Silent console that suppresses all output.
/// Used for testing and JSON-only operation.
/// Mirrors Python SilentConsole.
class SilentConsole : public OutputHandler {
public:
    explicit SilentConsole(bool silenceFinalAnswer = false)
        : silenceFinalAnswer_(silenceFinalAnswer) {}

    void printProcessingStart(const std::string&, int, const std::string&) override {}
    void printStepHeader(int, int) override {}
    void printStateInfo(const std::string&) override {}
    void printThought(const std::string&) override {}
    void printGoal(const std::string&) override {}
    void printPlan(const json&, int) override {}
    void printToolUsage(const std::string&) override {}
    void printToolComplete() override {}
    void prettyPrintJson(const json&, const std::string&) override {}
    void printError(const std::string&) override {}
    void printWarning(const std::string&) override {}
    void printInfo(const std::string&) override {}
    void startProgress(const std::string&) override {}
    void stopProgress() override {}
    void printFinalAnswer(const std::string& answer) override;
    void printCompletion(int, int) override {}

private:
    bool silenceFinalAnswer_;
};

} // namespace gaia
