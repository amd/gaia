// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/clean_console.h>
#include <gaia/types.h>

using namespace gaia;

// --- Decision struct tests (types.h) ---

TEST(DecisionTest, DefaultConstruction) {
    Decision d;
    EXPECT_TRUE(d.label.empty());
    EXPECT_TRUE(d.value.empty());
    EXPECT_TRUE(d.description.empty());
}

TEST(DecisionTest, InitializerList) {
    Decision d{"Yes", "yes", "Confirm"};
    EXPECT_EQ(d.label, "Yes");
    EXPECT_EQ(d.value, "yes");
    EXPECT_EQ(d.description, "Confirm");
}

// --- OutputHandler::printDecisionMenu default no-op ---

TEST(DecisionTest, OutputHandlerHasDefaultNoOp) {
    SilentConsole console;
    std::vector<Decision> decisions = {{"Yes", "yes", "Confirm"}};
    console.printDecisionMenu(decisions);  // no-op, just verify it compiles
}

// --- CleanConsole::printDecisionMenu ---

TEST(DecisionTest, CleanConsolePrintDecisionMenu) {
    CleanConsole console;
    std::vector<Decision> decisions = {
        {"Yes", "yes", "Confirm and proceed"},
        {"No",  "no",  "Cancel"}
    };
    testing::internal::CaptureStdout();
    console.printDecisionMenu(decisions);
    std::string output = testing::internal::GetCapturedStdout();
    EXPECT_NE(output.find("Yes"), std::string::npos);
    EXPECT_NE(output.find("No"), std::string::npos);
    EXPECT_NE(output.find("[1]"), std::string::npos);
    EXPECT_NE(output.find("[2]"), std::string::npos);
}
