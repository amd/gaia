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

// ---- MIME detection tests (Ttest1) ----

TEST(TypesTest, DetectImageMimeFromPng) {
    const std::uint8_t png[] = {0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,0,0,0,13};
    EXPECT_EQ(detectImageMimeType(png, sizeof(png)), "image/png");
}
TEST(TypesTest, DetectImageMimeFromJpeg) {
    const std::uint8_t jpg[] = {0xFF,0xD8,0xFF,0xE0,0,0x10,'J','F','I','F',0,0};
    EXPECT_EQ(detectImageMimeType(jpg, sizeof(jpg)), "image/jpeg");
}
TEST(TypesTest, DetectImageMimeFromGif87a) {
    const std::uint8_t gif[] = {'G','I','F','8','7','a',0,0,0,0,0,0};
    EXPECT_EQ(detectImageMimeType(gif, sizeof(gif)), "image/gif");
}
TEST(TypesTest, DetectImageMimeFromGif89a) {
    const std::uint8_t gif[] = {'G','I','F','8','9','a',0,0,0,0,0,0};
    EXPECT_EQ(detectImageMimeType(gif, sizeof(gif)), "image/gif");
}
TEST(TypesTest, DetectImageMimeFromWebp) {
    const std::uint8_t webp[] = {'R','I','F','F',0x24,0,0,0,'W','E','B','P'};
    EXPECT_EQ(detectImageMimeType(webp, sizeof(webp)), "image/webp");
}
TEST(TypesTest, DetectImageMimeFromBmp) {
    const std::uint8_t bmp[] = {'B','M',0,0,0,0,0,0,0,0,0,0};
    EXPECT_EQ(detectImageMimeType(bmp, sizeof(bmp)), "image/bmp");
}
TEST(TypesTest, DetectImageMimeFromShortBufferFallsBackToPng) {
    // AC-15e: 1, 5, 11-byte buffers must not OOB-read the WebP offset-8 probe.
    const std::uint8_t one[] = {0xFF};
    EXPECT_EQ(detectImageMimeType(one, 1), "image/png");
    const std::uint8_t five[] = {'R','I','F','F',0x24};
    EXPECT_EQ(detectImageMimeType(five, 5), "image/png");
    const std::uint8_t eleven[] = {'R','I','F','F',0x24,0,0,0,'W','E','B'};
    EXPECT_EQ(detectImageMimeType(eleven, 11), "image/png");
    // Null buffer guard
    EXPECT_EQ(detectImageMimeType(nullptr, 0), "image/png");
}

// ---- ContentPart tests ----

TEST(TypesTest, ContentPartTextToJson) {
    auto p = ContentPart::makeText("hi");
    json j = p.toJson();
    EXPECT_EQ(j["type"], "text");
    EXPECT_EQ(j["text"], "hi");
}
TEST(TypesTest, ContentPartImageUrlToJson) {
    auto p = ContentPart::makeImageUrl("data:image/png;base64,abc");
    json j = p.toJson();
    EXPECT_EQ(j["type"], "image_url");
    EXPECT_EQ(j["image_url"]["url"], "data:image/png;base64,abc");
}

// ---- Message VLM tests (Ttest3) ----

TEST(TypesTest, MessageBackwardCompatStringContent) {
    // AC-6: text-only message with no parts → content is a JSON string.
    Message msg;
    msg.role = MessageRole::USER;
    msg.content = "hello";
    json j = msg.toJson();
    EXPECT_TRUE(j["content"].is_string());
    EXPECT_EQ(j["content"], "hello");
}

TEST(TypesTest, MessageToJsonArrayForm) {
    // AC-7: with parts set, content is a JSON array of parts.
    Message msg;
    msg.role = MessageRole::USER;
    msg.parts = std::vector<ContentPart>{
        ContentPart::makeText("look"),
        ContentPart::makeImageUrl("data:image/png;base64,abc"),
    };
    json j = msg.toJson();
    ASSERT_TRUE(j["content"].is_array());
    EXPECT_EQ(j["content"].size(), 2u);
    EXPECT_EQ(j["content"][0]["type"], "text");
    EXPECT_EQ(j["content"][0]["text"], "look");
    EXPECT_EQ(j["content"][1]["type"], "image_url");
    EXPECT_EQ(j["content"][1]["image_url"]["url"], "data:image/png;base64,abc");
}

TEST(TypesTest, MessageFromUserTextOnly) {
    Message m = Message::fromUser("plain text", {});
    json j = m.toJson();
    EXPECT_TRUE(j["content"].is_string());
    EXPECT_EQ(j["content"], "plain text");
}

TEST(TypesTest, MessageFromUserWithOneImage) {
    std::vector<std::uint8_t> pngBytes = {0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,0,0,0,13,0,0,0,0};
    Image img = Image::fromBytes(pngBytes);
    Message m = Message::fromUser("hello", {img});
    json j = m.toJson();
    ASSERT_TRUE(j["content"].is_array());
    EXPECT_EQ(j["content"].size(), 2u);
    EXPECT_EQ(j["content"][0]["type"], "text");
    EXPECT_EQ(j["content"][0]["text"], "hello");
    EXPECT_EQ(j["content"][1]["type"], "image_url");
    std::string url = j["content"][1]["image_url"]["url"].get<std::string>();
    EXPECT_EQ(url.rfind("data:image/png;base64,", 0), 0u);
}

TEST(TypesTest, MessageFromUserWithMultipleImages) {
    std::vector<std::uint8_t> pngBytes = {0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,0,0,0,13,0,0,0,0};
    std::vector<std::uint8_t> jpgBytes = {0xFF,0xD8,0xFF,0xE0,0,0x10,'J','F','I','F',0,0,0,0,0,0};
    Image a = Image::fromBytes(pngBytes);
    Image b = Image::fromBytes(jpgBytes);
    Message m = Message::fromUser("see these", {a, b});
    json j = m.toJson();
    ASSERT_EQ(j["content"].size(), 3u);
    EXPECT_EQ(j["content"][0]["type"], "text");
    EXPECT_EQ(j["content"][1]["image_url"]["url"].get<std::string>().substr(0, 22),
              "data:image/png;base64,");
    EXPECT_EQ(j["content"][2]["image_url"]["url"].get<std::string>().substr(0, 23),
              "data:image/jpeg;base64,");
}

TEST(TypesTest, MessageFromUserEmptyTextImageOnly) {
    // AC-8b: empty text + images → array with ONLY image parts.
    std::vector<std::uint8_t> pngBytes = {0x89,'P','N','G',0x0D,0x0A,0x1A,0x0A,0,0,0,13,0,0,0,0};
    Image img = Image::fromBytes(pngBytes);
    Message m = Message::fromUser("", {img});
    json j = m.toJson();
    ASSERT_TRUE(j["content"].is_array());
    EXPECT_EQ(j["content"].size(), 1u);
    EXPECT_EQ(j["content"][0]["type"], "image_url");
}

TEST(TypesTest, MessageToolMessageUnaffected) {
    Message msg;
    msg.role = MessageRole::TOOL;
    msg.content = "tool output";
    msg.name = "my_tool";
    msg.toolCallId = "call_42";
    json j = msg.toJson();
    EXPECT_EQ(j["role"], "tool");
    EXPECT_TRUE(j["content"].is_string());
    EXPECT_EQ(j["content"], "tool output");
    EXPECT_EQ(j["name"], "my_tool");
    EXPECT_EQ(j["tool_call_id"], "call_42");
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
