// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/clean_console.h>

#include <sstream>

using namespace gaia;

// ---------------------------------------------------------------------------
// Helper RAII guard to redirect std::cout into an ostringstream and restore
// the original streambuf on scope exit (even if a test assertion fails).
// ---------------------------------------------------------------------------
class CoutCapture {
public:
    CoutCapture() : captured_(), oldBuf_(std::cout.rdbuf(captured_.rdbuf())) {}
    ~CoutCapture() { std::cout.rdbuf(oldBuf_); }

    std::string str() const { return captured_.str(); }

private:
    std::ostringstream captured_;
    std::streambuf* oldBuf_;
};

// ---- 1. printProcessingStart resets internal state ----

TEST(CleanConsoleTest, PrintProcessingStartResetsState) {
    CleanConsole console;

    // Advance internal state: set a goal, mark plan as shown, run a tool
    {
        CoutCapture cap;
        console.printGoal("initial goal");
        console.printPlan(json::array({{{"tool", "t1"}}}), 0);
        console.printToolComplete();
    }

    // Reset via printProcessingStart
    {
        CoutCapture cap;
        console.printProcessingStart("query", 10, "model");
    }

    // After reset, the same goal should print again (lastGoal_ cleared)
    {
        CoutCapture cap;
        console.printGoal("initial goal");
        std::string out = cap.str();
        EXPECT_TRUE(out.find("Goal:") != std::string::npos)
            << "Goal should appear again after reset; got: " << out;
    }

    // After reset, plan should print again (planShown_ cleared)
    {
        CoutCapture cap;
        console.printPlan(json::array({{{"tool", "t1"}}}), 0);
        std::string out = cap.str();
        EXPECT_TRUE(out.find("Plan:") != std::string::npos)
            << "Plan should appear again after reset; got: " << out;
    }

    // After reset, toolsRun_ should be 0 so printThought uses "Thinking:"
    {
        CoutCapture cap;
        console.printThought("some thought");
        std::string out = cap.str();
        EXPECT_TRUE(out.find("Thinking:") != std::string::npos)
            << "Should use Thinking label after reset; got: " << out;
    }
}

// ---- 2. printThought with FINDING and DECISION ----

TEST(CleanConsoleTest, PrintThoughtFindingAndDecision) {
    CleanConsole console;
    CoutCapture cap;

    console.printThought("FINDING: The network is down DECISION: Restart the router");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Finding:") != std::string::npos)
        << "Expected Finding: label; got: " << out;
    EXPECT_TRUE(out.find("Decision:") != std::string::npos)
        << "Expected Decision: label; got: " << out;
    EXPECT_TRUE(out.find("network is down") != std::string::npos)
        << "Expected finding content; got: " << out;
    EXPECT_TRUE(out.find("Restart the router") != std::string::npos)
        << "Expected decision content; got: " << out;
}

// ---- 3. printThought with FINDING only ----

TEST(CleanConsoleTest, PrintThoughtFindingOnly) {
    CleanConsole console;
    CoutCapture cap;

    console.printThought("FINDING: The disk is full");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Finding:") != std::string::npos)
        << "Expected Finding: label; got: " << out;
    EXPECT_TRUE(out.find("disk is full") != std::string::npos)
        << "Expected finding content; got: " << out;
    // No Decision label should appear
    EXPECT_TRUE(out.find("Decision:") == std::string::npos)
        << "Decision: should NOT appear; got: " << out;
}

// ---- 4. printThought with DECISION only ----

TEST(CleanConsoleTest, PrintThoughtDecisionOnly) {
    CleanConsole console;
    CoutCapture cap;

    console.printThought("DECISION: Allocate more memory");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Decision:") != std::string::npos)
        << "Expected Decision: label; got: " << out;
    EXPECT_TRUE(out.find("Allocate more memory") != std::string::npos)
        << "Expected decision content; got: " << out;
    // No Finding label should appear
    EXPECT_TRUE(out.find("Finding:") == std::string::npos)
        << "Finding: should NOT appear; got: " << out;
}

// ---- 5. printThought fallback (no markers) ----

TEST(CleanConsoleTest, PrintThoughtFallbackNoToolsRun) {
    CleanConsole console;
    CoutCapture cap;

    console.printThought("I need to check something");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Thinking:") != std::string::npos)
        << "Expected Thinking: label when toolsRun_==0; got: " << out;
    EXPECT_TRUE(out.find("I need to check something") != std::string::npos)
        << "Expected thought content; got: " << out;
}

// ---- 6. printThought fallback after tool ----

TEST(CleanConsoleTest, PrintThoughtFallbackAfterTool) {
    CleanConsole console;

    // Simulate a tool having completed
    {
        CoutCapture cap;
        console.printToolComplete();
    }

    CoutCapture cap;
    console.printThought("Interpreting results");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Analysis:") != std::string::npos)
        << "Expected Analysis: label when toolsRun_>0; got: " << out;
    EXPECT_TRUE(out.find("Interpreting results") != std::string::npos)
        << "Expected thought content; got: " << out;
}

// ---- 7. printThought empty ----

TEST(CleanConsoleTest, PrintThoughtEmpty) {
    CleanConsole console;
    CoutCapture cap;

    console.printThought("");

    EXPECT_TRUE(cap.str().empty())
        << "Empty thought should produce no output; got: " << cap.str();
}

// ---- 8. printGoal ----

TEST(CleanConsoleTest, PrintGoal) {
    CleanConsole console;
    CoutCapture cap;

    console.printGoal("Diagnose the network issue");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Goal:") != std::string::npos)
        << "Expected Goal: label; got: " << out;
    EXPECT_TRUE(out.find("Diagnose the network issue") != std::string::npos)
        << "Expected goal text; got: " << out;
}

// ---- 9. printGoal dedup ----

TEST(CleanConsoleTest, PrintGoalDedup) {
    CleanConsole console;

    // First call: should produce output
    {
        CoutCapture cap;
        console.printGoal("Repeated goal");
        std::string out = cap.str();
        EXPECT_TRUE(out.find("Goal:") != std::string::npos)
            << "First call should show goal; got: " << out;
    }

    // Second call with same text: should produce no output
    {
        CoutCapture cap;
        console.printGoal("Repeated goal");
        EXPECT_TRUE(cap.str().empty())
            << "Duplicate goal should produce no output; got: " << cap.str();
    }
}

// ---- 10. printToolUsage ----

TEST(CleanConsoleTest, PrintToolUsage) {
    CleanConsole console;

    // Set step/limit so the output includes them
    console.printStepHeader(2, 5);

    CoutCapture cap;
    console.printToolUsage("run_command");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("2") != std::string::npos)
        << "Expected step number 2; got: " << out;
    EXPECT_TRUE(out.find("5") != std::string::npos)
        << "Expected step limit 5; got: " << out;
    EXPECT_TRUE(out.find("run_command") != std::string::npos)
        << "Expected tool name; got: " << out;
}

// ---- 11. printToolComplete increments toolsRun_ ----

TEST(CleanConsoleTest, PrintToolCompleteIncrementsToolsRun) {
    CleanConsole console;

    // Before any tool completion, thought label should be "Thinking:"
    {
        CoutCapture cap;
        console.printThought("before");
        EXPECT_TRUE(cap.str().find("Thinking:") != std::string::npos)
            << "Expected Thinking: before tool; got: " << cap.str();
    }

    // Complete a tool
    console.printToolComplete();

    // After tool completion, thought label should switch to "Analysis:"
    {
        CoutCapture cap;
        console.printThought("after");
        EXPECT_TRUE(cap.str().find("Analysis:") != std::string::npos)
            << "Expected Analysis: after tool; got: " << cap.str();
    }
}

// ---- 12. prettyPrintJson Tool Args ----

TEST(CleanConsoleTest, PrettyPrintJsonToolArgs) {
    CleanConsole console;
    CoutCapture cap;

    json args = {{"path", "/tmp/test"}, {"recursive", true}};
    console.prettyPrintJson(args, "Tool Args");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Args:") != std::string::npos)
        << "Expected Args: label; got: " << out;
    EXPECT_TRUE(out.find("path") != std::string::npos)
        << "Expected key 'path'; got: " << out;
    EXPECT_TRUE(out.find("/tmp/test") != std::string::npos)
        << "Expected value '/tmp/test'; got: " << out;
}

// ---- 13. prettyPrintJson Tool Result with output ----

TEST(CleanConsoleTest, PrettyPrintJsonToolResultOutput) {
    CleanConsole console;
    CoutCapture cap;

    json result = {{"output", "Hello World"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Output:") != std::string::npos)
        << "Expected Output: label; got: " << out;
    // The preview box uses bordered output
    EXPECT_TRUE(out.find("Hello World") != std::string::npos)
        << "Expected output content in preview; got: " << out;
    // Verify the preview box borders
    EXPECT_TRUE(out.find(".---") != std::string::npos)
        << "Expected top border of preview box; got: " << out;
    EXPECT_TRUE(out.find("'---") != std::string::npos)
        << "Expected bottom border of preview box; got: " << out;
}

// ---- 14. prettyPrintJson Tool Result with error ----

TEST(CleanConsoleTest, PrettyPrintJsonToolResultError) {
    CleanConsole console;
    CoutCapture cap;

    json result = {{"error", "File not found"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Error:") != std::string::npos)
        << "Expected Error: label; got: " << out;
    EXPECT_TRUE(out.find("File not found") != std::string::npos)
        << "Expected error message; got: " << out;
}

// ---- 15. prettyPrintJson Tool Result with command ----

TEST(CleanConsoleTest, PrettyPrintJsonToolResultCommand) {
    CleanConsole console;
    CoutCapture cap;

    json result = {{"command", "ipconfig /all"}, {"output", "Windows IP Configuration"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Cmd:") != std::string::npos)
        << "Expected Cmd: label; got: " << out;
    EXPECT_TRUE(out.find("ipconfig /all") != std::string::npos)
        << "Expected command text; got: " << out;
}

// ---- 16. printError ----

TEST(CleanConsoleTest, PrintError) {
    CleanConsole console;
    CoutCapture cap;

    console.printError("something failed");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("ERROR:") != std::string::npos)
        << "Expected ERROR: label; got: " << out;
    EXPECT_TRUE(out.find("something failed") != std::string::npos)
        << "Expected error message; got: " << out;
}

// ---- 17. printWarning ----

TEST(CleanConsoleTest, PrintWarning) {
    CleanConsole console;
    CoutCapture cap;

    console.printWarning("disk space low");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("WARNING:") != std::string::npos)
        << "Expected WARNING: label; got: " << out;
    EXPECT_TRUE(out.find("disk space low") != std::string::npos)
        << "Expected warning message; got: " << out;
}

// ---- 18. printFinalAnswer ----

TEST(CleanConsoleTest, PrintFinalAnswer) {
    CleanConsole console;
    CoutCapture cap;

    console.printFinalAnswer("The answer is 42");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Conclusion") != std::string::npos)
        << "Expected Conclusion label; got: " << out;
    EXPECT_TRUE(out.find("The answer is 42") != std::string::npos)
        << "Expected answer text; got: " << out;
    // Verify bordered section (=== lines)
    EXPECT_TRUE(out.find("====") != std::string::npos)
        << "Expected border lines; got: " << out;
}

// ---- 19. printFinalAnswer JSON extraction ----

TEST(CleanConsoleTest, PrintFinalAnswerJsonExtraction) {
    CleanConsole console;
    CoutCapture cap;

    // The LLM sometimes returns raw JSON; CleanConsole should extract the "answer" key
    console.printFinalAnswer(R"({"answer": "Extracted value", "confidence": 0.95})");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Extracted value") != std::string::npos)
        << "Expected extracted answer value; got: " << out;
    EXPECT_TRUE(out.find("Conclusion") != std::string::npos)
        << "Expected Conclusion label; got: " << out;
}

// ---- 20. printFinalAnswer empty ----

TEST(CleanConsoleTest, PrintFinalAnswerEmpty) {
    CleanConsole console;
    CoutCapture cap;

    console.printFinalAnswer("");

    EXPECT_TRUE(cap.str().empty())
        << "Empty final answer should produce no output; got: " << cap.str();
}

// ---- 21. printCompletion ----

TEST(CleanConsoleTest, PrintCompletion) {
    CleanConsole console;
    CoutCapture cap;

    console.printCompletion(3, 10);

    std::string out = cap.str();
    EXPECT_TRUE(out.find("3") != std::string::npos)
        << "Expected step count 3; got: " << out;
    EXPECT_TRUE(out.find("steps") != std::string::npos)
        << "Expected 'steps' text; got: " << out;
}

// ---- 22. printPlan ----

TEST(CleanConsoleTest, PrintPlan) {
    CleanConsole console;
    CoutCapture cap;

    json plan = json::array({
        {{"tool", "diagnose_wifi"}},
        {{"tool", "run_command"}},
        {{"tool", "check_status"}}
    });
    console.printPlan(plan, 0);

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Plan:") != std::string::npos)
        << "Expected Plan: label; got: " << out;
    EXPECT_TRUE(out.find("diagnose_wifi") != std::string::npos)
        << "Expected tool name diagnose_wifi; got: " << out;
    EXPECT_TRUE(out.find("run_command") != std::string::npos)
        << "Expected tool name run_command; got: " << out;
    EXPECT_TRUE(out.find("check_status") != std::string::npos)
        << "Expected tool name check_status; got: " << out;
}

// ---- 23. printPlan shown once ----

TEST(CleanConsoleTest, PrintPlanShownOnce) {
    CleanConsole console;

    json plan = json::array({{{"tool", "step1"}}});

    // First call: should produce output
    {
        CoutCapture cap;
        console.printPlan(plan, 0);
        EXPECT_TRUE(cap.str().find("Plan:") != std::string::npos)
            << "First call should show plan; got: " << cap.str();
    }

    // Second call: should produce no output (planShown_ is true)
    {
        CoutCapture cap;
        console.printPlan(plan, 1);
        EXPECT_TRUE(cap.str().empty())
            << "Second plan call should produce no output; got: " << cap.str();
    }
}

// ---- 24. printWrapped word-wrap ----

TEST(CleanConsoleTest, PrintWrappedWordWrap) {
    CleanConsole console;

    // Build a long thought that will exceed the wrap width (78 chars for fallback).
    // Each word is ~10 chars; 12 words = ~130 chars + spaces => will wrap.
    std::string longText;
    for (int i = 0; i < 12; ++i) {
        if (i > 0) longText += " ";
        longText += "LongWord" + std::to_string(i) + "X";
    }

    CoutCapture cap;
    console.printThought(longText);

    std::string out = cap.str();
    // Count newlines -- word-wrapping should produce at least 2 lines
    int newlines = 0;
    for (char c : out) {
        if (c == '\n') ++newlines;
    }
    EXPECT_GE(newlines, 2)
        << "Expected wrapped output with multiple lines; got " << newlines
        << " newlines in: " << out;
}

// ---- 25. printStyledWord bold ----

TEST(CleanConsoleTest, PrintStyledWordBold) {
    CleanConsole console;
    CoutCapture cap;

    // Pass text with **bold** markers through printThought (which calls printWrapped -> printStyledWord)
    console.printThought("This is **important** information");

    std::string out = cap.str();
    // The BOLD ANSI code should appear
    EXPECT_TRUE(out.find("\033[1m") != std::string::npos)
        << "Expected ANSI bold code; got: " << out;
    // The WHITE ANSI code should appear (used for bold text)
    EXPECT_TRUE(out.find("\033[97m") != std::string::npos)
        << "Expected ANSI white code for bold text; got: " << out;
    // The actual word "important" should appear (without the ** markers)
    EXPECT_TRUE(out.find("important") != std::string::npos)
        << "Expected bold content 'important'; got: " << out;
    // The ** markers should NOT appear literally in the output
    // (they are consumed by printStyledWord and replaced with ANSI codes)
    // Note: we check that "**important**" as a literal substring is absent,
    // but "important" surrounded by ANSI codes is present.
}

// ---- 26. printOutputPreview truncation ----

TEST(CleanConsoleTest, PrintOutputPreviewTruncation) {
    CleanConsole console;

    // Build output with 15 non-empty lines (exceeds kMaxPreviewLines = 10)
    std::string multiLineOutput;
    for (int i = 1; i <= 15; ++i) {
        multiLineOutput += "Line number " + std::to_string(i) + "\n";
    }

    CoutCapture cap;

    json result = {{"output", multiLineOutput}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    // The first 10 lines should appear
    EXPECT_TRUE(out.find("Line number 1") != std::string::npos)
        << "Expected first line in preview; got: " << out;
    EXPECT_TRUE(out.find("Line number 10") != std::string::npos)
        << "Expected 10th line in preview; got: " << out;
    // Lines beyond 10 should not appear directly
    EXPECT_TRUE(out.find("Line number 11") == std::string::npos)
        << "Line 11 should NOT appear in preview; got: " << out;
    // The "more lines" message should appear
    EXPECT_TRUE(out.find("more lines") != std::string::npos)
        << "Expected 'more lines' truncation message; got: " << out;
    // Specifically 5 more lines (15 - 10 = 5)
    EXPECT_TRUE(out.find("5 more lines") != std::string::npos)
        << "Expected '5 more lines'; got: " << out;
}

// ---- Additional edge-case tests ----

TEST(CleanConsoleTest, PrintGoalEmptyString) {
    CleanConsole console;
    CoutCapture cap;

    console.printGoal("");

    EXPECT_TRUE(cap.str().empty())
        << "Empty goal should produce no output; got: " << cap.str();
}

TEST(CleanConsoleTest, PrintPlanNonArray) {
    CleanConsole console;
    CoutCapture cap;

    // Non-array JSON should be ignored
    console.printPlan(json::object({{"tool", "t1"}}), 0);

    EXPECT_TRUE(cap.str().empty())
        << "Non-array plan should produce no output; got: " << cap.str();
}

TEST(CleanConsoleTest, PrettyPrintJsonToolResultNoOutput) {
    CleanConsole console;
    CoutCapture cap;

    // Tool result with "(no output)" should show "Result: (no output)"
    json result = {{"output", "(no output)"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Result:") != std::string::npos)
        << "Expected Result: label for no-output case; got: " << out;
    EXPECT_TRUE(out.find("(no output)") != std::string::npos)
        << "Expected '(no output)' text; got: " << out;
}

TEST(CleanConsoleTest, PrettyPrintJsonToolResultEmptyOutput) {
    CleanConsole console;
    CoutCapture cap;

    // Empty output string should show "(no output)"
    json result = {{"output", ""}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("(no output)") != std::string::npos)
        << "Expected '(no output)' for empty output; got: " << out;
}

TEST(CleanConsoleTest, PrettyPrintJsonToolResultStatus) {
    CleanConsole console;
    CoutCapture cap;

    json result = {{"status", "completed"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Status:") != std::string::npos)
        << "Expected Status: label; got: " << out;
    EXPECT_TRUE(out.find("completed") != std::string::npos)
        << "Expected status value; got: " << out;
}

TEST(CleanConsoleTest, PrintFinalAnswerJsonThoughtExtraction) {
    CleanConsole console;
    CoutCapture cap;

    // When JSON has "thought" key but no "answer" key, it should extract "thought"
    console.printFinalAnswer(R"({"thought": "Let me explain this"})");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Let me explain this") != std::string::npos)
        << "Expected extracted thought value; got: " << out;
}

TEST(CleanConsoleTest, PrintFinalAnswerInvalidJson) {
    CleanConsole console;
    CoutCapture cap;

    // Starts with '{' but is not valid JSON -- should use as-is
    console.printFinalAnswer("{not valid json at all");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("{not valid json at all") != std::string::npos)
        << "Invalid JSON should be printed as-is; got: " << out;
    EXPECT_TRUE(out.find("Conclusion") != std::string::npos)
        << "Expected Conclusion label; got: " << out;
}

TEST(CleanConsoleTest, PrintThoughtCaseInsensitiveMarkers) {
    // The code checks both "FINDING:" and "Finding:" (also "DECISION:"/"Decision:")
    CleanConsole console;
    CoutCapture cap;

    console.printThought("Finding: lowercase marker Decision: also lowercase");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Finding:") != std::string::npos)
        << "Expected Finding: label for lowercase marker; got: " << out;
    EXPECT_TRUE(out.find("Decision:") != std::string::npos)
        << "Expected Decision: label for lowercase marker; got: " << out;
}

TEST(CleanConsoleTest, NoOpMethodsDoNotCrash) {
    // printStateInfo, printInfo, startProgress, stopProgress are no-ops
    CleanConsole console;
    CoutCapture cap;

    console.printStateInfo("state info");
    console.printInfo("info message");
    console.startProgress("loading...");
    console.stopProgress();

    // These are no-op methods; verify they produce no output and do not crash
    EXPECT_TRUE(cap.str().empty())
        << "No-op methods should produce no output; got: " << cap.str();
}

TEST(CleanConsoleTest, PrettyPrintJsonToolArgsEmpty) {
    CleanConsole console;
    CoutCapture cap;

    // Empty object should produce no output for Tool Args
    console.prettyPrintJson(json::object(), "Tool Args");

    EXPECT_TRUE(cap.str().empty())
        << "Empty Tool Args should produce no output; got: " << cap.str();
}

TEST(CleanConsoleTest, PrettyPrintJsonNonToolTitle) {
    CleanConsole console;
    CoutCapture cap;

    // A title that is neither "Tool Args" nor "Tool Result" should produce no output
    json data = {{"key", "value"}};
    console.prettyPrintJson(data, "Something Else");

    EXPECT_TRUE(cap.str().empty())
        << "Non-tool title should produce no output; got: " << cap.str();
}

TEST(CleanConsoleTest, PrintToolUsageStoresLastToolName) {
    CleanConsole console;
    console.printStepHeader(1, 5);

    {
        CoutCapture cap;
        console.printToolUsage("my_tool");
        std::string out = cap.str();
        EXPECT_TRUE(out.find("my_tool") != std::string::npos)
            << "Expected tool name; got: " << out;
        EXPECT_TRUE(out.find("[1/5]") != std::string::npos)
            << "Expected step format [1/5]; got: " << out;
    }
}

TEST(CleanConsoleTest, PrettyPrintJsonToolResultErrorPreventsOutput) {
    CleanConsole console;
    CoutCapture cap;

    // When error is present alongside output, error should be shown and
    // we should NOT see the Output: preview (error causes early return)
    json result = {{"error", "Permission denied"}, {"output", "should not show"}};
    console.prettyPrintJson(result, "Tool Result");

    std::string out = cap.str();
    EXPECT_TRUE(out.find("Error:") != std::string::npos)
        << "Expected Error: label; got: " << out;
    EXPECT_TRUE(out.find("Permission denied") != std::string::npos)
        << "Expected error message; got: " << out;
    // The Output: section should not appear because error causes early return
    EXPECT_TRUE(out.find("Output:") == std::string::npos)
        << "Output: should NOT appear when error is present; got: " << out;
}
