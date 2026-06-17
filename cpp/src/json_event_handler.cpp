// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include "gaia/json_event_handler.h"

#include <iostream>

namespace gaia {

// ---------------------------------------------------------------------------
// Core emit — one JSON object per line, flushed immediately
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::emit(const json& event) {
    std::lock_guard<std::mutex> lock(mutex_);
    std::cout << event.dump(-1, ' ', false, json::error_handler_t::replace)
              << "\n" << std::flush;
}

// ---------------------------------------------------------------------------
// Core Progress/State
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::printProcessingStart(const std::string& /*query*/,
                                                   int maxSteps,
                                                   const std::string& /*modelId*/) {
    // Reset counters for the new query.
    stepsTaken_ = 0;
    stepsLimit_ = maxSteps;
    toolsUsed_ = 0;
    currentTool_.clear();
    // No event emitted — matches Python SSEOutputHandler behavior.
}

void JsonEventOutputHandler::printStepHeader(int stepNum, int stepLimit) {
    stepsTaken_ = stepNum;
    stepsLimit_ = stepLimit;
    emit({{"type", "step"},
          {"step", stepNum},
          {"total", stepLimit},
          {"status", "started"}});
}

void JsonEventOutputHandler::printStateInfo(const std::string& message) {
    emit({{"type", "status"},
          {"status", "warning"},
          {"message", message}});
}

void JsonEventOutputHandler::printThought(const std::string& thought) {
    if (thought.empty()) return;
    emit({{"type", "thinking"},
          {"content", thought}});
}

void JsonEventOutputHandler::printGoal(const std::string& goal) {
    if (goal.empty()) return;
    emit({{"type", "status"},
          {"status", "working"},
          {"message", goal}});
}

void JsonEventOutputHandler::printPlan(const json& plan, int currentStep) {
    emit({{"type", "plan"},
          {"steps", plan},
          {"current_step", currentStep}});
}

// ---------------------------------------------------------------------------
// Tool Execution
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::printToolUsage(const std::string& toolName) {
    currentTool_ = toolName;
    ++toolsUsed_;
    emit({{"type", "tool_start"},
          {"tool", toolName}});
}

void JsonEventOutputHandler::printToolComplete() {
    emit({{"type", "tool_end"},
          {"success", true}});
}

void JsonEventOutputHandler::prettyPrintJson(const json& data,
                                              const std::string& title) {
    if (title == "Tool Args") {
        // Emit tool_args with the full argument object.
        emit({{"type", "tool_args"},
              {"tool", currentTool_},
              {"args", data}});
    } else if (title == "Tool Result") {
        // Build a tool_result event from the result JSON.
        json event = {
            {"type", "tool_result"},
            {"title", currentTool_},
            {"success", data.value("status", "success") != "error"}
        };

        // Include command_output if the tool result has stdout/stderr.
        if (data.contains("stdout") || data.contains("stderr") || data.contains("output")) {
            json cmdOutput;
            if (data.contains("stdout")) cmdOutput["stdout"] = data["stdout"];
            if (data.contains("stderr")) cmdOutput["stderr"] = data["stderr"];
            if (data.contains("output")) cmdOutput["output"] = data["output"];
            event["command_output"] = cmdOutput;
        }

        // Summary: prefer error message, then a short description.
        if (data.contains("error")) {
            event["summary"] = data["error"];
        } else if (data.contains("stdout") && data["stdout"].is_string()) {
            const auto& out = data["stdout"].get_ref<const std::string&>();
            event["summary"] = out.size() > 200 ? out.substr(0, 200) + "..." : out;
        } else {
            event["summary"] = data.value("status", "completed");
        }

        event["result_data"] = data;
        emit(event);
    } else {
        // Generic JSON output — emit as status info.
        emit({{"type", "status"},
              {"status", "info"},
              {"message", data.dump()}});
    }
}

// ---------------------------------------------------------------------------
// Status Messages
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::printError(const std::string& message) {
    emit({{"type", "agent_error"},
          {"content", message}});
}

void JsonEventOutputHandler::printWarning(const std::string& message) {
    emit({{"type", "status"},
          {"status", "warning"},
          {"message", message}});
}

void JsonEventOutputHandler::printInfo(const std::string& message) {
    emit({{"type", "status"},
          {"status", "info"},
          {"message", message}});
}

// ---------------------------------------------------------------------------
// Progress Indicators
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::startProgress(const std::string& message) {
    emit({{"type", "status"},
          {"status", "working"},
          {"message", message}});
}

void JsonEventOutputHandler::stopProgress() {
    // No event — progress end is implicit when the next event arrives.
}

// ---------------------------------------------------------------------------
// Completion
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::printFinalAnswer(const std::string& answer,
                                               const UsageStats& usage) {
    json event = {{"type", "answer"},
                  {"content", answer},
                  {"steps", stepsTaken_},
                  {"tools_used", toolsUsed_}};
    if (usage.totalTokens > 0) {
        event["usage"] = usage.toJson();
    }
    emit(event);
}

void JsonEventOutputHandler::printCompletion(int stepsTaken, int stepsLimit) {
    emit({{"type", "status"},
          {"status", "complete"},
          {"steps", stepsTaken},
          {"total", stepsLimit}});
}

// ---------------------------------------------------------------------------
// Streaming
// ---------------------------------------------------------------------------

void JsonEventOutputHandler::printStreamToken(const std::string& token) {
    emit({{"type", "chunk"},
          {"content", token}});
}

void JsonEventOutputHandler::printStreamEnd() {
    // No event — stream end is signaled by the answer event.
}

} // namespace gaia
