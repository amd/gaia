// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/json_event_handler.h>

#include <sstream>

using namespace gaia;
using json = nlohmann::json;

// ---------------------------------------------------------------------------
// RAII helper to capture stdout into an ostringstream.
// ---------------------------------------------------------------------------
class CoutCapture {
public:
    CoutCapture() : captured_(), oldBuf_(std::cout.rdbuf(captured_.rdbuf())) {}
    ~CoutCapture() { std::cout.rdbuf(oldBuf_); }

    std::string str() const { return captured_.str(); }

    /// Parse the captured output as one or more JSONL lines.
    /// Returns a vector of parsed JSON objects.
    std::vector<json> lines() const {
        std::vector<json> result;
        std::istringstream iss(captured_.str());
        std::string line;
        while (std::getline(iss, line)) {
            if (!line.empty()) {
                result.push_back(json::parse(line));
            }
        }
        return result;
    }

    /// Parse the first (and usually only) JSONL line.
    json first() const {
        auto l = lines();
        EXPECT_FALSE(l.empty()) << "Expected at least one JSONL line, got none";
        return l.empty() ? json{} : l[0];
    }

private:
    std::ostringstream captured_;
    std::streambuf* oldBuf_;
};

// ===========================================================================
// Step Events
// ===========================================================================

TEST(JsonEventHandlerTest, StepHeader) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printStepHeader(3, 10);
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "step");
    EXPECT_EQ(ev["step"], 3);
    EXPECT_EQ(ev["total"], 10);
    EXPECT_EQ(ev["status"], "started");
}

// ===========================================================================
// Thinking Events
// ===========================================================================

TEST(JsonEventHandlerTest, Thought) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printThought("Analyzing the request...");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "thinking");
    EXPECT_EQ(ev["content"], "Analyzing the request...");
}

TEST(JsonEventHandlerTest, EmptyThoughtSkipped) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printThought("");
    EXPECT_TRUE(cap.str().empty());
}

// ===========================================================================
// Goal / Status Events
// ===========================================================================

TEST(JsonEventHandlerTest, Goal) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printGoal("Check network status");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "working");
    EXPECT_EQ(ev["message"], "Check network status");
}

TEST(JsonEventHandlerTest, EmptyGoalSkipped) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printGoal("");
    EXPECT_TRUE(cap.str().empty());
}

TEST(JsonEventHandlerTest, StateInfo) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printStateInfo("ERROR RECOVERY");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "warning");
    EXPECT_EQ(ev["message"], "ERROR RECOVERY");
}

// ===========================================================================
// Plan Events
// ===========================================================================

TEST(JsonEventHandlerTest, Plan) {
    JsonEventOutputHandler handler;
    json plan = json::array({{{"tool", "bash_execute"}, {"args", "ls"}},
                              {{"tool", "read_file"}, {"args", "foo.txt"}}});
    CoutCapture cap;
    handler.printPlan(plan, 1);
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "plan");
    EXPECT_EQ(ev["steps"].size(), 2);
    EXPECT_EQ(ev["current_step"], 1);
}

// ===========================================================================
// Tool Events
// ===========================================================================

TEST(JsonEventHandlerTest, ToolLifecycle) {
    JsonEventOutputHandler handler;
    CoutCapture cap;

    handler.printToolUsage("bash_execute");
    handler.prettyPrintJson({{"command", "ls -la"}}, "Tool Args");
    handler.printToolComplete();
    handler.prettyPrintJson({{"status", "success"}, {"stdout", "file1\nfile2"}}, "Tool Result");

    auto events = cap.lines();
    ASSERT_EQ(events.size(), 4);

    // tool_start
    EXPECT_EQ(events[0]["type"], "tool_start");
    EXPECT_EQ(events[0]["tool"], "bash_execute");

    // tool_args
    EXPECT_EQ(events[1]["type"], "tool_args");
    EXPECT_EQ(events[1]["tool"], "bash_execute");
    EXPECT_EQ(events[1]["args"]["command"], "ls -la");

    // tool_end
    EXPECT_EQ(events[2]["type"], "tool_end");
    EXPECT_EQ(events[2]["success"], true);

    // tool_result
    EXPECT_EQ(events[3]["type"], "tool_result");
    EXPECT_EQ(events[3]["title"], "bash_execute");
    EXPECT_EQ(events[3]["success"], true);
    EXPECT_TRUE(events[3].contains("command_output"));
    EXPECT_EQ(events[3]["command_output"]["stdout"], "file1\nfile2");
}

TEST(JsonEventHandlerTest, ToolResultError) {
    JsonEventOutputHandler handler;
    CoutCapture cap;

    handler.printToolUsage("bash_execute");
    handler.prettyPrintJson({{"status", "error"}, {"error", "command not found"}}, "Tool Result");

    auto events = cap.lines();
    ASSERT_GE(events.size(), 2);
    auto result = events[1];
    EXPECT_EQ(result["type"], "tool_result");
    EXPECT_EQ(result["success"], false);
    EXPECT_EQ(result["summary"], "command not found");
}

// ===========================================================================
// Status Message Events
// ===========================================================================

TEST(JsonEventHandlerTest, Error) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printError("Something went wrong");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "agent_error");
    EXPECT_EQ(ev["content"], "Something went wrong");
}

TEST(JsonEventHandlerTest, Warning) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printWarning("Running low on context");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "warning");
    EXPECT_EQ(ev["message"], "Running low on context");
}

TEST(JsonEventHandlerTest, Info) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printInfo("Model loaded successfully");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "info");
    EXPECT_EQ(ev["message"], "Model loaded successfully");
}

// ===========================================================================
// Progress Events
// ===========================================================================

TEST(JsonEventHandlerTest, StartProgress) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.startProgress("Executing bash_execute");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "working");
    EXPECT_EQ(ev["message"], "Executing bash_execute");
}

TEST(JsonEventHandlerTest, StopProgressNoEvent) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.stopProgress();
    EXPECT_TRUE(cap.str().empty());
}

// ===========================================================================
// Answer / Completion Events
// ===========================================================================

TEST(JsonEventHandlerTest, FinalAnswer) {
    JsonEventOutputHandler handler;

    // Simulate some steps and tools
    {
        CoutCapture cap;
        handler.printProcessingStart("test query", 10, "model");
        handler.printStepHeader(1, 10);
        handler.printToolUsage("bash_execute");
        handler.printToolComplete();
        handler.printStepHeader(2, 10);
    }

    CoutCapture cap;
    handler.printFinalAnswer("Your WiFi is working correctly.");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "answer");
    EXPECT_EQ(ev["content"], "Your WiFi is working correctly.");
    EXPECT_EQ(ev["steps"], 2);
    EXPECT_EQ(ev["tools_used"], 1);
}

TEST(JsonEventHandlerTest, Completion) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printCompletion(5, 10);
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "complete");
    EXPECT_EQ(ev["steps"], 5);
    EXPECT_EQ(ev["total"], 10);
}

// ===========================================================================
// Streaming Events
// ===========================================================================

TEST(JsonEventHandlerTest, StreamToken) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printStreamToken("Hello");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "chunk");
    EXPECT_EQ(ev["content"], "Hello");
}

TEST(JsonEventHandlerTest, StreamEndNoEvent) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.printStreamEnd();
    EXPECT_TRUE(cap.str().empty());
}

// ===========================================================================
// Processing Reset
// ===========================================================================

TEST(JsonEventHandlerTest, ProcessingStartResetsCounters) {
    JsonEventOutputHandler handler;

    // Run some steps/tools to accumulate counts
    {
        CoutCapture cap;
        handler.printStepHeader(1, 10);
        handler.printToolUsage("bash_execute");
        handler.printToolComplete();
        handler.printStepHeader(2, 10);
    }

    // Reset
    {
        CoutCapture cap;
        handler.printProcessingStart("new query", 20, "model");
        // No event emitted for processingStart
        EXPECT_TRUE(cap.str().empty());
    }

    // Final answer should have reset counters
    CoutCapture cap;
    handler.printFinalAnswer("Result");
    auto ev = cap.first();
    EXPECT_EQ(ev["steps"], 0);
    EXPECT_EQ(ev["tools_used"], 0);
}

// ===========================================================================
// Generic prettyPrintJson (not Tool Args or Tool Result)
// ===========================================================================

TEST(JsonEventHandlerTest, GenericPrettyPrintJson) {
    JsonEventOutputHandler handler;
    CoutCapture cap;
    handler.prettyPrintJson({{"key", "value"}}, "Custom");
    auto ev = cap.first();
    EXPECT_EQ(ev["type"], "status");
    EXPECT_EQ(ev["status"], "info");
}

// ===========================================================================
// Full Query Simulation
// ===========================================================================

TEST(JsonEventHandlerTest, FullQueryFlow) {
    JsonEventOutputHandler handler;
    CoutCapture cap;

    handler.printProcessingStart("list files", 10, "Qwen3-4B");
    handler.printStepHeader(1, 10);
    handler.printThought("I need to list the files in the current directory.");
    handler.printGoal("List files");
    handler.printToolUsage("bash_execute");
    handler.prettyPrintJson({{"command", "ls"}}, "Tool Args");
    handler.startProgress("Executing bash_execute");
    handler.stopProgress();
    handler.printToolComplete();
    handler.prettyPrintJson({{"status", "success"}, {"stdout", "file1.txt\nfile2.txt"}}, "Tool Result");
    handler.printStepHeader(2, 10);
    handler.printFinalAnswer("The directory contains file1.txt and file2.txt.");
    handler.printCompletion(2, 10);

    auto events = cap.lines();

    // Count event types
    int steps = 0, thinking = 0, tool_starts = 0, answers = 0;
    for (const auto& ev : events) {
        if (ev["type"] == "step") ++steps;
        if (ev["type"] == "thinking") ++thinking;
        if (ev["type"] == "tool_start") ++tool_starts;
        if (ev["type"] == "answer") ++answers;
    }

    EXPECT_EQ(steps, 2);
    EXPECT_EQ(thinking, 1);
    EXPECT_EQ(tool_starts, 1);
    EXPECT_EQ(answers, 1);

    // Verify last event is completion
    EXPECT_EQ(events.back()["type"], "status");
    EXPECT_EQ(events.back()["status"], "complete");
}
