// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT
//
// JSON-line event output handler for TUI / WebUI integration.
//
// Emits one JSON object per line to stdout, following the SSE event protocol
// defined in docs/plans/cpp-webui-integration.md. Each OutputHandler method
// maps to a single JSONL event.
//
// Usage:
//   agent.setOutputHandler(std::make_unique<gaia::JsonEventOutputHandler>());
//   agent.config().structuredEvents = true;
//   agent.config().streaming = false;  // avoid raw JSON tokens

#pragma once

#include <iostream>
#include <mutex>
#include <string>

#include "gaia/console.h"
#include "gaia/export.h"

namespace gaia {

/// Emits structured JSONL events to stdout for consumption by gaia-tui
/// or the Python CppAgentBackend subprocess bridge.
///
/// Thread-safe: all emit() calls are serialized via mutex.
class GAIA_API JsonEventOutputHandler : public OutputHandler {
public:
    // === Core Progress/State ===
    void printProcessingStart(const std::string& query, int maxSteps,
                              const std::string& modelId) override;
    void printStepHeader(int stepNum, int stepLimit) override;
    void printStateInfo(const std::string& message) override;
    void printThought(const std::string& thought) override;
    void printGoal(const std::string& goal) override;
    void printPlan(const json& plan, int currentStep) override;

    // === Tool Execution ===
    void printToolUsage(const std::string& toolName) override;
    void printToolComplete() override;
    void prettyPrintJson(const json& data, const std::string& title) override;

    // === Status Messages ===
    void printError(const std::string& message) override;
    void printWarning(const std::string& message) override;
    void printInfo(const std::string& message) override;

    // === Progress Indicators ===
    void startProgress(const std::string& message) override;
    void stopProgress() override;

    // === Completion ===
    void printFinalAnswer(const std::string& answer,
                          const UsageStats& usage = {}) override;
    void printCompletion(int stepsTaken, int stepsLimit) override;

    // === Streaming ===
    void printStreamToken(const std::string& token) override;
    void printStreamEnd() override;

private:
    /// Write a JSON object as a single line to stdout.
    void emit(const json& event);

    std::string currentTool_;
    int stepsTaken_ = 0;
    int stepsLimit_ = 0;
    int toolsUsed_ = 0;
    std::mutex mutex_;
};

} // namespace gaia
