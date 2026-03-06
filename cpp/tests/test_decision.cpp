// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/agent.h>
#include <gaia/clean_console.h>

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

// --- detectPendingDecisions tests ---

class DecisionAgent : public Agent {
public:
    explicit DecisionAgent() : Agent(silentConfig()) { init(); }
    using Agent::detectPendingDecisions;
private:
    static AgentConfig silentConfig() {
        AgentConfig c; c.silentMode = true; return c;
    }
};

TEST(DecisionTest, DetectsYesSlashNo) {
    DecisionAgent agent;
    auto d = agent.detectPendingDecisions(
        "Kill svc_helper.exe and quarantine? (yes / no)");
    ASSERT_EQ(d.size(), 2u);
    EXPECT_EQ(d[0].value, "yes");
    EXPECT_EQ(d[1].value, "no");
}

TEST(DecisionTest, DetectsYesNo) {
    DecisionAgent agent;
    auto d = agent.detectPendingDecisions(
        "Proceed with this action? (yes/no)");
    ASSERT_EQ(d.size(), 2u);
}

TEST(DecisionTest, DetectsYN) {
    DecisionAgent agent;
    auto d = agent.detectPendingDecisions(
        "Continue? (Y/N)");
    ASSERT_EQ(d.size(), 2u);
}

TEST(DecisionTest, DetectsYesOrNo) {
    DecisionAgent agent;
    auto d = agent.detectPendingDecisions(
        "Would you like to proceed? Please answer yes or no.");
    ASSERT_EQ(d.size(), 2u);
}

TEST(DecisionTest, NoDecisionForNormalText) {
    DecisionAgent agent;
    auto d = agent.detectPendingDecisions(
        "System analysis complete. All processes normal.");
    EXPECT_TRUE(d.empty());
}

TEST(DecisionTest, OnlyScansLast300Chars) {
    DecisionAgent agent;
    // "yes / no" appears early but NOT in the tail
    std::string answer = "Should we proceed? (yes / no) ";
    answer += std::string(500, 'x');  // pad to push pattern out of tail
    answer += "Analysis complete.";
    auto d = agent.detectPendingDecisions(answer);
    EXPECT_TRUE(d.empty());  // pattern is outside the 300-char tail
}

TEST(DecisionTest, DetectsInTail) {
    DecisionAgent agent;
    std::string answer = std::string(500, 'x');
    answer += "Do you want to proceed? (yes / no)";
    auto d = agent.detectPendingDecisions(answer);
    ASSERT_EQ(d.size(), 2u);
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
