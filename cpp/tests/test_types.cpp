// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/types.h>

using namespace gaia;

// ---- AgentState Tests ----

TEST(TypesTest, AgentStateToString) {
    EXPECT_EQ(agentStateToString(AgentState::PLANNING), "PLANNING");
    EXPECT_EQ(agentStateToString(AgentState::EXECUTING_PLAN), "EXECUTING_PLAN");
    EXPECT_EQ(agentStateToString(AgentState::DIRECT_EXECUTION), "DIRECT_EXECUTION");
    EXPECT_EQ(agentStateToString(AgentState::ERROR_RECOVERY), "ERROR_RECOVERY");
    EXPECT_EQ(agentStateToString(AgentState::COMPLETION), "COMPLETION");
}

// ---- MessageRole Tests ----

TEST(TypesTest, RoleToString) {
    EXPECT_EQ(roleToString(MessageRole::SYSTEM), "system");
    EXPECT_EQ(roleToString(MessageRole::USER), "user");
    EXPECT_EQ(roleToString(MessageRole::ASSISTANT), "assistant");
    EXPECT_EQ(roleToString(MessageRole::TOOL), "tool");
}

// ---- Message Tests ----

TEST(TypesTest, MessageToJson) {
    Message msg;
    msg.role = MessageRole::USER;
    msg.content = "Hello, world!";

    json j = msg.toJson();
    EXPECT_EQ(j["role"], "user");
    EXPECT_EQ(j["content"], "Hello, world!");
    EXPECT_FALSE(j.contains("name"));
    EXPECT_FALSE(j.contains("tool_call_id"));
}

TEST(TypesTest, MessageToJsonWithOptionals) {
    Message msg;
    msg.role = MessageRole::TOOL;
    msg.content = "result data";
    msg.name = "my_tool";
    msg.toolCallId = "call_123";

    json j = msg.toJson();
    EXPECT_EQ(j["role"], "tool");
    EXPECT_EQ(j["content"], "result data");
    EXPECT_EQ(j["name"], "my_tool");
    EXPECT_EQ(j["tool_call_id"], "call_123");
}

// ---- ToolParamType Tests ----

TEST(TypesTest, ParamTypeToString) {
    EXPECT_EQ(paramTypeToString(ToolParamType::STRING), "string");
    EXPECT_EQ(paramTypeToString(ToolParamType::INTEGER), "integer");
    EXPECT_EQ(paramTypeToString(ToolParamType::NUMBER), "number");
    EXPECT_EQ(paramTypeToString(ToolParamType::BOOLEAN), "boolean");
    EXPECT_EQ(paramTypeToString(ToolParamType::ARRAY), "array");
    EXPECT_EQ(paramTypeToString(ToolParamType::OBJECT), "object");
    EXPECT_EQ(paramTypeToString(ToolParamType::UNKNOWN), "unknown");
}

// ---- AgentConfig Tests ----

TEST(TypesTest, AgentConfigDefaults) {
    AgentConfig config;
    EXPECT_EQ(config.maxSteps, 20);
    EXPECT_EQ(config.maxPlanIterations, 3);
    EXPECT_EQ(config.maxConsecutiveRepeats, 4);
    EXPECT_FALSE(config.debug);
    EXPECT_FALSE(config.showPrompts);
    EXPECT_FALSE(config.streaming);
    EXPECT_FALSE(config.silentMode);
}

// ---- ParsedResponse Tests ----

TEST(TypesTest, ParsedResponseDefaults) {
    ParsedResponse parsed;
    EXPECT_TRUE(parsed.thought.empty());
    EXPECT_TRUE(parsed.goal.empty());
    EXPECT_FALSE(parsed.answer.has_value());
    EXPECT_FALSE(parsed.toolName.has_value());
    EXPECT_FALSE(parsed.toolArgs.has_value());
    EXPECT_FALSE(parsed.plan.has_value());
}
