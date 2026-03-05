// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/console.h>

#include <sstream>

using namespace gaia;

// ---- SilentConsole Tests ----

TEST(ConsoleTest, SilentConsoleNoOutput) {
    // SilentConsole should not crash when methods are called
    SilentConsole console(true);

    console.printProcessingStart("test", 10, "model");
    console.printStepHeader(1, 10);
    console.printStateInfo("state");
    console.printThought("thought");
    console.printGoal("goal");
    console.printPlan(json::array(), 0);
    console.printToolUsage("tool");
    console.printToolComplete();
    console.prettyPrintJson(json::object(), "title");
    console.printError("error");
    console.printWarning("warning");
    console.printInfo("info");
    console.startProgress("progress");
    console.stopProgress();
    console.printCompletion(5, 10);

    // No assertions needed - just verifying no crashes
    SUCCEED();
}

TEST(ConsoleTest, SilentConsoleFinalAnswerSilenced) {
    // Redirect stdout to capture output
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    SilentConsole console(true); // silence final answer
    console.printFinalAnswer("test answer");

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().empty());
}

TEST(ConsoleTest, SilentConsoleFinalAnswerShown) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    SilentConsole console(false); // show final answer
    console.printFinalAnswer("test answer");

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().find("test answer") != std::string::npos);
}

// ---- TerminalConsole Tests ----

TEST(ConsoleTest, TerminalConsoleProcessingStart) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printProcessingStart("test query", 20, "test-model");

    std::cout.rdbuf(oldBuf);

    std::string output = captured.str();
    EXPECT_TRUE(output.find("test query") != std::string::npos);
    EXPECT_TRUE(output.find("20") != std::string::npos);
    EXPECT_TRUE(output.find("test-model") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsoleStepHeader) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printStepHeader(3, 10);

    std::cout.rdbuf(oldBuf);

    std::string output = captured.str();
    EXPECT_TRUE(output.find("3") != std::string::npos);
    EXPECT_TRUE(output.find("10") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsoleThought) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printThought("I am thinking deeply");

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().find("I am thinking deeply") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsoleEmptyThought) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printThought(""); // Should produce no output

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().empty());
}

TEST(ConsoleTest, TerminalConsoleError) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printError("something went wrong");

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().find("ERROR") != std::string::npos);
    EXPECT_TRUE(captured.str().find("something went wrong") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsoleFinalAnswer) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printFinalAnswer("The answer is 42");

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().find("Answer") != std::string::npos);
    EXPECT_TRUE(captured.str().find("The answer is 42") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsolePrettyPrintJson) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    json data = {{"key", "value"}, {"number", 42}};
    console.prettyPrintJson(data, "Test Output");

    std::cout.rdbuf(oldBuf);
    EXPECT_TRUE(captured.str().find("Test Output") != std::string::npos);
    EXPECT_TRUE(captured.str().find("key") != std::string::npos);
    EXPECT_TRUE(captured.str().find("value") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsolePlan) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    json plan = json::array({
        {{"tool", "step1"}, {"tool_args", {{"a", 1}}}},
        {{"tool", "step2"}, {"tool_args", {{"b", 2}}}}
    });
    console.printPlan(plan, 0);

    std::cout.rdbuf(oldBuf);
    std::string output = captured.str();
    EXPECT_TRUE(output.find("Plan") != std::string::npos);
    EXPECT_TRUE(output.find("step1") != std::string::npos);
    EXPECT_TRUE(output.find("step2") != std::string::npos);
}

TEST(ConsoleTest, TerminalConsoleSeparator) {
    std::ostringstream captured;
    std::streambuf* oldBuf = std::cout.rdbuf(captured.rdbuf());

    TerminalConsole console;
    console.printSeparator(20);

    std::cout.rdbuf(oldBuf);
    EXPECT_EQ(captured.str(), std::string(20, '-') + "\n");
}

TEST(ConsoleTest, OutputHandlerOptionalMethods) {
    // Verify default no-op implementations don't crash
    TerminalConsole console;
    console.printPrompt("prompt", "title");
    console.printResponse("response", "title");
    SUCCEED();
}
