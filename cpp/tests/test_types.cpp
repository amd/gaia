// Copyright(C) 2025-2026 Advanced Micro Devices, Inc. All rights reserved.
// SPDX-License-Identifier: MIT

#include <gtest/gtest.h>
#include <gaia/types.h>

#include <cstdlib>
#include <filesystem>
#include <fstream>

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
    EXPECT_EQ(config.contextSize, 16384);
    EXPECT_EQ(config.maxTokens, 4096);
    EXPECT_FALSE(config.debug);
    EXPECT_FALSE(config.showPrompts);
    EXPECT_FALSE(config.streaming);
    EXPECT_FALSE(config.silentMode);
}

TEST(TypesTest, AgentConfigStreamingCanBeEnabled) {
    AgentConfig config;
    config.streaming = true;
    EXPECT_TRUE(config.streaming);
}

TEST(TypesTest, DefaultStreamingReturnsBool) {
    // Verifies defaultStreaming() is callable and returns a bool.
    // Value depends on GAIA_STREAMING env var; we just assert it doesn't crash
    // and matches what AgentConfig picks up.
    AgentConfig config;
    EXPECT_EQ(config.streaming, defaultStreaming());
}

TEST(TypesTest, AgentConfigToJson) {
    AgentConfig config;
    config.maxTokens = 8192;
    config.temperature = 0.5;
    config.debug = true;

    json j = config.toJson();
    EXPECT_EQ(j["maxTokens"], 8192);
    EXPECT_DOUBLE_EQ(j["temperature"].get<double>(), 0.5);
    EXPECT_EQ(j["debug"], true);
    EXPECT_EQ(j["maxSteps"], 20);
    EXPECT_EQ(j["contextSize"], 16384);
}

TEST(TypesTest, AgentConfigFromJsonRoundTrip) {
    AgentConfig orig;
    orig.maxSteps = 10;
    orig.maxTokens = 2048;
    orig.modelId = "test-model";
    orig.temperature = 1.0;

    json j = orig.toJson();
    AgentConfig restored = AgentConfig::fromJson(j);

    EXPECT_EQ(restored.maxSteps, 10);
    EXPECT_EQ(restored.maxTokens, 2048);
    EXPECT_EQ(restored.modelId, "test-model");
    EXPECT_DOUBLE_EQ(restored.temperature, 1.0);
}

TEST(TypesTest, AgentConfigFromJsonPartial) {
    // Only override a subset of fields — the rest should retain defaults
    json j = json::object();
    j["maxTokens"] = 512;
    j["debug"] = true;

    AgentConfig config = AgentConfig::fromJson(j);
    EXPECT_EQ(config.maxTokens, 512);
    EXPECT_TRUE(config.debug);
    EXPECT_EQ(config.maxSteps, 20);           // default
    EXPECT_EQ(config.contextSize, 16384);     // default
    EXPECT_DOUBLE_EQ(config.temperature, 0.7); // default
}

TEST(TypesTest, AgentConfigValidateEmptyBaseUrl) {
    json j;
    j["baseUrl"] = "";
    EXPECT_THROW(AgentConfig::fromJson(j), std::invalid_argument);
}

TEST(TypesTest, AgentConfigValidateEmptyModelId) {
    json j;
    j["modelId"] = "";
    EXPECT_THROW(AgentConfig::fromJson(j), std::invalid_argument);
}

TEST(TypesTest, AgentConfigValidateInvalidMaxSteps) {
    json j;
    j["maxSteps"] = 0;
    EXPECT_THROW(AgentConfig::fromJson(j), std::invalid_argument);
}

TEST(TypesTest, AgentConfigValidateInvalidMaxTokens) {
    json j;
    j["maxTokens"] = -1;
    EXPECT_THROW(AgentConfig::fromJson(j), std::invalid_argument);
}

TEST(TypesTest, AgentConfigValidateInvalidTemperature) {
    json j;
    j["temperature"] = 3.0;
    EXPECT_THROW(AgentConfig::fromJson(j), std::invalid_argument);
}

TEST(TypesTest, AgentConfigValidateInvalidContextSize) {
    json j;
    j["contextSize"] = 0;
    EXPECT_THROW(AgentConfig::fromJson(j), std::invalid_argument);
}

TEST(TypesTest, AgentConfigFromJsonFileNotFound) {
    EXPECT_THROW(AgentConfig::fromJsonFile("/nonexistent/path/config.json"),
                 std::runtime_error);
}

TEST(TypesTest, AgentConfigFromJsonFileMalformed) {
    // Write a temp file with invalid JSON
    std::string tmpPath = (std::filesystem::temp_directory_path() / "gaia_test_malformed.json").string();
    {
        std::ofstream f(tmpPath);
        f << "{ invalid json }";
    }
    EXPECT_THROW(AgentConfig::fromJsonFile(tmpPath), std::runtime_error);
    std::remove(tmpPath.c_str());
}

TEST(TypesTest, AgentConfigFromJsonFileValid) {
    std::string tmpPath = (std::filesystem::temp_directory_path() / "gaia_test_valid.json").string();
    {
        std::ofstream f(tmpPath);
        f << R"({"maxSteps": 5, "maxTokens": 1024, "temperature": 0.3})";
    }
    AgentConfig config = AgentConfig::fromJsonFile(tmpPath);
    EXPECT_EQ(config.maxSteps, 5);
    EXPECT_EQ(config.maxTokens, 1024);
    EXPECT_DOUBLE_EQ(config.temperature, 0.3);
    std::remove(tmpPath.c_str());
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
